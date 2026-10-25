"""Airflow adapter: static parse of DAG files.

DAG files are read with ``ast`` and never executed, so the adapter needs no
Airflow install and cannot run a side effect. It recovers the DAG, its
tasks, ``>>`` dependencies, cross-DAG sensors, and the dbt selectors and
table flags inside bash commands, and links them to graph assets.

TaskFlow DAGs (``@dag`` functions with ``@task`` and ``@task_group`` inside) are read the same way: a task is
named after its function, prefixed by its group, and a bash or dbt task's command is the string it returns.

Gusty DAGs are read too: a folder with ``METADATA.yml`` is a DAG, and every
``.yml``, ``.sql`` or ``.py`` file under it (with YAML front matter for the
last two) is a task named after the file, with ``dependencies`` and
``external_dependencies`` as edges.
"""

from __future__ import annotations

import ast
import re
import shlex
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

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


def _call_name(func: ast.AST) -> str | None:
    """``DAG`` or ``models.DAG`` -> ``DAG``; ``task.bash`` -> ``task.bash``; ``airflow.decorators.dag`` -> ``dag``."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name) and func.value.id == "task":
            return f"task.{func.attr}"
        return func.attr
    return None


def _decorator(fn: ast.FunctionDef | ast.AsyncFunctionDef, names: tuple[str, ...]) -> tuple[str, dict[str, Any]] | None:
    """The first decorator of ``fn`` named in ``names`` (bare or called), with its keyword arguments."""
    for d in fn.decorator_list:
        call = d if isinstance(d, ast.Call) else None
        name = _call_name(call.func if call else d)
        if name and (name in names or name.split(".")[0] in names):
            return name, (_kwargs(call) if call else {})
    return None


def _default_args(value: ast.AST) -> dict[str, Any]:
    """A literal dict, or the keyword arguments of a helper call such as ``dict(owner=...)`` or ``set_default_args(owner=...)``."""
    if isinstance(value, ast.Call):
        return _kwargs(value)
    v = _const(value)
    return v if isinstance(v, dict) else {}


def _returned_string(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """The literal command strings a TaskFlow bash or dbt task returns, joined: a task that picks one of two commands
    at run time may run either."""
    found: list[str] = []
    stack: list[ast.AST] = list(fn.body)
    while stack:  # the function's own returns, not those of a helper defined inside it
        n = stack.pop(0)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            found.append(n.value.value)
        stack.extend(ast.iter_child_nodes(n))
    return " && ".join(found) or None


def _parse_taskflow(tree: ast.Module) -> dict[str, Any] | None:
    """A ``@dag`` function: its tasks are ``@task`` functions and classic operators inside it, ``@task_group`` functions
    prefix their tasks' ids, and dependencies come from ``>>`` chains and from passing one task's result to another."""
    fn = next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _decorator(n, ("dag",))), None)
    if fn is None:
        return None
    _, dag = _decorator(fn, ("dag",)) or ("", {})
    dag = {**dag, "dag_id": dag.get("dag_id") if isinstance(dag.get("dag_id"), str) else fn.name}
    deco = next((d for d in fn.decorator_list if isinstance(d, ast.Call) and _call_name(d.func) == "dag"), None)
    default_args = next((_default_args(kw.value) for kw in (deco.keywords if deco else []) if kw.arg == "default_args"), {})
    tasks: dict[str, dict[str, Any]] = {}  # function or variable name -> task info
    groups: dict[str, list[str]] = {}  # task_group function name -> the task keys inside it

    def visit(body: list[ast.stmt], prefix: str, members: list[str]) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                g = _decorator(node, ("task_group",))
                t = _decorator(node, ("task",))
                if g:
                    gid = g[1].get("group_id") if isinstance(g[1].get("group_id"), str) else node.name
                    inner: list[str] = []
                    visit(node.body, f"{prefix}{gid}.", inner)
                    groups[node.name] = inner
                    members.extend(inner)
                elif t:
                    name, kw = t
                    tid = kw.get("task_id") if isinstance(kw.get("task_id"), str) else node.name
                    info: dict[str, Any] = {"operator": name, **{k: v for k, v in kw.items() if k in ("sla", "pool")}, "task_id": prefix + tid}
                    if any(w in name for w in ("bash", "dbt")) and (cmd := _returned_string(node)):
                        info["bash_command"] = cmd
                    tasks[node.name] = info
                    members.append(node.name)
                continue
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign) and isinstance(sub.value, ast.Call) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name):
                    kw = _kwargs(sub.value)
                    if isinstance(kw.get("task_id"), str):
                        tasks[sub.targets[0].id] = {"operator": _call_name(sub.value.func), **kw, "task_id": prefix + kw["task_id"]}
                        members.append(sub.targets[0].id)

    visit(fn.body, "", [])
    alias: dict[str, list[str]] = {}

    def keys(e: ast.AST) -> list[str]:
        """The task keys an expression stands for: a task call, a group call, a name bound to either, or a list of them."""
        if isinstance(e, ast.Call):
            name = _call_name(e.func)
            if name in groups:
                return groups[name]
            if name in tasks:
                return [name]
            return []
        if isinstance(e, ast.Name):
            return alias.get(e.id) or ([e.id] if e.id in tasks else [])
        if isinstance(e, (ast.List, ast.Tuple)):
            return [k for x in e.elts for k in keys(x)]
        return []

    deps: list[tuple[str, str]] = []

    def chain(e: ast.AST) -> list[list[str]]:
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.RShift):
            return chain(e.left) + chain(e.right)
        if isinstance(e, ast.BinOp) and isinstance(e.op, ast.LShift):
            return chain(e.right) + chain(e.left)
        return [keys(e)]

    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            ks = keys(node.value)
            if ks and node.targets[0].id not in tasks:
                alias[node.targets[0].id] = ks
        if isinstance(node, ast.Call) and _call_name(node.func) in tasks:  # data passing: b(a()) or b(x) where x = a()
            down = _call_name(node.func)
            for arg in [*node.args, *(kw.value for kw in node.keywords)]:
                deps.extend((up, down) for up in keys(arg) if up != down)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.RShift, ast.LShift)):
            links = chain(node)
            for a, b in zip(links, links[1:], strict=False):
                deps.extend((x, y) for x in a for y in b)
    return {"dag": dag, "tasks": tasks, "deps": list(dict.fromkeys(deps)), "default_args": default_args}


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
        if isinstance(node, ast.Call) and _call_name(node.func) == "DAG":
            dag = _kwargs(node)
            if isinstance(dag.get("default_args"), dict):
                default_args = {**default_args, **dag["default_args"]}
    if dag is None:
        return _parse_taskflow(tree)

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


class _TolerantLoader(yaml.SafeLoader):
    """Safe YAML that reads custom tags (gusty's ``!timedelta``) as plain values instead of failing."""


def _any_tag(loader: yaml.SafeLoader, suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_scalar(node)


_TolerantLoader.add_multi_constructor("!", _any_tag)

TASK_SUFFIXES = (".yml", ".yaml", ".sql", ".py")


def _yaml(text: str) -> dict[str, Any]:
    try:
        data = yaml.load(text, Loader=_TolerantLoader)  # noqa: S506 - a SafeLoader subclass
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _front_matter(text: str) -> dict[str, Any]:
    """Gusty's YAML block between ``---`` lines at the top of a .sql or .py task."""
    m = re.match(r"\A\s*---\s*\n(.*?)\n---\s*(\n|\Z)", text, re.S)
    return _yaml(m.group(1)) if m else {}


def gusty_dag_dirs(dags_dir: Path) -> list[Path]:
    """Folders holding a ``METADATA.yml`` that no enclosing folder also holds (those are task groups)."""
    found = sorted({p.parent for p in dags_dir.rglob("METADATA.y*ml")})
    return [d for d in found if not any(o != d and o in d.parents for o in found)]


def parse_gusty_dag(dag_dir: Path) -> dict[str, Any]:
    meta_file = next(dag_dir / n for n in ("METADATA.yml", "METADATA.yaml") if (dag_dir / n).exists())
    meta = _yaml(meta_file.read_text())
    found: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(dag_dir.rglob("*")):
        if not path.is_file() or path.suffix not in TASK_SUFFIXES or path.stem == "METADATA":
            continue
        text = path.read_text(errors="replace")
        spec = _yaml(text) if path.suffix in (".yml", ".yaml") else _front_matter(text)
        if spec.get("operator"):
            found.append((path, spec))
    stems = Counter(p.stem for p, _ in found)
    tasks: dict[str, dict[str, Any]] = {}
    for path, spec in found:
        # a task is named by its file stem; two task-group files with one stem are told apart by their group path, as Airflow prefixes group ids
        key = path.stem if stems[path.stem] == 1 else ".".join(path.relative_to(dag_dir).with_suffix("").parts)
        tasks[key] = {"file": path, **spec}
    return {"meta": meta, "meta_file": meta_file, "tasks": tasks}


def dag_files(dags_dir: Path) -> list[Path]:
    """Every file a sync reads: Python DAG files anywhere under the folder, and gusty DAG folders."""
    out = set(dags_dir.rglob("*.py"))
    for d in gusty_dag_dirs(dags_dir):
        out |= {p for p in d.rglob("*") if p.is_file() and p.suffix in TASK_SUFFIXES}
    return sorted(out)


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
                while j < len(toks) and not toks[j].startswith("-") and toks[j] not in ("&&", "||", ";", "|"):
                    out["dbt_select"].extend(toks[j].split())  # -s 'tag:a+ tag:b+' is two selectors
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
    # a selector can start at a source (`tag:daily+` where the tag is on the source); dbt builds only what is downstream
    selectable = {**{n["name"]: n["properties"] for n in graph_assets.values() if n["kind"] == "source_table"}, **models}
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

    # a task that loads a warehouse table names it as dataset.table; dbt sources record schema and identifier
    for nid, n in graph_assets.items():
        props = n.get("properties") or {}
        if n["kind"] == "source_table" and props.get("schema") and props.get("identifier"):
            by_short_name.setdefault(f"{props['schema']}.{props['identifier']}".lower(), nid)

    task_ids: dict[tuple[str, str], str] = {}
    dag_sources: dict[str, str] = {}
    pending_sensors: list[tuple[str, str, str, str | None]] = []

    def add_dag(dag_id: str, rel: str, prov: Provenance, props: dict[str, Any], owner: str | None) -> Node:
        if dag_id in dag_sources and dag_sources[dag_id] != rel:  # the store merges same-id nodes; say so rather than lose one quietly
            out.notes.append(f"DAG {dag_id} is defined in both {dag_sources[dag_id]} and {rel}; their tasks are merged under one DAG")
        dag_sources.setdefault(dag_id, rel)
        dag_node = Node(NodeKind.DAG, dag_id, properties={"path": rel, **props}, owner=owner, provenance=[prov])
        out.nodes.append(dag_node)
        if owner:
            out.nodes.append(Node(NodeKind.TEAM if owner.startswith("team/") else NodeKind.PERSON, owner, provenance=[prov]))
            out.edges.append(Edge(dag_node.id, node_id(NodeKind.TEAM if owner.startswith("team/") else NodeKind.PERSON, owner), EdgeKind.OWNED_BY, Confidence.DECLARED, provenance=[prov]))
        return dag_node

    def add_task(dag_node: Node, t: dict[str, Any], rel: str, prov: Provenance, owner: str | None) -> str:
        dag_id = dag_node.name
        tname = f"{dag_id}.{t['task_id']}"
        tid = node_id(NodeKind.TASK, tname)
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
            built |= _resolve_dbt_selector(sel, selectable, children, parents)
        built &= models.keys()
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
        dest = t.get("destination_project_dataset_table")
        if isinstance(dest, str):
            aid = by_short_name.get(".".join(dest.split(".")[-2:]).lower())
            if aid:
                out.edges.append(Edge(tid, aid, EdgeKind.PRODUCES, Confidence.DECLARED, {"via": "destination_project_dataset_table"}, provenance=[prov]))
        if t.get("operator") == "ExternalTaskSensor":
            pending_sensors.append((tid, dag_id, t.get("external_dag_id"), t.get("external_task_id")))
        return tid

    gusty = gusty_dag_dirs(dags_dir)
    for path in sorted(dags_dir.rglob("*.py")):
        if any(g in path.parents for g in gusty):
            continue  # a gusty task with front matter, read below
        try:
            parsed = parse_dag_file(path)
        except (SyntaxError, UnicodeDecodeError, ValueError) as e:
            out.notes.append(f"skipped {repo_relative(path, repo_root)}: not parseable as Python ({type(e).__name__})")
            continue
        if not parsed:
            continue
        rel = repo_relative(path, repo_root)
        prov = Provenance(adapter=ADAPTER, source=rel)
        dag = parsed["dag"]
        owner = parsed["default_args"].get("owner")
        dag_node = add_dag(
            dag.get("dag_id") or path.stem,
            rel,
            prov,
            {"schedule": dag.get("schedule") or dag.get("schedule_interval"), "tags": dag.get("tags", []), "catchup": dag.get("catchup"), "retries": parsed["default_args"].get("retries")},
            owner,
        )
        var_to_task = {var: add_task(dag_node, t, rel, prov, owner) for var, t in parsed["tasks"].items()}
        for a, b in parsed["deps"]:
            if a in var_to_task and b in var_to_task:
                out.edges.append(Edge(var_to_task[b], var_to_task[a], EdgeKind.DEPENDS_ON, Confidence.PARSED, provenance=[prov]))

    for d in gusty:
        g = parse_gusty_dag(d)
        meta, default_args = g["meta"], (g["meta"].get("default_args") or {})
        rel = repo_relative(g["meta_file"], repo_root)
        owner = default_args.get("owner")
        owner = owner if isinstance(owner, str) else None
        dag_node = add_dag(
            d.name,
            rel,
            Provenance(adapter=ADAPTER, source=rel),
            {"schedule": meta.get("schedule") or meta.get("schedule_interval"), "tags": meta.get("tags", []), "catchup": meta.get("catchup"), "retries": default_args.get("retries"), "gusty": True},
            owner,
        )
        local: dict[str, str] = {}
        for stem, spec in g["tasks"].items():
            trel = repo_relative(spec["file"], repo_root)
            local[stem] = add_task(dag_node, {**spec, "task_id": spec.get("task_id") or stem}, trel, Provenance(adapter=ADAPTER, source=trel), owner)
        for stem, spec in g["tasks"].items():
            prov = Provenance(adapter=ADAPTER, source=repo_relative(spec["file"], repo_root))
            deps = spec.get("dependencies") or []
            for up in [deps] if isinstance(deps, str) else deps if isinstance(deps, list) else []:
                if isinstance(up, str) and up in local:
                    out.edges.append(Edge(local[stem], local[up], EdgeKind.DEPENDS_ON, Confidence.DECLARED, provenance=[prov]))
            exts = spec.get("external_dependencies")
            for ext in exts if isinstance(exts, list) else []:
                for ext_dag, ext_task in (ext.items() if isinstance(ext, dict) else []):
                    if isinstance(ext_dag, str):  # anything else is not a gusty external dependency
                        pending_sensors.append((local[stem], d.name, ext_dag, ext_task if isinstance(ext_task, str) and ext_task != "all" else None))

    for tid, dag_id, ext_dag, ext_task in pending_sensors:
        prov = Provenance(adapter=ADAPTER, source=f"ExternalTaskSensor in {dag_id}")
        if ext_task and (ext_dag, ext_task) in task_ids:
            out.edges.append(Edge(tid, task_ids[(ext_dag, ext_task)], EdgeKind.DEPENDS_ON, Confidence.DECLARED, {"cross_dag": True}, provenance=[prov]))
        if ext_dag:
            out.edges.append(Edge(node_id(NodeKind.DAG, dag_id), node_id(NodeKind.DAG, ext_dag), EdgeKind.DEPENDS_ON, Confidence.DECLARED, {"cross_dag": True}, provenance=[prov]))
    return out
