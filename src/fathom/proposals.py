"""Constraint proposals from ledger patterns.

Fathom proposes, humans approve: this module reads the ledger and drafts
constraint files under ``<constraints>/proposed/``. A nightly job runs it and
opens a pull request; merging is the approval. Four patterns:

1. A postmortem names a cause (``drop_column``, ``partition_change``,
   ``backfill``, ``schedule``) on assets no active constraint of the matching
   predicate covers.
2. An asset missed its SLA at least twice in the window and has no
   ``freshness_sla``.
3. An asset failed at least twice shortly after changes landed and nothing
   requires sign-off on it.
4. A verdict said ``inform`` or ``warn`` and the post-flight watcher saw a
   failure or an incident: the touched assets get ``requires_approval``.

Every proposal cites the events it was drawn from.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from .constraints import Constraint, load_constraints
from .model import Event, EventKind
from .resolver import resolve_ref
from .store import Store

CAUSE_TO_PREDICATE = {
    "drop_column": "columns_locked",
    "rename_column": "columns_locked",
    "change_type": "columns_locked",
    "partition_change": "partition_key",
    "change_partition": "partition_key",
    "backfill": "no_backfill_window",
    "schedule": "schedule_locked",
    "change_schedule": "schedule_locked",
}


@dataclass
class Proposal:
    id: str
    constraint: dict[str, Any]
    why: str
    evidence: list[str] = field(default_factory=list)
    pattern: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "pattern": self.pattern, "why": self.why, "evidence": self.evidence, "constraint": self.constraint}


def _pid(kind: str, assets: list[str]) -> str:
    return "CON-P" + hashlib.sha1(f"{kind}|{'|'.join(sorted(assets))}".encode()).hexdigest()[:6]


def _covered(constraints: list[Constraint], kind: str, assets: set[str], resolve) -> Constraint | None:
    for c in constraints:
        if c.rule.kind != kind or c.expired:
            continue
        scoped = {resolve(a) for a in c.scope.assets}
        if scoped & assets:
            return c
    return None


def _events(store: Store, kinds: list[str], since: datetime | None) -> list[dict[str, Any]]:
    sql = "SELECT id, kind, occurred_at, assets, payload, source_ref FROM events WHERE kind IN ({})".format(",".join("?" for _ in kinds))
    args: list[Any] = list(kinds)
    if since is not None:
        sql += " AND occurred_at >= ?"
        args.append(since)
    rows = store.con.execute(sql + " ORDER BY occurred_at", args).fetchall()
    return [dict(id=i, kind=k, occurred_at=o if o.tzinfo else o.replace(tzinfo=UTC), assets=json.loads(a), payload=json.loads(p), ref=r) for i, k, o, a, p, r in rows]


def _owner(store: Store, assets: list[str]) -> list[str]:
    owners = [(store.node(a) or {}).get("owner") for a in assets]
    return sorted({o for o in owners if o})


def propose(store: Store, constraints: list[Constraint], *, window_days: int = 90, min_sla_misses: int = 2, min_failures: int = 2) -> list[Proposal]:
    resolve = lambda r: resolve_ref(store, r).node_id or r
    since = datetime.now(UTC) - timedelta(days=window_days)
    proposals: dict[str, Proposal] = {}

    def add(p: Proposal) -> None:
        proposals.setdefault(p.id, p)

    # 1. postmortems whose cause has no matching constraint
    for e in _events(store, ["postmortem_filed"], None):
        cause = str(e["payload"].get("cause") or "").lower()
        kind = CAUSE_TO_PREDICATE.get(cause)
        if not kind or not e["assets"]:
            continue
        primary = [a for a in e["assets"] if not a.startswith(("dag:", "task:", "dashboard:", "report:"))] or e["assets"]
        if _covered(constraints, kind, set(primary), resolve):
            continue
        inc = e["payload"].get("incident") or e["ref"]
        rule: dict[str, Any] = {"kind": kind}
        if kind == "columns_locked":
            rule["allow"] = ["add"]
        elif kind == "partition_key":
            keys = (store.node(primary[0]) or {}).get("properties", {}).get("partition_by") or []
            rule["key"] = keys[0] if keys else "<current partition key>"
        elif kind == "no_backfill_window":
            h = e["occurred_at"].hour
            rule["window"] = {"start": f"{max(h - 3, 0):02d}:00", "end": f"{min(h + 3, 23):02d}:00", "tz": "UTC"}
        add(Proposal(
            _pid(kind, primary),
            {"scope": {"assets": primary, **({"columns": "*"} if kind == "columns_locked" else {})}, "rule": rule, "severity": "warn", "reason": f"{inc}: {e['payload'].get('title') or cause}. No constraint of kind {kind} covered these assets.", "source": inc, "approvers": _owner(store, primary)},
            f"postmortem {inc} names cause '{cause}' and no {kind} constraint covers {', '.join(primary)}",
            [f"event:{e['id']}"],
            "postmortem_without_constraint",
        ))

    # 2. repeated SLA misses without a freshness_sla: one proposal per SLA-bound task, scoped to what it builds
    misses: dict[str, list[dict[str, Any]]] = {}
    for e in _events(store, ["sla_missed"], since):
        for a in e["assets"]:
            if a.startswith("task:"):
                misses.setdefault(a, []).append(e)
    for task, evs in sorted(misses.items()):
        if len(evs) < min_sla_misses:
            continue
        built = [d["dst"] for d in store.edges_from(task, ["produces"]) if d["dst"].startswith(("model:", "source_table:", "iceberg_table:", "seed:"))]
        tier1 = [a for a in built if (store.node(a) or {}).get("tier") == 1]
        assets = tier1 or built or [task]
        if _covered(constraints, "freshness_sla", set(assets), resolve):
            continue
        minutes = next((int(e["payload"]["sla_s"] // 60) for e in evs if e["payload"].get("sla_s")), None)
        add(Proposal(
            _pid("freshness_sla", assets),
            {"scope": {"assets": assets}, "rule": {"kind": "freshness_sla", **({"minutes": minutes} if minutes else {})}, "severity": "warn", "reason": f"{task} missed its SLA {len(evs)} times in the last {window_days} days (latest {evs[-1]['occurred_at'].date()}); changes that add work upstream of what it builds should be flagged.", "source": f"event:{evs[-1]['id']}", "approvers": _owner(store, [task, *assets])},
            f"{task} missed its SLA {len(evs)} times since {evs[0]['occurred_at'].date()}; it builds {', '.join(assets)}",
            [f"event:{e['id']}" for e in evs],
            "repeated_sla_miss",
        ))

    # 3. repeated failures soon after changes landed
    landed = _events(store, ["change_landed"], since)
    fails: dict[str, list[dict[str, Any]]] = {}
    for e in _events(store, ["run_failed"], since):
        for a in e["assets"]:
            if not a.startswith(("model:", "source_table:", "iceberg_table:", "seed:")):
                continue
            recent_change = any(a in c["assets"] and timedelta(0) <= e["occurred_at"] - c["occurred_at"] <= timedelta(days=7) for c in landed)
            if recent_change:
                fails.setdefault(a, []).append(e)
    for asset, evs in sorted(fails.items()):
        if len(evs) < min_failures or _covered(constraints, "requires_approval", {asset}, resolve):
            continue
        add(Proposal(
            _pid("requires_approval", [asset]),
            {"scope": {"assets": [asset]}, "rule": {"kind": "requires_approval", "approvers": _owner(store, [asset])}, "severity": "warn", "reason": f"{asset} failed {len(evs)} times within a week of a change landing; changes to it should carry the owner's sign-off.", "source": f"event:{evs[-1]['id']}", "approvers": _owner(store, [asset])},
            f"{asset} failed {len(evs)} times within a week of a landed change",
            [f"event:{e['id']}" for e in evs],
            "failures_after_change",
        ))

    # 4. verdicts that under-called
    for e in _events(store, ["outcome_observed"], None):
        p = e["payload"]
        if p.get("predicted") not in ("inform", "warn") or p.get("outcome") in (None, "clean"):
            continue
        v = store.get_verdict(p.get("verdict_id", "")) or {}
        touched = list((v.get("change") or {}).get("touched") or [])
        if not touched or _covered(constraints, "requires_approval", set(touched), resolve):
            continue
        add(Proposal(
            _pid("requires_approval", touched),
            {"scope": {"assets": touched}, "rule": {"kind": "requires_approval", "approvers": _owner(store, touched)}, "severity": "warn", "reason": f"Verdict {p['verdict_id']} said {p['predicted']} and the change was followed by {p['outcome']}; changes to these assets should carry a recorded decision.", "source": f"event:{e['id']}", "approvers": _owner(store, touched)},
            f"verdict {p['verdict_id']} predicted {p['predicted']}, outcome was {p['outcome']}",
            [f"event:{e['id']}", *p.get("evidence", [])],
            "verdict_under_called",
        ))
    return list(proposals.values())


def write(store: Store, proposals: list[Proposal], constraints_dir: Path, repo_root: Path) -> list[Path]:
    """Write each proposal as ``proposed/<id>.yaml`` (skipping ones already there) and record it in the ledger."""
    out_dir = constraints_dir / "proposed"
    out_dir.mkdir(parents=True, exist_ok=True)
    existing, _ = load_constraints(out_dir)
    known = {c.id for c in existing}
    written: list[Path] = []
    for p in proposals:
        if p.id in known:
            continue
        body = {"id": p.id, **p.constraint, "status": "proposed", "proposed_by": "fathom propose", "proposed_at": datetime.now(UTC).isoformat(), "pattern": p.pattern, "evidence": p.evidence}
        Constraint.model_validate({**body, "path": "proposed"})  # refuse to write what the loader would reject
        path = out_dir / f"{p.id}.yaml"
        path.write_text(f"# Proposed by Fathom from ledger evidence; merging this file is the approval.\n# {p.why}\n" + yaml.safe_dump(body, sort_keys=False))
        written.append(path)
        try:
            rel = str(path.relative_to(repo_root))
        except ValueError:
            rel = str(path)
        store.append_event(Event(EventKind.CONSTRAINT_PROPOSED, datetime.now(UTC), p.constraint["scope"]["assets"], source="fathom propose", source_ref=p.id, payload={"path": rel, "pattern": p.pattern, "evidence": p.evidence, "title": p.why}))
    return written
