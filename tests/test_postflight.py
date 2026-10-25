"""Slice 3: the post-flight watcher closes the loop from verdict to outcome."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fathom.changes import collect_changes
from fathom.constraints import load_constraints
from fathom.model import Event, EventKind
from fathom.postflight import calibration, watch
from fathom.proposals import propose
from fathom.risk import RiskModel
from fathom.verdict import preflight

ROOT = Path(__file__).resolve().parents[1]


def _verdict(store, tmp_path, **kwargs):
    constraints, _ = load_constraints(ROOT / "fixture" / "fathom" / "constraints")
    cs = collect_changes(store, ROOT, tmp=tmp_path, **kwargs)
    return preflight(store, cs, constraints, RiskModel.load(None), record=True)


def _land(store, assets, at, sha):
    return store.append_event(Event(EventKind.CHANGE_LANDED, at, assets, source="git", source_ref=sha, payload={"subject": "test change"}))


def _run(store, assets, at, kind=EventKind.RUN_SUCCEEDED, ref="r"):
    return store.append_event(Event(kind, at, assets, source="test", source_ref=f"{ref}-{at.isoformat()}", payload={}))


def test_not_landed_then_watching_then_clean(synced_store, tmp_path):
    v = _verdict(synced_store, tmp_path, ops=[{"kind": "add_column", "asset": "dim_products", "column": "colour"}])
    rows = {r["verdict_id"]: r for r in watch(synced_store, runs=3)}
    assert rows[v["verdict_id"]]["status"] == "not landed"

    t0 = datetime.now(UTC) + timedelta(minutes=1)
    _land(synced_store, ["model:dim_products"], t0, "abc123")
    _run(synced_store, ["model:dim_products"], t0 + timedelta(hours=1))
    rows = {r["verdict_id"]: r for r in watch(synced_store, runs=3)}
    assert rows[v["verdict_id"]]["status"] == "watching"
    assert rows[v["verdict_id"]]["runs_seen"] == 1

    _run(synced_store, ["model:dim_products"], t0 + timedelta(hours=2))
    _run(synced_store, ["model:dim_products"], t0 + timedelta(hours=3))
    rows = {r["verdict_id"]: r for r in watch(synced_store, runs=3)}
    row = rows[v["verdict_id"]]
    assert row["status"] == "observed" and row["outcome"] == "clean" and row["runs_seen"] == 3
    observed = synced_store.events_for(["model:dim_products"], kinds=["outcome_observed"])
    assert len(observed) == 1
    assert observed[0]["payload"]["verdict_id"] == v["verdict_id"]
    assert observed[0]["payload"]["predicted"] == "inform"
    assert len(observed[0]["payload"]["evidence"]) == 4  # landing + three runs

    # idempotent: a second watch reports the same event and writes nothing new
    again = {r["verdict_id"]: r for r in watch(synced_store, runs=3)}
    assert again[v["verdict_id"]]["event"] == row["event"]
    assert len(synced_store.events_for(["model:dim_products"], kinds=["outcome_observed"])) == 1


def test_failure_after_landing_is_an_under_call_and_yields_a_proposal(synced_store, tmp_path):
    v = _verdict(synced_store, tmp_path, ops=[{"kind": "add_column", "asset": "fct_product_engagement", "column": "scroll_depth"}])
    assert v["severity"] == "inform"
    t0 = datetime.now(UTC) + timedelta(minutes=2)
    _land(synced_store, ["model:fct_product_engagement"], t0, "def456")
    _run(synced_store, ["model:fct_product_engagement"], t0 + timedelta(hours=1))
    _run(synced_store, ["model:fct_product_engagement"], t0 + timedelta(hours=2), EventKind.RUN_FAILED)
    rows = {r["verdict_id"]: r for r in watch(synced_store, runs=3)}
    assert rows[v["verdict_id"]]["outcome"] == "failed"
    cal = calibration(synced_store)
    assert cal["missed"] >= 1 and cal["table"]["inform"]["failed"] >= 1

    constraints, _ = load_constraints(ROOT / "fixture" / "fathom" / "constraints")
    props = propose(synced_store, constraints)
    under = [p for p in props if p.pattern == "verdict_under_called"]
    assert under and under[0].constraint["scope"]["assets"] == ["model:fct_product_engagement"]
    assert under[0].constraint["rule"]["kind"] == "requires_approval"
    assert any(e.startswith("event:") for e in under[0].evidence)
