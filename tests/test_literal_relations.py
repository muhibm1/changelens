"""A model that reads another asset by its warehouse name instead of ``ref()`` or ``source()`` is a finding, and changed
SQL is parsed in the warehouse's own dialect. Both come from the cal-itp shadow study
(``pipelinebench/results/calitp-shadow-2026-11.md``): a BigQuery model whose refs were swapped for hardcoded
production table names was approved, merged and fixed hours later, and ChangeLens had only reported a parse error."""

from pathlib import Path

import pytest

from changelens.changes import collect_changes, literal_relations, ops_from_sql_change
from changelens.constraints import load_constraints
from changelens.providers import StubProvider
from changelens.resolver import PathResolver
from changelens.review import review
from changelens.risk import RiskModel
from changelens.verdict import preflight

ROOT = Path(__file__).resolve().parents[1]
FCT_ORDERS = "fixture/dbt_project/models/marts/fct_orders.sql"

BQ_OLD = """with datasets as (
    select * from {{ ref('dim_gtfs_datasets') }}
),
candidates as (
    select key, count(distinct trip_id) as n_trips
    from {{ ref('int_candidates') }}
    group by 1
)
select datasets.key, candidates.n_trips from datasets join candidates using (key)
"""
BQ_NEW = BQ_OLD.replace("{{ ref('dim_gtfs_datasets') }}", "`cal-itp-data-infra`.`mart_transit_database`.`dim_gtfs_datasets`--{{ ref('dim_gtfs_datasets') }}")
RELATIONS = {"cal-itp-data-infra.mart_transit_database.dim_gtfs_datasets": "model:dim_gtfs_datasets", "mart_transit_database.dim_gtfs_datasets": "model:dim_gtfs_datasets", "staging.int_candidates": "model:int_candidates"}


@pytest.fixture(scope="module")
def constraints():
    """The fixture's constraints, loaded once."""
    cs, problems = load_constraints(ROOT / "fixture" / "changelens" / "constraints")
    assert not problems
    return cs


def test_bigquery_sql_is_parsed_in_its_own_dialect():
    """A backticked, hyphenated project name parses as BigQuery; the old duckdb reading reported it as a parse error."""
    ops = ops_from_sql_change("model:dim_provider", "m.sql", BQ_OLD, BQ_NEW, {}, dialect="bigquery", relations=RELATIONS)
    assert not any("did not parse" in str(o.details.get("note", "")) for o in ops), [o.details for o in ops]
    literal = [o for o in ops if o.kind == "literal_relation"]
    assert len(literal) == 1
    assert literal[0].details["node"] == "model:dim_gtfs_datasets"
    assert literal[0].details["relation"].endswith("dim_gtfs_datasets`")
    assert literal[0].details["line"] == 2
    assert "node:model:dim_gtfs_datasets" in literal[0].evidence


def test_a_hardcoded_name_already_there_or_a_plain_ref_is_not_reported():
    """Only a warehouse name the change introduces is a finding; one the old SQL already had, or a ref, is not."""
    assert not [o for o in ops_from_sql_change("model:m", "m.sql", BQ_NEW, BQ_NEW + "\n", {}, dialect="bigquery", relations=RELATIONS) if o.kind == "literal_relation"]
    assert not [o for o in ops_from_sql_change("model:m", "m.sql", BQ_OLD, BQ_OLD + "\n", {}, dialect="bigquery", relations=RELATIONS) if o.kind == "literal_relation"]
    # restoring the ref is the fix, so the reverse diff reports nothing either
    assert not [o for o in ops_from_sql_change("model:m", "m.sql", BQ_NEW, BQ_OLD, {}, dialect="bigquery", relations=RELATIONS) if o.kind == "literal_relation"]


def test_textual_fallback_when_the_sql_does_not_parse():
    """SQL sqlglot cannot read still gets the check, from the quoted names in the text."""
    broken = "select count(distinct trip_id where flex = true) from `cal-itp-data-infra`.`mart_transit_database`.`dim_gtfs_datasets`"
    assert literal_relations(broken, "bigquery", RELATIONS) == {"`cal-itp-data-infra`.`mart_transit_database`.`dim_gtfs_datasets`": "model:dim_gtfs_datasets"}
    assert literal_relations("select 1 from other.schema.table", "bigquery", RELATIONS) == {}


def test_fixture_graph_records_dialect_and_warehouse_names(synced_store):
    """The dbt adapter records the dialect and each asset's database, schema and alias; the resolver maps them."""
    r = PathResolver(synced_store)
    assert r.dialect == "duckdb"
    assert r.relations["main.int_order_payments"] == "model:int_order_payments"
    # the database name depends on how the fixture was built (dbt parse locally, dbt build in CI), so read it back
    db = synced_store.node("model:int_order_payments")["properties"]["database"]
    assert db and r.relations[f"{db}.main.int_order_payments"] == "model:int_order_payments"


def test_a_ref_replaced_by_a_warehouse_name_warns_in_preflight_and_review(synced_store, constraints, tmp_path):
    """End to end on the fixture: the verdict is at least warn, the summary says why, and the review carries a finding
    on the line that reads the table."""
    old = (ROOT / FCT_ORDERS).read_text()
    new = old.replace("{{ ref('int_order_payments') }}", "test_warehouse.main.int_order_payments")  # the schema.table part is what resolves
    assert new != old
    cs = collect_changes(synced_store, ROOT, tmp=tmp_path, file_changes=[(FCT_ORDERS, old, new)])
    literal = [o for o in cs.ops if o.kind == "literal_relation"]
    assert [o.details["node"] for o in literal] == ["model:int_order_payments"]
    v = preflight(synced_store, cs, constraints, RiskModel.load(None), record=False)
    assert v["severity"] in ("warn", "block")
    assert "int_order_payments by its warehouse name test_warehouse.main.int_order_payments" in v["summary"]

    from pipelinebench.review_eval import Change

    r = review(synced_store, ROOT, StubProvider(), constraints=constraints, risk_model=RiskModel.load(None), diff_text=Change(FCT_ORDERS, old, new).diff(), scope=["fixture"], ignore=["fixture/changelens"])
    det = [f for f in r["findings"] if f["source"] == "deterministic" and f["title"].endswith("not ref()")]
    assert len(det) == 1
    f = det[0]
    assert f["severity"] == "warn" and f["path"] == FCT_ORDERS
    assert f["line"] == new.splitlines().index(next(ln for ln in new.splitlines() if "test_warehouse.main.int_order_payments" in ln)) + 1
    assert "node:model:int_order_payments" in f["evidence"] and f"hunk:{FCT_ORDERS}:{f['line']}" in f["evidence"]
    assert r["severity"] in ("warn", "block")
