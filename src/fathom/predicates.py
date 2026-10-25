"""Evaluate the closed constraint predicate set against typed change ops.

Checking is a lookup over ops, the graph and the ledger. No model is involved,
so a constraint fires the same way every time and the verdict can say why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .changes import ChangeOp
from .constraints import Constraint
from .model import EdgeKind
from .resolver import resolve_ref
from .store import Store


@dataclass
class ConstraintHit:
    constraint: Constraint
    status: str  # fires | satisfied | expired | not_applicable
    ops: list[ChangeOp] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        c = self.constraint
        return {
            "id": c.id,
            "kind": c.rule.kind,
            "severity": c.severity,
            "status": self.status,
            "reason": c.reason,
            "source": c.source,
            "approvers": c.approvers,
            "detail": self.detail,
            "ops": [o.to_dict() for o in self.ops],
            "evidence": [f"constraint:{c.id}"] + ([f"event:{c.source}"] if c.source and c.source.startswith("INC-") else []),
        }


def _scoped_assets(store: Store, c: Constraint) -> set[str]:
    out = set()
    for ref in c.scope.assets:
        r = resolve_ref(store, ref)
        out.add(r.node_id if r.ok else ref)
    return out


def _columns_downstream_assets(store: Store, asset: str, column: str) -> set[str]:
    """Assets whose columns derive from ``asset.column``."""
    name = asset.split(":", 1)[1]
    col_id = f"column:{name}.{column}"
    if store.node(col_id) is None:
        return set()
    found = store.walk([col_id], "downstream", kinds=(EdgeKind.DERIVES_FROM,), max_hops=20, always_include_tier=None)
    assets = set()
    for cid in found:
        n = store.node(cid)
        if n and n["properties"].get("asset"):
            assets.add(n["properties"]["asset"])
    return assets


def _in_window(at: datetime, window: dict[str, str]) -> bool:
    start = window.get("start", "00:00")
    end = window.get("end", "23:59")
    hhmm = at.astimezone(UTC).strftime("%H:%M")
    if start <= end:
        return start <= hhmm <= end
    return hhmm >= start or hhmm <= end


def _ledger_has_decision(store: Store, asset_ids: set[str], approvers: list[str], since_days: int = 30) -> bool:
    events = store.events_for(asset_ids, kinds=["decision_recorded"], limit=50)
    now = datetime.now(UTC)
    for e in events:
        occurred = e["occurred_at"] if e["occurred_at"].tzinfo else e["occurred_at"].replace(tzinfo=UTC)
        if (now - occurred).days > since_days:
            continue
        by = e["payload"].get("by") or e["actor"] or ""
        if e["payload"].get("decision") in ("approve", "waive") and (not approvers or by in approvers):
            return True
    return False


def evaluate(store: Store, constraints: list[Constraint], ops: list[ChangeOp], affected_assets: set[str]) -> list[ConstraintHit]:
    hits: list[ConstraintHit] = []
    touched = {o.asset for o in ops}
    for c in constraints:
        scoped = _scoped_assets(store, c)
        if c.expired:
            if scoped & (touched | affected_assets):
                hits.append(ConstraintHit(c, "expired", detail=f"expired {c.expires}"))
            continue
        kind = c.rule.kind
        firing: list[ChangeOp] = []
        detail = ""
        if kind == "columns_locked":
            allow = set(c.rule.allow)
            for op in ops:
                if op.kind not in ("drop_column", "rename_column", "change_type", "change_expression"):
                    continue
                verb = {"drop_column": "drop", "rename_column": "rename", "change_type": "retype", "change_expression": "redefine"}[op.kind]
                if verb in allow:
                    continue
                reach = {op.asset} | (_columns_downstream_assets(store, op.asset, op.column) if op.column else set())
                if reach & scoped:
                    firing.append(op)
                    via = "directly" if op.asset in scoped else f"through {', '.join(sorted(reach & scoped))}"
                    detail = f"{op.kind} on {op.asset.split(':',1)[1]}.{op.column} reaches a locked asset {via}"
        elif kind == "partition_key":
            for op in ops:
                if op.kind == "change_partition" and op.asset in scoped and (op.details.get("to") or "") != c.rule.key:
                    firing.append(op)
                    detail = f"partition key would become {op.details.get('to')}, constraint requires {c.rule.key}"
        elif kind == "no_backfill_window":
            for op in ops:
                if op.kind == "backfill" and op.asset in scoped:
                    at = op.details.get("at")
                    when = datetime.fromisoformat(at) if at else datetime.now(UTC)
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=UTC)
                    if c.rule.window and _in_window(when, c.rule.window):
                        firing.append(op)
                        detail = f"backfill at {when:%H:%M} UTC falls inside the blocked window {c.rule.window.get('start')}-{c.rule.window.get('end')} UTC"
        elif kind == "schedule_locked":
            for op in ops:
                if op.kind == "change_schedule" and op.asset in scoped:
                    firing.append(op)
                    detail = f"schedule would change from {op.details.get('from')} to {op.details.get('to')}"
        elif kind == "requires_approval":
            touched_in_scope = {o.asset for o in ops} & scoped
            if touched_in_scope and not _ledger_has_decision(store, touched_in_scope, c.rule.approvers or c.approvers):
                firing = [o for o in ops if o.asset in scoped]
                detail = f"no approval recorded from {', '.join(c.rule.approvers or c.approvers)}"
        elif kind == "freshness_sla":
            for op in ops:
                if op.kind in ("add_dependency", "change_schedule", "change_selection") and (op.asset in scoped or affected_assets & scoped):
                    firing.append(op)
                    detail = "the change adds work upstream of an SLA-bound asset"
        elif kind == "deprecated":
            for op in ops:
                if op.kind in ("add_dependency", "add_asset", "modify") and (op.details.get("on") in scoped or op.asset in scoped):
                    firing.append(op)
                    detail = "the change builds on a deprecated asset"
        if firing:
            hits.append(ConstraintHit(c, "fires", firing, detail))
        elif scoped & (touched | affected_assets):
            hits.append(ConstraintHit(c, "satisfied", detail="in scope, no op violates it"))
    return hits
