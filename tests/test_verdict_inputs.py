"""A verdict depends on everything it reads, renders the same when reused, and never drops a broken rule silently."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from changelens.changes import ChangeOp, ChangeSet, collect_changes
from changelens.config import load_config
from changelens.constraints import Constraint, load_constraints
from changelens.model import Event, EventKind
from changelens.predicates import _in_window, evaluate
from changelens.risk import RiskModel, severity
from changelens.store import Store
from changelens.sync import run_sync
from changelens.verdict import preflight, to_markdown

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CONSTRAINTS = ROOT / "fixture" / "changelens" / "constraints"


@pytest.fixture
def fresh_store(manifest: Path, tmp_path: Path) -> Store:
    """A store of its own, for tests that write decisions to the ledger."""
    store = Store(tmp_path / "graph.duckdb")
    run_sync(load_config(ROOT / "changelens.yaml"), store)
    return store


@pytest.fixture(scope="module")
def constraints() -> list[Constraint]:
    cs, problems = load_constraints(FIXTURE_CONSTRAINTS)
    assert not problems
    return cs


def _lock(columns) -> Constraint:
    return Constraint.model_validate({
        "id": "CON-T1", "scope": {"assets": ["model:fct_orders"], "columns": columns},
        "rule": {"kind": "columns_locked", "allow": ["add"]}, "severity": "block", "reason": "test",
    })


def test_a_reused_verdict_renders_like_a_fresh_one(fresh_store, constraints, tmp_path):
    cs = collect_changes(fresh_store, ROOT, tmp=tmp_path, intent="drop the discount_pct column from fct_orders")
    first = preflight(fresh_store, cs, constraints, RiskModel.load(None))
    again = preflight(fresh_store, cs, constraints, RiskModel.load(None))
    assert again.get("reused") and again["verdict_id"] == first["verdict_id"]
    assert first["history"], "the fixture has incidents on fct_orders, so the history section renders"
    assert to_markdown(again) == to_markdown({**first, "reused": True})


def test_an_approval_or_a_new_unmapped_path_is_a_new_verdict(fresh_store, constraints, tmp_path):
    ops = [{"kind": "modify", "asset": "model:fct_orders"}]
    cs = collect_changes(fresh_store, ROOT, tmp=tmp_path, ops=ops)
    first = preflight(fresh_store, cs, constraints, RiskModel.load(None))
    fresh_store.append_event(Event(EventKind.DECISION_RECORDED, datetime.now(UTC), ["model:fct_orders"], source="test", source_ref=first["verdict_id"], actor="team/finance-data", payload={"decision": "approve", "by": "team/finance-data"}))
    after = preflight(fresh_store, cs, constraints, RiskModel.load(None), record=False)
    assert not after.get("reused") and after["verdict_id"] != first["verdict_id"]
    with_path = collect_changes(fresh_store, ROOT, tmp=tmp_path, ops=ops, paths=["fixture/dags/not_a_dag_yet.py"])
    v = preflight(fresh_store, with_path, constraints, RiskModel.load(None), record=False)
    assert not v.get("reused") and v["change"]["unresolved"]


def test_a_later_reject_withdraws_an_approval(fresh_store):
    gate = Constraint.model_validate({
        "id": "CON-T2", "scope": {"assets": ["model:fct_orders"]}, "rule": {"kind": "requires_approval", "approvers": ["team/x"]},
        "severity": "block", "reason": "test",
    })
    op = ChangeOp("modify", "model:fct_orders")

    def decide(decision: str, minutes_ago: int) -> None:
        when = datetime.now(UTC) - timedelta(minutes=minutes_ago)
        fresh_store.append_event(Event(EventKind.DECISION_RECORDED, when, ["model:fct_orders"], source="test", source_ref=f"t{minutes_ago}", actor="team/x", payload={"decision": decision, "by": "team/x"}))

    decide("approve", 10)
    assert evaluate(fresh_store, [gate], [op], set())[0].status == "satisfied"
    decide("reject", 5)
    assert evaluate(fresh_store, [gate], [op], set())[0].status == "fires"


def test_a_column_list_locks_only_those_columns(synced_store):
    drop_discount = [ChangeOp("drop_column", "model:fct_orders", "discount_pct")]
    assert [h.status for h in evaluate(synced_store, [_lock(["order_id"])], drop_discount, set())] == ["satisfied"]
    assert [h.status for h in evaluate(synced_store, [_lock(["discount_pct"])], drop_discount, set())] == ["fires"]
    assert [h.status for h in evaluate(synced_store, [_lock("*")], drop_discount, set())] == ["fires"]


def test_a_window_is_read_in_its_own_time_zone():
    noon_utc = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)  # 04:00 in Los Angeles
    assert _in_window(noon_utc, {"start": "06:00", "end": "20:00"})
    assert not _in_window(noon_utc, {"start": "06:00", "end": "20:00", "tz": "America/Los_Angeles"})


def test_unquoted_yaml_times_load_and_a_bad_time_zone_is_a_problem(tmp_path):
    (tmp_path / "a.yaml").write_text("id: CON-A\nscope: {assets: [x]}\nrule: {kind: no_backfill_window, window: {start: 06:00, end: 20:00}}\nseverity: block\nreason: r\n")
    (tmp_path / "b.yaml").write_text("id: CON-B\nscope: {assets: [x]}\nrule: {kind: no_backfill_window, window: {start: '06:00', end: '20:00', tz: Mars/Olympus}}\nseverity: block\nreason: r\n")
    loaded, problems = load_constraints(tmp_path)
    assert [c.id for c in loaded] == ["CON-A"] and loaded[0].rule.window == {"start": "06:00", "end": "20:00"}
    assert len(problems) == 1 and "Mars/Olympus" in problems[0]


def test_a_scope_typo_and_a_broken_file_show_in_the_verdict(synced_store, constraints, tmp_path):
    typo = Constraint.model_validate({"id": "CON-T3", "scope": {"assets": ["dag:orders_pipline"]}, "rule": {"kind": "schedule_locked"}, "severity": "warn", "reason": "test"})
    cs = ChangeSet(source="ops")
    cs.add(ChangeOp("change_schedule", "dag:orders_pipeline", details={"to": "hourly"}))
    v = preflight(synced_store, cs, [*constraints, typo], RiskModel.load(None), record=False, constraint_problems=["bad.yaml: not valid"])
    assert any("orders_pipline" in p for p in v["constraint_problems"]) and "bad.yaml: not valid" in v["constraint_problems"]
    md = to_markdown(v)
    assert "**Constraint problems**" in md and "orders_pipline" in md


def test_a_partial_threshold_override_keeps_the_rest(tmp_path):
    f = tmp_path / "risk.yaml"
    f.write_text("thresholds: {warn: 40}\n")
    m = RiskModel.load(f)
    assert m.thresholds["warn"] == 40 and "block" in m.thresholds
    severity(m, 50, [])


def test_cli_rejects_unknown_choices(tmp_path, monkeypatch):
    from changelens.cli import app

    monkeypatch.chdir(ROOT)
    bad_gate = CliRunner().invoke(app, ["gate", "--fail-on", "blok"])
    assert bad_gate.exit_code == 2 and "warn or block" in bad_gate.output
    bad_decision = CliRunner().invoke(app, ["record-decision", "pf_x", "--decision", "aproved", "--by", "me"])
    assert bad_decision.exit_code == 2 and "approve, waive or reject" in bad_decision.output
