"""Incident notes that name a dbt source by its warehouse relation (``source:raw.load_runs``) must reach the
source node dbt names ``odoo.load_runs``; an ambiguous relation must stay unresolved, never guessed."""

import json
from pathlib import Path

from fathom.config import load_config
from fathom.resolver import resolve_ref
from fathom.store import Store
from fathom.sync import run_sync


def _source(source_name: str, table: str, schema: str = "raw") -> dict:
    return {
        "unique_id": f"source.p.{source_name}.{table}",
        "resource_type": "source",
        "source_name": source_name,
        "name": table,
        "identifier": table,
        "database": "db",
        "schema": schema,
        "relation_name": f'"db"."{schema}"."{table}"',
        "original_file_path": "models/sources.yml",
    }


def _note(inc: str, assets: list[str]) -> str:
    return (
        f"---\nid: {inc}\ntitle: A scheduled run waited behind an open transaction\nassets: [{', '.join(assets)}]\n"
        "started_at: 2026-10-27T09:03:00Z\nresolved_at: 2026-10-27T09:13:00Z\nseverity: sev2\ncause: lock_wait\n"
        'trigger_change: "build"\nconstraints_proposed: []\nowner: team/data-platform\n---\nThe load waited.\n'
    )


def _sync(tmp_path: Path, sources: list[dict], notes: dict[str, list[str]]):
    target = tmp_path / "dbt" / "target"
    target.mkdir(parents=True)
    manifest = {
        "metadata": {"adapter_type": "duckdb"},
        "sources": {s["unique_id"]: s for s in sources},
        "nodes": {
            "model.p.revenue_by_customer": {
                "unique_id": "model.p.revenue_by_customer",
                "resource_type": "model",
                "name": "revenue_by_customer",
                "original_file_path": "models/revenue_by_customer.sql",
                "depends_on": {"nodes": [sources[0]["unique_id"]]},
                "config": {"materialized": "table"},
                "columns": {},
            }
        },
        "exposures": {},
        "child_map": {},
        "parent_map": {},
    }
    (target / "manifest.json").write_text(json.dumps(manifest))
    inc = tmp_path / "incidents"
    inc.mkdir()
    for inc_id, assets in notes.items():
        (inc / f"{inc_id}.md").write_text(_note(inc_id, assets))
    (tmp_path / "fathom.yaml").write_text("db: g.duckdb\nadapters:\n  dbt:\n    project: dbt\n  incidents:\n    dir: incidents\n")
    store = Store(tmp_path / "g.duckdb")
    results = run_sync(load_config(tmp_path / "fathom.yaml"), store)
    notes_out = [n for r in results for n in r.notes]
    return store, notes_out


def test_schema_named_source_reaches_dbt_named_source(tmp_path):
    store, notes = _sync(
        tmp_path,
        [_source("odoo", "load_runs"), _source("odoo", "account_move")],
        {"INC-002": ["source:raw.load_runs", "model:revenue_by_customer"], "INC-003": ["source:raw.account_move"]},
    )
    assert not [n for n in notes if "unresolved asset" in n]
    node = "source_table:odoo.load_runs"
    ev = store.events_for([node], kinds=["incident_opened"])
    assert [e["source_ref"] for e in ev] == ["INC-002"]
    assert node in ev[0]["assets"] and "model:revenue_by_customer" in ev[0]["assets"]
    # what `fathom history raw.load_runs` and `fathom history source:raw.load_runs` look up
    for ref in ("source:raw.load_runs", "raw.load_runs"):
        r = resolve_ref(store, ref)
        assert r.node_id == node
        assert [e["source_ref"] for e in store.events_for([r.node_id])] == ["INC-002", "INC-002"]
    assert {e["source_ref"] for e in store.events_for(["source_table:odoo.account_move"])} == {"INC-003"}


def test_dbt_named_source_still_resolves(tmp_path):
    store, notes = _sync(tmp_path, [_source("odoo", "load_runs")], {"INC-004": ["source:odoo.load_runs"]})
    assert not [n for n in notes if "unresolved asset" in n]
    assert {e["source_ref"] for e in store.events_for(["source_table:odoo.load_runs"])} == {"INC-004"}


def test_ambiguous_schema_relation_is_unresolved_not_guessed(tmp_path):
    store, notes = _sync(
        tmp_path,
        [_source("odoo", "load_runs"), _source("erp", "load_runs")],
        {"INC-005": ["source:raw.load_runs"], "INC-006": ["source:odoo.load_runs"]},
    )
    assert any("INC-005.md: unresolved asset source:raw.load_runs" in n for n in notes)
    assert not any("INC-006" in n for n in notes)
    assert not resolve_ref(store, "source:raw.load_runs").ok
    assert {e["source_ref"] for e in store.events_for(["source_table:odoo.load_runs", "source_table:erp.load_runs"])} == {"INC-006"}
