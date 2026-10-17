"""Which definitions a diff removes or changes, and who still references them.

A reviewer who sees only the diff cannot know whether a renamed function has
callers outside it, so a model warns that "other callers may break" whether
or not any exist. This module answers that from the repository: for each
definition a diff removes or re-signs, it lists every reference by file and
line and says which of those lines the diff touches. The answer comes from a
word search over the working tree (``git grep``, so ignored files stay out),
not from a type checker, so it is a fact about text: a same-named symbol in
another package shows up as a reference too, and the facts say so.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .credscan import redact
from .diffparse import FileDiff

CODE_EXT = (".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".rb", ".sql")
_ID = r"([A-Za-z_$][\w$]*)"
# Definition line patterns by file extension, matched after leading whitespace is stripped.
DEF_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "py": [re.compile(r"^(?:async\s+)?def\s+" + _ID + r"\s*\("), re.compile(r"^class\s+" + _ID)],
    "js": [
        re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*" + _ID),
        re.compile(r"^(?:export\s+)?(?:declare\s+)?(?:abstract\s+)?class\s+" + _ID),
        re.compile(r"^(?:export\s+)?(?:const|let|var)\s+" + _ID + r"\s*[:=]"),
        re.compile(r"^(?:export\s+)?(?:declare\s+)?(?:type|interface|enum)\s+" + _ID),
    ],
    "go": [re.compile(r"^func\s+(?:\([^)]*\)\s*)?" + _ID + r"\s*\("), re.compile(r"^type\s+" + _ID + r"\s")],
    "rs": [re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+" + _ID), re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|type)\s+" + _ID)],
    "rb": [re.compile(r"^def\s+(?:self\.)?" + _ID), re.compile(r"^(?:class|module)\s+" + _ID)],
}
_LANG = {".py": "py", ".pyi": "py", ".ts": "js", ".tsx": "js", ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js", ".go": "go", ".rs": "rs", ".rb": "rb"}
MAX_REFS = 50
IMPORT_RE = re.compile(r"^\s*(?:from\s+\S+\s+import\b|import\b|export\s+.*\bfrom\b|.*\brequire\s*\()")


@dataclass
class SymbolChange:
    name: str
    path: str
    kind: str  # removed | signature_changed
    line: int | None  # new-side line of the definition, or where the removed one stood
    old: str
    new: str | None
    refs: list[dict[str, Any]] = field(default_factory=list)  # {path, line, text, kind: import | use, in_diff}
    search_failed: bool = False  # git grep could not run, so only the overlay (the diff's own files) was searched
    truncated: bool = False  # the search stopped at MAX_REFS references; more exist

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "path": self.path, "kind": self.kind, "line": self.line, "old": self.old, "new": self.new, "refs": self.refs, "search_failed": self.search_failed, "truncated": self.truncated}

    @property
    def outside(self) -> list[dict[str, Any]]:
        return [r for r in self.refs if not r["in_diff"]]


def _lang(path: str) -> str | None:
    return _LANG.get(Path(path).suffix.lower())


def _definition(lang: str, text: str) -> str | None:
    s = text.strip()
    for pat in DEF_PATTERNS[lang]:
        if m := pat.match(s):
            return m.group(1)
    return None


def changed_definitions(fd: FileDiff, new_text: str | None) -> list[SymbolChange]:
    """Definitions on removed lines of ``fd`` that no added line defines again (removed), or defines differently (signature changed)."""
    lang = _lang(fd.path)
    if not lang or fd.binary or fd.status == "deleted":
        return []
    old: dict[str, tuple[str, int | None]] = {}
    new: dict[str, str] = {}
    for h in fd.hunks:
        for i, (op, _n, text) in enumerate(h.lines):
            name = _definition(lang, text)
            if not name:
                continue
            if op == "-":
                where = next((m for o, m, _ in h.lines[i:] if m is not None), h.new_end)
                old.setdefault(name, (text.strip(), where))
            elif op == "+":
                new.setdefault(name, text.strip())
    still_defined = {_definition(lang, t) for t in new_text.splitlines()} - {None} if new_text is not None else set(new)
    out = []
    for name, (old_line, where) in old.items():
        if name in new:
            if new[name] != old_line:
                out.append(SymbolChange(name, fd.path, "signature_changed", where, old_line, new[name]))
        elif name not in still_defined:
            out.append(SymbolChange(name, fd.path, "removed", where, old_line, None))
    return out


def _git_grep_files(repo_root: Path, name: str) -> list[str] | None:
    """Files that mention ``name`` as a whole word, or None when the search itself failed (not a git repository, git missing, timeout)."""
    cmd = ["git", "-C", str(repo_root), "grep", "-l", "-w", "-F", "-I", "--untracked", "-e", name, "--", *[f"*{e}" for e in CODE_EXT]]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode not in (0, 1):  # 1 is "no match"
        return None
    return [p for p in res.stdout.splitlines() if p]


@dataclass
class Search:
    refs: list[tuple[str, int, str]] = field(default_factory=list)  # (path, line, text)
    failed: bool = False  # the repository could not be searched; refs come from ``files`` only
    truncated: bool = False  # stopped at MAX_REFS


def search(repo_root: Path, name: str, files: dict[str, str]) -> Search:
    """Every (path, line, text) that mentions ``name`` as a whole word, in the repository or in ``files`` (text that overrides or extends the tree), and whether that search was complete."""
    word = re.compile(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])")
    found = _git_grep_files(repo_root, name)
    result = Search(failed=found is None)
    paths = list(dict.fromkeys([*(found or []), *files]))
    out = result.refs
    for p in paths:
        if not p.lower().endswith(CODE_EXT):
            continue
        text = files.get(p)
        if text is None:
            try:
                full = (repo_root / p).resolve()
                if not full.is_relative_to(repo_root.resolve()) or not full.is_file():
                    continue
                text = full.read_text(errors="replace")
            except OSError:
                continue
        for i, line in enumerate(text.splitlines(), 1):
            if word.search(line):
                out.append((p, i, line.strip()[:160]))
                if len(out) >= MAX_REFS:
                    result.truncated = True
                    return result
    return result


def references(repo_root: Path, name: str, files: dict[str, str]) -> list[tuple[str, int, str]]:
    return search(repo_root, name, files).refs


def symbol_changes(files: list[FileDiff], repo_root: Path, overlay: dict[str, str]) -> list[SymbolChange]:
    """Removed and re-signed definitions across the diff, each with its references and whether the diff touches them."""
    by_path = {f.path: f for f in files}
    out = []
    for fd in files:
        for sc in changed_definitions(fd, overlay.get(fd.path)):
            # Skip the definition's own line, and the old definition's replacement when it was renamed in place.
            def_lines = {(sc.path, sc.line)}
            found = search(repo_root, sc.name, overlay)
            sc.search_failed, sc.truncated = found.failed, found.truncated
            for p, ln, text in found.refs:
                if (p, ln) in def_lines or (p == sc.path and _definition(_lang(p) or "py", text) == sc.name):
                    continue
                fd2 = by_path.get(p)
                # Reference text is quoted in the facts and the review JSON, so a credential on that line is redacted here, once.
                sc.refs.append({"path": p, "line": ln, "text": redact(text), "kind": "import" if IMPORT_RE.match(text) else "use", "in_diff": bool(fd2 and fd2.contains(ln))})
            sc.refs.sort(key=lambda r: (r["path"], r["line"]))
            out.append(sc)
    return out
