"""``orion sync``: run adapters in dependency order and record a ``graph_synced`` event."""

from __future__ import annotations

from datetime import UTC, datetime

from .adapters import Emitted, SyncResult, digest_paths
from .adapters import airflow as airflow_adapter
from .adapters import dbt as dbt_adapter
from .adapters import git as git_adapter
from .adapters import incidents as incidents_adapter
from .adapters import runs as runs_adapter
from .config import OrionConfig
from .model import Event, EventKind
from .resolver import PathResolver
from .store import Store
from .tiers import infer_tiers

ORDER = ("dbt", "airflow", "git", "incidents", "runs", "code")


def _write(store: Store, emitted: Emitted, result: SyncResult) -> None:
    store.write_graph(emitted.nodes, emitted.edges)
    for ev in emitted.events:
        store.append_event(ev)
    result.nodes = len(emitted.nodes)
    result.edges = len(emitted.edges)
    result.events = len(emitted.events)
    result.notes.extend(emitted.notes)


def run_sync(cfg: OrionConfig, store: Store, only: list[str] | None = None, force: bool = False) -> list[SyncResult]:
    results: list[SyncResult] = []
    wanted = [a for a in ORDER if (not only or a in only)]
    root = cfg.root

    for name in wanted:
        res = SyncResult(adapter=name)
        if name == "dbt" and cfg.adapters.dbt:
            project = cfg.path(cfg.adapters.dbt.project)
            digest = f"v{dbt_adapter.GRAPH_VERSION}:" + digest_paths([project / "target" / "manifest.json", project / "target" / "catalog.json"])
            if not force and store.last_digest(name, str(project)) == digest:
                res.skipped = True
            else:
                _write(store, dbt_adapter.load(project, root, cache_path=store.path.parent / "lineage-cache.json" if str(store.path) != ":memory:" else None), res)
                store.mark_synced(name, str(project), digest)
        elif name == "airflow" and cfg.adapters.airflow:
            dags = cfg.path(cfg.adapters.airflow.dags)
            digest = digest_paths(airflow_adapter.dag_files(dags))
            if not force and store.last_digest(name, str(dags)) == digest:
                res.skipped = True
            else:
                assets = {n["id"]: n for n in store.find_nodes()}
                consumes = [(e[0], e[1]) for e in store.con.execute("SELECT src, dst FROM edges WHERE kind = 'consumes'").fetchall()]
                _write(store, airflow_adapter.load(dags, root, assets, consumes), res)
                store.mark_synced(name, str(dags), digest)
        elif name == "git" and cfg.adapters.git:
            resolver = PathResolver(store)
            _write(store, git_adapter.load(root, resolver, cfg.adapters.git.paths, cfg.adapters.git.max_commits), res)
        elif name == "incidents" and cfg.adapters.incidents:
            inc_dir = cfg.path(cfg.adapters.incidents.dir)
            _write(store, incidents_adapter.load(inc_dir, root, store), res)
        elif name == "runs" and cfg.adapters.runs:
            _write(store, runs_adapter.load(cfg.adapters.runs, root, store), res)
        elif name == "code" and cfg.adapters.code:
            from . import codegraph

            out = codegraph.sync(store, codegraph.specs_from_config(cfg), cache_dir=codegraph.cache_dir_for(store), force=force)
            res.skipped = out["skipped"]
            res.nodes, res.edges = out["nodes"], out["edges"]
            res.notes.extend(out["notes"])
        else:
            continue
        results.append(res)

    inferred = infer_tiers(store)
    from .constraints import load_constraints
    from .resolver import resolve_ref
    from .retrieval import add_constraint_chunks, rebuild_chunks

    chunks = rebuild_chunks(store)
    if cfg.constraints:
        constraints, _ = load_constraints(cfg.path(cfg.constraints))
        chunks += add_constraint_chunks(store, constraints, lambda r: resolve_ref(store, r).node_id or r)
    store.append_event(
        Event(
            kind=EventKind.GRAPH_SYNCED,
            occurred_at=datetime.now(UTC),
            assets=[],
            source="orion sync",
            payload={"adapters": [r.adapter for r in results if not r.skipped], "tiers_inferred": inferred, "chunks": chunks, **store.counts()},
        )
    )
    return results


def graph_as_of(store: Store) -> datetime | None:
    row = store.con.execute(
        "SELECT max(occurred_at) FROM events WHERE kind = 'graph_synced'"
    ).fetchone()
    return row[0] if row else None
