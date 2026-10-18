"""Airflow adapter: static parse of DAG files.

DAG files are read with ``ast`` and never executed, so the adapter needs no
Airflow install and cannot run a side effect. It recovers the DAG, its
tasks, ``>>`` dependencies, cross-DAG sensors, and the dbt selectors and
table flags inside bash commands, and links them to graph assets.
"""

from __future__ import annotations

import ast
import re
import shlex
from pathlib import Path
from typing import Any

from ..model import Confidence, Edge, EdgeKind, Node, NodeKind, Provenance, node_id
from . import Emitted, repo_relative

ADAPTER = "airflow"

DBT_CMD_RE = re.compile(r"\bdbt\s+(run|build|test|seed|snapshot)\b(.*)")


def _const(node: ast.AST) -> Any:
    if isinstance(node, ast.Dict):
        return {_const(k): _const(v) for k, v in zip(node.keys, node.values, strict=False) if k is not None}
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_const(e) for e in node.elts]
    try:
        return ast.literal_eval(node)
    except Exception:  # noqa: BLE001
        return ast.unparse(node)


def _kwargs(call: ast.Call) -> dict[str, Any]:
    return {kw.arg: _const(kw.value) for kw in call.keywords if kw.arg}


def parse_dag_file(path: Path) -> dict[str, Any] | None:
    tree = ast.parse(path.read_text(), filename=str(path))
    dag: dict[str, Any] | None = None
    tasks: dict[str, dict[str, Any]] = {}  # variable name -> task info
    deps: list[tuple[str, str]] = []
    default_args: dict[str, Any] = {}

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "default_args"
        ):
            value = _const(node.value)
            default_args = value if isinstance(value, dict) else {}
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "DAG":
            dag = _kwargs(node)
            if isinstance(dag.get("default_args"), dict):
                default_args = {**default_args, **dag["default_args"]}
    if dag is None:
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and len(node.targets) == 1:
            call = node.value
            op = call.func.id if isinstance(call.func, ast.Name) else getattr(call.func, "attr", None)
            kw = _kwargs(call)
            if "task_id" in kw and isinstance(node.targets[0], ast.Name):
                tasks[node.targets[0].id] = {"operator": op, **kw}
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.BinOp):
            chain = _rshift_chain(node.value)
            for a, b in zip(chain, chain[1:], strict=False):
                for x in a:
                    for y in b:
                        deps.append((x, y))
    return {"dag": dag, "tasks": tasks, "deps": deps, "default_args": default_args}


def _rshift_chain(expr: ast.AST) -> list[list[str]]:
    """``a >> b >> [c, d]`` -> [[a], [b], [c, d]]"""
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.RShift):
        return _rshift_chain(expr.left) + _rshift_chain(expr.right)
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.LShift):
        return _rshift_chain(expr.right) + _rshift_chain(expr.left)
    if isinstance(expr, ast.Name):
        return [[expr.id]]
    if isinstance(expr, (ast.List, ast.Tuple)):
        return [[e.id for e in expr.elts if isinstance(e, ast.Name)]]
    return [[]]


def _split(cmd: str) -> list[str]:
    """Shell words; a command with unbalanced quotes (often a Jinja template) falls back to whitespace splitting."""
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


def parse_bash_command(cmd: str) -> dict[str, list[str]]:
    """Pull dbt selectors and table-ish flags out of a shell command."""
    out: dict[str, list[str]] = {"dbt_select": [], "produces": [], "consumes": [], "partition_by": [], "dbt_command": []}
    if not isinstance(cmd, str):
        return out
    m = DBT_CMD_RE.search(cmd)
    if m:
        out["dbt_command"].append(m.group(1))
        toks = _split(m.group(2))
        for i, t in enumerate(toks):
            if t in ("--select", "-s", "--models", "-m"):
                j = i + 1
                while j < len(toks) and not toks[j].startswith("-"):
                    out["dbt_select"].append(toks[j])
                    j += 1
    toks = _split(cmd) if cmd else []
    for i, t in enumerate(toks[:-1]):
        val = toks[i + 1]
        if t in ("--target", "--to", "--output"):
            out["produces"].extend(v for v in val.split(",") if v and not v.startswith("s3://"))
        elif t in ("--from", "--tables", "--input", "--source"):
            out["consumes"].extend(v for v in val.split(",") if v)
        elif t == "--partition-by":
            out["partition_by"].append(val)
    return out


def _resolve_dbt_selector(sel: str, models: dict[str, dict[str, Any]], children: dict[str, set[str]], parents: dict[str, set[str]]) -> set[str]:
    plus_after = sel.endswith("+")
    plus_before = sel.startswith("+")
    core = sel.strip("+")
    base: set[str] = set()
    if core.startswith("tag:"):
        tag = core[4:]
        base = {n for n, m in models.items() if tag in m.get("tags", [])}
    elif core in models:
        base = {core}
    out = set(base)
    if plus_after:
        frontier = set(base)
        while frontier:
            nxt = set().union(*(children.get(f, set()) for f in frontier)) - out
            out |= nxt
            frontier = nxt
    if plus_before:
        frontier = set(base)
        while frontier:
            nxt = set().union(*(parents.get(f, set()) for f in frontier)) - out
            out |= nxt
            frontier = nxt
    return out


def load(dags_dir: Path, repo_root: Path, graph_assets: dict[str, dict[str, Any]], consumes_edges: list[tuple[str, str]]) -> Emitted:
    """``graph_assets``: {node_id: node} already in the store (dbt first), used to link tasks to assets."""
    out = Emitted()
    models = {n["name"]: n["properties"] for n in graph_assets.values() if n["kind"] in ("model", "seed", "snapshot")}
    children: dict[str, set[str]] = {}
    parents: dict[str, set[str]] = {}
    for src, dst in consumes_edges:  # src consumes dst
        s, d = src.split(":", 1)[1], dst.split(":", 1)[1]
        children.setdefault(d, set()).add(s)
        parents.setdefault(s, set()).add(d)
    # Short names resolve to source tables first: a seed or model that happens to
    # share a table's name is the same relation, and the source carries the tier.
    by_short_name: dict[str, str] = {}
    for nid, n in graph_assets.items():
        if n["kind"] != "column":
            by_short_name.setdefault(n["name"], nid)
    for nid, n in graph_assets.items():
        if n["kind"] == "source_table":
            by_short_name[n["name"]] = nid
            by_short_name[n["name"].split(".")[-1]] = nid

    task_ids: dict[tuple[str, str], str] = {}
    pending_sensors: list[tuple[str, str, str, str | None]] = []

    for path in sorted(dags_dir.glob("*.py")):
        parsed = parse_dag_file(path)
        if not parsed:
            continue
        rel = repo_relative(path, repo_root)
        prov = Provenance(adapter=ADAPTER, source=rel)
        dag = parsed["dag"]
        dag_id = dag.get("dag_id") or path.stem
        owner = parsed["default_args"].get("owner")
        dag_node = Node(
            NodeKind.DAG,
            dag_id,
            properties={
                "path": rel,
                "schedule": dag.get("schedule") or dag.get("schedule_interval"),
                "tags": dag.get("tags", []),
                "catchup": dag.get("catchup"),
                "retries": parsed["default_args"].get("retries"),
            },
            owner=owner,
            provenance=[prov],
        )
        out.nodes.append(dag_node)
        if owner:
            out.nodes.append(Node(NodeKind.TEAM if owner.startswith("team/") else NodeKind.PERSON, owner, provenance=[prov]))
            out.edges.append(Edge(dag_node.id, node_id(NodeKind.TEAM if owner.startswith("team/") else NodeKind.PERSON, owner), EdgeKind.OWNED_BY, Confidence.DECLARED, provenance=[prov]))

        var_to_task: dict[str, str] = {}
        for var, t in parsed["tasks"].items():
            tname = f"{dag_id}.{t['task_id']}"
            tid = node_id(NodeKind.TASK, tname)
            var_to_task[var] = tid
            task_ids[(dag_id, t["task_id"])] = tid
            cmd = parse_bash_command(t.get("bash_command", ""))
            props = {
                "path": rel,
                "dag": dag_id,
                "task_id": t["task_id"],
                "operator": t.get("operator"),
                "sla": t.get("sla"),
                "pool": t.get("pool"),
                "bash_command": t.get("bash_command"),
                "dbt_command": cmd["dbt_command"],
                "dbt_select": cmd["dbt_select"],
                "partition_by": cmd["partition_by"],
            }
            out.nodes.append(Node(NodeKind.TASK, tname, properties=props, owner=owner, provenance=[prov]))
            out.edges.append(Edge(dag_node.id, tid, EdgeKind.TRIGGERS, Confidence.PARSED, provenance=[prov]))
            if t.get("sla"):
                out.edges.append(Edge(tid, dag_node.id, EdgeKind.HAS_SLA, Confidence.DECLARED, {"sla": t["sla"]}, provenance=[prov]))
            # dbt selectors -> the models this task builds
            built: set[str] = set()
            for sel in cmd["dbt_select"]:
                built |= _resolve_dbt_selector(sel, models, children, parents)
            dbt_kind = EdgeKind.CONSUMES if cmd["dbt_command"] == ["test"] else EdgeKind.PRODUCES
            for mname in sorted(built):
                mid = by_short_name.get(mname)
                if mid:
                    out.edges.append(Edge(tid, mid, dbt_kind, Confidence.PARSED, {"via": f"dbt {' '.join(cmd['dbt_command'])} --select"}, provenance=[prov]))
            for name in cmd["produces"]:
                aid = by_short_name.get(name)
                if aid:
                    out.edges.append(Edge(tid, aid, EdgeKind.PRODUCES, Confidence.INFERRED, {"via": "command flag"}, provenance=[prov]))
            for name in cmd["consumes"]:
                aid = by_short_name.get(name)
                if aid:
                    out.edges.append(Edge(tid, aid, EdgeKind.CONSUMES, Confidence.INFERRED, {"via": "command flag"}, provenance=[prov]))
            if t.get("operator") == "ExternalTaskSensor":
                pending_sensors.append((tid, dag_id, t.get("external_dag_id"), t.get("external_task_id")))
        for a, b in parsed["deps"]:
            if a in var_to_task and b in var_to_task:
                out.edges.append(Edge(var_to_task[b], var_to_task[a], EdgeKind.DEPENDS_ON, Confidence.PARSED, provenance=[prov]))

    for tid, dag_id, ext_dag, ext_task in pending_sensors:
        prov = Provenance(adapter=ADAPTER, source=f"ExternalTaskSensor in {dag_id}")
        if ext_task and (ext_dag, ext_task) in task_ids:
            out.edges.append(Edge(tid, task_ids[(ext_dag, ext_task)], EdgeKind.DEPENDS_ON, Confidence.DECLARED, {"cross_dag": True}, provenance=[prov]))
        if ext_dag:
            out.edges.append(Edge(node_id(NodeKind.DAG, dag_id), node_id(NodeKind.DAG, ext_dag), EdgeKind.DEPENDS_ON, Confidence.DECLARED, {"cross_dag": True}, provenance=[prov]))
    return out
