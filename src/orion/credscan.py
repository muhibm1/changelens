"""Credentials in a diff: found by code, redacted before anything leaves the machine.

The patterns are the well-known token shapes (the same ones gitleaks and
detect-secrets start from) plus an assignment of a long literal to a name
like ``password`` or ``api_key``. A match is a blocking finding with the diff
line as evidence, and the matched span is replaced by ``[redacted]`` in the
text sent to the model, so a review never forwards a secret to a provider.

A line that ends in ``pragma: allowlist secret`` (the detect-secrets
convention) or ``orion: allow-secret`` raises no finding, as does a value that
names itself a placeholder (``EXAMPLE``, ``changeme``, ``${VAR}``). Redaction
ignores the pragma: an allowlisted value is one nobody needs the model to see,
so it is replaced all the same and a real secret marked by mistake still never
leaves the machine.
"""

from __future__ import annotations

import re
from typing import Any

from .diffparse import FileDiff

TOKEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}\b")),
    ("OpenAI API key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}")),
    ("Stripe key", re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{20,}\b")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----")),
    ("JSON web token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("database URL with password", re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^\s:/]+:([^\s@/]{4,})@", re.I)),
]
ASSIGNMENT = re.compile(r"""(?i)\b(?:api[_-]?key|secret[_-]?key|client[_-]?secret|secret|password|passwd|pwd|token|auth[_-]?token|access[_-]?token|access[_-]?key|private[_-]?key)\b\s*[:=]\s*["']([^"'\s]{8,})["']""")
# Assigned values that are obviously not credentials: placeholders, lookups and templates.
PLACEHOLDER = re.compile(r"(?i)^(?:<.*>|\$\{.*\}|\{\{.*\}\}|\$[A-Z_]+|os\.environ.*|process\.env.*|x{4,}|\*{4,}|.*(?:changeme|example|placeholder|your[_-]|dummy|redacted|sample).*)$")
# A token-shaped value is a placeholder only by the documented convention (AWS example keys end in EXAMPLE);
# a real token can contain any word, so the list above must not apply to it.
TOKEN_PLACEHOLDER = re.compile(r"EXAMPLE$")
ALLOW = re.compile(r"(?i)(?:pragma:\s*allowlist\s+secret|orion:\s*allow-secret)\s*$")
KEY_BEGIN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----")
KEY_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----")
KEY_BODY = re.compile(r"^[\s\d+\-]*[A-Za-z0-9+/=]{16,}\s*$")  # a base64 line, possibly behind a diff's line number and marker


def _spans(text: str) -> list[tuple[str, int, int]]:
    """(kind, start, end) of each credential-shaped value in ``text``; only the value, never the name it is assigned to."""
    out = []
    for kind, pat in TOKEN_PATTERNS:
        for m in pat.finditer(text):
            g = 1 if m.groups() else 0
            if not TOKEN_PLACEHOLDER.search(m.group(g)):
                out.append((kind, m.start(g), m.end(g)))
    for m in ASSIGNMENT.finditer(text):
        if not PLACEHOLDER.match(m.group(1)):
            out.append(("credential assigned to a literal", m.start(1), m.end(1)))
    return _merge(out)


def _merge(spans: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """Overlapping spans from two patterns become one, so replacing them never leaves a tail of the value behind."""
    merged: list[tuple[str, int, int]] = []
    for kind, a, b in sorted(spans, key=lambda s: (s[1], -s[2])):
        if merged and a <= merged[-1][2]:
            k, pa, pb = merged[-1]
            merged[-1] = (k, pa, max(pb, b))
        else:
            merged.append((kind, a, b))
    return merged


def find_secrets(text: str) -> list[tuple[str, str]]:
    """(kind, value) for each credential-shaped span in ``text``, unless the line is allowlisted."""
    out = []
    for line in text.splitlines() or [text]:
        if ALLOW.search(line):
            continue
        out.extend((kind, line[a:b]) for kind, a, b in _spans(line))
    return out


def redact(text: str) -> str:
    """``text`` with every credential-shaped value replaced in place, so nothing else in it changes.

    The body of a private key (the base64 lines between BEGIN and END) is redacted line by line too,
    whether the text is a file or a diff with line numbers in front.
    """
    lines = []
    in_key = False
    for line in text.splitlines(keepends=True):
        if in_key:
            if KEY_END.search(line):
                in_key = False
            elif KEY_BODY.match(line):
                lines.append(re.sub(r"[A-Za-z0-9+/=]{16,}", "[redacted]", line))
                continue
            else:
                in_key = False
        if KEY_BEGIN.search(line):
            in_key = True
        # The BEGIN/END lines of a key stay, so the model can still see what the body was.
        for kind, a, b in sorted(_spans(line), key=lambda s: s[1], reverse=True):
            if kind != "private key":
                line = line[:a] + "[redacted]" + line[b:]
        lines.append(line)
    return "".join(lines)


def secret_findings(files: list[FileDiff]) -> list[dict[str, Any]]:
    """Blocking findings for credentials on added lines."""
    findings = []
    for f in files:
        for h in f.hunks:
            for op, n, text in h.lines:
                if op != "+" or n is None:
                    continue
                hits = find_secrets(text)
                if not hits:
                    continue
                kinds = sorted({k for k, _ in hits})
                findings.append({
                    "source": "deterministic",
                    "path": f.path,
                    "line": n,
                    "end_line": None,
                    "severity": "block",
                    "category": "security",
                    "title": f"Possible {', '.join(kinds)} committed",
                    "body": "This line looks like it carries a credential. Remove it, load the value from the environment or a secret store, and rotate the key if it was ever real. The value was redacted before the model saw the diff.",
                    "evidence": [f"hunk:{f.path}:{n}"],
                    "status": "valid",
                    "notes": [],
                })
    return findings
