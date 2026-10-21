"""Turn a proposed change into typed operations on named assets.

Three inputs end as the same thing: a git diff against a base ref, a list
of paths, or a sentence of intent. Resolution is deterministic first (paths,
SQL and DAG diffs) and falls back to a keyword parser for intent. Anything
Orion cannot place lands in ``unresolved`` rather than disappearing.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import yaml

from .adapters.airflow import parse_bash_command, parse_dag_file
from .lineage import dejinja, output_columns, schema_key
from .resolver import PathResolver, resolve_ref
from .store import Store

DESTRUCTIVE = {"drop_column", "rename_column", "change_type", "change_partition", "change_expression"}
COLUMN_OPS = {"drop_column", "rename_column", "change_type", "add_column", "change_expression"}


@dataclass
class ChangeOp:
    kind: str
    asset: str  # node id
    column: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    from_intent: bool = False
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        d = {"kind": self.kind, "asset": self.asset, "evidence": self.evidence}
        if self.column:
            d["column"] = self.column
        if self.details:
            d["details"] = self.details
        if self.from_intent:
            d["from_intent"] = True
            d["confidence"] = self.confidence
        return d


@dataclass
class ChangeSet:
    ops: list[ChangeOp] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    touched: list[str] = field(default_factory=list)  # node ids, in order of first appearance
    source: str = ""
    ignored: list[str] = field(default_factory=list)  # paths outside the platform's roots, and prose

    def add(self, op: ChangeOp) -> None:
        self.ops.append(op)
        if op.asset not in self.touched:
            self.touched.append(op.asset)


# ------------------------------------------------------------------ file diffs
def _graph_schema(store: Store) -> dict[str, dict[str, str]]:
    schema: dict[str, dict[str, str]] = {}
    for n in store.find_nodes():
        if n["kind"] in ("model", "seed", "snapshot", "source_table") and n["properties"].get("columns"):
            schema[schema_key(n["name"])] = dict.fromkeys(n["properties"]["columns"], "")
    return schema


def _select_items(sql: str) -> dict[str, str]:
    """output column -> normalised expression text, for change detection."""
    import sqlglot

    try:
        expr = sqlglot.parse_one(sql, read="duckdb")
    except Exception:  # noqa: BLE001
        return {}
    items: dict[str, str] = {}
    for sel in expr.selects:
        name = sel.alias_or_name
        inner = sel.this if hasattr(sel, "this") and sel.key == "alias" else sel
        items[name] = inner.sql(dialect="duckdb").lower()
    return items


def _partition_by(raw_sql: str) -> str | None:
    m = re.search(r"partition_by\s*=\s*['\"]?([\w\.]+)", raw_sql)
    return m.group(1) if m else None


def ops_from_sql_change(asset: str, path: str, old_sql: str | None, new_sql: str | None, schema: dict) -> list[ChangeOp]:
    ev = [f"diff:{path}"]
    if new_sql is None:
        return [ChangeOp("delete_asset", asset, evidence=ev)]
    if old_sql is None:
        return [ChangeOp("add_asset", asset, evidence=ev)]
    ops: list[ChangeOp] = []
    old_plain, new_plain = dejinja(old_sql, asset), dejinja(new_sql, asset)
    try:
        old_cols = output_columns(old_plain, schema)
        new_cols = output_columns(new_plain, schema)
    except Exception as e:  # noqa: BLE001
        return [ChangeOp("modify", asset, details={"note": f"SQL did not parse: {e}"}, evidence=ev)]
    old_items, new_items = _select_items(old_plain), _select_items(new_plain)
    removed = [c for c in old_cols if c not in new_cols]
    added = [c for c in new_cols if c not in old_cols]
    # one out, one in, same expression -> a rename
    if len(removed) == 1 and len(added) == 1 and old_items.get(removed[0]) == new_items.get(added[0]):
        ops.append(ChangeOp("rename_column", asset, removed[0], {"to": added[0]}, ev))
        removed, added = [], []
    for c in removed:
        ops.append(ChangeOp("drop_column", asset, c, evidence=ev))
    for c in added:
        ops.append(ChangeOp("add_column", asset, c, evidence=ev))
    for c in new_cols:
        if c in old_cols and old_items.get(c) != new_items.get(c) and c in old_items and c in new_items:
            kind = "change_type" if re.match(r"^cast\(", new_items[c]) and not re.match(r"^cast\(", old_items[c]) else "change_expression"
            ops.append(ChangeOp(kind, asset, c, {"from": old_items[c], "to": new_items[c]}, ev))
    old_part, new_part = _partition_by(old_sql), _partition_by(new_sql)
    if old_part != new_part:
        ops.append(ChangeOp("change_partition", asset, details={"from": old_part, "to": new_part}, evidence=ev))
    old_where = re.search(r"\bwhere\b(.*?)(\bgroup by\b|\border by\b|$)", old_plain, re.S | re.I)
    new_where = re.search(r"\bwhere\b(.*?)(\bgroup by\b|\border by\b|$)", new_plain, re.S | re.I)
    if (old_where.group(1).split() if old_where else None) != (new_where.group(1).split() if new_where else None):
        ops.append(ChangeOp("change_filter", asset, details={"from": old_where.group(1).strip() if old_where else None, "to": new_where.group(1).strip() if new_where else None}, evidence=ev))
    if not ops:
        ops.append(ChangeOp("modify", asset, details={"note": "no schema, filter or partition change detected"}, evidence=ev))
    return ops


def ops_from_dag_change(store: Store, path: str, old_src: str | None, new_src: str | None, tmp: Path) -> tuple[list[ChangeOp], list[str]]:
    ev = [f"diff:{path}"]
    ops: list[ChangeOp] = []
    notes: list[str] = []

    def parse(src: str | None):
        if src is None:
            return None
        f = tmp / "dag.py"
        f.write_text(src)
        return parse_dag_file(f)

    old, new = parse(old_src), parse(new_src)
    if new is None or old is None:
        dag_id = (new or old or {}).get("dag", {}).get("dag_id")
        aid = f"dag:{dag_id}" if dag_id else None
        if aid:
            ops.append(ChangeOp("add_asset" if old is None else "delete_asset", aid, evidence=ev))
        return ops, notes
    dag_id = new["dag"].get("dag_id") or old["dag"].get("dag_id")
    dag_asset = f"dag:{dag_id}"
    o_sched = old["dag"].get("schedule") or old["dag"].get("schedule_interval")
    n_sched = new["dag"].get("schedule") or new["dag"].get("schedule_interval")
    if o_sched != n_sched:
        ops.append(ChangeOp("change_schedule", dag_asset, details={"from": o_sched, "to": n_sched}, evidence=ev))
    o_tasks, n_tasks = old["tasks"], new["tasks"]
    for var, t in n_tasks.items():
        task_asset = f"task:{dag_id}.{t['task_id']}"
        ot = o_tasks.get(var)
        if ot is None:
            ops.append(ChangeOp("add_task", task_asset, details={"operator": t.get("operator")}, evidence=ev))
            continue
        for key, kind in (("sla", "change_sla"), ("pool", "change_pool"), ("retries", "change_retries")):
            if ot.get(key) != t.get(key):
                ops.append(ChangeOp(kind, task_asset, details={"from": ot.get(key), "to": t.get(key)}, evidence=ev))
        oc, nc = parse_bash_command(ot.get("bash_command", "")), parse_bash_command(t.get("bash_command", ""))
        if oc["partition_by"] != nc["partition_by"]:
            targets = nc["produces"] or oc["produces"]
            for tgt in targets or [None]:
                r = resolve_ref(store, tgt) if tgt else None
                asset = r.node_id if r and r.ok else task_asset
                ops.append(ChangeOp("change_partition", asset, details={"from": oc["partition_by"], "to": nc["partition_by"], "task": task_asset}, evidence=ev))
        if oc["dbt_select"] != nc["dbt_select"]:
            ops.append(ChangeOp("change_selection", task_asset, details={"from": oc["dbt_select"], "to": nc["dbt_select"]}, evidence=ev))
        elif ot.get("bash_command") != t.get("bash_command") and not ops:
            ops.append(ChangeOp("modify", task_asset, details={"note": "command changed"}, evidence=ev))
    for var in o_tasks:
        if var not in n_tasks:
            ops.append(ChangeOp("delete_asset", f"task:{dag_id}.{o_tasks[var]['task_id']}", evidence=ev))
    o_deps, n_deps = set(old["deps"]), set(new["deps"])
    for a, b in sorted(n_deps - o_deps):
        if a in n_tasks and b in n_tasks:
            ops.append(ChangeOp("add_dependency", f"task:{dag_id}.{n_tasks[b]['task_id']}", details={"on": f"task:{dag_id}.{n_tasks[a]['task_id']}"}, evidence=ev))
    for a, b in sorted(o_deps - n_deps):
        if a in o_tasks and b in o_tasks:
            ops.append(ChangeOp("remove_dependency", f"task:{dag_id}.{o_tasks[b]['task_id']}", details={"on": f"task:{dag_id}.{o_tasks[a]['task_id']}"}, evidence=ev))
    if not ops:
        ops.append(ChangeOp("modify", dag_asset, details={"note": "no schedule, task or dependency change detected"}, evidence=ev))
    return ops, notes


def _schema_entries(doc: Any) -> dict[str, dict[str, Any]]:
    """The named entries of a dbt schema .yml: models, seeds, snapshots, exposures, and source tables as ``source.table``."""
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(doc, dict):
        return out
    for section in ("models", "seeds", "snapshots", "exposures"):
        for m in doc.get(section) or []:
            if isinstance(m, dict) and m.get("name"):
                out[str(m["name"])] = m
    for src in doc.get("sources") or []:
        if not isinstance(src, dict):
            continue
        for t in src.get("tables") or []:
            if isinstance(t, dict) and t.get("name"):
                out[f"{src.get('name')}.{t['name']}"] = t
    return out


def ops_from_yaml_change(resolver: PathResolver, store: Store, path: str, old_text: str | None, new_text: str | None, missing: list[str] | None = None) -> list[ChangeOp]:
    """One op per schema entry the diff changed, so editing one model's tests in a shared .yml does not
    touch every model the file documents. Falls back to every asset at the path when no entry is named."""
    ev = [f"diff:{path}"]
    ops: list[ChangeOp] = []
    try:
        old = yaml.safe_load(old_text) if old_text else {}
        new = yaml.safe_load(new_text) if new_text else {}
    except yaml.YAMLError as e:
        return [ChangeOp("modify", a, details={"note": f"YAML did not parse: {e}"}, evidence=ev) for a in resolver.resolve(path)]
    old_entries, new_entries = _schema_entries(old), _schema_entries(new)
    for name in sorted(set(old_entries) | set(new_entries)):
        om, m = old_entries.get(name) or {}, new_entries.get(name) or {}
        if om == m:
            continue
        r = resolve_ref(store, name)
        if not r.ok or r.method == "fuzzy":
            if missing is not None:
                missing.append(name)
            continue
        before = len(ops)
        for key in ("owner", "tier", "contract"):
            if (om.get("meta") or {}).get(key) != (m.get("meta") or {}).get(key):
                ops.append(ChangeOp("change_metadata", r.node_id, details={"field": key, "from": (om.get("meta") or {}).get(key), "to": (m.get("meta") or {}).get(key)}, evidence=ev))
        o_cols = {c["name"] for c in om.get("columns", []) or [] if isinstance(c, dict) and "name" in c}
        n_cols = {c["name"] for c in m.get("columns", []) or [] if isinstance(c, dict) and "name" in c}
        for c in sorted(o_cols - n_cols):
            ops.append(ChangeOp("change_metadata", r.node_id, c, {"field": "column documentation removed"}, ev))
        if len(ops) == before:
            keys = sorted(k for k in set(om) | set(m) if om.get(k) != m.get(k))
            if not m:
                note = "schema entry removed"
            elif not om:
                note = "schema entry added"
            else:
                note = "schema entry changed: " + ", ".join(keys)
            # config in a schema file can change how the model builds; tests and docs cannot
            kind = "modify" if "config" in keys else "change_metadata"
            ops.append(ChangeOp(kind, r.node_id, details={"field" if kind == "change_metadata" else "note": note}, evidence=ev))
    if not ops and not (old_entries or new_entries):
        ops.extend(ChangeOp("modify", a, details={"note": "yaml changed"}, evidence=ev) for a in resolver.resolve(path))
    return ops


def ops_from_file_change(store: Store, resolver: PathResolver, path: str, old_text: str | None, new_text: str | None, tmp: Path) -> tuple[list[ChangeOp], list[dict[str, Any]]]:
    assets = resolver.resolve(path)
    unresolved: list[dict[str, Any]] = []
    missing = (
        "deleted, and the graph has no asset here; sync the graph from the base branch so deleted assets are known"
        if new_text is None and old_text is not None
        else "no asset defined at this path"
    )
    if assets and all(a.startswith("task:") for a in assets):
        # a gusty task file: one task, defined by its YAML (or front matter), not by SQL or a DAG
        return [ChangeOp("modify", a, details={"note": "task definition changed"}, evidence=[f"diff:{path}"]) for a in assets], unresolved
    if path.endswith(".py") and ("/dags/" in f"/{path}" or path.startswith("dags/")):
        ops, _ = ops_from_dag_change(store, path, old_text, new_text, tmp)
        return ops, unresolved
    if path.endswith(".sql") and (tested := resolver.tested_by(path)):
        return [ChangeOp("change_metadata", a, details={"field": "singular test"}, evidence=[f"diff:{path}"]) for a in tested], unresolved
    if path.endswith(".sql"):
        if not assets:
            unresolved.append({"path": path, "reason": missing})
            return [], unresolved
        schema = _graph_schema(store)
        ops: list[ChangeOp] = []
        for a in assets:
            ops.extend(ops_from_sql_change(a, path, old_text, new_text, schema))
        return ops, unresolved
    if path.endswith((".yml", ".yaml")):
        entries: list[str] = []
        ops = ops_from_yaml_change(resolver, store, path, old_text, new_text, entries)
        if entries:
            unresolved.append({"path": path, "reason": "changed schema entries the graph has no asset for: " + ", ".join(entries[:10]) + ("…" if len(entries) > 10 else ""), "entries": entries})
        if ops or assets or entries:
            return ops, unresolved
        if Path(path).name == "dbt_project.yml":
            missing = "dbt project configuration; Orion does not map its settings to the models they apply to, so check which models it changes"
        elif Path(path).name in ("packages.yml", "package-lock.yml", "dependencies.yml"):
            missing = "dbt package versions; an upgrade can change any macro the models call, so build the whole project before merging"
    if assets:
        return [ChangeOp("modify", a, evidence=[f"diff:{path}"]) for a in assets], unresolved
    unresolved.append({"path": path, "reason": missing})
    return [], unresolved


# ------------------------------------------------------------------------- git
def fork_point(repo_root: Path, base_ref: str) -> str:
    """Where this branch left ``base_ref`` (``git merge-base``), so a base that moved on after the branch was cut does not
    show its own new commits as this change undoing them; ``base_ref`` itself when there is no common ancestor."""
    r = subprocess.run(["git", "-C", str(repo_root), "merge-base", base_ref, "HEAD"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else base_ref


def _settle_removed_entries(cs: ChangeSet, changes: list[tuple[str, str | None, str | None]], renames: dict[str, str]) -> None:
    """A schema entry for a model whose SQL file this same change adds, deletes or renames is accounted for: the graph
    has no asset by that name because it was synced on the other side of the change."""
    # Matched by directory and name: a schema file documents the models beside it.
    gone = {(str(Path(p).parent), Path(p).stem) for pair in renames.items() for p in pair if p.endswith(".sql")}
    gone |= {(str(Path(p).parent), Path(p).stem) for p, old, new in changes if p.endswith(".sql") and (old is None) != (new is None)}
    if not gone:
        return
    kept = []
    for u in cs.unresolved:
        entries = u.get("entries")
        here = str(Path(u.get("path") or "").parent)
        settled = [e for e in entries or [] if (here, e) in gone]
        if entries and len(settled) == len(entries):
            cs.ignored.append(f"{u['path']}: schema entries for {', '.join(entries)}, added or removed with their SQL file")
            continue
        if settled:
            left = [e for e in entries if e not in settled]
            u = {**u, "entries": left, "reason": "changed schema entries the graph has no asset for: " + ", ".join(left[:10]) + ("…" if len(left) > 10 else "")}
        kept.append(u)
    cs.unresolved = kept


def git_changes(repo_root: Path, base_ref: str, renames: dict[str, str] | None = None) -> list[tuple[str, str | None, str | None]]:
    """(path, old_text, new_text) for every file that differs between where this branch left base_ref and the working tree.

    A renamed file is reported at its new path with the old path's text, so it reads as an edit, not a new file;
    ``renames`` (new path -> old path) collects them for callers that need the old name."""
    base_ref = fork_point(repo_root, base_ref)
    out: list[tuple[str, str | None, str | None]] = []
    status = subprocess.run(["git", "-C", str(repo_root), "diff", "--name-status", base_ref], check=True, capture_output=True, text=True).stdout
    untracked = subprocess.run(["git", "-C", str(repo_root), "ls-files", "--others", "--exclude-standard"], check=True, capture_output=True, text=True).stdout.split()
    entries: list[tuple[str, str, str]] = []
    for line in status.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            code, path = parts[0][0], parts[-1]
            entries.append((code, path, parts[1] if code == "R" and len(parts) >= 3 else path))
    entries += [("A", p, p) for p in untracked]
    for code, path, old_path in entries:
        old = None
        if old_path != path and renames is not None:
            renames[path] = old_path
        if code != "A":
            r = subprocess.run(["git", "-C", str(repo_root), "show", f"{base_ref}:{old_path}"], capture_output=True)
            old = r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else None
        new_path = repo_root / path
        # errors="replace": a binary file (an image, a font) in the diff must not stop the whole pre-flight.
        new = new_path.read_bytes().decode("utf-8", errors="replace") if new_path.exists() and code != "D" else None
        out.append((path, old, new))
    return out


# ---------------------------------------------------------------------- intent
INTENT_PATTERNS = [
    (re.compile(r"\b(drop|remove|delete)\s+(?:the\s+)?(?:column\s+)?`?(\w+)`?(?:\s+column)?\s+(?:from|in|on)\s+`?([\w\.]+)`?", re.I), "drop_column"),
    (re.compile(r"\brename\s+(?:the\s+)?(?:column\s+)?`?(\w+)`?\s+to\s+`?(\w+)`?\s+(?:in|on|across|of)\s+`?([\w\.]+)`?", re.I), "rename_column"),
    (re.compile(r"\badd\s+(?:a\s+)?(?:new\s+)?(?:column\s+)?`?(\w+)`?\s+(?:column\s+)?(?:to|in|on)\s+`?([\w\.]+)`?", re.I), "add_column"),
    (re.compile(r"\bbackfill\s+`?([\w\.]+)`?", re.I), "backfill"),
    (re.compile(r"\b(?:re-?partition|partition)\s+`?([\w\.]+)`?\s+(?:by|on)\s+`?(\w+)`?", re.I), "change_partition"),
    (re.compile(r"\b(?:move|change|set)\s+(?:the\s+)?(?:schedule\s+(?:of|for)\s+)?`?([\w\.]+)`?\s+(?:schedule\s+)?to\s+(?:run\s+)?(hourly|daily|weekly|every\s+\d+\s+(?:minutes?|hours?|days?)|[\d\*\s/,-]+)", re.I), "change_schedule"),
]


def ops_from_intent(store: Store, text: str) -> tuple[list[ChangeOp], list[dict[str, Any]]]:
    """A deterministic keyword parser. An LLM extractor can replace it behind the same signature."""
    ops: list[ChangeOp] = []
    unresolved: list[dict[str, Any]] = []
    ev = ["intent"]
    for pat, kind in INTENT_PATTERNS:
        for m in pat.finditer(text):
            g = m.groups()
            if kind == "drop_column":
                col, asset = g[1], g[2]
                details = {}
            elif kind == "rename_column":
                col, asset = g[0], g[2]
                details = {"to": g[1]}
            elif kind == "add_column":
                col, asset = g[0], g[1]
                details = {}
            elif kind == "backfill":
                col, asset, details = None, g[0], {"at": datetime.now(UTC).isoformat()}
            elif kind == "change_partition":
                col, asset, details = None, g[0], {"to": g[1]}
            else:
                col, asset, details = None, g[0], {"to": g[1].strip()}
            r = resolve_ref(store, asset)
            if r.ok:
                node = store.node(r.node_id) or {}
                if kind == "change_schedule":
                    details.setdefault("from", (node.get("properties") or {}).get("schedule"))
                if kind == "change_partition":
                    details.setdefault("from", ((node.get("properties") or {}).get("meta") or {}).get("partition_key"))
                ops.append(ChangeOp(kind, r.node_id, col, details, ev, from_intent=True, confidence=0.9 if r.method == "exact" else 0.7))
            else:
                unresolved.append({"intent": m.group(0), "reason": f"no asset matches '{asset}'", "candidates": [c["id"] for c in r.candidates]})
    if not ops and not unresolved:
        # last resort: any asset name mentioned becomes a generic modify
        for tok in set(re.findall(r"[A-Za-z_][\w\.]+", text)):
            r = resolve_ref(store, tok)
            if r.ok and r.method in ("exact", "alias", "short-name"):
                ops.append(ChangeOp("modify", r.node_id, evidence=ev, from_intent=True, confidence=0.5))
        if not ops:
            unresolved.append({"intent": text, "reason": "no asset or operation recognised"})
    return ops, unresolved


# -------------------------------------------------------------------- assembly
def ignored_path(path: str, ignore: list[str]) -> bool:
    """Whether ``path`` is under one of the ``ignore`` roots, or matches one that is a glob (``runs/dbt/*.run_results.json``)."""
    return any(fnmatch(path, r) if any(c in r for c in "*?[") else path == r or path.startswith(r.rstrip("/") + "/") for r in ignore if r)


def collect_changes(
    store: Store,
    repo_root: Path,
    *,
    base_ref: str | None = None,
    paths: list[str] | None = None,
    intent: str | None = None,
    ops: list[dict[str, Any]] | None = None,
    file_changes: list[tuple[str, str | None, str | None]] | None = None,
    tmp: Path | None = None,
    scope: list[str] | None = None,
    ignore: list[str] | None = None,
) -> ChangeSet:
    """``scope``: repo-relative roots the platform lives under; files outside are ignored, not
    unresolved. ``ignore``: roots whose files are not platform changes (constraints, incidents)."""
    import tempfile

    cs = ChangeSet()
    resolver = PathResolver(store)
    tmpdir = tmp or Path(tempfile.mkdtemp(prefix="orion-"))
    def platform_path(path: str) -> bool:
        inside = not scope or any(path == r or path.startswith(r.rstrip("/") + "/") for r in scope)
        if inside and not ignored_path(path, ignore or []):
            return True
        cs.ignored.append(path)
        return False

    # A diff from a file is filtered like one from git, so a path outside the platform is never "unmapped".
    changes = [c for c in file_changes or [] if platform_path(c[0])]
    if paths and not (base_ref or file_changes):
        cs.source = "paths"  # kept even when every path is outside the platform
    paths = [p for p in paths or [] if platform_path(p)] or None
    renames: dict[str, str] = {}
    if base_ref:
        for path, old, new in git_changes(repo_root, base_ref, renames):
            if platform_path(path):
                changes.append((path, old, new))
        cs.source = f"git diff {base_ref}"
    if file_changes:
        cs.source = cs.source or "file changes"
    for path, old, new in changes:
        if path.endswith(".md") and not resolver.resolve(path):
            cs.ignored.append(path)  # prose (dbt doc blocks, READMEs) changes no asset
            continue
        if path.endswith(".sql") and old is None and new is not None and not resolver.resolve(path) and not resolver.tested_by(path):
            # A model this change adds is not in a graph synced from the base branch, and nothing there can read it yet.
            cs.ignored.append(f"{path}: new file, nothing in the graph reads it yet")
            continue
        file_ops, unresolved = ops_from_file_change(store, resolver, path, old, new, tmpdir)
        for op in file_ops:
            cs.add(op)
        cs.unresolved.extend(unresolved)
    _settle_removed_entries(cs, changes, renames)
    if paths:
        cs.source = cs.source or "paths"
        for p in paths:
            assets = resolver.resolve(p)
            if assets:
                for a in assets:
                    cs.add(ChangeOp("modify", a, evidence=[f"path:{p}"]))
            else:
                cs.unresolved.append({"path": p, "reason": "no asset defined at this path"})
    if intent:
        cs.source = cs.source or "intent"
        intent_ops, unresolved = ops_from_intent(store, intent)
        for op in intent_ops:
            same = next((o for o in cs.ops if (o.kind, o.asset, o.column) == (op.kind, op.asset, op.column) and all(o.details.get(k) == v for k, v in op.details.items())), None)
            if same:  # the intent names a change the diff already shows: one op, with the intent as more evidence
                same.evidence = list(dict.fromkeys(same.evidence + ["intent"]))
            else:
                cs.add(op)
        cs.unresolved.extend(unresolved)
    if ops:
        cs.source = cs.source or "ops"
        for raw in ops:
            r = resolve_ref(store, raw["asset"])
            if not r.ok:
                cs.unresolved.append({"op": raw, "reason": f"no asset matches '{raw['asset']}'"})
                continue
            cs.add(ChangeOp(raw["kind"], r.node_id, raw.get("column"), dict(raw.get("details") or {}), raw.get("evidence") or ["ops"]))
    return cs
