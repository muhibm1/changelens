"""Slice 3: constraint proposals from ledger patterns."""

from pathlib import Path

from fathom.constraints import load_constraints
from fathom.proposals import propose, write

ROOT = Path(__file__).resolve().parents[1]


def test_fixture_proposals_cover_sla_misses_but_not_filed_incidents(synced_store):
    constraints, _ = load_constraints(ROOT / "fixture" / "fathom" / "constraints")
    props = propose(synced_store, constraints)
    patterns = {p.pattern for p in props}
    assert "postmortem_without_constraint" not in patterns, "all three postmortems already have constraints"
    sla = [p for p in props if p.pattern == "repeated_sla_miss"]
    assert len(sla) == 2, "finance_close.dbt_finance (30 min SLA) and orders_pipeline.dbt_marts (45 min SLA)"
    finance = next(p for p in sla if p.constraint["rule"]["minutes"] == 30)
    assert finance.constraint["scope"]["assets"] == ["model:fct_refunds", "model:fct_revenue"]
    assert finance.constraint["approvers"]
    assert len(finance.evidence) >= 2
    # deterministic ids
    assert {p.id for p in props} == {p.id for p in propose(synced_store, constraints)}


def test_postmortem_without_matching_constraint_is_proposed(synced_store):
    constraints, _ = load_constraints(ROOT / "fixture" / "fathom" / "constraints")
    without_partition = [c for c in constraints if c.rule.kind != "partition_key"]
    props = propose(synced_store, without_partition)
    pk = [p for p in props if p.constraint["rule"]["kind"] == "partition_key"]
    assert len(pk) == 1
    assert pk[0].constraint["rule"]["key"] == "event_date"
    assert "source_table:raw.raw_events" in pk[0].constraint["scope"]["assets"]
    assert pk[0].constraint["source"] == "INC-214"


def test_write_proposals_is_idempotent_and_loadable(synced_store, tmp_path):
    constraints, _ = load_constraints(ROOT / "fixture" / "fathom" / "constraints")
    props = propose(synced_store, constraints)
    paths = write(synced_store, props, tmp_path, tmp_path)
    assert len(paths) == len(props)
    loaded, problems = load_constraints(tmp_path / "proposed")
    assert not problems and {c.id for c in loaded} == {p.id for p in props}
    assert write(synced_store, props, tmp_path, tmp_path) == []
    recorded = synced_store.con.execute("SELECT count(*) FROM events WHERE kind = 'constraint_proposed'").fetchone()[0]
    assert recorded == len(props)
