"""Slice 2: changes -> ops -> predicates -> verdict, on the fixture platform."""

from pathlib import Path

import pytest

from orion.changes import collect_changes, ops_from_intent
from orion.constraints import load_constraints
from orion.risk import RiskModel
from orion.verdict import preflight, to_markdown

ROOT = Path(__file__).resolve().parents[1]
FCT_ORDERS = ROOT / "fixture" / "dbt_project" / "models" / "marts" / "fct_orders.sql"
EVENTS_DAG = ROOT / "fixture" / "dags" / "events_pipeline.py"
ORDERS_DAG = ROOT / "fixture" / "dags" / "orders_pipeline.py"


@pytest.fixture(scope="module")
def constraints():
    cs, problems = load_constraints(ROOT / "fixture" / "orion" / "constraints")
    assert not problems
    return cs


def run(store, constraints, tmp_path, **kwargs):
    cs = collect_changes(store, ROOT, tmp=tmp_path, **kwargs)
    return cs, preflight(store, cs, constraints, RiskModel.load(None), record=False)


def test_sql_diff_detects_dropped_column_and_blocks(synced_store, constraints, tmp_path):
    old = FCT_ORDERS.read_text()
    new = old.replace("    discount_pct,\n", "")
    rel = str(FCT_ORDERS.relative_to(ROOT))
    cs, v = run(synced_store, constraints, tmp_path, file_changes=[(rel, old, new)])
    kinds = {(o.kind, o.column) for o in cs.ops}
    assert ("drop_column", "discount_pct") in kinds
    assert v["severity"] == "block"
    fired = {c["id"] for c in v["constraints"] if c["status"] == "fires"}
    assert fired == {"CON-031"}
    assert any(h["ref"] == "INC-230" and h["same_kind_of_change"] for h in v["history"])
    assert any(c["kind"] == "get_approval" and c["from"] == ["team/finance-data"] for c in v["checks"])
    # every affected asset and constraint carries evidence ids
    assert all(a["evidence"] for a in v["affected"])
    assert all(c["evidence"] for c in v["constraints"])
    assert "CON-031" in v["summary"] and "INC-230" in v["summary"]


def test_sql_diff_detects_rename(synced_store, constraints, tmp_path):
    old = FCT_ORDERS.read_text()
    new = old.replace("    payment_count\n", "    payment_count as payments\n")
    rel = str(FCT_ORDERS.relative_to(ROOT))
    cs, _ = run(synced_store, constraints, tmp_path, file_changes=[(rel, old, new)])
    assert any(o.kind == "rename_column" and o.column == "payment_count" and o.details["to"] == "payments" for o in cs.ops)


def test_add_column_is_inform_and_reaches_nothing(synced_store, constraints, tmp_path):
    cs, v = run(synced_store, constraints, tmp_path, ops=[{"kind": "add_column", "asset": "fct_orders", "column": "currency_rate"}])
    assert v["severity"] == "inform"
    assert v["affected"] == []
    assert not [c for c in v["constraints"] if c["status"] == "fires"]


def test_dag_diff_partition_change_fires_incident_constraint(synced_store, constraints, tmp_path):
    old = EVENTS_DAG.read_text()
    new = old.replace("--partition-by event_date", "--partition-by ingest_ts")
    rel = str(EVENTS_DAG.relative_to(ROOT))
    cs, v = run(synced_store, constraints, tmp_path, file_changes=[(rel, old, new)])
    op = next(o for o in cs.ops if o.kind == "change_partition")
    assert op.asset == "source_table:raw.raw_events"
    assert op.details == {"from": ["event_date"], "to": ["ingest_ts"], "task": "task:events_pipeline.ingest_events"} or op.details["to"] in (["ingest_ts"], "ingest_ts")
    assert v["severity"] == "block"
    assert {c["id"] for c in v["constraints"] if c["status"] == "fires"} == {"CON-014"}
    assert any(h["ref"] == "INC-214" for h in v["history"])


def test_dag_schedule_change_warns(synced_store, constraints, tmp_path):
    old = ORDERS_DAG.read_text()
    new = old.replace('schedule="0 * * * *"', 'schedule="0 3 * * *"')
    rel = str(ORDERS_DAG.relative_to(ROOT))
    cs, v = run(synced_store, constraints, tmp_path, file_changes=[(rel, old, new)])
    assert any(o.kind == "change_schedule" and o.details == {"from": "0 * * * *", "to": "0 3 * * *"} for o in cs.ops)
    assert v["severity"] == "warn"
    assert {c["id"] for c in v["constraints"] if c["status"] == "fires"} == {"CON-040"}
    assert any(a["asset"] == "dag:finance_close" for a in v["affected"])


def test_intent_parser_and_backfill_window(synced_store, constraints, tmp_path):
    ops, unresolved = ops_from_intent(synced_store, "backfill raw_events for the last two weeks")
    assert not unresolved and ops[0].kind == "backfill" and ops[0].asset == "source_table:raw.raw_events"
    ops[0].details["at"] = "2026-10-16T13:00:00+00:00"
    cs, v = run(synced_store, constraints, tmp_path, ops=[{"kind": "backfill", "asset": "raw_events", "details": {"at": "2026-10-16T13:00:00+00:00"}}])
    assert v["severity"] == "block" and {c["id"] for c in v["constraints"] if c["status"] == "fires"} == {"CON-022"}
    cs, v = run(synced_store, constraints, tmp_path, ops=[{"kind": "backfill", "asset": "raw_events", "details": {"at": "2026-10-16T22:30:00+00:00"}}])
    assert not [c for c in v["constraints"] if c["status"] == "fires"]


def test_unresolved_path_is_never_silent(synced_store, constraints, tmp_path):
    cs, v = run(synced_store, constraints, tmp_path, paths=["jobs/mystery_job.py"])
    assert v["change"]["unresolved"] and v["severity"] == "warn"
    assert any(c["kind"] == "map_unresolved_paths" for c in v["checks"])


def test_verdict_is_idempotent_and_recorded(synced_store, constraints):
    cs = collect_changes(synced_store, ROOT, ops=[{"kind": "drop_column", "asset": "fct_orders", "column": "status"}])
    first = preflight(synced_store, cs, constraints, RiskModel.load(None))
    second = preflight(synced_store, cs, constraints, RiskModel.load(None))
    assert first["verdict_id"] == second["verdict_id"] and second.get("reused")
    events = synced_store.events_for(["model:fct_orders"], kinds=["preflight_issued"])
    assert any(e["source_ref"] == first["verdict_id"] for e in events)


def test_markdown_render(synced_store, constraints, tmp_path):
    _, v = run(synced_store, constraints, tmp_path, intent="drop the discount_pct column from fct_orders")
    md = to_markdown(v)
    assert md.startswith("**Orion pre-flight: BLOCK**") and "CON-031" in md and "diff:" not in md


def test_risk_factors_are_transparent(synced_store, constraints, tmp_path):
    _, v = run(synced_store, constraints, tmp_path, intent="drop the discount_pct column from fct_orders")
    names = {f["name"] for f in v["risk"]["factors"]}
    assert names == {"tier1_consumers", "block_constraint", "incident_recency", "destructive_op", "blast_radius"}
    assert v["risk"]["score"] == round(min(100, sum(f["contribution"] for f in v["risk"]["factors"])))


SCHEMA_YML = ROOT / "fixture" / "dbt_project" / "models" / "marts" / "schema.yml"


def test_schema_yml_edit_touches_only_the_entries_it_changes(synced_store, constraints, tmp_path):
    old = SCHEMA_YML.read_text()
    tests_only = old.replace("        tests: [unique, not_null]\n", "        tests: [unique, not_null, relationships]\n")
    rel = str(SCHEMA_YML.relative_to(ROOT))
    cs, v = run(synced_store, constraints, tmp_path, file_changes=[(rel, old, tests_only)])
    assert [(o.kind, o.asset) for o in cs.ops] == [("change_metadata", "model:fct_orders")]
    assert cs.ops[0].details["field"] == "schema entry changed: columns"
    assert not cs.unresolved

    config = old.replace("      contract: { enforced: false }\n", "      contract: { enforced: false }\n      materialized: table\n")
    cs, _ = run(synced_store, constraints, tmp_path, file_changes=[(rel, old, config)])
    assert [(o.kind, o.asset) for o in cs.ops] == [("modify", "model:fct_revenue")], "config in a schema file can change the build"


def test_prose_is_ignored_and_a_deleted_unknown_model_says_why(synced_store, constraints, tmp_path):
    cs, v = run(synced_store, constraints, tmp_path, file_changes=[
        ("fixture/dbt_project/models/docs/orders.md", "{% docs orders %}old{% enddocs %}", "{% docs orders %}new{% enddocs %}"),
        ("fixture/dbt_project/models/marts/fct_gone.sql", "select 1 as id", None),
    ])
    assert cs.ignored == ["fixture/dbt_project/models/docs/orders.md"]
    assert [u["path"] for u in cs.unresolved] == ["fixture/dbt_project/models/marts/fct_gone.sql"]
    assert cs.unresolved[0]["reason"].startswith("deleted")
    assert v["severity"] == "warn" and v["raised_for_unresolved"]


def test_schema_files_and_singular_tests_map_to_their_models(tmp_path):
    from orion.adapters.dbt import _patch_path, _singular_tests
    from orion.model import Node, NodeKind, Provenance
    from orion.resolver import PathResolver
    from orion.store import Store

    project = tmp_path / "warehouse"
    manifest = {
        "nodes": {
            "model.p.orders": {"resource_type": "model", "patch_path": "p://models/_orders.yml"},
            "test.p.orders_balance": {"resource_type": "test", "original_file_path": "tests/orders_balance.sql"},
            "test.p.not_null_orders_id": {"resource_type": "test", "original_file_path": "models/_orders.yml"},
        },
        "child_map": {"model.p.orders": ["test.p.orders_balance", "test.p.not_null_orders_id"]},
    }
    assert _patch_path(manifest["nodes"]["model.p.orders"], project, tmp_path) == "warehouse/models/_orders.yml"
    assert _singular_tests(manifest, "model.p.orders", project, tmp_path) == ["warehouse/tests/orders_balance.sql"]

    store = Store(":memory:")
    store.upsert_node(Node(NodeKind.MODEL, "orders", properties={"path": "warehouse/models/orders.sql", "schema_path": "warehouse/models/_orders.yml", "test_paths": ["warehouse/tests/orders_balance.sql"]}, provenance=[Provenance("test", "unit")]))
    r = PathResolver(store)
    assert r.resolve("warehouse/models/_orders.yml") == ["model:orders"]
    assert r.tested_by("warehouse/tests/orders_balance.sql") == ["model:orders"]
    assert r.resolve("warehouse/tests/orders_balance.sql") == []


def test_a_changed_schema_entry_the_graph_lacks_is_unresolved_not_dropped(synced_store, constraints, tmp_path):
    old = SCHEMA_YML.read_text()
    new = old.replace("  - name: fct_refunds\n", "  - name: fct_returns_v9\n    description: new\n  - name: fct_refunds\n")
    rel = str(SCHEMA_YML.relative_to(ROOT))
    cs, _ = run(synced_store, constraints, tmp_path, file_changes=[(rel, old, new)])
    assert cs.unresolved == [{"path": rel, "reason": "changed schema entries the graph has no asset for: fct_returns_v9", "entries": ["fct_returns_v9"]}]


def test_an_intent_naming_the_diffs_change_adds_evidence_not_a_second_op(synced_store, constraints, tmp_path):
    old = FCT_ORDERS.read_text()
    new = old.replace("    discount_pct,\n", "")
    rel = str(FCT_ORDERS.relative_to(ROOT))
    cs, v = run(synced_store, constraints, tmp_path, file_changes=[(rel, old, new)], intent="drop discount_pct from fct_orders")
    drops = [o for o in cs.ops if o.kind == "drop_column" and o.column == "discount_pct"]
    assert len(drops) == 1 and not drops[0].from_intent and "intent" in drops[0].evidence


def test_run_history_and_dbt_build_output_are_not_platform_changes():
    from orion.cli import _ignore
    from orion.config import load_config

    out = _ignore(load_config(ROOT / "orion.yaml"))
    assert {"fixture/dbt_project/target", "fixture/runs/airflow_runs.jsonl", "fixture/runs/dbt/*.run_results.json", "fixture/runs/iceberg"} <= set(out)


def test_a_config_with_no_platform_adapter_maps_no_file_and_raises_nothing(tmp_path):
    from orion.cli import _ignore
    from orion.config import OrionConfig
    from orion.store import Store

    cfg = OrionConfig(root=tmp_path)  # an orion.yaml naming only a database, as in an application repository
    assert _ignore(cfg) == ["*"]
    (tmp_path / "main.go").write_text("package main\n")
    store = Store(tmp_path / "g.duckdb")
    cs = collect_changes(store, tmp_path, file_changes=[("main.go", "package main\n", "package main\n\nfunc f() {}\n")], ignore=_ignore(cfg))
    v = preflight(store, cs, [], RiskModel.load(None), record=False)
    assert cs.unresolved == [] and cs.ignored == ["main.go"]
    assert v["severity"] == "inform"


def test_ignore_roots_and_globs():
    from orion.changes import ignored_path

    ign = ["fixture/runs/dbt/*.run_results.json", "fixture/dbt_project/target", "", "*.nothing-else"]
    assert ignored_path("fixture/runs/dbt/2026.run_results.json", ign)
    assert ignored_path("fixture/dbt_project/target/manifest.json", ign)
    assert not ignored_path("fixture/runs/dbt/notes.md", ign)
    assert not ignored_path("fixture/dbt_project/models/x.sql", ign)


def test_a_task_change_reaches_the_tables_it_writes_and_their_readers():
    from orion.changes import ChangeOp, ChangeSet
    from orion.model import Confidence, Edge, EdgeKind, Node, NodeKind, Provenance
    from orion.store import Store
    from orion.verdict import affected_assets

    p = [Provenance("test", "unit")]
    s = Store(":memory:")
    s.write_graph(
        [Node(NodeKind.TASK, "load.settlements", provenance=p), Node(NodeKind.SOURCE_TABLE, "raw.settlements", provenance=p), Node(NodeKind.MODEL, "stg_settlements", provenance=p)],
        [Edge("task:load.settlements", "source_table:raw.settlements", EdgeKind.PRODUCES, Confidence.DECLARED, provenance=p), Edge("model:stg_settlements", "source_table:raw.settlements", EdgeKind.CONSUMES, Confidence.PARSED, provenance=p)],
    )
    cs = ChangeSet()
    cs.add(ChangeOp("modify", "task:load.settlements"))
    assert {k: v["hops"] for k, v in affected_assets(s, cs).items()} == {"source_table:raw.settlements": 1, "model:stg_settlements": 2}


def test_routine_tests_are_not_worded_as_a_gate(synced_store, constraints, tmp_path):
    path = ROOT / "fixture" / "dbt_project" / "models" / "marts" / "dim_customers.sql"
    old = path.read_text()
    new = old.replace("    last_order_date\n", "    last_order_date,\n    1 as tenure\n")
    _, v = run(synced_store, constraints, tmp_path, file_changes=[(str(path.relative_to(ROOT)), old, new)])
    assert v["severity"] == "inform" and [c["kind"] for c in v["checks"]] == ["run_tests"]
    assert "Nothing beyond normal review and CI: run tests for dim_customers+." in v["summary"] and "Before merging" not in v["summary"]
    assert "**Routine checks**" in to_markdown(v)
    _, block = run(synced_store, constraints, tmp_path, intent="drop the discount_pct column from fct_orders")
    assert "Before merging: " in block["summary"] and "**Recommended checks**" in to_markdown(block)


def test_unrelated_history_is_background_not_a_reason_for_caution(synced_store, constraints, tmp_path):
    _, v = run(synced_store, constraints, tmp_path, ops=[{"kind": "add_column", "asset": "fct_orders", "column": "currency_rate"}])
    # incidents stay visible, but an add_column is not the change that caused them
    assert {h["ref"] for h in v["history"]} >= {"INC-230", "INC-241"}
    assert not any(h["same_kind_of_change"] for h in v["history"])
    assert {h["kind"] for h in v["history"]} <= {"postmortem_filed", "incident_opened"}
    assert v["background_events"] and {b["kind"] for b in v["background_events"]} <= {"run_failed", "sla_missed", "schema_changed", "outcome_observed"}
    assert "not from this kind of change" in v["summary"] and "incident history" not in v["summary"]
    recency = next(f for f in v["risk"]["factors"] if f["name"] == "incident_recency")
    assert recency["contribution"] == 0
    md = to_markdown(v)
    assert md.count("INC-230") == 2  # summary, and one history line for its opening and postmortem
    assert "Other events on these assets" in md


def test_repartitioning_to_the_same_key_reaches_nothing(synced_store, constraints, tmp_path):
    _, v = run(synced_store, constraints, tmp_path, ops=[{"kind": "change_partition", "asset": "raw_events", "details": {"to": "event_date", "from": "event_date"}}])
    assert v["severity"] == "inform"
    assert v["affected"] == []
    assert not any(h["same_kind_of_change"] for h in v["history"])
    assert "to the key it already has" in v["summary"]


def test_many_failed_runs_do_not_crowd_an_incident_out_of_history(tmp_path):
    from datetime import UTC, datetime, timedelta

    from orion.changes import ChangeOp, ChangeSet
    from orion.model import Event, EventKind
    from orion.store import Store
    from orion.verdict import _history

    store = Store(tmp_path / "g.duckdb")
    now = datetime.now(UTC)
    store.append_event(Event(EventKind.POSTMORTEM_FILED, now - timedelta(days=90), ["model:m"], source="pm", source_ref="INC-1", payload={"cause": "drop_column", "title": "dropped a column"}))
    store.append_event(Event(EventKind.SLA_MISSED, now - timedelta(days=200), ["model:m"], source="runs", source_ref="late", payload={"cause": "SLA"}))
    for i in range(30):
        store.append_event(Event(EventKind.SLA_MISSED, now - timedelta(hours=i), ["model:m"], source="runs", source_ref=f"s{i}", payload={}))
    for i in range(120):
        store.append_event(Event(EventKind.RUN_FAILED, now - timedelta(hours=i), ["model:m"], source="runs", source_ref=f"r{i}", payload={}))
    cs = ChangeSet()
    cs.add(ChangeOp("drop_column", "model:m", column="c"))
    history, background = _history(store, cs, {})
    assert [h["ref"] for h in history] == ["INC-1"] and history[0]["same_kind_of_change"]
    assert len(background) == 20
    cs = ChangeSet()
    cs.add(ChangeOp("change_schedule", "model:m"))
    history, _ = _history(store, cs, {})
    assert "late" in [h["ref"] for h in history], "an older missed SLA is the same kind of change as a schedule change, behind 30 newer ones"
    store.append_event(Event(EventKind.SCHEMA_CHANGED, now - timedelta(days=30), ["model:m"], source="git", source_ref="typed", payload={"cause": "change_type"}))
    cs = ChangeSet()
    cs.add(ChangeOp("change_type", "model:m", column="c"))
    history, _ = _history(store, cs, {})
    assert "typed" in [h["ref"] for h in history], "an op kind with no family still matches its own name"


def test_an_unmapped_dbt_project_file_says_why_in_words(synced_store, constraints, tmp_path):
    cs, _ = run(synced_store, constraints, tmp_path, file_changes=[("fixture/dbt_project/dbt_project.yml", "name: x\n", "name: x\nvars:\n  a: 1\n")])
    [u] = cs.unresolved
    assert isinstance(u["reason"], str) and "dbt project configuration" in u["reason"]
    cs, _ = run(synced_store, constraints, tmp_path, file_changes=[("fixture/dbt_project/package-lock.yml", "packages: []\n", "packages:\n  - package: dbt-labs/dbt_utils\n    version: 1.4.1\n")])
    [u] = cs.unresolved
    assert "dbt package versions" in u["reason"]
