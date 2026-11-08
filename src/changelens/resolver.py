"""Resolve references and paths to graph assets.

Two jobs: turn a human or incident-file reference like ``fct_orders``,
``source:raw.raw_events`` or ``exposure:dash_revenue_weekly`` into a node id,
and turn a repository path into the assets whose definition lives there.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from .model import NodeKind
from .store import Store

PREFIX_ALIASES = {
    "source": [NodeKind.SOURCE_TABLE],
    "exposure": [NodeKind.DASHBOARD, NodeKind.REPORT],
    "table": [NodeKind.SOURCE_TABLE, NodeKind.MODEL, NodeKind.ICEBERG_TABLE],
}
ASSET_KINDS_IN_ORDER = [
    NodeKind.MODEL, NodeKind.SOURCE_TABLE, NodeKind.SEED, NodeKind.SNAPSHOT, NodeKind.ICEBERG_TABLE,
    NodeKind.DAG, NodeKind.TASK, NodeKind.SPARK_JOB, NodeKind.DASHBOARD, NodeKind.REPORT,
]


@dataclass
class Resolution:
    ref: str
    node_id: str | None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    method: str = "exact"

    @property
    def ok(self) -> bool:
        return self.node_id is not None


def resolve_ref(store: Store, ref: str) -> Resolution:
    ref = ref.strip()
    if ":" in ref:
        prefix, name = ref.split(":", 1)
        kinds = PREFIX_ALIASES.get(prefix)
        if kinds is None:
            node = store.node(ref)
            return Resolution(ref, node["id"] if node else None)
        for k in kinds:
            node = store.node(f"{k}:{name}")
            if node:
                return Resolution(ref, node["id"], method="alias")
        if NodeKind.SOURCE_TABLE in kinds:
            hit = _source_by_relation(store, name)
            if hit:
                return Resolution(ref, hit, method="relation")
        return Resolution(ref, None, candidates=store.search_nodes(name.split(".")[-1], limit=5), method="fuzzy")
    for k in ASSET_KINDS_IN_ORDER:
        node = store.node(f"{k}:{ref}")
        if node:
            return Resolution(ref, node["id"])
        if k is NodeKind.SOURCE_TABLE:
            # a bare source table name, e.g. raw_events -> source_table:raw.raw_events
            for n in store.find_nodes(kind=str(NodeKind.SOURCE_TABLE)):
                if n["name"].split(".")[-1] == ref:
                    return Resolution(ref, n["id"], method="short-name")
            hit = _source_by_relation(store, ref)
            if hit:
                return Resolution(ref, hit, method="relation")
    cands = store.search_nodes(ref, limit=5)
    return Resolution(ref, None, candidates=cands, method="fuzzy")


def _source_by_relation(store: Store, name: str) -> str | None:
    """A source named the way the warehouse names it, ``<schema>.<table>``, rather than dbt's
    ``<source_name>.<table>``: ``raw.load_runs`` for dbt source ``odoo.load_runs`` in schema ``raw``.
    Only an unambiguous match resolves."""
    if "." not in name:
        return None
    schema, table = name.lower().rsplit(".", 1)
    hits = [
        n["id"]
        for n in store.find_nodes(kind=str(NodeKind.SOURCE_TABLE))
        if str(n["properties"].get("schema") or "").lower().split(".")[-1] == schema
        and str(n["properties"].get("identifier") or n["name"].split(".")[-1]).lower() == table
    ]
    return hits[0] if len(hits) == 1 else None


class PathResolver:
    """Map repository paths to assets by the ``path`` property adapters record, plus the schema .yml
    (``schema_path``), hand-written test files (``test_paths``) and project macro files (``macro_paths``) dbt records
    for a node."""

    def __init__(self, store: Store):
        self.store = store
        self._dialect: str | None = None
        self._relations: dict[str, str] | None = None
        self.by_path: dict[str, list[str]] = {}
        self.by_test: dict[str, list[str]] = {}
        self.by_macro: dict[str, list[str]] = {}
        for nid, path, schema_path, test_paths, macro_paths in store.con.execute(
            "SELECT id, json_extract_string(properties, '$.path'), json_extract_string(properties, '$.schema_path'),"
            " json_extract(properties, '$.test_paths'), json_extract(properties, '$.macro_paths') FROM nodes WHERE kind <> 'column'"
        ).fetchall():
            for p in json.loads(macro_paths) if macro_paths else []:
                self.by_macro.setdefault(str(PurePosixPath(p)), []).append(nid)
            for p in dict.fromkeys(x for x in (path, schema_path) if x):
                self.by_path.setdefault(str(PurePosixPath(p)), []).append(nid)
            for p in json.loads(test_paths) if test_paths else []:
                self.by_test.setdefault(str(PurePosixPath(p)), []).append(nid)

    def resolve(self, path: str) -> list[str]:
        p = str(PurePosixPath(path))
        if p in self.by_path:
            return list(self.by_path[p])
        # a schema.yml names several assets; a model file names one
        return [nid for k, ids in self.by_path.items() if k.endswith(p) or p.endswith(k) for nid in ids]

    @property
    def dialect(self) -> str:
        """The SQL dialect the dbt adapter recorded on its nodes; duckdb when the graph has none."""
        if self._dialect is None:
            row = self.store.con.execute("SELECT json_extract_string(properties, '$.dialect') FROM nodes WHERE kind IN ('model', 'seed', 'snapshot', 'source_table') AND json_extract_string(properties, '$.dialect') IS NOT NULL LIMIT 1").fetchone()
            self._dialect = (row[0] if row and row[0] else None) or "duckdb"
        return self._dialect

    @property
    def relations(self) -> dict[str, str]:
        """Warehouse names -> node id, for a model, seed, snapshot or source table: ``db.schema.table`` and
        ``schema.table``, lower-cased and unquoted, so a model that reads one by that name instead of ``ref()`` or
        ``source()`` can be told."""
        if self._relations is None:
            self._relations = {}
            for n in self.store.find_nodes():
                if n["kind"] not in ("model", "seed", "snapshot", "source_table"):
                    continue
                props = n["properties"]
                table = props.get("alias") or props.get("identifier") or n["name"].split(".")[-1]
                schema = props.get("schema")
                if not schema:
                    continue
                keys = {f"{schema}.{table}"}
                if props.get("database"):
                    keys.add(f"{props['database']}.{schema}.{table}")
                for key in keys:
                    self._relations.setdefault(key.lower(), n["id"])
        return self._relations

    def macro_users(self, path: str) -> list[str]:
        """Assets whose SQL calls the project macro defined at ``path``."""
        return list(self.by_macro.get(str(PurePosixPath(path)), []))

    def tested_by(self, path: str) -> list[str]:
        """Assets a hand-written test file at ``path`` reads."""
        return list(self.by_test.get(str(PurePosixPath(path)), []))
