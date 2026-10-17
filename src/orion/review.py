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
from .diffparse import FileDiff, both_sides, git_diff, parse, render
from .providers import Provider, ProviderError
from .risk import RiskModel
from .store import Store
from .verdict import preflight

SEVERITIES = ("inform", "warn", "block")
RANK = {s: i for i, s in enumerate(SEVERITIES)}
CATEGORIES = ("correctness", "security", "performance", "maintainability", "data_contract", "operational")
EVIDENCE_RE = re.compile(r"^(hunk|constraint|incident|event|node|edge):(.+)$")

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
- cite evidence: `hunk:<path>:<line>` for a line in the diff, or an id listed under "System facts" written exactly as given (`constraint:CON-031`, `incident:INC-230`, `event:ev_...`, `node:model:fct_orders`, `edge:e_...`);
- use severity `block` only for a change that should not merge as is, `warn` for one that needs a check or a sign-off, `inform` otherwise.

Never invent owners, dependencies, lineage, constraints, incidents or approvals. If the system facts do not cover something, do not claim it. An empty findings list is a valid answer."""


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


def _known_ids(store: Store, verdict: dict[str, Any] | None, constraints: list[Constraint]) -> dict[str, set[str]]:
    known: dict[str, set[str]] = {"constraint": {c.id for c in constraints}, "incident": set(), "event": set(), "node": set(), "edge": set()}
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


def gather(store: Store, repo_root: Path, diff_text: str, *, constraints: list[Constraint], risk_model: RiskModel, base_ref: str | None = None, scope: list[str] | None = None, ignore: list[str] | None = None) -> ReviewContext:
    files = parse(diff_text)
    verdict = None
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
    return ReviewContext(files, verdict, _known_ids(store, verdict, constraints))


def _facts(ctx: ReviewContext) -> str:
    v = ctx.verdict
    if not v or not v["change"]["ops"]:
        return "No platform assets resolved for this diff; review it as ordinary code."
    lines = [f"Deterministic pre-flight: severity {v['severity']}, risk {v['risk']['score']}. {v.get('summary', '')}"]
    for o in v["change"]["ops"]:
        d = o.get("details") or {}
        change = f": `{d['from']}` -> `{d['to']}`" if "from" in d and "to" in d else ""
        lines.append(f"- op {o['kind']} on node:{o['asset']}" + (f" column {o['column']}" if o.get("column") else "") + change)
    for c in v["constraints"]:
        if c["status"] == "fires":
            lines.append(f"- constraint:{c['id']} ({c['kind']}, {c['severity']}) fires: {c['detail']}. Reason: {c['reason']}")
    for a in v["affected"][:20]:
        edge = next((e for e in a["evidence"] if e.startswith("edge:")), "")
        lines.append(f"- affected node:{a['asset']} tier {a['tier']} owner {a['owner']} {edge}".rstrip())
    for h in v["history"][:10]:
        ref = f"incident:{h['ref']}" if h["kind"] in ("postmortem_filed", "incident_opened") else f"event:{h['event']}"
        lines.append(f"- history {ref} {h['kind']} {str(h['occurred_at'])[:10]}: {h.get('title') or ''} ({h['relevance']})")
    return "\n".join(lines)


def generate(provider: Provider, ctx: ReviewContext) -> tuple[list[dict[str, Any]], str | None]:
    diff = render(ctx.files)
    if not diff.strip():
        return [], None
    user = f"System facts:\n{_facts(ctx)}\n\nDiff:\n{diff}"
    try:
        out = provider.complete_json(SYSTEM, user, FINDINGS_SCHEMA, schema_name="report_findings")
    except ProviderError as e:
        return [], str(e)
    items = out.get("findings") if isinstance(out, dict) else None
    return [f for f in (items or []) if isinstance(f, dict)], None


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


def deterministic_findings(ctx: ReviewContext) -> list[dict[str, Any]]:
    v = ctx.verdict
    if not v or not v["change"]["ops"]:
        return []
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

    out = []
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
    if not out and v["severity"] in ("warn", "block"):
        tier1 = [a for a in v["affected"] if a["tier"] == 1]
        path, line = locate(v["change"]["touched"])
        out.append({"source": "deterministic", "path": path, "line": line, "end_line": None, "severity": v["severity"], "category": "operational", "title": f"Change reaches {len(v['affected'])} downstream assets, {len(tier1)} tier 1", "body": v["summary"], "evidence": [f"node:{a['asset']}" for a in tier1[:5]] or [f"node:{t}" for t in v["change"]["touched"][:3]], "status": "valid", "notes": []})
    return out


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


def review(store: Store, repo_root: Path, provider: Provider, *, constraints: list[Constraint], risk_model: RiskModel, base_ref: str | None = None, diff_text: str | None = None, scope: list[str] | None = None, ignore: list[str] | None = None) -> dict[str, Any]:
    if diff_text is None:
        if not base_ref:
            raise ValueError("give a base ref or a diff")
        diff_text = git_diff(repo_root, base_ref)
    ctx = gather(store, repo_root, diff_text, constraints=constraints, risk_model=risk_model, base_ref=base_ref, scope=scope, ignore=ignore)
    candidates, provider_error = generate(provider, ctx)
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
        "verdict_id": v.get("verdict_id"),
        "verdict_summary": v.get("summary") or v.get("error"),
        "usage": getattr(provider, "last_usage", None),
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
    return "\n".join(lines) + "\n"


__all__ = ["FINDINGS_SCHEMA", "SYSTEM", "review", "to_markdown", "validate", "gather", "generate", "deterministic_findings", "json"]
