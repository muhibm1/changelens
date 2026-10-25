"""Run history: what production did, as ledger events.

Three sources, all read from files so the adapter stays deterministic and
testable; each is the shape its system exports:

* Airflow: one JSON object per line, as ``airflow tasks states-for-dag-run`` or
  the REST API's ``taskInstances`` return (``dag_id``, ``task_id``, ``run_id``,
  ``state``, ``start_date``, ``end_date``). A task that has a declared ``sla``
  and ran longer than it also gets an ``sla_missed`` event.
* dbt: ``run_results.json`` files. Model, seed and snapshot results become run
  events on the asset; failed tests become ``run_failed`` on the models they test.
* Iceberg: table metadata JSON. Every snapshot is a ``snapshot_committed``;
  a snapshot whose schema id differs from its parent's is a ``schema_changed``
  with the column diff; a change of default partition spec is a
  ``schema_changed`` with ``cause: partition_change``.

Like every adapter, this one reads and emits; the post-flight watcher and the
proposal job reason about what it wrote.
"""

from __future__ import annotations

import glob
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..model import Confidence, Event, EventKind, Node, NodeKind, Provenance
from ..resolver import resolve_ref
from ..store import Store
from . import Emitted, repo_relative

ADAPTER = "runs"
SUCCESS = {"success", "pass", "skipped"}
FAILURE = {"failed", "upstream_failed", "error", "fail", "runtime error"}
TD_RE = re.compile(r"timedelta\((.*)\)")


def _dt(v: Any) -> datetime | None:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=UTC)
    d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def parse_timedelta(text: str | None) -> timedelta | None:
    """``timedelta(minutes=45)`` as the Airflow adapter stores it -> timedelta."""
    if not text:
        return None
    m = TD_RE.search(text)
    if not m:
        return None
    kwargs: dict[str, float] = {}
    for part in m.group(1).split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                kwargs[k.strip()] = float(v)
            except ValueError:
                return None
    try:
        return timedelta(**kwargs)
    except TypeError:
        return None


# ------------------------------------------------------------------ airflow
def load_airflow(path: Path, repo_root: Path, store: Store) -> Emitted:
    out = Emitted()
    rel = repo_relative(path, repo_root)
    if not path.exists():
        out.notes.append(f"airflow run export not found: {rel}")
        return out
    tasks = {n["id"]: n for n in store.find_nodes(kind="task")}
    produces: dict[str, list[str]] = {}
    for src, dst in store.con.execute("SELECT src, dst FROM edges WHERE kind = 'produces'").fetchall():
        produces.setdefault(src, []).append(dst)
    consumes: dict[str, list[str]] = {}
    for src, dst in store.con.execute("SELECT src, dst FROM edges WHERE kind = 'consumes'").fetchall():
        consumes.setdefault(src, []).append(dst)
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            out.notes.append(f"{rel}:{lineno}: not JSON")
            continue
        dag_id, task_id = rec.get("dag_id"), rec.get("task_id")
        state = str(rec.get("state") or "").lower()
        started, ended = _dt(rec.get("start_date")), _dt(rec.get("end_date"))
        when = ended or started
        if not dag_id or when is None:
            out.notes.append(f"{rel}:{lineno}: needs dag_id and a date")
            continue
        node_id = f"task:{dag_id}.{task_id}" if task_id else f"dag:{dag_id}"
        if node_id not in tasks and store.node(node_id) is None:
            out.notes.append(f"{rel}:{lineno}: unknown {node_id}")
            continue
        assets = [node_id, *produces.get(node_id, [])]
        if "test" in ((tasks.get(node_id) or {}).get("properties", {}).get("dbt_command") or []):
            # a failed `dbt test` is a fact about the models it tested
            assets += [a for a in consumes.get(node_id, []) if not a.startswith("task:")]
        if task_id:
            assets.append(f"dag:{dag_id}")
        assets = list(dict.fromkeys(a for a in assets if store.node(a)))
        kind = EventKind.RUN_FAILED if state in FAILURE else EventKind.RUN_SUCCEEDED if state in SUCCESS else None
        duration = (ended - started).total_seconds() if started and ended else None
        payload = {"dag": dag_id, "task": task_id, "run_id": rec.get("run_id"), "state": state, "duration_s": duration, "try_number": rec.get("try_number"), "path": rel}
        if kind:
            out.events.append(Event(kind, when, assets, source=rel, source_ref=rec.get("run_id"), payload=payload))
        sla = parse_timedelta((tasks.get(node_id) or {}).get("properties", {}).get("sla"))
        if rec.get("sla_miss") or (sla and duration is not None and duration > sla.total_seconds()):
            out.events.append(
                Event(EventKind.SLA_MISSED, when, assets, source=rel, source_ref=rec.get("run_id"), payload={**payload, "sla_s": sla.total_seconds() if sla else None, "cause": "sla"})
            )
    return out


# ---------------------------------------------------------------------- dbt
def load_dbt(patterns: list[str], repo_root: Path, store: Store) -> Emitted:
    out = Emitted()
    files: list[Path] = []
    for pat in patterns:
        files += [Path(p) for p in sorted(glob.glob(str(repo_root / pat)))]
    for path in files:
        rel = repo_relative(path, repo_root)
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            out.notes.append(f"{rel}: {e}")
            continue
        meta = data.get("metadata", {})
        invocation = meta.get("invocation_id")
        generated = _dt(meta.get("generated_at")) or datetime.now(UTC)
        manifest = path.with_name("manifest.json")
        depends: dict[str, list[str]] = {}
        if manifest.exists():
            try:
                for uid, n in json.loads(manifest.read_text()).get("nodes", {}).items():
                    depends[uid] = n.get("depends_on", {}).get("nodes", [])
            except (OSError, json.JSONDecodeError):
                pass
        model_names = sorted((n["name"] for n in store.find_nodes(kind="model")), key=len, reverse=True)
        for r in data.get("results", []):
            uid = r.get("unique_id", "")
            parts = uid.split(".")
            if len(parts) < 3:
                continue
            rtype, name = parts[0], parts[2] if rtype_is_test(parts) else parts[-1]
            status = str(r.get("status") or "").lower()
            when = generated
            for t in r.get("timing", []):
                if t.get("name") == "execute" and t.get("completed_at"):
                    when = _dt(t["completed_at"]) or generated
            if rtype in ("model", "seed", "snapshot"):
                nid = f"{rtype}:{name}"
                if store.node(nid) is None:
                    continue
                kind = EventKind.RUN_FAILED if status in FAILURE else EventKind.RUN_SUCCEEDED if status in SUCCESS else None
                if kind:
                    out.events.append(Event(kind, when, [nid], source=rel, source_ref=invocation, payload={"status": status, "message": r.get("message"), "execution_time": r.get("execution_time"), "unique_id": uid, "path": rel}))
            elif rtype == "test" and status in FAILURE:
                tested = [f"{d.split('.')[0]}:{d.split('.')[-1]}" for d in depends.get(uid, []) if d.split(".")[0] in ("model", "seed", "snapshot")]
                tested = [t for t in tested if store.node(t)] or _guess_tested(name, model_names)
                if tested:
                    out.events.append(Event(EventKind.RUN_FAILED, when, tested, source=rel, source_ref=invocation, payload={"status": status, "test": uid, "failures": r.get("failures"), "message": r.get("message"), "path": rel}))
    return out


def rtype_is_test(parts: list[str]) -> bool:
    return parts[0] == "test"


def _guess_tested(test_name: str, model_names: list[str]) -> list[str]:
    """Without a manifest, a generic test's name embeds the model it tests (``not_null_fct_refunds_order_id``)."""
    for m in model_names:
        if m in test_name:
            return [f"model:{m}"]
    return []


# ------------------------------------------------------------------ iceberg
def _schema_fields(schema: dict[str, Any]) -> dict[str, str]:
    return {f["name"]: str(f.get("type")) for f in schema.get("fields", [])}


def _spec_keys(spec: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    by_id = {f["id"]: f["name"] for f in schema.get("fields", [])}
    return [by_id.get(f.get("source-id"), f.get("name")) for f in spec.get("fields", [])]


def load_iceberg(directory: Path, repo_root: Path, store: Store) -> Emitted:
    out = Emitted()
    if not directory.exists():
        out.notes.append(f"iceberg metadata dir not found: {repo_relative(directory, repo_root)}")
        return out
    for path in sorted(directory.glob("*.json")):
        rel = repo_relative(path, repo_root)
        try:
            meta = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            out.notes.append(f"{rel}: {e}")
            continue
        table = meta.get("fathom-asset") or path.name.split(".")[0]
        r = resolve_ref(store, table)
        prov = Provenance(ADAPTER, rel, ref=meta.get("table-uuid"))
        schemas = {s.get("schema-id", 0): s for s in meta.get("schemas", [])}
        specs = {s.get("spec-id", 0): s for s in meta.get("partition-specs", [])}
        cur_schema = schemas.get(meta.get("current-schema-id", 0), {})
        cur_spec = specs.get(meta.get("default-spec-id", 0), {})
        if r.ok:
            nid = r.node_id
            node = store.node(nid)
            props = {**node["properties"], "iceberg": {"location": meta.get("location"), "table_uuid": meta.get("table-uuid")}, "columns": list(_schema_fields(cur_schema)), "partition_by": _spec_keys(cur_spec, cur_schema)}
            out.nodes.append(Node(NodeKind(node["kind"]), node["name"], props, tier=node["tier"], owner=node["owner"], provenance=[prov]))
        else:
            nid = f"iceberg_table:{table}"
            out.nodes.append(Node(NodeKind.ICEBERG_TABLE, table, {"location": meta.get("location"), "columns": list(_schema_fields(cur_schema)), "partition_by": _spec_keys(cur_spec, cur_schema), "path": rel}, provenance=[prov]))
        snaps = sorted(meta.get("snapshots", []), key=lambda s: s.get("timestamp-ms", 0))
        by_id = {s.get("snapshot-id"): s for s in snaps}
        for s in snaps:
            when = _dt(s.get("timestamp-ms")) or datetime.now(UTC)
            summary = s.get("summary", {})
            sid = str(s.get("snapshot-id"))
            out.events.append(
                Event(EventKind.SNAPSHOT_COMMITTED, when, [nid], source=rel, source_ref=sid, payload={"operation": summary.get("operation"), "added_records": summary.get("added-records"), "deleted_records": summary.get("deleted-records"), "schema_id": s.get("schema-id"), "path": rel})
            )
            parent = by_id.get(s.get("parent-snapshot-id"))
            if parent is None:
                continue
            old_s, new_s = schemas.get(parent.get("schema-id", 0), {}), schemas.get(s.get("schema-id", 0), {})
            if s.get("schema-id") != parent.get("schema-id"):
                old_f, new_f = _schema_fields(old_s), _schema_fields(new_s)
                diff = {
                    "added": sorted(set(new_f) - set(old_f)),
                    "dropped": sorted(set(old_f) - set(new_f)),
                    "retyped": sorted(c for c in set(old_f) & set(new_f) if old_f[c] != new_f[c]),
                }
                cause = "drop_column" if diff["dropped"] else "change_type" if diff["retyped"] else "add_column"
                out.events.append(Event(EventKind.SCHEMA_CHANGED, when, [nid], source=rel, source_ref=sid, payload={"cause": cause, **diff, "from_schema": parent.get("schema-id"), "to_schema": s.get("schema-id"), "path": rel}))
            old_spec, new_spec = parent.get("summary", {}).get("partition-spec-id"), summary.get("partition-spec-id")
            if new_spec is not None and old_spec is not None and new_spec != old_spec:
                old_keys = _spec_keys(specs.get(old_spec, {}), old_s)
                new_keys = _spec_keys(specs.get(new_spec, {}), new_s)
                out.events.append(Event(EventKind.SCHEMA_CHANGED, when, [nid], source=rel, source_ref=sid, payload={"cause": "partition_change", "from": old_keys, "to": new_keys, "path": rel}))
    return out


def load(cfg_runs, repo_root: Path, store: Store) -> Emitted:
    out = Emitted()
    parts: list[Emitted] = []
    if cfg_runs.airflow:
        parts.append(load_airflow(repo_root / cfg_runs.airflow, repo_root, store))
    if cfg_runs.dbt:
        parts.append(load_dbt(cfg_runs.dbt, repo_root, store))
    if cfg_runs.iceberg:
        parts.append(load_iceberg(repo_root / cfg_runs.iceberg, repo_root, store))
    for p in parts:
        out.nodes += p.nodes
        out.edges += p.edges
        out.events += p.events
        out.notes += p.notes
    return out


__all__ = ["load", "load_airflow", "load_dbt", "load_iceberg", "parse_timedelta", "Confidence"]
