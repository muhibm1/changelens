"""``orion review``: a structured review of any diff, in four stages.

1. **Gather.** Parse the diff into hunks with new-side line numbers and run the
   deterministic pre-flight on the same change. Its verdict is context and the
   source of deterministic findings, not the output.
2. **Generate.** One provider call returns candidate findings as JSON. Each
   names a location and the evidence it relies on.
3. **Validate.** Code, not a model. A finding outside the diff is rejected; an
   evidence reference that does not resolve is removed; a finding left with no
   valid evidence is marked uncertain; a finding that cites a firing
   constraint cannot be less severe than the constraint.
4. **Format.** Deterministic findings first and always, then valid model
   findings, deduplicated and ordered by severity; uncertain and rejected
   findings and unresolved paths stay visible in their own lists.

Facts and judgement stay apart: every finding carries ``source`` of
``deterministic`` or ``model`` and a ``status``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .changes import collect_changes
from .constraints import Constraint
from .credscan import redact, secret_findings
from .diffparse import FileDiff, batches, both_sides, git_diff, parse, render_file
from .providers import Provider, ProviderError
from .risk import RiskModel
from .store import Store
from .symbols import SymbolChange, is_linked, symbol_changes
from .verdict import preflight

SEVERITIES = ("inform", "warn", "block")
RANK = {s: i for i, s in enumerate(SEVERITIES)}
CATEGORIES = ("correctness", "security", "performance", "maintainability", "data_contract", "operational")
FILE_CONTEXT_LINES = 400  # changed files up to this long go to the model whole
FILE_CONTEXT_BYTES = 200_000
# Never send a whole file that may hold credentials; its hunks still go, as before.
SECRET_PATH_RE = re.compile(r"(^|/)(\.env(\..*)?|.*\.(pem|key|p12|pfx|jks)|id_(rsa|dsa|ecdsa|ed25519)|credentials(\.\w+)?|\.netrc|\.npmrc|\.pypirc)$", re.I)
EVIDENCE_RE = re.compile(r"^(hunk|ref|constraint|incident|event|node|edge):(.+)$")

FINDINGS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "title": {"type": "string"},
                    "body": {"type": "string"},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["path", "line", "severity", "category", "title", "body", "evidence"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["findings"],
    "additionalProperties": False,
}

SYSTEM = """You review a code change for a developer or a coding agent.
Report only real problems a reviewer would raise: bugs, security issues, broken contracts, risky operational changes, clear maintainability problems. Do not restate the diff, praise it, or comment on style a linter would catch.

Every finding must:
- point at a path and a line that appear in the diff (the numbers in the left column are new-file line numbers);
- cite evidence: `hunk:<path>:<line>` for a line in the diff, `ref:<path>:<line>` for a reference listed under "System facts", or an id listed there written exactly as given (`constraint:CON-031`, `incident:INC-230`, `event:ev_...`, `node:model:fct_orders`, `edge:e_...`);
- use severity `block` only for a change that should not merge as is, `warn` for one that needs a check or a sign-off, `inform` otherwise.

Never invent owners, dependencies, lineage, constraints, incidents or approvals. If the system facts do not cover something, do not claim it. When the facts list the references to a changed symbol, they come from a search of the whole repository: do not warn about callers or consumers the facts show do not exist, and do not repeat a reference the facts say the diff already updates. An empty findings list is a valid answer."""


def _norm_path(path: str) -> str:
    """Drop a leading ``./``; ``lstrip`` would also eat the dot of ``.github/``."""
    path = path.strip()
    while path.startswith("./"):
        path = path[2:]
    return path


@dataclass
class ReviewContext:
    files: list[FileDiff]
    verdict: dict[str, Any] | None
    known: dict[str, set[str]] = field(default_factory=dict)  # evidence kind -> valid values
    new_text: dict[str, str] = field(default_factory=dict)  # path -> full new file, when known
    symbols: list[SymbolChange] = field(default_factory=list)
    secrets: list[dict[str, Any]] = field(default_factory=list)


def _known_ids(store: Store, verdict: dict[str, Any] | None, constraints: list[Constraint]) -> dict[str, set[str]]:
    known: dict[str, set[str]] = {"constraint": {c.id for c in constraints}, "incident": set(), "event": set(), "node": set(), "edge": set(), "ref": set()}
    if verdict:
        for h in verdict["history"]:
            known["event"].add(h["event"])
            if h["kind"] in ("postmortem_filed", "incident_opened") and h["ref"]:
                known["incident"].add(h["ref"])
        for a in verdict["affected"]:
            known["node"].add(a["asset"])
            for e in a["evidence"]:
                if e.startswith("edge:"):
                    known["edge"].add(e[5:])
        known["node"].update(verdict["change"]["touched"])
    for (ref,) in store.con.execute("SELECT DISTINCT source_ref FROM events WHERE kind = 'postmortem_filed' AND source_ref IS NOT NULL").fetchall():
        known["incident"].add(ref)
    return known


def gather(store: Store, repo_root: Path, diff_text: str, *, constraints: list[Constraint], risk_model: RiskModel, base_ref: str | None = None, scope: list[str] | None = None, ignore: list[str] | None = None, new_files: dict[str, str] | None = None, repo_files: dict[str, str] | None = None, search_root: Path | None = None) -> ReviewContext:
    """``new_files``: the new text of changed files when the tree does not hold it. ``repo_files``: other files to search for references as if they were in the tree. ``search_root``: where that search runs (default: the repository)."""
    files = parse(diff_text)
    verdict = None
    new_text: dict[str, str] = {}
    for f in files:
        if f.status == "deleted" or f.binary or SECRET_PATH_RE.search(f.path):
            continue
        if new_files and f.path in new_files:
            text = new_files[f.path]
        else:
            current = _read_in_repo(repo_root, f.path)
            # With a base ref the working tree is the new side; a diff from elsewhere may sit on either side.
            sides = (None, current) if base_ref else both_sides(f, current)
            text = sides[1] if sides else None
        if text is not None and _is_new_side(f, text):
            new_text[f.path] = text
    try:
        if base_ref:
            cs = collect_changes(store, repo_root, base_ref=base_ref, scope=scope, ignore=ignore)
        else:
            file_changes, fallback = [], []
            for f in files:
                p = repo_root / f.path
                current = p.read_text() if p.is_file() else None
                sides = both_sides(f, current)
                if f.status == "deleted":
                    file_changes.append((f.path, current or "", None))
                elif sides is not None:
                    file_changes.append((f.path, sides[0], sides[1]))
                else:
                    fallback.append(f.path)
            cs = collect_changes(store, repo_root, file_changes=file_changes or None, paths=fallback or None, scope=scope, ignore=ignore)
        verdict = preflight(store, cs, constraints, risk_model, record=False)
    except Exception as e:  # noqa: BLE001 - the review still runs without system context, and says so
        verdict = {"error": f"pre-flight failed: {e}", "severity": "inform", "constraints": [], "history": [], "affected": [], "change": {"touched": [], "unresolved": [], "ops": [], "ignored": []}, "summary": "", "risk": {"score": 0}}
    ctx = ReviewContext(files, verdict, _known_ids(store, verdict, constraints), new_text)
    ctx.symbols = symbol_changes(files, search_root or repo_root, {**(repo_files or {}), **new_text})
    ctx.known["ref"].update(f"{r['path']}:{r['line']}" for sc in ctx.symbols for r in sc.refs)
    ctx.secrets = secret_findings(files)
    return ctx


def _symbol_facts(ctx: ReviewContext) -> list[str]:
    lines = []
    for sc in ctx.symbols:
        what = "removed" if sc.kind == "removed" else f"signature changed to `{sc.new}`"
        head = f"- symbol `{sc.name}` defined in {sc.path}:{sc.line} ({what}; was `{sc.old}`):"
        scope = "in the files of this diff (the rest of the repository could not be searched, so other callers may exist)" if sc.search_failed else "anywhere in the repository"
        if not sc.refs:
            lines.append(f"{head} no other reference to this name {scope}")
            continue
        unparsed = [r for r in sc.refs if r["scope"] == "unknown"]
        if not unparsed:
            how = "from parsed syntax, so comments, strings and same-named definitions are excluded"
        elif len(unparsed) == len(sc.refs):
            how = "from a whole-word text search, so a comment, string or same-named symbol counts too"
        else:
            how = f"from parsed syntax except {len(unparsed)} in files that could not be parsed ({', '.join(sorted({r['path'] for r in unparsed})[:5])}), which are whole-word text matches"
        linked = [r for r in sc.refs if is_linked(r)]
        rest = [r for r in sc.refs if not is_linked(r) and not r["in_diff"]]
        inside = [r for r in linked if r["in_diff"] and r["kind"] != "import"]
        outside = [r for r in linked if not r["in_diff"] and r["kind"] != "import"]
        imports = [r for r in linked if r["kind"] == "import"]
        members = [r for r in rest if r["kind"] in ("method_call", "attribute")]
        unlinked = [r for r in rest if r["kind"] not in ("method_call", "attribute")]
        parts = [f"references {how}"]
        if inside:
            parts.append(f"{len(inside)} use{'s' if len(inside) != 1 else ''} on lines this diff changes (" + ", ".join(f"{r['path']}:{r['line']}" for r in inside[:10]) + ")")
        if outside:
            parts.append(f"{len(outside)} use{'s' if len(outside) != 1 else ''} on lines this diff does not touch (" + ", ".join(f"ref:{r['path']}:{r['line']} `{r['text'][:80]}`" for r in outside[:10]) + ")")
        else:
            parts.append("no use outside this diff in the same file, in a file that imports the name, or in the same package")
        if imports:
            parts.append("imported at " + ", ".join(f"ref:{r['path']}:{r['line']}" for r in imports[:10]))
        if members:
            parts.append(f"{len(members)} method or attribute access{'es' if len(members) != 1 else ''} `.{sc.name}` on some object, which may or may not be this definition (" + ", ".join(f"ref:{r['path']}:{r['line']}" for r in members[:10]) + ")")
        if unlinked:
            why = "do not import the name" + (" or define their own" if any(r["scope"] == "defines_own" for r in unlinked) else "") + (" or import it from another module" if any(r["scope"] == "imports_other" for r in unlinked) else "")
            parts.append(f"{len(unlinked)} mention{'s' if len(unlinked) != 1 else ''} in files that {why}, so probably a different `" + sc.name + "` (" + ", ".join(f"ref:{r['path']}:{r['line']}" for r in unlinked[:10]) + ")")
        if sc.search_failed:
            parts.append("only the files of this diff were searched; the rest of the repository could not be, so other callers may exist")
        if sc.truncated:
            parts.append(f"the search stopped at {len(sc.refs)} references, so there are more")
        lines.append(head + " " + "; ".join(parts))
    return lines


def _facts(ctx: ReviewContext) -> str:
    v = ctx.verdict
    if not v or not v["change"]["ops"]:
        return "\n".join(["No platform assets resolved for this diff; review it as ordinary code.", *_symbol_facts(ctx)])
    lines = [f"Deterministic pre-flight: severity {v['severity']}, risk {v['risk']['score']}. {v.get('summary', '')}"]
    for o in v["change"]["ops"]:
        d = o.get("details") or {}
        change = f": `{d['from']}` -> `{d['to']}`" if "from" in d and "to" in d else ""
        lines.append(f"- op {o['kind']} on node:{o['asset']}" + (f" column {o['column']}" if o.get("column") else "") + change)
    for c in v["constraints"]:
        if c["status"] == "fires":
            lines.append(f"- constraint:{c['id']} ({c['kind']}, {c['severity']}) fires: {c['detail']}. Reason: {c['reason']}")
    readers = v.get("readers", [])
    for r in readers:
        if r["op"] in ("drop_column", "rename_column"):
            state = "also changed in this diff; check it no longer selects the old name" if r["in_change"] else "not changed in this diff, so it still selects the old name and will fail"
        else:
            state = "also changed in this diff" if r["in_change"] else "not changed in this diff; it still builds, but the values it reads change"
        lines.append(f"- reader node:{r['reader']} selects {r['column']} ({r['op']}) {' '.join(r.get('evidence') or [])}: {state}".replace(")  :", "):"))
    for col in sorted({r["column"] for r in readers if r["op"] in ("drop_column", "rename_column")}):
        if all(r["in_change"] for r in readers if r["column"] == col):
            lines.append(f"- every model that selects {col} directly is changed in this diff; if those changes stop selecting the old name, assets further downstream are not broken by it")
    for a in v["affected"][:20]:
        edge = next((e for e in a["evidence"] if e.startswith("edge:")), "")
        lines.append(f"- downstream node:{a['asset']} tier {a['tier']} owner {a['owner']} {edge} (reached by lineage; whether it breaks depends on what it reads)".rstrip())
    for h in v["history"][:10]:
        ref = f"incident:{h['ref']}" if h["kind"] in ("postmortem_filed", "incident_opened") else f"event:{h['event']}"
        lines.append(f"- history {ref} {h['kind']} {str(h['occurred_at'])[:10]}: {h.get('title') or ''} ({h['relevance']})")
    return "\n".join(lines + _symbol_facts(ctx))


def _read_in_repo(repo_root: Path, path: str) -> str | None:
    """The file's text, or None if it is missing, too big, unreadable, or resolves outside the repo (a diff can name ``../x``)."""
    root = repo_root.resolve()
    p = (root / path).resolve()
    if not p.is_relative_to(root):
        return None
    try:
        return p.read_text(errors="replace") if p.is_file() and p.stat().st_size <= FILE_CONTEXT_BYTES else None
    except OSError:
        return None


def _is_new_side(fd: FileDiff, text: str) -> bool:
    """True when every added and context line of the diff sits at its line number in ``text``."""
    lines = text.splitlines()
    return all(n is not None and n <= len(lines) and lines[n - 1] == t for h in fd.hunks for op, n, t in h.lines if op != "-")


def _file_context(ctx: ReviewContext, files: list[FileDiff], max_lines: int = FILE_CONTEXT_LINES, max_chars: int = 40_000) -> str:
    """Whole new versions of small changed files, numbered like the diff, so the model sees definitions outside the hunks.

    A file that is too long, or does not fit the budget, is named as omitted so the model does not take silence for an empty file.
    """
    parts, used = [], 0
    for f in files:
        text = ctx.new_text.get(f.path)
        if text is None:
            continue
        lines = text.splitlines()
        if not lines:
            continue
        if len(lines) > max_lines:
            parts.append(f"=== {f.path}: whole file not shown ({len(lines)} lines; only its hunks below)\n")
            continue
        block = f"=== {f.path} (whole file after the change)\n" + "\n".join(f"{i:>5}   {t}" for i, t in enumerate(lines, 1)) + "\n"
        if used + len(block) > max_chars:
            parts.append(f"=== {f.path}: whole file not shown (over the context budget; only its hunks below)\n")
            continue
        parts.append(block)
        used += len(block)
    return "\n".join(parts)


def _add_usage(total: dict[str, Any] | None, usage: Any) -> dict[str, Any] | None:
    if not isinstance(usage, dict):
        return total
    total = dict(total or {})
    for k, v in usage.items():
        if isinstance(v, (int, float)):
            total[k] = total.get(k, 0) + v
    return total


def generate(provider: Provider, ctx: ReviewContext, *, file_context: bool = True, groups: list[list[FileDiff]] | None = None, omitted: list[str] | None = None) -> tuple[list[dict[str, Any]], str | None, dict[str, Any] | None]:
    """Candidate findings from the model, the first provider error, and summed token usage.

    A diff that does not fit one call is reviewed in several, one group of files per call, each with the
    same system facts and the whole-file context for its own files. Findings are pooled; a later group is
    still reviewed when an earlier call fails. ``groups`` and ``omitted`` come from ``batches`` (computed
    here when not given); omitted files are named in the facts so the model knows they exist.
    """
    if groups is None or omitted is None:
        groups, omitted = batches(ctx.files)
    facts = _facts(ctx)
    if omitted:
        facts += "\n- files in this change whose hunks are too large to show, so they are not under review here: " + ", ".join(omitted)
    candidates: list[dict[str, Any]] = []
    error: str | None = None
    usage: dict[str, Any] | None = None
    for i, group in enumerate(groups, 1):
        diff = "\n".join(render_file(f) for f in group)
        if not diff.strip():
            continue
        files = _file_context(ctx, group) if file_context else ""
        context = f"Changed files in full, for context only. Findings must still point at lines inside the diff below:\n{files}\n" if files else ""
        part = f" (part {i} of {len(groups)}; other files of this change are reviewed separately)" if len(groups) > 1 else ""
        user = redact(f"System facts:\n{facts}\n\n{context}Diff{part}:\n{diff}")
        try:
            out = provider.complete_json(SYSTEM, user, FINDINGS_SCHEMA, schema_name="report_findings")
        except ProviderError as e:
            error = error or str(e)
            continue
        usage = _add_usage(usage, getattr(provider, "last_usage", None))
        items = out.get("findings") if isinstance(out, dict) else None
        candidates.extend(f for f in (items or []) if isinstance(f, dict))
    return candidates, error, usage


def _file(ctx: ReviewContext, path: str) -> FileDiff | None:
    path = _norm_path(path)
    return next((f for f in ctx.files if f.path == path), None)


def _check_evidence(ctx: ReviewContext, ref: str) -> bool:
    m = EVIDENCE_RE.match(ref.strip())
    if not m:
        return False
    kind, value = m.group(1), m.group(2)
    if kind == "hunk":
        path, _, line = value.rpartition(":")
        f = _file(ctx, path)
        return bool(f and line.isdigit() and f.contains(int(line)))
    return value in ctx.known.get(kind, set())


def validate(candidates: list[dict[str, Any]], ctx: ReviewContext) -> list[dict[str, Any]]:
    firing = {c["id"]: c["severity"] for c in (ctx.verdict or {}).get("constraints", []) if c["status"] == "fires"}
    out = []
    for raw in candidates:
        f = {
            "source": "model",
            "path": _norm_path(str(raw.get("path") or "")),
            "line": raw.get("line"),
            "end_line": raw.get("end_line"),
            "severity": raw.get("severity") if raw.get("severity") in SEVERITIES else "inform",
            "category": raw.get("category") if raw.get("category") in CATEGORIES else "correctness",
            "title": str(raw.get("title") or "").strip()[:200],
            "body": str(raw.get("body") or "").strip()[:2000],
            "evidence": [],
            "status": "valid",
            "notes": [],
        }
        fd = _file(ctx, f["path"])
        if fd is None:
            f["status"], f["notes"] = "rejected", [f"{f['path'] or '(no path)'} is not in the diff"]
            out.append(f)
            continue
        if not isinstance(f["line"], int) or not fd.contains(f["line"], slack=0):
            f["status"], f["notes"] = "rejected", [f"line {f['line']} is outside the changed hunks of {f['path']}"]
            out.append(f)
            continue
        if not f["title"]:
            f["status"], f["notes"] = "rejected", ["finding has no title"]
            out.append(f)
            continue
        dropped = []
        for ref in raw.get("evidence") or []:
            ref = str(ref).strip()
            (f["evidence"] if _check_evidence(ctx, ref) else dropped).append(ref)
        if dropped:
            f["notes"].append("removed evidence that does not resolve: " + ", ".join(dropped))
        if not f["evidence"]:
            f["status"] = "uncertain"
            f["notes"].append("no valid evidence")
        cited = [e.split(":", 1)[1] for e in f["evidence"] if e.startswith("constraint:")]
        floor = max((firing[c] for c in cited if c in firing), key=lambda s: RANK[s], default=None)
        if floor and RANK[floor] > RANK[f["severity"]]:
            f["notes"].append(f"severity raised from {f['severity']} to {floor}: it cites a firing {floor} constraint")
            f["severity"] = floor
        out.append(f)
    return out


def code_findings(ctx: ReviewContext) -> list[dict[str, Any]]:
    """Findings that need no platform graph: committed credentials, and removed symbols the diff leaves referenced."""
    out = list(ctx.secrets)
    for sc in ctx.symbols:
        if sc.kind != "removed" or not sc.linked_outside:
            continue
        refs = sc.linked_outside
        unparsed = [r for r in refs if r["scope"] == "unknown"]
        text_matches = ", ".join(f"{r['path']}:{r['line']}" for r in unparsed[:5])
        how = "A whole-word search of the repository found them" if len(unparsed) == len(refs) else "They are uses or imports of the name in the same file, in files that import it or its module, or in the same package, found by parsing those files" + (f" (except {text_matches}, whole-word text matches in files that could not be parsed)" if unparsed else "")
        where = ", ".join(f"{r['path']}:{r['line']}" for r in refs[:5]) + (f" and {len(refs) - 5} more" if len(refs) > 5 else "")
        out.append({"source": "deterministic", "path": sc.path, "line": sc.line, "end_line": None, "severity": "warn", "category": "correctness", "title": f"`{sc.name}` removed but still referenced at {where}", "body": f"This diff removes the definition of `{sc.name}` from {sc.path} and does not change the lines that reference it. {how}; if they refer to a different `{sc.name}`, say so, otherwise update or remove them in the same change.", "evidence": [f"hunk:{sc.path}:{sc.line}"] + [f"ref:{r['path']}:{r['line']}" for r in refs[:10]], "status": "valid", "notes": []})
    return out


def deterministic_findings(ctx: ReviewContext) -> list[dict[str, Any]]:
    v = ctx.verdict
    if not v or not v["change"]["ops"]:
        return code_findings(ctx)
    op_files: dict[str, str] = {}
    for o in v["change"]["ops"]:
        for e in o.get("evidence", []):
            if e.startswith("diff:"):
                op_files.setdefault(o["asset"], e[5:])

    def locate(assets: list[str]) -> tuple[str | None, int | None]:
        for a in assets:
            p = op_files.get(a)
            fd = _file(ctx, p) if p else None
            if fd:
                return fd.path, fd.first_changed_line()
        if len(ctx.files) == 1:
            return ctx.files[0].path, ctx.files[0].first_changed_line()
        return None, None

    out: list[dict[str, Any]] = []
    incidents = [h for h in v["history"] if h["kind"] == "postmortem_filed"]
    for c in v["constraints"]:
        if c["status"] != "fires":
            continue
        path, line = locate([o["asset"] for o in v["change"]["ops"]])
        same = [h for h in incidents if h["same_kind_of_change"]]
        extra = [("incident:" + e[6:]) if e.startswith("event:INC-") else e for e in c.get("evidence", []) if not e.startswith("constraint:")]
        ev = [f"constraint:{c['id']}"] + [f"incident:{h['ref']}" for h in same] + extra
        body = f"{c['detail']}. {c['reason']}" + (f" The same kind of change caused {', '.join(h['ref'] for h in same)}." if same else "")
        if c.get("approvers"):
            body += f" Sign-off needed from {', '.join(c['approvers'])}."
        out.append({"source": "deterministic", "path": path, "line": line, "end_line": None, "severity": c["severity"], "category": "data_contract", "title": f"{c['id']} {c['kind']} fires", "body": body, "evidence": list(dict.fromkeys(ev)), "status": "valid", "notes": []})
    for r in v.get("readers", []):
        if r["in_change"] or r["op"] not in ("drop_column", "rename_column"):
            continue
        path, line = locate([r["asset"]])
        verb = "drops" if r["op"] == "drop_column" else "renames"
        out.append({"source": "deterministic", "path": path, "line": line, "end_line": None, "severity": "warn", "category": "data_contract", "title": f"{r['reader'].split(':', 1)[1]} still selects {r['column']}", "body": f"This change {verb} {r['column']}, and {r['reader']} selects it directly but is not changed in this diff, so it will fail to build. Update it in the same change.", "evidence": [f"node:{r['reader']}"] + r["evidence"], "status": "valid", "notes": []})
    if not out and v["severity"] in ("warn", "block"):
        tier1 = [a for a in v["affected"] if a["tier"] == 1]
        path, line = locate(v["change"]["touched"])
        out.append({"source": "deterministic", "path": path, "line": line, "end_line": None, "severity": v["severity"], "category": "operational", "title": f"Change reaches {len(v['affected'])} downstream assets, {len(tier1)} tier 1", "body": v["summary"], "evidence": [f"node:{a['asset']}" for a in tier1[:5]] or [f"node:{t}" for t in v["change"]["touched"][:3]], "status": "valid", "notes": []})
    return out + code_findings(ctx)


def _dedupe(det: list[dict[str, Any]], model: list[dict[str, Any]]) -> list[dict[str, Any]]:
    covered = {(d["path"], e) for d in det for e in d["evidence"] if e.startswith("constraint:")}
    kept = []
    for m in model:
        # A finding that only restates a firing constraint is a duplicate; one that points at a diff line says something new.
        restates = not any(e.startswith("hunk:") for e in m["evidence"])
        if m["status"] == "valid" and restates and any((m["path"], e) in covered for e in m["evidence"] if e.startswith("constraint:")):
            continue
        key = (m["path"], m["line"], m["title"].lower())
        if any((k["path"], k["line"], k["title"].lower()) == key for k in kept):
            continue
        kept.append(m)
    return kept


def review(store: Store, repo_root: Path, provider: Provider, *, constraints: list[Constraint], risk_model: RiskModel, base_ref: str | None = None, diff_text: str | None = None, scope: list[str] | None = None, ignore: list[str] | None = None, new_files: dict[str, str] | None = None, repo_files: dict[str, str] | None = None, search_root: Path | None = None, file_context: bool = True) -> dict[str, Any]:
    if diff_text is None:
        if not base_ref:
            raise ValueError("give a base ref or a diff")
        diff_text = git_diff(repo_root, base_ref)
    ctx = gather(store, repo_root, diff_text, constraints=constraints, risk_model=risk_model, base_ref=base_ref, scope=scope, ignore=ignore, new_files=new_files, repo_files=repo_files, search_root=search_root)
    groups, omitted = batches(ctx.files)
    candidates, provider_error, usage = generate(provider, ctx, file_context=file_context, groups=groups, omitted=omitted)
    checked = validate(candidates, ctx)
    det = deterministic_findings(ctx)
    model = _dedupe(det, checked)
    valid = det + sorted([m for m in model if m["status"] == "valid"], key=lambda f: (-RANK[f["severity"]], f["path"], f["line"] or 0))
    severity = max([f["severity"] for f in valid] + [(ctx.verdict or {}).get("severity", "inform")], key=lambda s: RANK[s])
    rid = "rv_" + hashlib.sha1((diff_text + provider.name).encode()).hexdigest()[:16]
    v = ctx.verdict or {}
    return {
        "review_id": rid,
        "issued_at": datetime.now(UTC).isoformat(),
        "provider": provider.name,
        "provider_error": provider_error,
        "severity": severity,
        "files": [{"path": f.path, "status": f.status, "hunks": len(f.hunks)} for f in ctx.files],
        "findings": valid,
        "uncertain": [m for m in model if m["status"] == "uncertain"],
        "rejected": [m for m in model if m["status"] == "rejected"],
        "unresolved": v.get("change", {}).get("unresolved", []),
        "omitted": omitted,
        "symbols": [sc.to_dict() for sc in ctx.symbols],
        "verdict_id": v.get("verdict_id"),
        "verdict_summary": v.get("summary") or v.get("error"),
        "usage": usage,
        "counts": {"deterministic": len(det), "model_valid": sum(m["status"] == "valid" for m in model), "uncertain": sum(m["status"] == "uncertain" for m in model), "rejected": sum(m["status"] == "rejected" for m in model)},
    }


def to_markdown(r: dict[str, Any]) -> str:
    lines = [f"**Orion review: {r['severity'].upper()}** ({len(r['findings'])} findings, provider `{r['provider']}`, review `{r['review_id']}`)", ""]
    if r["provider_error"]:
        lines += [f"> Model review unavailable: {r['provider_error']}. Deterministic findings only.", ""]
    for f in r["findings"]:
        loc = f"`{f['path']}:{f['line']}`" if f["path"] else "(no single location)"
        lines.append(f"- **{f['severity']}** {loc} {f['title']} _({f['source']}, {f['category']})_")
        lines.append(f"  {f['body']}")
        lines.append(f"  evidence: {', '.join(f['evidence'])}")
    if not r["findings"]:
        lines.append("No findings.")
    if r["uncertain"]:
        lines += ["", "<details><summary>Uncertain (no valid evidence)</summary>", ""]
        lines += [f"- `{f['path']}:{f['line']}` {f['title']}: {'; '.join(f['notes'])}" for f in r["uncertain"]]
        lines.append("</details>")
    if r["rejected"]:
        lines += ["", f"{len(r['rejected'])} model findings rejected (location outside the diff or no title)."]
    if r["unresolved"]:
        lines += ["", "Unresolved: " + ", ".join(str(u.get("path") or u.get("intent")) for u in r["unresolved"])]
    if r.get("omitted"):
        lines += ["", "Not reviewed by the model (a single file's hunks are too large for a call): " + ", ".join(f"`{p}`" for p in r["omitted"])]
    return "\n".join(lines) + "\n"


__all__ = ["FINDINGS_SCHEMA", "SYSTEM", "review", "to_markdown", "validate", "gather", "generate", "deterministic_findings", "json"]
