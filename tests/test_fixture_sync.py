"""End-to-end: sync the fixture platform and ask the graph the questions a verdict will ask."""

import json
from pathlib import Path

from fathom.constraints import load_constraints
from fathom.explain import explain
from fathom.model import EdgeKind
from fathom.resolver import PathResolver, resolve_ref

ROOT = Path(__file__).resolve().parents[1]


def test_every_fact_has_provenance(synced_store):
    missing = synced_store.con.execute(
        """
        SELECT count(*) FROM (
            SELECT id FROM nodes UNION ALL SELECT id FROM edges
        ) f LEFT JOIN provenance p ON p.subject_id = f.id WHERE p.subject_id IS NULL
        """
    ).fetchone()[0]
    assert missing == 0


def test_resolver_handles_prefixes_and_short_names(synced_store):
    assert resolve_ref(synced_store, "fct_orders").node_id == "model:fct_orders"
    assert resolve_ref(synced_store, "source:raw.raw_events").node_id == "source_table:raw.raw_events"
    assert resolve_ref(synced_store, "raw_events").node_id == "source_table:raw.raw_events"
    assert resolve_ref(synced_store, "exposure:dash_revenue_weekly").node_id == "dashboard:dash_revenue_weekly"
    assert resolve_ref(synced_store, "dag:finance_close").node_id == "dag:finance_close"
    miss = resolve_ref(synced_store, "fct_order")
    assert not miss.ok and any(c["id"] == "model:fct_orders" for c in miss.candidates)


def test_column_lineage_crosses_ephemeral_models(synced_store):
    out = explain(synced_store, "fct_revenue")
    gross = next(c for c in out["columns"] if c["name"] == "gross_revenue")
    assert {d["name"] for d in gross["derives_from"]} == {"fct_orders.net_amount"}
    out = explain(synced_store, "fct_orders")
    net = next(c for c in out["columns"] if c["name"] == "net_amount")
    assert {d["name"] for d in net["derives_from"]} == {"int_order_payments.amount", "int_order_payments.discount_pct"}
    # and the ephemeral model's columns reach the staging layer
    out = explain(synced_store, "int_order_payments")
    amount = next(c for c in out["columns"] if c["name"] == "amount")
    assert {d["name"] for d in amount["derives_from"]} == {"stg_orders.amount"}


def test_blast_radius_reaches_finance_close_through_two_hops(synced_store):
    out = explain(synced_store, "fct_orders")
    tier1 = {d["id"]: d["hops"] for d in out["tier1_downstream"]}
    assert tier1["dashboard:dash_revenue_weekly"] == 1
    assert tier1["report:finance_close_job"] == 2


def test_airflow_links_tasks_to_models_and_across_dags(synced_store):
    produces = {e["dst"] for e in synced_store.edges_from("task:orders_pipeline.dbt_marts", (EdgeKind.PRODUCES,))}
    assert {"model:fct_orders", "model:fct_revenue", "model:dim_customers", "model:mart_churn_scores"} <= produces
    sensor = synced_store.edges_from("task:finance_close.wait_for_orders", (EdgeKind.DEPENDS_ON,))
    assert any(e["dst"] == "task:orders_pipeline.dbt_test" for e in sensor)
    ingest = synced_store.edges_from("task:events_pipeline.ingest_events", (EdgeKind.PRODUCES,))
    assert any(e["dst"] == "source_table:raw.raw_events" for e in ingest)


def test_incidents_reach_the_assets_they_name(synced_store):
    events = synced_store.events_for(["source_table:raw.raw_events"], kinds=["postmortem_filed"])
    assert {e["source_ref"] for e in events} == {"INC-214", "INC-241"}
    assert "six hours" in events[-1]["payload"]["body"] or any("six hours" in e["payload"]["body"] for e in events)


def test_constraints_load_and_scope(synced_store):
    constraints, problems = load_constraints(ROOT / "fixture" / "fathom" / "constraints")
    assert problems == []
    assert {c.id for c in constraints} == {"CON-014", "CON-022", "CON-031", "CON-040", "CON-050", "CON-061"}
    out = explain(synced_store, "fct_revenue", constraints)
    assert [c["id"] for c in out["constraints"]] == ["CON-031"]


def test_path_resolver_maps_model_files(synced_store):
    r = PathResolver(synced_store)
    assert r.resolve("fixture/dbt_project/models/marts/fct_orders.sql") == ["model:fct_orders"]
    assert "dag:events_pipeline" in r.resolve("fixture/dags/events_pipeline.py")


def test_tier_inference_fills_gaps_upstream_of_tier_one(synced_store):
    node = synced_store.node("model:int_order_payments")
    assert node["tier"] == 1 and node["properties"].get("tier_source") == "inferred"


def test_column_lineage_is_cached_between_syncs(tmp_path):
    from fathom.adapters import dbt as dbt_adapter

    project = ROOT / "fixture" / "dbt_project"
    cache = tmp_path / "lineage-cache.json"
    first = dbt_adapter.load(project, ROOT, cache_path=cache)
    assert cache.exists() and json.loads(cache.read_text())
    calls = []
    real = dbt_adapter._lineage_job
    dbt_adapter._lineage_job = lambda job: calls.append(job[0]) or real(job)
    try:
        again = dbt_adapter.load(project, ROOT, cache_path=cache)
    finally:
        dbt_adapter._lineage_job = real
    assert calls == [], "nothing changed, so nothing is recomputed"
    edges = lambda e: sorted((x.src, x.dst, str(x.kind)) for x in e.edges)  # noqa: E731
    assert edges(first) == edges(again)
    cache.write_text(json.dumps({k: "not an entry" for k in json.loads(cache.read_text())}))
    assert edges(dbt_adapter.load(project, ROOT, cache_path=cache)) == edges(first), "a malformed cache is recomputed"
