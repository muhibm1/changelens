"""dbt adapter: manifest.json (+ catalog.json when present) to graph.

Emits models, seeds, snapshots, sources, exposures, teams, columns and the
edges between them. Column lineage is computed with sqlglot from the model
SQL, so it needs no warehouse connection.
"""

from __future__ import annotations

import hashlib
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ..lineage import column_lineage, dejinja, parse_model, schema_key, tables_schema
from ..model import Confidence, Edge, EdgeKind, Node, NodeKind, Provenance, node_id
from . import Emitted, repo_relative

ADAPTER = "dbt"

EXPOSURE_KIND = {"dashboard": NodeKind.DASHBOARD}

# dbt adapter type -> sqlglot dialect. Unknown adapters are parsed as duckdb, which reads most ANSI SQL.
DIALECTS = {"bigquery": "bigquery", "snowflake": "snowflake", "redshift": "redshift", "postgres": "postgres", "duckdb": "duckdb", "databricks": "databricks", "spark": "spark", "trino": "trino", "athena": "athena", "clickhouse": "clickhouse", "sqlserver": "tsql", "fabric": "tsql", "oracle": "oracle", "mysql": "mysql", "teradata": "teradata"}


def _model_sql(n: dict, relations: dict[str, str]) -> str | None:
    """The SQL to read lineage from. dbt's compiled SQL has every macro expanded, so it is used when the manifest has it,
    with each relation it reads renamed to the key the graph uses; otherwise the raw SQL with Jinja reduced (``dejinja``)."""
    compiled = n.get("compiled_code")
    if compiled:
        for rel in sorted(relations, key=len, reverse=True):
            if rel in compiled:
                compiled = compiled.replace(rel, relations[rel])
        return compiled
    return dejinja(n["raw_code"]) if n.get("raw_code") else None


def _meta(d: dict) -> dict:
    meta = dict(d.get("meta") or {})
    meta.update((d.get("config") or {}).get("meta") or {})
    return meta


def _patch_path(n: dict, project_dir: Path, repo_root: Path) -> str | None:
    """The schema .yml that documents and tests this node; dbt writes it as ``<project>://models/x.yml``."""
    patch = n.get("patch_path")
    return repo_relative(project_dir / patch.split("://", 1)[-1], repo_root) if patch else None


def _singular_tests(manifest: dict, uid: str, project_dir: Path, repo_root: Path) -> list[str]:
    """Paths of the hand-written test .sql files that read this node (generic tests live in the schema .yml)."""
    out = []
    for t in manifest.get("child_map", {}).get(uid, []):
        path = (manifest.get("nodes", {}).get(t) or {}).get("original_file_path") or ""
        if t.startswith("test.") and path.endswith(".sql"):
            out.append(repo_relative(project_dir / path, repo_root))
    return sorted(set(out))


def _owner_node(owner: str, prov: Provenance) -> Node:
    kind = NodeKind.TEAM if owner.startswith("team/") else NodeKind.PERSON
    return Node(kind=kind, name=owner, provenance=[prov])


PARALLEL_MIN = 40  # below this many models, column lineage runs in this process


def _lineage_job(job: tuple[str, str, dict[str, dict[str, str]], str]) -> tuple[str, list[str], dict[str, list[tuple[str, str]]], str | None]:
    gid, sql, schema, dialect = job
    lin = column_lineage(sql, schema, dialect)
    return gid, lin.columns, {c: sorted(d) for c, d in lin.derives.items()}, lin.error


LINEAGE_VERSION = 1  # bump when column lineage changes what it computes, so cached results are recomputed


def _job_key(sql: str, schema: dict[str, dict[str, str]], dialect: str) -> str:
    return hashlib.sha1(json.dumps([LINEAGE_VERSION, sql, dialect, sorted((k, sorted(v)) for k, v in schema.items())]).encode()).hexdigest()


def _all_lineage(jobs: list[tuple[str, str, dict[str, dict[str, str]], str]], cache_path: Path | None, notes: list[str] | None = None) -> list[tuple[str, list[str], dict[str, list[tuple[str, str]]], str | None]]:
    """Column lineage for every model. A model whose SQL, dialect and input tables are unchanged since the last sync is
    read from ``cache_path``; the rest are computed, across processes when there are many (sqlglot is pure Python)."""
    cache: dict[str, list] = {}
    if cache_path and cache_path.exists():
        try:
            raw = json.loads(cache_path.read_text())
        except (OSError, ValueError):
            raw = {}
        # an entry in any other shape (an older format, a hand edit) is recomputed, not trusted
        cache = {k: v for k, v in raw.items() if isinstance(v, list) and len(v) == 3 and isinstance(v[0], list) and isinstance(v[1], dict)} if isinstance(raw, dict) else {}
    keys = {j[0]: _job_key(j[1], j[2], j[3]) for j in jobs}
    todo = [j for j in jobs if keys[j[0]] not in cache]
    results: dict[str, tuple] = {}
    if len(todo) >= PARALLEL_MIN and (os.cpu_count() or 1) > 1:
        try:
            with ProcessPoolExecutor(max_workers=min(os.cpu_count() or 1, 8)) as pool:
                for r in pool.map(_lineage_job, todo, chunksize=4):
                    results[r[0]] = r
        except Exception as e:  # noqa: BLE001 - a broken pool falls back to computing what is left in this process
            if notes is not None:
                notes.append(f"column lineage fell back to one process for {len(todo) - len(results)} models: the process pool failed ({type(e).__name__}: {e})")
    for j in todo:
        if j[0] not in results:
            results[j[0]] = _lineage_job(j)
    fresh = {}
    out = []
    for gid, *_ in jobs:
        k = keys[gid]
        r = results.get(gid) or (gid, *cache[k])
        cols, derives, err = r[1], {c: [tuple(x) for x in d] for c, d in r[2].items()}, r[3]
        out.append((gid, cols, derives, err))
        fresh[k] = [cols, {c: [list(x) for x in d] for c, d in derives.items()}, err]
    if cache_path:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache_path.with_name(cache_path.name + ".tmp")
            tmp.write_text(json.dumps(fresh))
            tmp.replace(cache_path)  # atomic, so an interrupted sync never leaves a half-written cache
        except OSError:
            pass
    return out


def load(project_dir: Path, repo_root: Path, cache_path: Path | None = None) -> Emitted:
    """``cache_path``: where to keep column lineage between syncs, so a re-sync only recomputes changed models."""
    out = Emitted()
    target = project_dir / "target"
    manifest_path = target / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"{manifest_path} not found; run `dbt parse` or `dbt build` first")
    manifest = json.loads(manifest_path.read_text())
    catalog_path = target / "catalog.json"
    catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else {"nodes": {}, "sources": {}}
    prov = Provenance(adapter=ADAPTER, source=repo_relative(manifest_path, repo_root))
    dialect = DIALECTS.get(str((manifest.get("metadata") or {}).get("adapter_type") or "").lower(), "duckdb")
    relations: dict[str, str] = {}  # quoted relation name in compiled SQL -> schema key
    for src in manifest.get("sources", {}).values():
        if src.get("relation_name"):
            relations[src["relation_name"]] = schema_key(f"{src['source_name']}.{src['name']}")
    for n in manifest.get("nodes", {}).values():
        if n.get("relation_name") and n.get("resource_type") in ("model", "seed", "snapshot"):
            relations[n["relation_name"]] = schema_key(n["name"])

    unique_to_graph: dict[str, str] = {}  # dbt unique_id -> graph node id
    sql_by_graph: dict[str, str] = {}
    schema: dict[str, dict[str, str]] = {}
    owners: set[str] = set()

    # --- sources
    for uid, src in manifest.get("sources", {}).items():
        name = f"{src['source_name']}.{src['name']}"
        meta = _meta(src)
        source_meta = _meta(manifest["sources"][uid].get("source_meta", {}) and {"meta": src.get("source_meta")})
        owner = meta.get("owner") or source_meta.get("owner")
        tier = meta.get("tier") or source_meta.get("tier")
        cols = {c["name"]: c.get("type", "") for c in catalog.get("sources", {}).get(uid, {}).get("columns", {}).values()}
        node = Node(
            kind=NodeKind.SOURCE_TABLE,
            name=name,
            properties={
                "path": repo_relative(project_dir / src.get("original_file_path", ""), repo_root),
                "database": src.get("database"),
                "schema": src.get("schema"),
                "identifier": src.get("identifier"),
                "description": src.get("description", ""),
                "meta": meta,
                "columns": sorted(cols),
            },
            tier=int(tier) if tier is not None else None,
            owner=owner,
            provenance=[prov],
        )
        out.nodes.append(node)
        unique_to_graph[uid] = node.id
        if cols:
            schema[schema_key(name)] = cols
        if owner:
            owners.add(owner)
            out.edges.append(Edge(node.id, node_id(NodeKind.TEAM if owner.startswith("team/") else NodeKind.PERSON, owner), EdgeKind.OWNED_BY, Confidence.DECLARED, provenance=[prov]))

    # --- models, seeds, snapshots
    kind_map = {"model": NodeKind.MODEL, "seed": NodeKind.SEED, "snapshot": NodeKind.SNAPSHOT}
    for uid, n in manifest.get("nodes", {}).items():
        if n.get("resource_type") not in kind_map:
            continue
        kind = kind_map[n["resource_type"]]
        meta = _meta(n)
        owner = meta.get("owner")
        tier = meta.get("tier")
        cat_cols = {c["name"]: c.get("type", "") for c in catalog.get("nodes", {}).get(uid, {}).get("columns", {}).values()}
        declared_cols = {c["name"]: "" for c in (n.get("columns") or {}).values()}
        cols = cat_cols
        node = Node(
            kind=kind,
            name=n["name"],
            properties={
                "path": repo_relative(project_dir / n.get("original_file_path", ""), repo_root),
                "materialized": (n.get("config") or {}).get("materialized"),
                "tags": n.get("tags", []),
                "description": n.get("description", ""),
                "schema": n.get("schema"),
                "alias": n.get("alias"),
                "contract": bool(meta.get("contract") or ((n.get("config") or {}).get("contract") or {}).get("enforced")),
                "meta": meta,
                "tests": [t for t in manifest.get("child_map", {}).get(uid, []) if t.startswith("test.")],
                "schema_path": _patch_path(n, project_dir, repo_root),
                "test_paths": _singular_tests(manifest, uid, project_dir, repo_root),
                "columns": sorted(cols),
                "declared_columns": sorted(declared_cols),
            },
            tier=int(tier) if tier is not None else None,
            owner=owner,
            provenance=[prov],
        )
        out.nodes.append(node)
        unique_to_graph[uid] = node.id
        if cols:
            schema[schema_key(n["name"])] = cols
        if kind is NodeKind.MODEL and (sql := _model_sql(n, relations)):
            sql_by_graph[node.id] = sql
        if owner:
            owners.add(owner)
            out.edges.append(Edge(node.id, node_id(NodeKind.TEAM if owner.startswith("team/") else NodeKind.PERSON, owner), EdgeKind.OWNED_BY, Confidence.DECLARED, provenance=[prov]))

    # --- a seed that materialises the relation a source points at is the same table
    seeds_by_identifier = {n.name: n.id for n in out.nodes if n.kind is NodeKind.SEED}
    for n in list(out.nodes):
        if n.kind is NodeKind.SOURCE_TABLE and n.properties.get("identifier") in seeds_by_identifier:
            out.edges.append(Edge(n.id, seeds_by_identifier[n.properties["identifier"]], EdgeKind.CONSUMES, Confidence.INFERRED, {"via": "same relation"}, provenance=[prov]))

    # --- model-level lineage from parent_map
    for uid, parents in manifest.get("parent_map", {}).items():
        if uid not in unique_to_graph:
            continue
        for p in parents:
            if p in unique_to_graph:
                out.edges.append(Edge(unique_to_graph[uid], unique_to_graph[p], EdgeKind.CONSUMES, Confidence.PARSED, provenance=[prov]))

    # --- exposures
    for uid, ex in manifest.get("exposures", {}).items():
        meta = _meta(ex)
        owner = meta.get("owner") or (ex.get("owner") or {}).get("name")
        tier = meta.get("tier")
        kind = EXPOSURE_KIND.get(ex.get("type"), NodeKind.REPORT)
        node = Node(
            kind=kind,
            name=ex["name"],
            properties={
                "path": repo_relative(project_dir / ex.get("original_file_path", ""), repo_root),
                "exposure": True,
                "exposure_type": ex.get("type"),
                "maturity": ex.get("maturity"),
                "description": ex.get("description", ""),
                "sla": meta.get("sla"),
                "meta": meta,
                "url": ex.get("url"),
            },
            tier=int(tier) if tier is not None else None,
            owner=owner,
            provenance=[prov],
        )
        out.nodes.append(node)
        unique_to_graph[uid] = node.id
        for dep in (ex.get("depends_on") or {}).get("nodes", []):
            if dep in unique_to_graph:
                out.edges.append(Edge(node.id, unique_to_graph[dep], EdgeKind.CONSUMES, Confidence.DECLARED, provenance=[prov]))
        if owner:
            owners.add(owner)
            out.edges.append(Edge(node.id, node_id(NodeKind.TEAM if owner.startswith("team/") else NodeKind.PERSON, owner), EdgeKind.OWNED_BY, Confidence.DECLARED, provenance=[prov]))

    for owner in sorted(owners):
        out.nodes.append(_owner_node(owner, prov))

    # --- column nodes and column lineage
    # Models missing from the catalog (ephemeral, or never built) get their
    # columns from SQL. Passes repeat until nothing new resolves, so a model is
    # inferred after the models it reads from.
    from ..lineage import output_columns

    pending = {gid: sql for gid, sql in sql_by_graph.items() if schema_key(gid.split(":", 1)[1]) not in schema}
    for _ in range(len(pending) + 1):
        progressed = False
        for gid, sql in list(pending.items()):
            key = schema_key(gid.split(":", 1)[1])
            try:
                cols = output_columns(sql, schema, dialect)
            except Exception as e:  # noqa: BLE001
                out.notes.append(f"{gid}: could not infer columns ({e})")
                pending.pop(gid)
                continue
            if cols and "*" not in cols:
                schema[key] = {c: "" for c in cols}
                pending.pop(gid)
                progressed = True
        if not progressed:
            break
    for gid in pending:
        out.notes.append(f"{gid}: columns unknown (select * without a catalog); run `dbt docs generate`")
    for node in out.nodes:
        if node.kind in (NodeKind.MODEL, NodeKind.SEED, NodeKind.SNAPSHOT) and not node.properties.get("columns"):
            inferred = schema.get(schema_key(node.name))
            if inferred:
                node.properties["columns"] = sorted(inferred)
                node.properties["columns_source"] = "sql"

    col_prov = Provenance(adapter=ADAPTER, source="sqlglot column lineage over model SQL")
    ids = set(unique_to_graph.values())
    jobs = [(gid, sql, tables_schema(parse_model(sql, dialect), schema) if _parses(sql, dialect) else {}, dialect) for gid, sql in sql_by_graph.items()]
    for gid, columns, derives, error in _all_lineage(jobs, cache_path, out.notes):
        name = gid.split(":", 1)[1]
        if error:
            out.notes.append(f"{gid}: {error}")
        for col in columns:
            cnode = Node(NodeKind.COLUMN, f"{name}.{col}", properties={"asset": gid}, provenance=[col_prov])
            out.nodes.append(cnode)
            out.edges.append(Edge(cnode.id, gid, EdgeKind.COLUMN_OF, Confidence.PARSED, provenance=[col_prov]))
            for table, src_col in derives.get(col, []):
                src_asset = _asset_id_for_table(table, ids)
                if src_asset is None:
                    continue
                src_name = src_asset.split(":", 1)[1]
                src_col_node = Node(NodeKind.COLUMN, f"{src_name}.{src_col}", properties={"asset": src_asset}, provenance=[col_prov])
                out.nodes.append(src_col_node)
                out.edges.append(Edge(src_col_node.id, src_asset, EdgeKind.COLUMN_OF, Confidence.PARSED, provenance=[col_prov]))
                out.edges.append(Edge(cnode.id, src_col_node.id, EdgeKind.DERIVES_FROM, Confidence.PARSED, provenance=[col_prov]))
    return out


def _parses(sql: str, dialect: str) -> bool:
    try:
        parse_model(sql, dialect)
    except Exception:  # noqa: BLE001 - sqlglot raises several kinds; column_lineage reports the error itself
        return False
    return True


def _asset_id_for_table(table: str, ids: set[str]) -> str | None:
    for kind in (NodeKind.MODEL, NodeKind.SEED, NodeKind.SNAPSHOT, NodeKind.SOURCE_TABLE):
        cand = node_id(kind, table)
        if cand in ids:
            return cand
    return None
