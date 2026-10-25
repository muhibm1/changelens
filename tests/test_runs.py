"""Slice 3: run history from Airflow, dbt and Iceberg becomes ledger events."""

from fathom.adapters.runs import parse_timedelta


def _kinds(store, asset, kind):
    return store.events_for([asset], kinds=[kind], limit=100)


def test_parse_timedelta():
    assert parse_timedelta("timedelta(minutes=45)").total_seconds() == 45 * 60
    assert parse_timedelta("timedelta(hours=2)").total_seconds() == 7200
    assert parse_timedelta(None) is None
    assert parse_timedelta("nonsense") is None


def test_airflow_runs_land_on_task_and_what_it_builds(synced_store):
    failed = _kinds(synced_store, "task:orders_pipeline.dbt_test", "run_failed")
    assert len(failed) == 4, "INC-230 afternoon: four failed test runs"
    assert all("model:fct_orders" in e["assets"] for e in failed)
    assert all(e["payload"]["run_id"] for e in failed)
    ok = _kinds(synced_store, "task:events_pipeline.ingest_events", "run_succeeded")
    assert ok and all("source_table:raw.raw_events" in e["assets"] for e in ok)


def test_sla_miss_is_derived_from_declared_sla(synced_store):
    misses = _kinds(synced_store, "task:finance_close.dbt_finance", "sla_missed")
    assert len(misses) == 2
    assert all(e["payload"]["sla_s"] == 1800 and e["payload"]["duration_s"] > 1800 for e in misses)
    marts = _kinds(synced_store, "task:orders_pipeline.dbt_marts", "sla_missed")
    assert len(marts) == 3  # INC-241 afternoon, 71-minute runs against a 45-minute SLA


def test_dbt_run_results_attach_test_failures_to_the_model(synced_store):
    failed = _kinds(synced_store, "model:fct_refunds", "run_failed")
    tests = [e for e in failed if e["payload"].get("test")]
    assert len(tests) == 2
    assert {e["payload"]["failures"] for e in tests} == {37, 12}
    assert all(e["source_ref"].endswith(("2026-10-15", "2026-10-12")) for e in tests)


def test_iceberg_snapshots_schema_and_partition_changes(synced_store):
    asset = "source_table:raw.raw_events"
    assert len(_kinds(synced_store, asset, "snapshot_committed")) == 5
    changes = _kinds(synced_store, asset, "schema_changed")
    by_cause = {}
    for c in changes:
        by_cause.setdefault(c["payload"]["cause"], []).append(c)
    assert len(by_cause["partition_change"]) == 2
    assert {tuple(c["payload"]["to"]) for c in by_cause["partition_change"]} == {("ingest_ts",), ("event_date",)}
    assert by_cause["add_column"][0]["payload"]["added"] == ["session_id"]
    node = synced_store.node(asset)
    assert node["properties"]["partition_by"] == ["event_date"]
    assert "session_id" in node["properties"]["columns"]
    assert any(p["adapter"] == "runs" for p in synced_store.provenance_for(asset))


def test_resync_does_not_duplicate_provenance(synced_store):
    before = synced_store.counts()["provenance"]
    from pathlib import Path

    from fathom.config import load_config
    from fathom.sync import run_sync

    run_sync(load_config(Path(__file__).resolve().parents[1] / "fathom.yaml"), synced_store, only=["runs"], force=True)
    assert synced_store.counts()["provenance"] == before
