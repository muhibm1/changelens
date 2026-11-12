"""Deterministic checks on a dbt warehouse change: the diff plus the repository's own SQL, macros and schema files.

No model, no warehouse and no lineage graph is involved, so a finding is the same every time and says which lines it
rests on. Three families, each a function of the change and the repository:

``row_count_change``
    a join that can fan out (its key is not provably unique on the joined side), a join key compared to itself, a join
    whose type changed, a removed filter, ``DISTINCT``, ``QUALIFY`` or ``UNION DISTINCT``, an added dedup, and a changed
    dedup or ``GROUP BY`` key.
``nullability_or_cast``
    a changed cast type, a ``SAFE_CAST`` to a type that turns bad values into NULL with no ``not_null`` test, a removed
    ``COALESCE``, and a join or filter on a column its schema file describes as possibly null.
``documented_column_not_selected``
    a column the schema file documents that the changed SQL no longer selects, and the readers of a column a change drops.

A few smaller checks that need the same parse sit beside them: SQL that stopped parsing, a dangling ``AS``, a table typed
by its warehouse name where ``ref()`` belongs, a surrogate key narrower than the ``GROUP BY`` it labels, and a macro
call that sits where its other call sites have ``WHERE``.

Every finding names its file and line and quotes the evidence it rests on; what the rule could not establish goes in
``unresolved`` (for example, that no unique test is recorded for the joined model). Severity is ``warn`` only where the
change itself shows the defect (a key compared to itself, a documented column that cannot exist, a key that covers part
of a declared composite key); a join the schema files simply do not prove unique is ``inform``.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from .diffparse import FileDiff
from .warehouse_schema import ColumnDoc, ModelDoc, SchemaIndex, build_index, parse_schema

RULES_VERSION = "warehouse-rules-v2"
DIALECTS = ("bigquery", "snowflake", "duckdb", None)
MAX_DEPTH = 4
TEMPORAL = {"TIMESTAMP", "DATE", "DATETIME", "TIME", "TIMESTAMPTZ", "TIMESTAMPNTZ", "TIMESTAMPLTZ"}
NUMERIC = {"INT", "BIGINT", "SMALLINT", "TINYINT", "NUMERIC", "DECIMAL", "BIGDECIMAL", "FLOAT", "DOUBLE", "INT64", "FLOAT64"}


# ---------------------------------------------------------------------------------------------------- findings
@dataclass
class Finding:
    rule: str
    bucket: str  # row_count_change | nullability_or_cast | documented_column_not_selected | other | schema_contract
    severity: str  # warn | inform
    path: str
    line: int | None
    title: str
    body: str
    end_line: int | None = None
    evidence: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    def to_review(self) -> dict[str, Any]:
        """The shape ``review`` returns for a deterministic finding."""
        ev = list(dict.fromkeys(self.evidence or ([f"hunk:{self.path}:{self.line}"] if self.line else [])))
        return {"source": "deterministic", "path": self.path, "line": self.line, "end_line": self.end_line, "severity": self.severity, "category": "data_contract" if self.bucket in ("documented_column_not_selected", "schema_contract") else "correctness", "title": self.title, "body": self.body, "evidence": ev, "status": "valid", "notes": list(self.unresolved), "rule": self.rule}


# ---------------------------------------------------------------------------------------------------- views
class View:
    """Read access to the repository on both sides of a change."""

    def paths(self) -> list[str]:
        raise NotImplementedError

    def read(self, path: str) -> str | None:
        raise NotImplementedError

    def read_old(self, path: str) -> str | None:
        raise NotImplementedError

    def blob(self, path: str) -> str | None:
        """A stable id of the new-side content, so parsed schema files can be reused across changes."""
        text = self.read(path)
        return hashlib.sha1(text.encode()).hexdigest() if text is not None else None


class TextView(View):
    """A repository held as ``{path: text}`` on each side (tests, and diffs applied in memory)."""

    def __init__(self, new: dict[str, str], old: dict[str, str] | None = None) -> None:
        self.new, self.old = new, old or {}

    def paths(self) -> list[str]:
        return sorted(self.new)

    def read(self, path: str) -> str | None:
        return self.new.get(path)

    def read_old(self, path: str) -> str | None:
        return self.old.get(path)


_BLOBS: dict[tuple[str, str], str | None] = {}  # git blobs read so far, shared by every view of the same repository


class GitView(View):
    """A repository read from git objects: ``head`` is the new side (None: the working tree) and ``base`` the old."""

    def __init__(self, repo: Path, base: str, head: str | None = None) -> None:
        self.repo, self.base, self.head = repo, base, head
        self._ls: dict[str, str] | None = None

    def _git(self, *args: str) -> str | None:
        p = subprocess.run(["git", "-C", str(self.repo), *args], capture_output=True)
        return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else None

    def _tree(self) -> dict[str, str]:
        if self._ls is None:
            self._ls = {}
            if self.head is None:
                out = self._git("ls-files", "-z", "-s") or ""
                for rec in out.split("\0"):
                    if "\t" in rec:
                        meta, name = rec.split("\t", 1)
                        self._ls[name] = meta.split()[1]
            else:
                out = self._git("ls-tree", "-r", "-z", self.head) or ""
                for rec in out.split("\0"):
                    if "\t" in rec:
                        meta, name = rec.split("\t", 1)
                        self._ls[name] = meta.split()[2]
        return self._ls

    def paths(self) -> list[str]:
        return list(self._tree())

    def blob(self, path: str) -> str | None:
        if self.head is None and (self.repo / path).is_file():
            return super().blob(path)  # the working tree may differ from the index
        return self._tree().get(path)

    def read(self, path: str) -> str | None:
        if self.head is None:
            p = self.repo / path
            try:
                return p.read_text(errors="replace") if p.is_file() else None
            except OSError:
                return None
        sha = self._tree().get(path)
        if sha is None:
            return None
        key = (str(self.repo), sha)
        if key not in _BLOBS:
            _BLOBS[key] = self._git("cat-file", "blob", sha)
        return _BLOBS[key]

    def read_old(self, path: str) -> str | None:
        return self._git("show", f"{self.base}:{path}")


# ---------------------------------------------------------------------------------------------------- jinja
JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)
JINJA_BLOCK = re.compile(r"\{%-?.*?-?%\}", re.S)
JINJA_EXPR = re.compile(r"\{\{-?(.*?)-?\}\}", re.S)
REF_CALL = re.compile(r"""^\s*ref\(\s*(?:['"][^'"]+['"]\s*,\s*)?['"]([^'"]+)['"]\s*(?:,[^)]*)?\)\s*$""", re.S)
SOURCE_CALL = re.compile(r"""^\s*source\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)\s*$""", re.S)
MACRO_CALL = re.compile(r"^\s*([A-Za-z_][\w.]*)\s*\((.*)\)\s*$", re.S)
MACRO_DEF = re.compile(r"\{%-?\s*macro\s+(\w+)\s*\((.*?)\)\s*-?%\}(.*?)\{%-?\s*endmacro\s*-?%\}", re.S)
HARDCODED = re.compile(r"(?i)\b(?:from|join)\s+(`[^`\n]+\.[^`\n]+\.[^`\n]+`|[\w-]+\.[\w-]+\.[\w-]+)")
SQL_COMMENT = re.compile(r"--[^\n]*")


def _blank(m: re.Match[str]) -> str:
    return re.sub(r"[^\n]", " ", m.group(0))


@dataclass
class Macro:
    name: str
    params: dict[str, str | None]  # name -> default text (quotes stripped) or None
    body: str
    path: str
    line: int

    @property
    def simple(self) -> bool:
        """No control flow: a body that is plain SQL with ``{{ param }}`` holes can be inlined."""
        return not JINJA_BLOCK.search(JINJA_COMMENT.sub("", self.body))


def _split_args(text: str) -> list[str]:
    out, depth, cur, quote = [], 0, "", ""
    for ch in text:
        if quote:
            cur += ch
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
            cur += ch
        elif ch in "([{":
            depth += 1
            cur += ch
        elif ch in ")]}":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def _unquote(s: str) -> str:
    s = s.strip()
    return s[1:-1] if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"" else s


def parse_macros(path: str, text: str) -> list[Macro]:
    out = []
    for m in MACRO_DEF.finditer(text):
        params: dict[str, str | None] = {}
        for a in _split_args(m.group(2)):
            k, sep, v = a.partition("=")
            params[k.strip()] = _unquote(v) if sep else None
        out.append(Macro(m.group(1), params, m.group(3), path, text.count("\n", 0, m.start()) + 1))
    return out


def _inline(macro: Macro, call_args: list[str], macros: dict[str, Macro], depth: int) -> str | None:
    """A macro call written out as one line of SQL, or None when it cannot be (control flow, unknown holes)."""
    if not macro.simple or depth > 3:
        return None
    values = dict(macro.params)
    names = list(macro.params)
    for i, a in enumerate(call_args):
        k, sep, v = a.partition("=")
        if sep and k.strip() in values and not k.strip().startswith(("'", '"')):
            values[k.strip()] = _unquote(v)
        elif i < len(names):
            values[names[i]] = _unquote(a)
    body = JINJA_COMMENT.sub("", macro.body)
    body = SQL_COMMENT.sub("", body)

    class _Unknown(Exception):
        pass

    def hole(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        if inner in values and values[inner] is not None:
            return str(values[inner])
        call = MACRO_CALL.match(inner)
        if call and call.group(1) in macros:
            sub = _inline(macros[call.group(1)], _split_args(call.group(2)), macros, depth + 1)
            if sub is not None:
                return sub
        raise _Unknown  # a hole the rules cannot fill would leave the SQL meaning something else

    try:
        return " ".join(JINJA_EXPR.sub(hole, body).split())
    except _Unknown:
        return None


def _scan_jinja(raw: str) -> list[tuple[str, str]]:
    """``raw`` as ('text', sql) and ('expr' | 'block' | 'comment', source) pieces. A closing ``}}`` or ``%}`` inside a
    quoted string does not end the tag, so a ``config()`` whose hooks hold their own ``{{ }}`` is read as one tag."""
    out: list[tuple[str, str]] = []
    i, n, text_from = 0, len(raw), 0
    while i < n - 1:
        two = raw[i: i + 2]
        if two not in ("{{", "{%", "{#"):
            i += 1
            continue
        close = {"{{": "}}", "{%": "%}", "{#": "#}"}[two]
        j, quote = i + 2, ""
        while j < n - 1:
            ch = raw[j]
            if two != "{#" and quote:
                if ch == quote:
                    quote = ""
            elif two != "{#" and ch in "'\"":
                quote = ch
            elif raw[j: j + 2] == close:
                break
            j += 1
        else:
            i += 2
            continue
        if text_from < i:
            out.append(("text", raw[text_from:i]))
        out.append(({"{{": "expr", "{%": "block", "{#": "comment"}[two], raw[i: j + 2]))
        i = text_from = j + 2
    out.append(("text", raw[text_from:]))
    return out


def dejinja(raw: str, macros: dict[str, Macro] | None = None, *, unknown: str = "NULL") -> str:
    """``raw`` as plain SQL with every line kept where it was: ``ref()`` and ``source()`` become table names, a simple
    macro is written out, any other expression becomes ``unknown`` and blocks and comments become blanks."""
    macros = macros or {}
    pieces = []
    for kind, src in _scan_jinja(raw):
        pad = "\n" * src.count("\n")
        if kind == "text":
            pieces.append(src)
        elif kind in ("comment", "block"):
            pieces.append(re.sub(r"[^\n]", " ", src))
        else:
            inner = src[2:-2].strip("-").strip()
            if mm := REF_CALL.match(inner):
                pieces.append(mm.group(1) + pad)
            elif mm := SOURCE_CALL.match(inner):
                pieces.append(f"{mm.group(1)}__{mm.group(2)}" + pad)
            elif inner.startswith("config("):
                pieces.append(pad)
            elif inner == "this":
                pieces.append("this" + pad)
            else:
                call = MACRO_CALL.match(inner)
                sql = None
                if call and (mac := macros.get(call.group(1).split(".")[-1])):
                    sql = _inline(mac, _split_args(call.group(2)), macros, 0)
                pieces.append((sql if sql is not None else unknown) + pad)
    return "".join(pieces)


# ---------------------------------------------------------------------------------------------------- parse
def _lines(node: exp.Expression | None) -> list[int]:
    return [n.meta["line"] for n in node.walk() if n.meta.get("line")] if node is not None else []


def _line(node: exp.Expression | None) -> int | None:
    """First source line of a node: identifiers and literals carry the position sqlglot read them at."""
    lines = _lines(node)
    return min(lines) if lines else None


def _last_line(node: exp.Expression | None) -> int | None:
    lines = _lines(node)
    return max(lines) if lines else None


def parse_sql(raw: str, macros: dict[str, Macro] | None = None) -> tuple[exp.Expression | None, str | None]:
    """(tree, dialect) or (None, None). The text is tried with unknown Jinja as NULL, then as nothing, in each dialect."""
    for unknown in ("NULL", ""):
        text = dejinja(raw, macros, unknown=unknown)
        for d in DIALECTS:
            try:
                tree = sqlglot.parse_one(text, read=d)
            except Exception:  # noqa: BLE001 - a statement sqlglot cannot read is reported, not fatal
                continue
            if tree is not None:
                return tree, d or "generic"
    return None, None


def _norm(e: exp.Expression | None, dialect: str | None = None) -> str:
    """An expression as lower-case text with table qualifiers dropped, so an alias rename is not a change."""
    if e is None:
        return ""
    c = e.copy()
    for col in list(c.find_all(exp.Column)):
        if col.args.get("table"):
            col.set("table", None)
    return " ".join(c.sql(dialect=None if dialect == "generic" else dialect).lower().split())


def _conjuncts(e: exp.Expression | None) -> list[exp.Expression]:
    if e is None:
        return []
    if isinstance(e, exp.Paren):
        return _conjuncts(e.this)
    if isinstance(e, exp.And):
        return _conjuncts(e.left) + _conjuncts(e.right)
    return [e]


def _cte_name(node: exp.Expression) -> str:
    p = node.parent
    while p is not None:
        if isinstance(p, exp.CTE):
            return p.alias
        p = p.parent
    return "<final>"


def _from(sel: exp.Select) -> exp.Expression | None:
    f = sel.args.get("from_") or sel.args.get("from")
    return f.this if f is not None else None


@dataclass
class JoinFact:
    scope: str
    kind: str  # inner | left | right | full | cross
    rel: str  # relation name after ref() and source() are reduced to names; "" for a derived table or UNNEST
    alias: str
    on: str  # normalized ON text
    line: int | None
    node: exp.Join
    select: exp.Select


@dataclass
class DedupFact:
    scope: str
    func: str
    partition: frozenset[str]
    one: bool  # the QUALIFY keeps only rank 1
    line: int | None


@dataclass
class FilterFact:
    text: str
    cols: frozenset[str]
    line: int | None
    node: exp.Expression


@dataclass
class Facts:
    path: str
    raw: str
    tree: exp.Expression | None
    dialect: str | None
    ctes: dict[str, exp.Expression] = field(default_factory=dict)
    joins: list[JoinFact] = field(default_factory=list)
    filters: list[FilterFact] = field(default_factory=list)
    dedups: list[DedupFact] = field(default_factory=list)
    distincts: int = 0
    groups: dict[str, tuple[frozenset[str], int | None]] = field(default_factory=dict)
    projections: dict[str, list[tuple[str, exp.Expression, int | None]]] = field(default_factory=dict)  # alias -> [(scope, expr, line)]
    unions_distinct: int = 0

    @property
    def parsed(self) -> bool:
        return self.tree is not None


def _join_kind(j: exp.Join) -> str:
    kind = (j.args.get("kind") or "").upper()
    side = (j.args.get("side") or "").upper()
    return (side or kind or "inner").lower()


def _rel_name(t: exp.Expression) -> tuple[str, str]:
    if isinstance(t, exp.Table):
        return t.name, t.alias_or_name
    return "", getattr(t, "alias", "") or ""


def _resolve_group(sel: exp.Select, dialect: str | None) -> frozenset[str]:
    g = sel.args.get("group")
    if g is None:
        return frozenset()
    keys = []
    for e in g.expressions:
        if isinstance(e, exp.Literal) and e.is_int and 0 < int(e.name) <= len(sel.expressions):
            e = sel.expressions[int(e.name) - 1]
            e = e.this if isinstance(e, exp.Alias) else e
        keys.append(_norm(e, dialect))
    return frozenset(keys)


def extract(path: str, raw: str, macros: dict[str, Macro] | None = None) -> Facts:
    tree, dialect = parse_sql(raw, macros)
    facts = Facts(path, raw, tree, dialect)
    if tree is None:
        return facts
    with_ = tree.args.get("with_") or tree.args.get("with")
    if with_ is not None:
        for cte in with_.expressions:
            facts.ctes[cte.alias] = cte.this
    for u in tree.find_all(exp.Union):
        if u.args.get("distinct"):
            facts.unions_distinct += 1
    for sel in tree.find_all(exp.Select):
        scope = _cte_name(sel)
        if sel.args.get("distinct"):
            facts.distincts += 1
        for j in sel.args.get("joins") or []:
            rel, alias = _rel_name(j.this)
            facts.joins.append(JoinFact(scope, _join_kind(j), rel, alias, _norm(j.args.get("on"), dialect) or ("using " + ",".join(sorted(i.name for i in j.args.get("using") or [])) if j.args.get("using") else ""), _line(j.this) or _line(j), j, sel))
        for clause in ("where", "having"):
            node = sel.args.get(clause)
            for c in _conjuncts(node.this if node is not None else None):
                facts.filters.append(FilterFact(_norm(c, dialect), frozenset(col.name.lower() for col in c.find_all(exp.Column)), _line(c), c))
        if (q := sel.args.get("qualify")) is not None:
            for c in _conjuncts(q.this):
                w = next(iter(c.find_all(exp.Window)), None)
                if w is None:
                    continue
                func = {"ROWNUMBER": "ROW_NUMBER", "DENSERANK": "DENSE_RANK"}.get(type(w.this).__name__.upper(), type(w.this).__name__.upper())
                part = frozenset(_norm(p, dialect) for p in w.args.get("partition_by") or [])
                one = isinstance(c, (exp.EQ, exp.LTE)) and any(isinstance(s, exp.Literal) and s.name == "1" for s in (c.right, c.left))
                facts.dedups.append(DedupFact(scope, func, part, one, _line(q) or _line(w)))
        if sel.args.get("group") is not None:
            facts.groups.setdefault(scope, (_resolve_group(sel, dialect), _line(sel.args["group"])))
        for p in sel.expressions:
            if isinstance(p, exp.Alias):
                facts.projections.setdefault(p.alias.lower(), []).append((scope, p.this, _line(p) or _line(p.this)))
            elif isinstance(p, exp.Column) and p.name:
                facts.projections.setdefault(p.name.lower(), []).append((scope, p, _line(p)))
    return facts


# ---------------------------------------------------------------------------------------------------- repository model
def _is_model_path(path: str) -> bool:
    return path.endswith(".sql") and ("/models/" in "/" + path or path.startswith("models/"))


def _is_schema_path(path: str) -> bool:
    return path.endswith((".yml", ".yaml")) and "/target/" not in path and "dbt_packages" not in path and ("/models/" in "/" + path or path.startswith("models/"))


_SCHEMA_CACHE: dict[str, list[ModelDoc] | None] = {}
_MACRO_CACHE: dict[str, list[Macro]] = {}


@dataclass
class Repo:
    """The new side of the repository as the rules need it: schema index, macros, model files, reader search."""

    view: View
    schema: SchemaIndex = field(default_factory=SchemaIndex)
    macros: dict[str, Macro] = field(default_factory=dict)
    models: dict[str, str] = field(default_factory=dict)  # model name -> sql path
    _facts: dict[str, Facts] = field(default_factory=dict)

    @classmethod
    def load(cls, view: View, overrides: dict[str, str] | None = None) -> Repo:
        repo = cls(view)
        idx = SchemaIndex()
        for p in view.paths():
            if _is_schema_path(p):
                key = view.blob(p) or p
                if key not in _SCHEMA_CACHE:
                    text = view.read(p)
                    _SCHEMA_CACHE[key] = parse_schema(p, text) if text is not None else None
                docs = _SCHEMA_CACHE[key]
                if docs is None:
                    idx.unreadable.append(p)
                else:
                    for d in docs:
                        idx.models.setdefault(d.name.lower(), d)
            elif _is_model_path(p):
                repo.models.setdefault(Path(p).stem.lower(), p)
            elif p.endswith(".sql") and ("/macros/" in "/" + p or p.startswith("macros/")):
                key = "m:" + (view.blob(p) or p)
                if key not in _MACRO_CACHE:
                    text = view.read(p)
                    _MACRO_CACHE[key] = parse_macros(p, text) if text and "macro" in text else []
                for m in _MACRO_CACHE[key]:
                    repo.macros.setdefault(m.name, m)
        repo.schema = idx
        return repo

    def facts(self, path: str, text: str | None = None) -> Facts | None:
        if path not in self._facts:
            raw = text if text is not None else self.view.read(path)
            if raw is None:
                return None
            self._facts[path] = extract(path, raw, self.macros)
        return self._facts[path]

    def model_facts(self, name: str) -> Facts | None:
        p = self.models.get(name.lower())
        return self.facts(p) if p else None


# ---------------------------------------------------------------------------------------------------- produced columns
def _output_names(sel: exp.Expression, repo: Repo, ctes: dict[str, exp.Expression], depth: int) -> list[str] | None:
    """The columns a SELECT produces, or None when a star or an unreadable source makes that unknowable."""
    if depth > MAX_DEPTH:
        return None
    if isinstance(sel, exp.Union):
        return _output_names(sel.this, repo, ctes, depth + 1)
    if isinstance(sel, exp.Subquery):
        return _output_names(sel.this, repo, ctes, depth + 1)
    if not isinstance(sel, exp.Select):
        return None
    out: list[str] = []
    for p in sel.expressions:
        if isinstance(p, exp.Star) or (isinstance(p, exp.Column) and isinstance(p.this, exp.Star)):
            cols = _star_columns(sel, p, repo, ctes, depth)
            if cols is None:
                return None
            out += cols
        elif isinstance(p, exp.Alias):
            out.append(p.alias)
        elif isinstance(p, exp.Column):
            out.append(p.name)
        else:
            name = p.alias_or_name
            if not name or name.upper() == "NULL":
                return None  # an unnamed expression, usually a macro the rules could not write out
            out.append(name)
    except_ = []
    for p in sel.expressions:
        if isinstance(p, exp.Star):
            except_ += [e.name for e in (p.args.get("except_") or p.args.get("except") or [])]
    return [c for c in out if c not in except_]


def _star_columns(sel: exp.Select, star: exp.Expression, repo: Repo, ctes: dict[str, exp.Expression], depth: int) -> list[str] | None:
    qualifier = star.table if isinstance(star, exp.Column) else None
    sources: list[exp.Expression] = []
    if (f := _from(sel)) is not None:
        sources.append(f)
    sources += [j.this for j in sel.args.get("joins") or []]
    if qualifier:
        sources = [s for s in sources if _rel_name(s)[1] == qualifier or _rel_name(s)[0] == qualifier]
    cols: list[str] = []
    for s in sources:
        name, _ = _rel_name(s)
        if isinstance(s, exp.Subquery):
            got = _output_names(s.this, repo, ctes, depth + 1)
        elif name in ctes:
            got = _output_names(ctes[name], repo, ctes, depth + 1)
        elif name:
            got = _model_columns(name, repo, depth + 1)
        else:
            got = None
        if got is None:
            return None
        cols += got
    excepts = [e.name for e in (star.args.get("except_") or star.args.get("except") or [])]
    return [c for c in cols if c not in excepts]


def _model_columns(name: str, repo: Repo, depth: int) -> list[str] | None:
    f = repo.model_facts(name)
    if f is not None and f.tree is not None:
        got = _output_names(f.tree, repo, f.ctes, depth)
        if got is not None:
            return got
    return None


def produced_columns(facts: Facts, repo: Repo) -> list[str] | None:
    """The columns a model's SQL produces, or None when that cannot be told from the SQL and the models it reads."""
    if facts.tree is None:
        return None
    tree = facts.tree
    if isinstance(tree, exp.Select) and (w := tree.args.get("with_") or tree.args.get("with")):
        return _output_names(tree, repo, {c.alias: c.this for c in w.expressions}, 0)
    return _output_names(tree, repo, facts.ctes, 0)


# ---------------------------------------------------------------------------------------------------- uniqueness
@dataclass
class Keys:
    keys: list[frozenset[str]]
    state: str  # known | undeclared | multiplied
    why: str = ""
    line: int | None = None  # the line the repeating comes from (the keyless join), for ``multiplied``
    start: int | None = None  # first line of the SELECT that repeats its rows


def _cols_unqualified(e: exp.Expression) -> frozenset[str]:
    return frozenset(c.name.lower() for c in e.find_all(exp.Column))


def _select_keys(sel: exp.Expression, facts: Facts, repo: Repo, depth: int) -> Keys:
    """Columns a SELECT's rows are unique on, from its own DISTINCT, GROUP BY or dedup, or inherited from its source."""
    if depth > MAX_DEPTH or not isinstance(sel, exp.Select):
        return Keys([], "undeclared", "a set operation or deeply nested query")
    names = _output_names(sel, repo, facts.ctes, 0)
    if sel.args.get("distinct") and names:
        return Keys([frozenset(n.lower() for n in names)], "known", "SELECT DISTINCT")
    if (g := sel.args.get("group")) is not None:
        resolved = []
        for e in g.expressions:
            if isinstance(e, exp.Literal) and e.is_int and 0 < int(e.name) <= len(sel.expressions):
                e = sel.expressions[int(e.name) - 1]
            resolved.append(e.alias if isinstance(e, exp.Alias) else e.name if isinstance(e, exp.Column) else _norm(e))
        return Keys([frozenset(str(r).lower() for r in resolved)], "known", "its GROUP BY")
    if (q := sel.args.get("qualify")) is not None:
        for c in _conjuncts(q.this):
            w = next(iter(c.find_all(exp.Window)), None)
            if w is not None and isinstance(w.this, (exp.RowNumber, exp.Rank, exp.DenseRank)):
                return Keys([frozenset(p.name.lower() for p in w.args.get("partition_by") or [] if isinstance(p, exp.Column))], "known", "its QUALIFY")
    src = _from(sel)
    joins = sel.args.get("joins") or []
    base: Keys
    if src is None:
        return Keys([], "undeclared")
    name, _ = _rel_name(src)
    if isinstance(src, exp.Subquery):
        base = _select_keys(src.this, facts, repo, depth + 1)
    elif name in facts.ctes:
        base = _select_keys(facts.ctes[name], facts, repo, depth + 1)
    else:
        base = relation_keys(name, facts, repo, depth + 1)
    for j in joins:
        on = j.args.get("on")
        eq = [c for c in _conjuncts(on) if isinstance(c, exp.EQ)] if on is not None else []
        if not eq and not j.args.get("using"):
            where = sel.args.get("where")
            quals = {c.table for c in where.find_all(exp.Column) if c.table} if where is not None else set()
            if isinstance(j.this, exp.Unnest) or len(quals) < 2:
                return Keys([], "undeclared", "a cross join with no condition (a spine or an array expansion)")
            return Keys([], "multiplied", "it joins its sources with a condition other than an equality on a key (a cross or comma join filtered by WHERE) and does not deduplicate", _line(j.this), min(_lines(sel.expressions[0]) or [_line(j.this) or 0]) if sel.expressions else None)
    if base.state == "multiplied":
        return base
    return Keys(base.keys, base.state, base.why or "its source", base.line)


def relation_keys(name: str, facts: Facts, repo: Repo, depth: int = 0) -> Keys:
    """What is known about the columns a relation's rows are unique on."""
    if name in facts.ctes:
        return _select_keys(facts.ctes[name], facts, repo, depth)
    doc = repo.schema.get(name)
    if doc is not None and doc.unique_keys:
        return Keys([frozenset(c.lower() for c in k) for k in doc.unique_keys], "known", f"{doc.path} declares it unique", doc.line)
    if (sub := repo.model_facts(name)) is not None and sub.tree is not None and sub.tree.args.get("with_") is None and isinstance(sub.tree, exp.Select) and depth <= MAX_DEPTH:
        k = _select_keys(sub.tree, sub, repo, depth + 1)
        if k.state == "known":
            return k
    return Keys([], "undeclared", f"no unique test or key is recorded for {name}" + (f" in {doc.path}" if doc else ""))


def _join_columns(j: JoinFact) -> tuple[frozenset[str], bool]:
    """(columns of the joined side the ON clause compares, whether the ON clause is a plain AND of comparisons)."""
    on = j.node.args.get("on")
    cols: set[str] = {i.name.lower() for i in j.node.args.get("using") or []}
    if on is None:
        return frozenset(cols), bool(cols)
    plain = True
    for c in _conjuncts(on):
        if isinstance(c, exp.Or):
            plain = False
            continue
        for col in c.find_all(exp.Column):
            t = col.table
            if t == j.alias or (not t and not isinstance(c, (exp.Between,))):
                # an unqualified column in a comparison with a qualified other-side column belongs to the joined side
                other = [x for x in c.find_all(exp.Column) if x is not col and x.table and x.table != j.alias]
                if t or other or len(list(c.find_all(exp.Column))) == 1:
                    cols.add(col.name.lower())
    return frozenset(cols), plain


# ---------------------------------------------------------------------------------------------------- line mapping
def _new_line_for_old(fd: FileDiff, old_line: int | None) -> int | None:
    """Where a line of the old file stood in the new one: itself shifted by the hunks before it, or the line after a deletion."""
    if old_line is None:
        return None
    delta = 0
    for h in fd.hunks:
        if old_line < h.old_start:
            break
        o = h.old_start
        for op, n, _ in h.lines:
            if op == " ":
                o += 1
            elif op == "-":
                if o == old_line:
                    nxt = next((m for op2, m, _ in h.lines[h.lines.index((op, n, _)):] if m is not None), None)
                    return nxt if nxt is not None else h.new_end
                o += 1
        delta = (h.new_start + h.new_len) - (h.old_start + h.old_len)
    return old_line + delta


def _value_coalesces(e: exp.Expression) -> int:
    """COALESCEs that shape the value a column holds; one inside a CASE or IF condition only steers a branch."""
    n = 0
    for c in e.find_all(exp.Coalesce):
        node, in_cond = c, False
        while node.parent is not None and node is not e:
            par = node.parent
            if (isinstance(par, exp.If) and node.arg_key == "this") or (isinstance(par, exp.Case) and node.arg_key == "this") or isinstance(par, exp.Where | exp.Join):
                in_cond = True
                break
            node = par
        n += 0 if in_cond else 1
    return n


def _added_lines(fd: FileDiff) -> set[int]:
    return {n for h in fd.hunks for op, n, _ in h.lines if op == "+" and n is not None}


def _quote(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _line_text(raw: str, line: int | None) -> str:
    if not line:
        return ""
    lines = raw.splitlines()
    return lines[line - 1].strip() if 0 < line <= len(lines) else ""


# ---------------------------------------------------------------------------------------------------- the rules
VERSION_COLUMNS = {"_valid_from", "_valid_to", "valid_from", "valid_to", "effective_from", "effective_to", "calitp_deleted_at", "calitp_extracted_at", "dbt_valid_from", "dbt_valid_to"}
DATE_LIKE = re.compile(r"(^|_)(date|dt|day|week|month|year|hour|ts|timestamp|datetime)($|_)|_at$")
ENTITY_ID = re.compile(r"^(id|key|_key|pk)$")


def _singular(word: str) -> str:
    return word[:-3] + "y" if word.endswith("ies") else word[:-2] if word.endswith(("ses", "xes")) else word[:-1] if word.endswith("s") and not word.endswith("ss") else word


class Checker:
    def __init__(self, repo: Repo, files: list[FileDiff]) -> None:
        self.repo = repo
        self.files = files
        self.out: list[Finding] = []
        self.unresolved: list[dict[str, Any]] = []
        self.dedup_gone: list[tuple[str, frozenset[str]]] = []  # (path, partition) of every dedup a file lost
        self.dedup_came: list[tuple[str, frozenset[str]]] = []  # (path, partition) of every dedup a file gained

    def add(self, f: Finding) -> None:
        self.out.append(f)

    def note(self, path: str, line: int | None, rule: str, reason: str) -> None:
        """What a rule could not establish. It is listed with the review's unresolved items, never raised as a finding."""
        self.unresolved.append({"path": path, "line": line, "rule": rule, "reason": reason})

    # ---- row_count_change ----------------------------------------------------------------------------------
    def joins(self, fd: FileDiff, old: Facts | None, new: Facts) -> None:
        """Joins the change adds or rewrites: fan-out, a key compared to itself, a changed join type."""
        old_joins = Counter((j.kind, j.rel, j.on) for j in (old.joins if old else []))
        old_rel_on = {(j.rel, j.on): j.kind for j in (old.joins if old else [])}
        added = _added_lines(fd)
        for j in new.joins:
            ident = (j.kind, j.rel, j.on)
            if old_joins[ident] > 0:
                old_joins[ident] -= 1
                continue
            if j.line is not None and fd.status != "added" and not (added & set(range(j.line, (_last_line(j.node) or j.line) + 1))):
                continue  # the join's own lines were not touched
            if j.rel and (prev := old_rel_on.get((j.rel, j.on))) and prev != j.kind:
                drops = j.kind == "inner" and prev in ("left", "full")
                if not drops:
                    self.note(fd.path, j.line, "join_type_changed", f"join to {j.rel} changed from {prev.upper()} to {j.kind.upper()}; the surviving rows can change")
                    continue
                self.add(Finding("join_type_changed", "row_count_change", "warn", fd.path, j.line, f"join to {j.rel} changed from {prev.upper()} to {j.kind.upper()}", f"{fd.path}:{j.line} joins `{j.rel}` as {j.kind.upper()} JOIN where it was {prev.upper()} JOIN on the same condition. " + ("Rows with no match are now dropped, so the row count falls." if drops else "The set of rows that survive the join changes, so the row count can change."), evidence=[f"hunk:{fd.path}:{j.line}"]))
                continue
            self._join(fd, new, j)

    def _left_columns(self, new: Facts, j: JoinFact) -> set[str] | None:
        """Columns on the left side of ``j``: its FROM source and the joins before it, or None if one of them is unreadable."""
        sel = j.select
        sources = [s for s in [_from(sel)] if s is not None]
        for other in sel.args.get("joins") or []:
            if other is j.node:
                break
            sources.append(other.this)
        cols: set[str] = set()
        for s in sources:
            name, _ = _rel_name(s)
            got = _output_names(s.this, self.repo, new.ctes, 1) if isinstance(s, exp.Subquery) else _output_names(new.ctes[name], self.repo, new.ctes, 1) if name in new.ctes else _model_columns(name, self.repo, 1) if name else None
            if got is None:
                return None
            cols |= {c.lower() for c in got}
        return cols

    def _join(self, fd: FileDiff, new: Facts, j: JoinFact) -> None:
        on = j.node.args.get("on")
        text = _quote(_line_text(new.raw, j.line))
        for c in _conjuncts(on) if on is not None else []:
            if isinstance(c, exp.EQ) and isinstance(c.left, exp.Column) and isinstance(c.right, exp.Column) and c.left.table == c.right.table and c.left.name.lower() == c.right.name.lower():
                line = _line(c) or j.line
                self.add(Finding("join_key_compared_to_itself", "row_count_change", "warn", fd.path, line, f"join to {j.rel or j.alias} compares {c.left.sql()} with itself", f"{fd.path}:{line} joins `{j.rel or j.alias}` on `{c.sql()}`, which is true for every row that has the column. That condition links nothing, so each row on the left matches every row on the right that satisfies the rest of the ON clause and the join fans out. Compare it with the column of `{j.alias}`.", evidence=[f"hunk:{fd.path}:{line}"], end_line=_last_line(on)))
                return
        if not j.rel or (j.kind == "cross" and on is None):
            return  # keyless joins are judged through the CTE they feed (see relation_keys)
        cols, plain = _join_columns(j)
        keys = relation_keys(j.rel, new, self.repo)
        if keys.state == "multiplied":
            first = keys.start or keys.line
            self.add(Finding("join_to_multiplied_relation", "row_count_change", "warn", fd.path, first, f"{j.rel} repeats its rows and is joined at line {j.line}", f"`{j.rel}` (lines {first}-{keys.line}) is built so that {keys.why}, so it can hold several rows for the same key. It is joined at {fd.path}:{j.line} ({text}), and every row on that join's left side that matches repeats once for each. Deduplicate `{j.rel}` first (DISTINCT, GROUP BY or QUALIFY) or join on its full key.", end_line=keys.line, evidence=[f"hunk:{fd.path}:{first}", f"hunk:{fd.path}:{j.line}"]))
            return
        if keys.keys and any(k <= cols for k in keys.keys):
            return
        if not plain or cols & VERSION_COLUMNS:
            self.note(fd.path, j.line, "join_fanout", f"join to {j.rel} is on a range, an OR or a validity window, so whether it matches one row per key was not decided")
            return
        if keys.state != "known":
            self.note(fd.path, j.line, "join_fanout", f"{keys.why}; whether {sorted(cols)} identifies one row of {j.rel} is not recorded")
            return
        left = self._left_columns(new, j)
        overlap = [k for k in keys.keys if k & cols]
        decl = self._declared(j.rel)
        ev = [f"hunk:{fd.path}:{j.line}"] + ([f"hunk:{self.repo.schema.get(j.rel).path}:{keys.line}"] if self.repo.schema.get(j.rel) and keys.line else [])
        if overlap:
            k = min(overlap, key=lambda k: len(k - cols))
            missing = sorted(k - cols)
            forgotten = sorted(c for c in missing if left is not None and c in left)
            if not forgotten:
                self.note(fd.path, j.line, "join_fanout", f"join to {j.rel} uses {sorted(cols)} of its declared key {sorted(k)}; {missing} are not on the left side, so the omission may be intended")
                return
            self.add(Finding("join_covers_part_of_key", "row_count_change", "warn", fd.path, j.line, f"join to {j.rel} uses part of its key", f"{fd.path}:{j.line} joins `{j.rel}` ({text}) on {sorted(cols)}. {decl} declares rows unique on {sorted(k)}; the join leaves out {missing}" + (f", and the left side has {forgotten} too, so it looks left out by mistake" if forgotten else "") + ". One left row can match several right rows (one per value of the missing column) and the result fans out.", evidence=ev, unresolved=[] if forgotten else [f"the left side's columns {'are unknown' if left is None else 'do not include ' + str(missing)}, so the omission may be intended"]))
            return
        dated = sorted(c for k in keys.keys for c in k if DATE_LIKE.search(c) and c not in cols)
        if not dated:
            self.note(fd.path, j.line, "join_on_non_key", f"join to {j.rel} on {sorted(cols) or 'no column of it'}, which are not declared unique there ({decl} declares {[sorted(k) for k in keys.keys[:3]]}); the join can fan out if they repeat")
            return
        self.add(Finding("join_on_non_key", "row_count_change", "warn", fd.path, j.line, f"join to {j.rel} on columns that are not its key", f"{fd.path}:{j.line} joins `{j.rel}` ({text}) on {sorted(cols) or 'no column of it'}, but {decl} declares rows unique on {', '.join(str(sorted(k)) for k in keys.keys[:3])}. Nothing recorded makes {sorted(cols)} unique in `{j.rel}`, so a left row can match several rows and the join can fan out" + (f"; its key includes {dated[:2]}, which the ON clause does not constrain, so every {dated[0]} of the same {sorted(cols)[0] if cols else 'key'} matches" if dated else "") + ".", evidence=ev, unresolved=[f"{sorted(cols)} are not declared unique in {j.rel}; if they are unique in practice, a unique test on them would say so"]))

    def _declared(self, rel: str) -> str:
        d = self.repo.schema.get(rel)
        return f"`{d.path}`" if d else f"`{rel}`'s definition"

    @staticmethod
    def _rewritten(fd: FileDiff, old: Facts) -> bool:
        """Most of the old file's lines are gone: a refactor or rewrite, where matching old and new pieces one by one is unreliable."""
        total = sum(1 for ln in old.raw.splitlines() if ln.strip())
        gone = sum(1 for h in fd.hunks for op, _, text in h.lines if op == "-" and text.strip())
        return fd.status != "added" and total >= 8 and gone * 5 >= total * 2

    def row_shape(self, fd: FileDiff, old: Facts, new: Facts) -> None:
        """Removed filters, DISTINCT, QUALIFY and UNION DISTINCT; changed dedup and GROUP BY keys; added dedups."""
        # filters: a conjunct that is gone and not merely edited (an added conjunct on the same columns); a bounded date
        # window is how a sample is cut, and removing it is not removing a condition on the data
        def real(f: FilterFact) -> bool:
            if f.text in ("null", ""):
                return False
            ranged = isinstance(f.node, (exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between)) and not list(f.node.find_all(exp.Subquery))
            literal = all(isinstance(x, (exp.Literal, exp.Cast, exp.Column, exp.Identifier, exp.Var, exp.CurrentDate, exp.Paren, exp.Neg, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between)) for x in f.node.walk())
            return not (ranged and literal)

        rewritten = self._rewritten(fd, old)
        old_f = Counter(f.text for f in old.filters if real(f))
        new_f = Counter(f.text for f in new.filters if real(f))
        gone = old_f - new_f
        came = new_f - old_f
        added_cols = [f.cols for f in new.filters if f.text in came]
        for f in old.filters:
            if not real(f) or gone[f.text] <= 0:
                continue
            gone[f.text] -= 1
            if any(f.cols & c for c in added_cols) or (f.cols and not f.cols <= {c.lower() for c in _all_columns(new)}):
                continue
            line = _new_line_for_old(fd, f.line)
            if rewritten:
                self.note(fd.path, line, "filter_removed", f"the condition `{_quote(f.text, 80)}` is not in the new file, but most of the file was rewritten, so it may have moved")
                continue
            self.add(Finding("filter_removed", "row_count_change", "warn", fd.path, line, f"filter removed: {_quote(f.text, 80)}", f"The change removes the condition `{_quote(f.text, 120)}` (it was at line {f.line} of the old file) and nothing in the new file filters on {sorted(f.cols) or 'the same columns'}. Rows it used to exclude now pass through, so the row count rises.", evidence=[f"hunk:{fd.path}:{line}"] if line else []))
        for what, o, n in (("SELECT DISTINCT", old.distincts, new.distincts), ("UNION DISTINCT", old.unions_distinct, new.unions_distinct)):
            if n < o:
                line = fd.first_changed_line()
                self.add(Finding("distinct_removed", "row_count_change", "warn", fd.path, line, f"{what} removed", f"The new file has {n} {what} where the old one had {o}. Duplicate rows that were collapsed now remain, so the row count rises unless something else deduplicates them.", evidence=[f"hunk:{fd.path}:{line}"] if line else []))
        # dedup (QUALIFY): changed key, removed, added
        old_d = Counter((d.func, d.partition) for d in old.dedups)
        new_d = Counter((d.func, d.partition) for d in new.dedups)
        removed = [d for d in old.dedups if (old_d - new_d)[(d.func, d.partition)] > 0]
        added = [d for d in new.dedups if (new_d - old_d)[(d.func, d.partition)] > 0]
        paired = min(len(removed), len(added))
        self.dedup_gone += [(fd.path, d.partition) for d in removed[paired:]]
        self.dedup_came += [(fd.path, d.partition) for d in added[paired:]]
        if rewritten and (removed or added):
            for d in removed + added:
                self.note(fd.path, d.line, "dedup_changed", f"a dedup over {sorted(d.partition)} differs between the old and new file, but most of the file was rewritten, so it may have moved")
            removed, added, paired = [], [], 0
        for o, n in zip(removed[:paired], added[:paired], strict=False):
            self.add(Finding("dedup_key_changed", "row_count_change", "warn", fd.path, n.line, f"dedup key changed from {sorted(o.partition)} to {sorted(n.partition)}", f"The QUALIFY that keeps one row per key partitions by {sorted(n.partition)} ({n.func}) where it was {sorted(o.partition)} ({o.func}). A different key changes which rows are dropped and how many survive.", evidence=[f"hunk:{fd.path}:{n.line}"]))
        for o in removed[paired:]:
            line = _new_line_for_old(fd, o.line)
            self.add(Finding("dedup_removed", "row_count_change", "warn", fd.path, line, f"dedup on {sorted(o.partition)} removed", f"The QUALIFY {o.func} over {sorted(o.partition)} is gone from the new file, so rows it dropped now remain.", evidence=[f"hunk:{fd.path}:{line}"] if line else []))
        for n in added[paired:]:
            self._dedup_added(fd, new, n)
        # group by: same CTE in both files, a different key; it matters when a declared unique key is no longer covered
        doc = self.repo.schema.get(Path(fd.path).stem)
        for scope, (keys, line) in new.groups.items():
            ok = old.groups.get(scope)
            if ok is None or ok[0] == keys:
                continue
            gone_k, came_k = sorted(ok[0] - keys), sorted(keys - ok[0])
            old_cols = {x.split(".")[-1] for x in ok[0]}
            lost = [k for k in (doc.unique_keys if doc else []) if {c.lower() for c in k} <= old_cols and not {c.lower() for c in k} <= {x.split(".")[-1] for x in keys}] if doc else []
            if not lost:
                self.note(fd.path, line, "group_by_key_changed", f"GROUP BY of {scope}: " + (f"no longer {gone_k}" if gone_k else "") + (f" now also {came_k}" if came_k else "") + "; no declared unique key says whether the grain was meant to change")
                continue
            self.add(Finding("group_by_key_changed", "row_count_change", "warn", fd.path, line, f"GROUP BY key of {scope} changed", f"The grain of `{scope}` changed: GROUP BY " + (f"no longer includes {gone_k}" if gone_k else "") + (" and " if gone_k and came_k else "") + (f"now also includes {came_k}" if came_k else "") + f". `{doc.path}` declares the model unique on {sorted(sorted(k) for k in lost)[0]}, which the new GROUP BY does not cover, so rows that were one are now several.", evidence=[f"hunk:{fd.path}:{line}"] if line else []))

    def _entity_id(self, fd: FileDiff, new: Facts) -> list[str]:
        """Identifier columns of the relation a dedup reads, named like the model's own entity (``settlement_id`` for ``..settlements..``)."""
        stem = Path(fd.path).stem.lower()
        words = [w for w in re.split(r"_+", stem) if w]
        entities = {_singular(w) for w in words if len(w) > 3}
        cols: set[str] = set()
        doc = self.repo.schema.get(stem)
        got = produced_columns(new, self.repo)
        cols |= {c.lower() for c in got} if got else set()
        if doc:
            cols |= {c.lower() for c in doc.columns}
        for src in {t.name for t in new.tree.find_all(exp.Table)} if new.tree is not None else set():
            if src in new.ctes:
                continue
            sd = self.repo.schema.get(src)
            cols |= {c.lower() for c in sd.columns} if sd else set()
            g = _model_columns(src, self.repo, 1)
            cols |= {c.lower() for c in g} if g else set()
        return sorted(c for c in cols if any(c == f"{e}_id" or c == f"{e}_key" for e in entities) or ENTITY_ID.match(c))

    def _dedup_added(self, fd: FileDiff, new: Facts, d: DedupFact) -> None:
        stem = Path(fd.path).stem
        doc = self.repo.schema.get(stem)
        declared = doc.unique_keys if doc else []
        part = {p.split(".")[-1] for p in d.partition}
        ties = d.func in ("RANK", "DENSE_RANK") and d.one and bool(part)
        reasons, sev = [], "inform"
        if declared and not any(frozenset(k) == frozenset(part) for k in declared):
            sev = "warn"
            reasons.append(f"`{doc.path}` declares {stem} unique on {sorted(sorted(k) for k in declared)}, which is not the partition key {sorted(part)}, so the dedup can drop rows that are distinct under the declared key or keep several for one")
        ids = [c for c in self._entity_id(fd, new) if c not in part]
        if ids and not declared and d.one:
            reasons.append(f"the partition key leaves out {ids[:3]}, which looks like the identifier of the rows being deduplicated, so rows with different {ids[0]} that share the partition key are dropped")
        if ties:
            reasons.append(f"{d.func} keeps every row tied for first, so it does not guarantee one row per key")
        if part and all(re.search(r"(^|_)hash$", c.lower().strip("`")) for c in part):
            self.note(fd.path, d.line, "dedup_added", f"a dedup was added over {sorted(part)}, a content hash, so it removes only rows that are identical")
            return
        if not reasons or not part:
            self.note(fd.path, d.line, "dedup_added", f"a dedup was added over {sorted(part) or 'the whole table'}; no unique key is recorded for {stem}, so whether that is its grain cannot be checked")
            return
        self.add(Finding("dedup_added", "row_count_change", sev, fd.path, d.line, f"dedup added: keeps rank 1 per {sorted(part)}", f"{fd.path}:{d.line} adds a QUALIFY {d.func} = 1 over {sorted(part)}, so rows that are not first in their partition are dropped. " + "; ".join(r[0].upper() + r[1:] for r in reasons) + ".", evidence=[f"hunk:{fd.path}:{d.line}"], unresolved=[] if declared else [f"no unique key is recorded for {stem}"]))

    # ---- nullability_or_cast -------------------------------------------------------------------------------
    def casts(self, fd: FileDiff, old: Facts, new: Facts) -> None:
        """A changed cast type, a SAFE_CAST that hides failures, a removed COALESCE."""
        doc = self.repo.schema.get(Path(fd.path).stem)
        safe: list[tuple[str, str, int | None]] = []
        text_lines = new.raw.splitlines()
        for alias, items in new.projections.items():
            olds_all = old.projections.get(alias, [])
            o_co = sum(_value_coalesces(x[1]) for x in olds_all)
            n_co = sum(_value_coalesces(x[1]) for x in items)
            for scope, e, line in items:
                olds = [x for x in olds_all if x[0] == scope] or olds_all
                nc = _cast_of(e)
                oc = _cast_of(olds[0][1]) if olds else None
                if nc and oc and (oc[1] != nc[1] or oc[0] != nc[0]) and _in_changed(fd, line):
                    self.add(Finding("cast_changed", "nullability_or_cast", "warn", fd.path, line, f"cast of {alias} changed from {oc[0]} {oc[1]} to {nc[0]} {nc[1]}", f"{fd.path}:{line} casts `{alias}` with {nc[0]}(... AS {nc[1]}) where it was {oc[0]}(... AS {oc[1]}). A different target type changes which values survive the cast and which become NULL or fail, and every reader of `{alias}` sees the new type.", evidence=[f"hunk:{fd.path}:{line}"]))
                elif nc and not any(not isinstance(x[1], exp.Column) for x in olds) and nc[0] == "SAFE_CAST" and nc[1].split("(")[0] in TEMPORAL and (doc is None or alias not in doc.columns or not doc.columns[alias].not_null) and _in_added(fd, line) and line and "safe_cast" in text_lines[line - 1].lower():
                    safe.append((alias, nc[1], line))
            if olds_all and o_co > n_co and any(_in_changed(fd, ln) for _, _, ln in items) and self._rewritten(fd, old):
                self.note(fd.path, next((ln for _, _, ln in items if _in_changed(fd, ln)), None), "coalesce_removed", f"`{alias}` lost a COALESCE, but most of the file was rewritten, so it may have moved")
            elif olds_all and o_co > n_co and any(_in_changed(fd, ln) for _, _, ln in items):
                line = next((ln for _, _, ln in items if _in_changed(fd, ln)), None)
                self.add(Finding("coalesce_removed", "nullability_or_cast", "warn", fd.path, line, f"COALESCE removed from {alias}", f"{fd.path}:{line} computes `{alias}` without the COALESCE/IFNULL the old file had around it, so rows whose input is NULL now produce NULL (and a sum, comparison or join downstream drops or mis-handles them).", evidence=[f"hunk:{fd.path}:{line}"]))
        if safe:
            first, last = safe[0][2], safe[-1][2]
            names = ", ".join(f"{a} ({t})" for a, t, _ in safe[:6]) + (f" and {len(safe) - 6} more" if len(safe) > 6 else "")
            self.add(Finding("safe_cast_hides_failures", "nullability_or_cast", "inform", fd.path, first, f"SAFE_CAST to a date or time type on {len(safe)} column{'s' if len(safe) != 1 else ''}", f"{fd.path}:{first} reads {names} with SAFE_CAST. A value in a format the cast does not accept becomes NULL with no error, and no not_null test on these columns is recorded, so a source that changes its format would fill them with NULL unnoticed.", end_line=last, evidence=[f"hunk:{fd.path}:{ln}" for _, _, ln in safe[:5]], unresolved=["no not_null test is recorded on these columns"]))

    def nullable_use(self, fd: FileDiff, old: Facts | None, new: Facts) -> None:
        """A join or filter newly written on a column its schema file describes as possibly null."""
        added = _added_lines(fd)
        old_texts = {f.text for f in (old.filters if old else [])}
        for j in new.joins:
            if not j.rel or j.line is None or (fd.status != "added" and j.line not in added) or j.kind == "cross":
                continue
            d = self.repo.schema.get(j.rel)
            if d is None:
                continue
            cols, _ = _join_columns(j)
            docs = {k.lower(): v for k, v in d.columns.items()}
            bad = sorted(c for c in cols if c in docs and docs[c].says_nullable)
            if bad:
                col = docs[bad[0]]
                self.add(Finding("join_on_nullable_column", "nullability_or_cast", "warn", fd.path, j.line, f"join to {j.rel} on {bad[0]}, which its schema file says can be null", f"{fd.path}:{j.line} joins `{j.rel}` on `{bad[0]}`. `{d.path}` line {col.line} describes it as possibly null (\"{_quote(col.description, 100)}\"). NULL never equals NULL, so rows where it is null find no match: an inner join drops them and a left join leaves them unmatched.", evidence=[f"hunk:{fd.path}:{j.line}"] + ([f"hunk:{d.path}:{col.line}"] if col.line else [])))
        sources = _sources(new)
        for f in new.filters:
            if f.text in old_texts or f.line is None or (fd.status != "added" and f.line not in added):
                continue
            if not isinstance(f.node, (exp.NEQ, exp.Not)):
                continue
            for col in f.node.find_all(exp.Column):
                rel = sources.get(col.table) if col.table else (next(iter(sources.values())) if len(sources) == 1 else None)
                d = self.repo.schema.get(rel) if rel else None
                cd = {k.lower(): v for k, v in d.columns.items()}.get(col.name.lower()) if d else None
                if cd is not None and cd.says_nullable:
                    self.add(Finding("filter_on_nullable_column", "nullability_or_cast", "warn", fd.path, f.line, f"filter on {col.name}, which its schema file says can be null", f"{fd.path}:{f.line} filters with `{_quote(f.text, 100)}`. `{d.path}` line {cd.line} describes `{col.name}` as possibly null; a negated comparison is not true for NULL, so rows where it is null are dropped along with the ones the filter targets.", evidence=[f"hunk:{fd.path}:{f.line}"] + ([f"hunk:{d.path}:{cd.line}"] if cd.line else [])))
                    break

    # ---- documented_column_not_selected --------------------------------------------------------------------
    def documented_for_model(self, name: str, sql_fd: FileDiff | None, yml_fds: dict[str, FileDiff]) -> None:
        """Columns the schema file documents that the model's SQL does not produce, where this change made it so."""
        doc = self.repo.schema.get(name)
        sql_path = self.repo.models.get(name.lower())
        if doc is None or not sql_path:
            return
        facts = self.repo.facts(sql_path)
        if facts is None or not facts.parsed:
            return
        got = produced_columns(facts, self.repo)
        if got is None:
            return
        have = {c.lower() for c in got}
        old_have: set[str] | None = None
        old_doc = _old_doc(self.repo, doc, name)
        if sql_fd is not None:
            old_raw = self.repo.view.read_old(sql_fd.old_path or sql_fd.path)
            if old_raw is not None:
                of = extract(sql_fd.path, old_raw, self.repo.macros)
                og = produced_columns(of, _OldRepo(self.repo)) if of.parsed else None
                old_have = {c.lower() for c in og} if og is not None else None
        missing = []
        for cname, cd in doc.columns.items():
            if cname.lower() in have:
                continue
            newly_documented = old_doc is None or cname not in old_doc.columns
            dropped_by_sql = sql_fd is not None and old_have is not None and cname.lower() in old_have
            if (newly_documented and doc.path in yml_fds) or dropped_by_sql:
                missing.append((cname, cd, "dropped" if dropped_by_sql else "documented"))
        if not missing:
            return
        tested = [c for c, cd, _ in missing if cd.tests]
        breaks = "tests" if tested else "contract" if doc.contract_enforced else None
        if not breaks and sql_fd is not None:
            # the author edited the model and its docs together and left no test on the column: drift, listed, not raised
            self.note(doc.path, missing[0][1].line, "documented_column_not_selected", f"{', '.join(c for c, _, _ in missing[:6])} documented on {name} but not selected by its SQL (no test on them)")
            return
        effect = "its tests fail on a column the table does not have" if breaks == "tests" else "the model's enforced contract fails the build" if breaks == "contract" else "the documentation describes columns the table does not have"
        shown = ", ".join(f"`{c}`" for c, _, _ in missing[:6]) + (f" and {len(missing) - 6} more" if len(missing) > 6 else "")
        first, last = missing[0][1].line, max((cd.line or 0) for _, cd, _ in missing) or None
        close = sorted({h for c, _, _ in missing[:3] for h in have if len(c) > 4 and (c.lower().replace("_", "") in h.replace("_", "") or h.replace("_", "") in c.lower().replace("_", ""))})
        hint = f" It selects {close[:3]}, which may be what was meant." if close else ""
        dropped = [c for c, _, k in missing if k == "dropped"]
        verb = f"This change stops `{sql_path}` from selecting {', '.join(f'`{c}`' for c in dropped[:4])}, which `{doc.path}` still documents" if dropped else f"`{doc.path}` documents {shown} on `{name}`, but `{sql_path}` as of this change produces no such column"
        self.add(Finding("documented_column_not_selected", "documented_column_not_selected", "warn", doc.path, first, f"{missing[0][0]} is documented on {name} but its SQL does not select it" + (f" (and {len(missing) - 1} more)" if len(missing) > 1 else ""), f"{verb}, so {effect}.{hint} Select the column in the model, or document it where it exists.", end_line=last if last and last != first else None, evidence=[f"hunk:{doc.path}:{cd.line}" for _, cd, _ in missing[:6] if cd.line]))

    def dropped_with_readers(self, fd: FileDiff, old: Facts, new: Facts) -> None:
        """Columns the change stops producing that a model outside the change still reads."""
        name = Path(fd.path).stem
        og = produced_columns(old, _OldRepo(self.repo)) if old.parsed else None
        ng = produced_columns(new, self.repo) if new.parsed else None
        if og is None or ng is None:
            return
        dropped = sorted({c.lower() for c in og} - {c.lower() for c in ng})
        if not dropped:
            return
        changed = {f.path for f in self.files}
        for reader, rfacts in self._readers(name):
            if reader in changed:
                continue
            used = _columns_used_from(rfacts, name)
            for col in dropped:
                if col in used:
                    line = used[col]
                    self.add(Finding("dropped_column_still_read", "documented_column_not_selected", "warn", fd.path, fd.first_changed_line(), f"{col} dropped from {name} but still read by {Path(reader).stem}", f"This change stops `{name}` from producing `{col}`, and `{reader}` line {line} reads `{col}` from it. That file is not in this change, so it will fail to build or read a column that is gone.", evidence=[f"hunk:{fd.path}:{fd.first_changed_line()}", f"ref:{reader}:{line}"]))

    def _readers(self, name: str) -> Iterable[tuple[str, Facts]]:
        pat = re.compile(r"""ref\(\s*(?:['"][^'"]+['"]\s*,\s*)?['"]""" + re.escape(name) + r"""['"]""")
        for p in self.repo.models.values():
            raw = self.repo.view.read(p)
            if raw and pat.search(raw) and (f := self.repo.facts(p)) is not None and f.parsed:
                yield p, f

    # ---- smaller checks ------------------------------------------------------------------------------------
    def parse_and_syntax(self, fd: FileDiff, old: Facts | None, new: Facts) -> None:
        """An ``AS`` with no alias, and SQL that parsed before the change and does not now."""
        added = _added_lines(fd)
        text = dejinja(new.raw, self.repo.macros, unknown="NULL")
        for m in re.finditer(r"(?i)\bAS\b(?=\s*(?:,|\)|\bFROM\b|\Z))", re.sub(r"--[^\n]*", lambda x: " " * len(x.group(0)), text)):
            line = text.count("\n", 0, m.start()) + 1
            if line in added or fd.status == "added":
                self.add(Finding("dangling_alias", "other", "warn", fd.path, line, "AS with no alias after it", f"{fd.path}:{line} has `AS` followed by nothing (`{_quote(_line_text(new.raw, line), 100)}`), so the select does not parse and the column it should have named is never produced.", evidence=[f"hunk:{fd.path}:{line}"]))
                return
        if old is not None and old.parsed and not new.parsed and fd.status != "added":
            line = fd.first_changed_line()
            if "{%" in new.raw:
                self.note(fd.path, line, "sql_stopped_parsing", "model SQL parsed before and does not now, but it uses Jinja control flow the rules cannot expand")
                return
            self.add(Finding("sql_stopped_parsing", "other", "warn", fd.path, line, "model SQL no longer parses", f"{fd.path} parsed before this change and does not parse now (read as {', '.join(d or 'generic' for d in DIALECTS)} with Jinja set aside). A syntax error in the changed lines would fail the build.", evidence=[f"hunk:{fd.path}:{line}"], unresolved=["Jinja the rules cannot expand may be the cause"]))

    def hardcoded(self, fd: FileDiff, new: Facts) -> None:
        """A table typed as ``project.dataset.table`` where ``ref()`` or ``source()`` belongs."""
        added = _added_lines(fd)
        text = new.raw
        for m in HARDCODED.finditer(re.sub(r"\{#.*?#\}", _blank, text, flags=re.S)):
            line = text.count("\n", 0, m.start()) + 1
            if fd.status != "added" and line not in added:
                continue
            rel = m.group(1).strip("`")
            if rel.upper().startswith(("UNNEST", "INFORMATION_SCHEMA")) or rel.count(".") < 2 or "{{" in rel:
                continue
            self.add(Finding("hardcoded_relation", "other", "warn", fd.path, line, f"reads {rel} by its warehouse name instead of ref() or source()", f"{fd.path}:{line} reads `{rel}` as a fixed table name. dbt's graph does not record the dependency, so `dbt build --select +{Path(fd.path).stem}` and the run order ignore it, and every environment reads that one hardcoded table. If it is a debugging leftover, restore the ref.", evidence=[f"hunk:{fd.path}:{line}"]))

    def surrogate_key(self, fd: FileDiff, new: Facts) -> None:
        """A surrogate key hashed from fewer columns than the GROUP BY of the query it is computed over."""
        text = JINJA_COMMENT.sub(_blank, new.raw)
        added = _added_lines(fd)
        for m in re.finditer(r"generate_surrogate_key\(\s*\[(.*?)\]\s*\)\s*(?:-?\}\})?\s*AS\s+(\w+)", text, re.S | re.I):
            line = text.count("\n", 0, m.start()) + 1
            if fd.status != "added" and not (added & set(range(line, line + m.group(0).count("\n") + 1))):
                continue
            cols = {c.strip().strip("'\"").split(".")[-1].lower() for c in _split_args(m.group(1))}
            alias = m.group(2)
            sel = _select_at(new, line)
            group = _grain_of(sel, new) if sel is not None else None
            if group is None or not group or cols >= group or cols & group == set():
                continue
            missing = sorted(group - cols)
            self.add(Finding("surrogate_key_narrower_than_grain", "schema_contract", "warn", fd.path, line, f"key {alias} is hashed from fewer columns than the query groups by", f"{fd.path}:{line} builds `{alias}` from {sorted(cols)}, but the rows it labels are grouped by {sorted(group)}. Rows that differ only in {missing} get the same `{alias}`, so it is not unique, and a unique or primary-key test on it fails.", evidence=[f"hunk:{fd.path}:{line}"], unresolved=[f"if {missing} are determined by {sorted(cols)}, the key is still unique"]))

    def bare_filter_macro(self, fd: FileDiff, new: Facts) -> None:
        """A macro call placed where its other call sites have WHERE or AND before them."""
        added = _added_lines(fd)
        text = JINJA_COMMENT.sub(_blank, new.raw)
        for m in re.finditer(r"\{\{-?\s*(\w+)\s*\(", text):
            name = m.group(1)
            if name not in self.repo.macros or name in ("ref", "source", "config", "var", "env_var"):
                continue
            line = text.count("\n", 0, m.start()) + 1
            if fd.status != "added" and line not in added:
                continue
            before = re.sub(r"\s+", " ", text[max(0, m.start() - 40): m.start()]).strip().upper()
            if before.endswith(("WHERE", "AND", "OR", "ON", "HAVING", "WHEN", "(", ",", "NOT", "=", "SELECT", "AS", "THEN", "ELSE", "BY", "FROM", "JOIN")):
                continue
            sites = self._macro_sites(name)
            if len(sites) >= 3 and sum(1 for b in sites if b.endswith(("WHERE", "AND", "OR"))) / len(sites) >= 0.8:
                self.add(Finding("filter_macro_without_where", "other", "warn", fd.path, line, f"{name}() used without WHERE", f"{fd.path}:{line} calls `{name}()` with no WHERE, AND or OR before it, but {len(sites)} other call sites in the repository put it after one. `{name}` writes a condition, so here it is not applied as a filter (or the statement does not parse) and the rows it was meant to limit all pass.", evidence=[f"hunk:{fd.path}:{line}"], unresolved=[f"{name} was not expanded; judged from how its other callers use it"]))

    def _macro_sites(self, name: str) -> list[str]:
        cache = getattr(self, "_sites", None)
        if cache is None:
            cache = self._sites = {}
        if name not in cache:
            out = []
            pat = re.compile(r"\{\{-?\s*" + re.escape(name) + r"\s*\(")
            for p in self.repo.models.values():
                raw = self.repo.view.read(p)
                if raw and name in raw:
                    t = JINJA_COMMENT.sub(_blank, raw)
                    for m in pat.finditer(t):
                        out.append(re.sub(r"\s+", " ", t[max(0, m.start() - 40): m.start()]).strip().upper())
            cache[name] = out
        return cache[name]

    def macro_dedup(self, fd: FileDiff) -> None:
        """A macro file that gains a row-dropping dedup (QUALIFY ... = 1) is the same change as one written in a model."""
        old_raw = self.repo.view.read_old(fd.old_path or fd.path) or ""
        new_raw = self.repo.view.read(fd.path) or ""
        old_m = {m.name: m for m in parse_macros(fd.path, old_raw)}
        for m in parse_macros(fd.path, new_raw):
            body = JINJA_COMMENT.sub("", SQL_COMMENT.sub("", m.body))
            q = re.search(r"(?is)\bQUALIFY\b\s*(ROW_NUMBER|RANK|DENSE_RANK)\s*\(\s*\)\s*OVER\s*\(\s*PARTITION\s+BY\s+(.*?)\s+ORDER\s+BY.*?\)\s*=\s*1", body)
            if not q:
                continue
            ob = old_m.get(m.name)
            if ob and re.sub(r"\s+", "", ob.body) == re.sub(r"\s+", "", m.body):
                continue
            func = q.group(1).upper()
            qline = m.line + m.body[: m.body.upper().find("QUALIFY")].count("\n")
            if func == "ROW_NUMBER":
                self.note(fd.path, qline, "dedup_added", f"macro {m.name} keeps one row per partition; the grain of the models that call it is not recorded")
                continue
            self.add(Finding("dedup_added", "row_count_change", "warn", fd.path, qline, f"macro {m.name} keeps rank 1 per partition", f"{fd.path}:{qline} defines `{m.name}`, which writes QUALIFY {func}() OVER (PARTITION BY {_quote(re.sub(r'[{}]', '', q.group(2)), 80)} ...) = 1. Every model that calls it drops all rows that are not first in their partition, and since {func} keeps ties, one key can still keep several rows. Rows that arrive only in an older partition are lost.", end_line=qline + 2, evidence=[f"hunk:{fd.path}:{qline}"], unresolved=["the partition columns are macro arguments; the grain they stand for is not recorded"]))


# ---------------------------------------------------------------------------------------------------- helpers
class _OldRepo(Repo):
    """The repository as the old side saw it, for reading the columns a model produced before the change."""

    def __init__(self, new: Repo) -> None:
        super().__init__(new.view, new.schema, new.macros, new.models)


def _old_doc(repo: Repo, doc: ModelDoc, name: str) -> ModelDoc | None:
    text = repo.view.read_old(doc.path)
    if text is None:
        return None
    docs = parse_schema(doc.path, text)
    return next((d for d in docs or [] if d.name.lower() == name.lower()), None)


def _cast_of(e: exp.Expression) -> tuple[str, str] | None:
    if isinstance(e, exp.TryCast):
        return "SAFE_CAST", e.args["to"].sql().upper()
    if isinstance(e, exp.Cast):
        return "CAST", e.args["to"].sql().upper()
    return None


def _in_added(fd: FileDiff, line: int | None) -> bool:
    return fd.status == "added" or (line in _added_lines(fd) if line else False)


def _in_changed(fd: FileDiff, line: int | None) -> bool:
    return fd.status == "added" or (line is not None and fd.contains(line))


def _sources(facts: Facts) -> dict[str, str]:
    out: dict[str, str] = {}
    if facts.tree is None:
        return out
    for t in facts.tree.find_all(exp.Table):
        out.setdefault(t.alias_or_name, t.name)
    return out


def _all_columns(facts: Facts) -> set[str]:
    """Every column name the file's SQL mentions."""
    return {c.name for c in facts.tree.find_all(exp.Column)} if facts.tree is not None else set()


def _select_at(facts: Facts, line: int) -> exp.Select | None:
    """The innermost SELECT that has an identifier on ``line`` (where a surrogate key is written)."""
    if facts.tree is None:
        return None
    best = None
    for sel in facts.tree.find_all(exp.Select):
        for p in sel.expressions:
            lo, hi = _line(p), _last_line(p)
            if lo is not None and hi is not None and lo - 1 <= line <= hi:
                best = sel
    return best


def _grain_of(sel: exp.Select, facts: Facts) -> set[str] | None:
    """The columns a query's rows are grouped by: its own GROUP BY, or the one of the single CTE it reads from."""
    if sel.args.get("group") is None:
        src = _from(sel)
        name = _rel_name(src)[0] if src is not None else ""
        body = facts.ctes.get(name)
        if body is None or sel.args.get("joins") or not isinstance(body, exp.Select) or body.args.get("group") is None:
            return None
        sel = body
    out: set[str] = set()
    for e in sel.args["group"].expressions:
        if isinstance(e, exp.Literal) and e.is_int:
            i = int(e.name)
            if not 0 < i <= len(sel.expressions):
                return None
            p = sel.expressions[i - 1]
            if isinstance(p, exp.Alias) and not isinstance(p.this, exp.Column) and (p.alias.lower() in ("key", "_key") or p.this.sql().upper() == "NULL"):
                continue
            out.add((p.alias if isinstance(p, exp.Alias) else p.name).lower())
        elif isinstance(e, exp.Column):
            out.add(e.name.lower())
        else:
            return None
    return out


def _columns_used_from(rf: Facts, model: str) -> dict[str, int]:
    """Columns a reader takes from ``model``: ``alias.col`` where the alias is the model, a star-CTE over it, or the table's alias."""
    aliases = {model}
    if rf.tree is None:
        return {}
    for name, body in rf.ctes.items():
        if isinstance(body, exp.Select) and _from(body) is not None and _rel_name(_from(body))[0] in aliases | {model} and not body.args.get("joins") and len(body.expressions) == 1 and isinstance(body.expressions[0], exp.Star):
            aliases.add(name)
    for t in rf.tree.find_all(exp.Table):
        if t.name in aliases:
            aliases.add(t.alias_or_name)
    used: dict[str, int] = {}
    for sel in rf.tree.find_all(exp.Select):
        srcs = [s for s in [_from(sel)] + [j.this for j in sel.args.get("joins") or []] if s is not None]
        single = len(srcs) == 1 and _rel_name(srcs[0])[0] in aliases | {model}
        for c in sel.find_all(exp.Column):
            if c.find_ancestor(exp.Select) is not sel:
                continue
            if (c.table and c.table in aliases) or (not c.table and single):
                used.setdefault(c.name.lower(), _line(c) or 0)
    return used


def _changed_models(fd: FileDiff) -> str | None:
    return Path(fd.path).stem if _is_model_path(fd.path) else None


# ---------------------------------------------------------------------------------------------------- entry
@dataclass
class Result:
    findings: list[Finding]
    unresolved: list[dict[str, Any]]  # what the rules could not establish: {"path", "line", "rule", "reason"}


def check_change(files: list[FileDiff], view: View, *, repo: Repo | None = None) -> list[Finding]:
    """Every finding the rules raise for ``files`` (a parsed diff) read against ``view``."""
    return check_change_full(files, view, repo=repo).findings


def check_change_full(files: list[FileDiff], view: View, *, repo: Repo | None = None) -> Result:
    """The findings and the unresolved notes for ``files`` (a parsed diff) read against ``view``."""
    repo = repo or Repo.load(view)
    chk = Checker(repo, files)
    yml_fds = {f.path: f for f in files if _is_schema_path(f.path) and f.status != "deleted"}
    sql_fds = {Path(f.path).stem.lower(): f for f in files if _is_model_path(f.path) and f.status != "deleted"}
    for fd in files:
        if fd.binary or fd.status == "deleted":
            continue
        if _is_model_path(fd.path):
            new = repo.facts(fd.path)
            if new is None:
                continue
            old_raw = view.read_old(fd.old_path or fd.path) if fd.status != "added" else None
            old = extract(fd.path, old_raw, repo.macros) if old_raw is not None else None
            chk.parse_and_syntax(fd, old, new)
            chk.hardcoded(fd, new)
            chk.surrogate_key(fd, new)
            chk.bare_filter_macro(fd, new)
            if not new.parsed:
                continue
            chk.joins(fd, old, new)
            chk.nullable_use(fd, old, new)
            if old is not None and old.parsed:
                chk.row_shape(fd, old, new)
                chk.casts(fd, old, new)
                chk.dropped_with_readers(fd, old, new)
            elif old is None:
                chk.row_shape(fd, extract(fd.path, "", repo.macros), new)
                chk.casts(fd, extract(fd.path, "", repo.macros), new)
        elif fd.path.endswith(".sql") and ("/macros/" in "/" + fd.path or fd.path.startswith("macros/")):
            chk.macro_dedup(fd)
    names = set(sql_fds)
    for fd in yml_fds.values():
        text = view.read(fd.path) or ""
        for d in parse_schema(fd.path, text) or []:
            names.add(d.name.lower())
    for name in sorted(names):
        if repo.schema.get(name) is not None:
            chk.documented_for_model(name, sql_fds.get(name), yml_fds)
    # a dedup that left one file and appeared in another file of the same change moved, it was not dropped
    came = Counter((p, k) for p, k in ((p, frozenset(x.split(".")[-1].lower() for x in part)) for p, part in chk.dedup_came))
    gone = Counter((p, k) for p, k in ((p, frozenset(x.split(".")[-1].lower() for x in part)) for p, part in chk.dedup_gone))
    drop: set[int] = set()
    for f in chk.out:
        if f.rule not in ("dedup_removed", "dedup_added"):
            continue
        m = re.search(r"\[(.*?)\]", f.title)
        part = frozenset(x.strip(" '\"").lower() for x in m.group(1).split(",") if x.strip()) if m else frozenset()
        other = came if f.rule == "dedup_removed" else gone
        hit = next((k for k in other if k[1] == part and k[0] != f.path and other[k] > 0), None)
        if hit:
            other[hit] -= 1
            drop.add(id(f))
            chk.note(f.path, f.line, f.rule, f"a dedup over {sorted(part)} " + ("left this file and a file in the same change gained one" if f.rule == "dedup_removed" else "arrived here and a file in the same change lost one") + ", so it moved")
    chk.out = [f for f in chk.out if id(f) not in drop]
    seen: set[tuple[str, str, int | None, str]] = set()
    out = []
    for f in chk.out:
        key = (f.rule, f.path, f.line, f.title)
        if key not in seen:
            seen.add(key)
            out.append(f)
    for u in repo.schema.unreadable:
        if u in yml_fds:
            chk.note(u, None, "schema_unreadable", f"{u} did not parse as YAML, so what it says about columns and tests was not used")
    return Result(out, chk.unresolved)


__all__ = ["Finding", "GitView", "Result", "TextView", "View", "check_change", "check_change_full", "Repo", "RULES_VERSION", "ModelDoc", "ColumnDoc", "build_index"]
