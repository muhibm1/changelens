"""The pre-flight verdict: a fixed-shape document in which every claim cites an id.

The structured parts are computed first. The ``summary`` is rendered from
them afterwards, so it cannot say anything the evidence does not support.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .changes import COLUMN_OPS, DESTRUCTIVE, ChangeSet
from .constraints import Constraint
from .model import EdgeKind, Event, EventKind
from .predicates import ConstraintHit, evaluate
from .risk import RiskModel, score, severity
from .store import Store
from .sync import graph_as_of

HISTORY_KINDS = ["postmortem_filed", "incident_opened", "sla_missed", "run_failed", "schema_changed", "outcome_observed"]
CAUSE_FAMILY = {
    "drop_column": {"drop_column", "rename_column"},
    "rename_column": {"drop_column", "rename_column"},
    "change_partition": {"partition_change", "change_partition"},
    "backfill": {"backfill"},
    "change_schedule": {"schedule", "change_schedule", "sla"},
}


def _short(nid: str) -> str:
    return nid.split(":", 1)[1] if ":" in nid else nid


def _destructive(op) -> bool:
    """A change_partition to the key the asset already has rewrites nothing."""
    if op.kind == "change_partition" and op.details.get("from") and op.details.get("to") == op.details.get("from"):
        return False
    return op.kind in DESTRUCTIVE


def affected_assets(store: Store, cs: ChangeSet) -> dict[str, dict[str, Any]]:
    """Column-level where lineage exists, asset-level where it does not."""
    out: dict[str, dict[str, Any]] = {}
    touched = set(cs.touched)

    def record(nid: str, via: str, hops: int, evidence: list[str]) -> None:
        if nid in touched or nid.startswith("column:"):
            return
        prev = out.get(nid)
        if prev is None or hops < prev["hops"]:
            out[nid] = {"via": via, "hops": hops, "evidence": evidence}

    for op in cs.ops:
        if op.kind == "add_column":
            continue  # a new column has no dependants yet
        if op.kind in COLUMN_OPS and op.column:
            col_id = f"column:{_short(op.asset)}.{op.column}"
            if store.node(col_id) is not None:
                cols = store.walk([col_id], "downstream", kinds=(EdgeKind.DERIVES_FROM,), max_hops=20, always_include_tier=None)
                reached_assets: dict[str, tuple[int, list[str]]] = {}
                for cid, info in cols.items():
                    n = store.node(cid)
                    asset = n["properties"].get("asset") if n else None
                    if asset:
                        path_edges = [f"edge:{info['via_edge']}"]
                        if asset not in reached_assets or info["hops"] < reached_assets[asset][0]:
                            reached_assets[asset] = (info["hops"], path_edges)
                for asset, (hops, ev) in reached_assets.items():
                    record(asset, "column", hops, ev)
                # consumers without column lineage (exposures, tasks) hang off the reached assets
                for asset in list(reached_assets) + [op.asset]:
                    for e in store.edges_to(asset, (EdgeKind.CONSUMES, EdgeKind.DEPENDS_ON)):
                        n = store.node(e["src"])
                        if n and n["kind"] not in ("model", "seed", "snapshot", "source_table"):
                            record(e["src"], "asset", (reached_assets.get(asset, (0, []))[0]) + 1, [f"edge:{e['id']}"])
                continue
        walked = store.walk([op.asset], "downstream")
        for nid, info in walked.items():
            record(nid, "asset", info["hops"], [f"edge:{info['via_edge']}"])
    return out


def column_readers(store: Store, cs: ChangeSet) -> list[dict[str, Any]]:
    """Models that select a dropped or renamed column directly, and whether this change also edits them.

    A reader the change leaves alone still selects a name that is gone, so it fails to build.
    """
    touched = set(cs.touched)
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for op in cs.ops:
        if op.kind not in ("drop_column", "rename_column") or not op.column:
            continue
        col = f"{_short(op.asset)}.{op.column}"
        col_id = f"column:{col}"
        if store.node(col_id) is None:
            continue
        for cid, info in store.walk([col_id], "downstream", kinds=(EdgeKind.DERIVES_FROM,), max_hops=1, always_include_tier=None).items():
            n = store.node(cid)
            reader = n["properties"].get("asset") if n else None
            if reader and reader != op.asset:
                rows.setdefault((col, reader), {"column": col, "asset": op.asset, "op": op.kind, "reader": reader, "in_change": reader in touched, "evidence": [f"edge:{info['via_edge']}"]})
    return list(rows.values())


def _history(store: Store, cs: ChangeSet, affected: dict[str, dict[str, Any]], horizon_days: int = 365) -> list[dict[str, Any]]:
    ids = set(cs.touched) | set(affected)
    since = datetime.now(UTC) - timedelta(days=horizon_days)
    events = store.events_for(ids, kinds=HISTORY_KINDS, since=since, limit=20)
    op_kinds = {o.kind for o in cs.ops}
    rows = []
    for e in events:
        same_asset = bool(set(e["assets"]) & set(cs.touched))
        cause = (e["payload"].get("cause") or "").lower()
        same_op = any(cause in CAUSE_FAMILY.get(k, set()) for k in op_kinds)
        relevance = ("same asset" if same_asset else "downstream asset") + (", same kind of change" if same_op else "")
        rows.append(
            {
                "event": e["id"],
                "kind": e["kind"],
                "ref": e["source_ref"],
                "occurred_at": e["occurred_at"],
                "assets": e["assets"],
                "title": e["payload"].get("title") or e["payload"].get("subject"),
                "relevance": relevance,
                "same_kind_of_change": same_op,
                "evidence": [f"event:{e['id']}"],
            }
        )
    return rows


def _checks(store: Store, cs: ChangeSet, affected: dict[str, dict[str, Any]], hits: list[ConstraintHit], readers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    broken = [r for r in readers if not r["in_change"]]
    if broken:
        checks.append({"kind": "update_readers", "readers": sorted({r["reader"] for r in broken}), "columns": sorted({r["column"] for r in broken}), "evidence": [e for r in broken for e in r["evidence"]]})
    touched_owners = {(store.node(t) or {}).get("owner") for t in cs.touched}
    for t in cs.touched:
        n = store.node(t)
        if n and n["kind"] in ("model", "seed", "snapshot"):
            checks.append({"kind": "run_tests", "target": f"{n['name']}+", "evidence": [f"node:{t}"]})
    for h in hits:
        if h.status == "fires" and h.constraint.severity in ("block", "warn") and h.constraint.approvers:
            checks.append({"kind": "get_approval", "from": h.constraint.approvers, "for": h.constraint.id, "evidence": [f"constraint:{h.constraint.id}"]})
    notify: dict[str, list[str]] = {}
    for nid, _info in affected.items():
        n = store.node(nid)
        if n and n["tier"] == 1 and n["owner"] and n["owner"] not in touched_owners:
            notify.setdefault(n["owner"], []).append(nid)
    for owner, assets in sorted(notify.items()):
        checks.append({"kind": "notify_owner", "owner": owner, "assets": assets, "evidence": [f"node:{a}" for a in assets]})
    if any(o.kind == "backfill" for o in cs.ops):
        checks.append({"kind": "schedule_off_hours", "evidence": [f"constraint:{h.constraint.id}" for h in hits if h.constraint.rule.kind == "no_backfill_window"] or ["ops"]})
    if any(o.from_intent for o in cs.ops):
        checks.append({"kind": "confirm_resolution", "note": "ops were resolved from intent; confirm the assets before acting", "evidence": ["intent"]})
    if cs.unresolved:
        checks.append({"kind": "map_unresolved_paths", "paths": [u.get("path") or u.get("intent") for u in cs.unresolved], "evidence": ["unresolved"]})
    return checks


def _summary(cs: ChangeSet, affected_rows: list[dict[str, Any]], hits: list[ConstraintHit], history: list[dict[str, Any]], checks: list[dict[str, Any]], sev: str) -> str:
    if not cs.ops:
        return "No change could be resolved to a known asset; nothing to check. Map the paths listed under unresolved."
    first = cs.ops[0]
    what = first.kind.replace("_", " ")
    target = _short(first.asset) + (f".{first.column}" if first.column else "")
    more = f" and {len(cs.ops) - 1} more change{'s' if len(cs.ops) > 2 else ''}" if len(cs.ops) > 1 else ""
    tier1 = [a for a in affected_rows if a.get("tier") == 1]
    parts = [f"{what.capitalize()} on {target}{more}"]
    if affected_rows:
        names = ", ".join(_short(a["asset"]) for a in tier1[:3])
        parts[0] += f" reaches {len(affected_rows)} downstream asset{'s' if len(affected_rows) != 1 else ''}"
        parts[0] += f", {len(tier1)} of them tier 1 ({names}{'…' if len(tier1) > 3 else ''})." if tier1 else "; none are tier 1."
    else:
        parts[0] += " reaches no downstream assets."
    firing = [h for h in hits if h.status == "fires"]
    if firing:
        parts.append("Fires " + ", ".join(f"{h.constraint.id} ({h.constraint.severity})" for h in firing) + ".")
    same_kind = [h for h in history if h["same_kind_of_change"] and h["kind"] == "postmortem_filed"]
    if same_kind:
        h = same_kind[0]
        parts.append(f"The same kind of change caused {h['ref']} on {h['occurred_at']:%Y-%m-%d}.")
    elif any(h["kind"] == "postmortem_filed" for h in history):
        h = next(h for h in history if h["kind"] == "postmortem_filed")
        parts.append(f"This path has incident history: {h['ref']} on {h['occurred_at']:%Y-%m-%d}.")
    asks = []
    for c in checks:
        if c["kind"] == "update_readers":
            asks.append(f"update {', '.join(_short(r) for r in c['readers'])}, which still select{'s' if len(c['readers']) == 1 else ''} {', '.join(c['columns'])}")
        elif c["kind"] == "get_approval":
            asks.append(f"get sign-off from {', '.join(c['from'])}")
        elif c["kind"] == "run_tests":
            asks.append(f"run tests for {c['target']}")
        elif c["kind"] == "notify_owner":
            asks.append(f"tell {c['owner']}")
        elif c["kind"] == "schedule_off_hours":
            asks.append("schedule the backfill off-hours")
        elif c["kind"] == "confirm_resolution":
            asks.append("confirm the resolved assets")
    if asks:
        parts.append("Before merging: " + "; ".join(asks[:4]) + ".")
    parts.append(f"Severity {sev}.")
    return " ".join(parts)


def preflight(
    store: Store,
    cs: ChangeSet,
    constraints: list[Constraint],
    risk_model: RiskModel,
    *,
    record: bool = True,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    as_of = graph_as_of(store)
    fingerprint = hashlib.sha1(json.dumps([o.to_dict() for o in cs.ops], sort_keys=True, default=str).encode() + str(as_of).encode()).hexdigest()[:16]
    verdict_id = f"pf_{fingerprint}"
    existing = store.get_verdict(verdict_id)
    if existing is not None:
        existing["reused"] = True
        return existing

    affected = affected_assets(store, cs)
    affected_rows = []
    for nid, info in sorted(affected.items(), key=lambda kv: (kv[1]["hops"], kv[0])):
        n = store.node(nid) or {"kind": "?", "name": nid, "tier": None, "owner": None}
        affected_rows.append({"asset": nid, "kind": n["kind"], "tier": n["tier"], "owner": n["owner"], "via": info["via"], "hops": info["hops"], "evidence": info["evidence"]})

    hits = evaluate(store, constraints, cs.ops, set(affected))
    history = _history(store, cs, affected)
    newest_incident_days = None
    for h in history:
        if h["kind"] in ("postmortem_filed", "incident_opened"):
            occurred = h["occurred_at"] if h["occurred_at"].tzinfo else h["occurred_at"].replace(tzinfo=UTC)
            days = (now - occurred).days
            newest_incident_days = days if newest_incident_days is None else min(newest_incident_days, days)
    risk = score(
        risk_model,
        tier1_consumers=sum(1 for a in affected_rows if a["tier"] == 1),
        block_constraints=sum(1 for h in hits if h.status == "fires" and h.constraint.severity == "block"),
        newest_incident_days=newest_incident_days,
        destructive_ops=sum(1 for o in cs.ops if _destructive(o)),
        blast_radius=len(affected_rows),
    )
    firing_sev = [h.constraint.severity for h in hits if h.status == "fires"]
    sev = severity(risk_model, risk.score, firing_sev)
    readers = column_readers(store, cs)
    # Readers left selecting a removed column are a check and a review finding, not a severity bump:
    # PipelineBench's hand-written rule rates those drops by tier and constraints alone.
    if cs.unresolved and sev == "inform":
        sev = "warn"
    checks = _checks(store, cs, affected, hits, readers)
    verdict = {
        "verdict_id": verdict_id,
        "issued_at": now,
        "graph_as_of": as_of,
        "severity": sev,
        "change": {"source": cs.source, "ops": [o.to_dict() for o in cs.ops], "touched": cs.touched, "unresolved": cs.unresolved, "ignored": cs.ignored},
        "affected": affected_rows,
        "constraints": [h.to_dict() for h in hits],
        "readers": readers,
        "history": history,
        "risk": risk.to_dict(),
        "checks": checks,
        "summary": "",
    }
    verdict["summary"] = _summary(cs, affected_rows, hits, history, checks, sev)
    if record:
        store.put_verdict(verdict_id, verdict)
        store.append_event(
            Event(
                EventKind.PREFLIGHT_ISSUED,
                now,
                list(dict.fromkeys(cs.touched + [a["asset"] for a in affected_rows])),
                source="orion preflight",
                source_ref=verdict_id,
                payload={"severity": sev, "risk": risk.score, "ops": [o.kind for o in cs.ops], "summary": verdict["summary"]},
            )
        )
    return verdict


def to_markdown(v: dict[str, Any]) -> str:
    sev = v["severity"].upper()
    lines = [f"**Orion pre-flight: {sev}** (risk {v['risk']['score']}/100, verdict `{v['verdict_id']}`, graph as of {v['graph_as_of']})", "", v["summary"], ""]
    if v["change"]["ops"]:
        lines.append("**Change**")
        for o in v["change"]["ops"]:
            col = f".{o['column']}" if o.get("column") else ""
            lines.append(f"- `{o['kind']}` {_short(o['asset'])}{col}" + (f" ({o['details']})" if o.get("details") else "") + f" — {', '.join(o['evidence'])}")
        lines.append("")
    if v["change"]["unresolved"]:
        lines.append("**Unresolved**")
        lines.extend(f"- {u}" for u in v["change"]["unresolved"])
        lines.append("")
    if v["affected"]:
        lines.append("**Affected**")
        for a in v["affected"]:
            tier = f"tier {a['tier']}" if a["tier"] is not None else "tier unset"
            lines.append(f"- {a['asset']} ({tier}, {a['owner'] or 'no owner'}, {a['hops']} hop{'s' if a['hops'] != 1 else ''} via {a['via']}) — {', '.join(a['evidence'])}")
        lines.append("")
    firing = [c for c in v["constraints"] if c["status"] == "fires"]
    if firing:
        lines.append("**Constraints**")
        for c in firing:
            lines.append(f"- {c['id']} `{c['kind']}` **{c['severity']}**: {c['reason']} ({c['detail']})")
        lines.append("")
    if v["history"]:
        lines.append("**History**")
        for h in v["history"][:6]:
            lines.append(f"- {h['occurred_at']:%Y-%m-%d} {h['kind']} {h['ref'] or ''}: {h['title'] or ''} ({h['relevance']}) — {h['event']}")
        lines.append("")
    if v["checks"]:
        lines.append("**Recommended checks**")
        for c in v["checks"]:
            desc = {k: val for k, val in c.items() if k not in ("kind", "evidence")}
            lines.append(f"- `{c['kind']}` {desc if desc else ''}")
        lines.append("")
    lines.append("Risk factors: " + "; ".join(f"{f['name']}={f['value']} (+{f['contribution']})" for f in v["risk"]["factors"]))
    return "\n".join(lines)


def load_risk(path: Path | None) -> RiskModel:
    return RiskModel.load(path)
