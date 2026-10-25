"""The post-flight watcher: did the change Fathom pre-flighted go the way the verdict said?

For every verdict, find the commit that landed it (the first ``change_landed``
after the verdict touching one of its assets), then watch the touched and
affected assets for the next N runs. Once N runs have been seen, or earlier
if one fails, misses its SLA or an incident opens, write one
``outcome_observed`` event linked to the verdict. Those events are what make
the risk weights tunable and the benchmark honest: predicted severity on one
side, what production did on the other.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from .model import Event, EventKind
from .store import Store

WATCH_KINDS = ("run_succeeded", "run_failed", "sla_missed", "incident_opened")
BAD = {"incident_opened": "incident", "run_failed": "failed", "sla_missed": "sla_missed"}


def _ts(v: Any) -> datetime:
    return v if v.tzinfo else v.replace(tzinfo=UTC)


def _events_after(store: Store, assets: list[str], after: datetime, kinds: tuple[str, ...]) -> list[dict[str, Any]]:
    if not assets:
        return []
    sql = """
        SELECT e.id, e.kind, e.occurred_at, e.assets, e.payload, e.source_ref
        FROM events e, json_each(e.assets) a
        WHERE a.value::VARCHAR IN ({}) AND e.kind IN ({}) AND e.occurred_at > ?
        GROUP BY ALL ORDER BY e.occurred_at ASC
    """.format(",".join("?" for _ in assets), ",".join("?" for _ in kinds))
    rows = store.con.execute(sql, [json.dumps(a) for a in assets] + list(kinds) + [after]).fetchall()
    return [dict(id=i, kind=k, occurred_at=_ts(o), assets=json.loads(a), payload=json.loads(p), ref=r) for i, k, o, a, p, r in rows]


def _already_observed(store: Store, verdict_id: str) -> str | None:
    row = store.con.execute(
        "SELECT id FROM events WHERE kind = 'outcome_observed' AND source_ref = ? LIMIT 1", [verdict_id]
    ).fetchone()
    return row[0] if row else None


def watch(store: Store, runs: int = 3, record: bool = True) -> list[dict[str, Any]]:
    """One row per verdict: not landed, watching (n/N runs), or the observed outcome."""
    out: list[dict[str, Any]] = []
    for vid, issued_at, severity, body in store.con.execute("SELECT id, issued_at, severity, body FROM verdicts ORDER BY issued_at").fetchall():
        v = json.loads(body)
        issued_at = _ts(issued_at)
        touched = list(v["change"]["touched"])
        affected = [a["asset"] for a in v["affected"]]
        watched = list(dict.fromkeys(touched + affected))
        row: dict[str, Any] = {"verdict_id": vid, "issued_at": issued_at, "predicted": severity, "risk": v["risk"]["score"], "touched": touched, "watched": len(watched)}
        existing = _already_observed(store, vid)
        if existing:
            ev = store.con.execute("SELECT payload FROM events WHERE id = ?", [existing]).fetchone()
            row.update({"status": "observed", "event": existing, **{k: json.loads(ev[0]).get(k) for k in ("outcome", "runs_seen", "landed_at", "landed_ref")}})
            out.append(row)
            continue
        landed = _events_after(store, touched, issued_at, ("change_landed",))
        if not landed:
            row.update({"status": "not landed"})
            out.append(row)
            continue
        landing = landed[0]
        row.update({"landed_at": landing["occurred_at"], "landed_ref": landing["ref"], "landed_event": landing["id"]})
        seen = _events_after(store, watched, landing["occurred_at"], WATCH_KINDS)
        run_count = 0
        evidence: list[str] = [f"event:{landing['id']}"]
        outcome = None
        last_at = landing["occurred_at"]
        for e in seen:
            evidence.append(f"event:{e['id']}")
            last_at = e["occurred_at"]
            if e["kind"] in BAD:
                outcome = BAD[e["kind"]]
                break
            run_count += 1
            if run_count >= runs:
                outcome = "clean"
                break
        if outcome is None:
            row.update({"status": "watching", "runs_seen": run_count, "runs_wanted": runs})
            out.append(row)
            continue
        row.update({"status": "observed", "outcome": outcome, "runs_seen": run_count + (0 if outcome == "clean" else 1)})
        if record:
            ev = Event(
                EventKind.OUTCOME_OBSERVED,
                last_at,
                watched,
                source="fathom postflight",
                source_ref=vid,
                payload={
                    "verdict_id": vid,
                    "predicted": severity,
                    "risk": v["risk"]["score"],
                    "outcome": outcome,
                    "runs_seen": row["runs_seen"],
                    "landed_at": landing["occurred_at"].isoformat(),
                    "landed_ref": landing["ref"],
                    "evidence": evidence,
                    "title": f"{severity} verdict {vid} landed as {landing['ref']}: {outcome}",
                },
            )
            row["event"] = store.append_event(ev)
        out.append(row)
    return out


def calibration(store: Store) -> dict[str, Any]:
    """Predicted severity against observed outcome, from the ledger alone."""
    rows = store.con.execute(
        "SELECT json_extract_string(payload, '$.predicted'), json_extract_string(payload, '$.outcome'), count(*) FROM events WHERE kind = 'outcome_observed' GROUP BY 1, 2"
    ).fetchall()
    table: dict[str, dict[str, int]] = {}
    for predicted, outcome, n in rows:
        table.setdefault(predicted, {})[outcome] = int(n)
    misses = sum(n for p, outs in table.items() for o, n in outs.items() if p in ("inform", "warn") and o != "clean")
    quiet_blocks = sum(n for p, outs in table.items() for o, n in outs.items() if p == "block" and o == "clean")
    total = sum(n for outs in table.values() for n in outs.values())
    return {"observed": total, "table": table, "missed": misses, "blocked_but_clean": quiet_blocks}
