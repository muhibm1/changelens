"""Column-level lineage over dbt model SQL with sqlglot.

Jinja is reduced to plain SQL by substituting ``ref()``, ``source()`` and ``this`` with
table names and stripping ``config()`` blocks; any other expression becomes ``null``.
That covers the large majority of dbt models; anything sqlglot cannot parse is reported, not guessed.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, SqlglotError
from sqlglot.lineage import lineage
from sqlglot.optimizer.qualify import qualify

# ref('model'), ref('package', 'model'), ref('model', v=2): the model is the last quoted positional argument
REF_RE = re.compile(r"\{\{\s*ref\(\s*(?:['\"][^'\"]+['\"]\s*,\s*)?['\"]([^'\"]+)['\"]\s*(?:,\s*(?:v|version)\s*=\s*[^)]*)?\)\s*\}\}")
SOURCE_RE = re.compile(r"\{\{\s*source\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)\s*\}\}")
CONFIG_RE = re.compile(r"\{\{\s*config\(.*?\)\s*\}\}(?!['\"])", re.DOTALL)  # not the end of a Jinja string inside the config
THIS_RE = re.compile(r"\{\{\s*this\s*\}\}")
# a macro that is a whole CTE body, `name as ({{ spine() }})`, keeps the CTE parseable
CTE_MACRO_RE = re.compile(r"(\bas\s*\(\s*)\{\{(?:(?!\}\}).)*\}\}(\s*\))", re.DOTALL | re.IGNORECASE)
EXPR_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)
JINJA_RE = re.compile(r"\{[{%#].*?[}%#]\}", re.DOTALL)


def source_table_name(source_name: str, table: str) -> str:
    return f"{source_name}.{table}"


def dejinja(raw_sql: str, this: str | None = None) -> str:
    """``this`` is the model's own name, which an incremental filter reads (``from {{ this }}``). Any other expression,
    a macro call or a ``var()``, becomes ``null``: it keeps the statement parseable and adds no column lineage."""
    sql = CONFIG_RE.sub("", raw_sql)
    sql = REF_RE.sub(lambda m: m.group(1), sql)
    sql = SOURCE_RE.sub(lambda m: f"{m.group(1)}__{m.group(2)}", sql)
    sql = THIS_RE.sub(this or "this", sql)
    sql = CTE_MACRO_RE.sub(r"\1select null\2", sql)
    sql = EXPR_RE.sub("null", sql)
    sql = JINJA_RE.sub("", sql)
    return sql.strip()


def schema_key(name: str) -> str:
    """Sources are keyed ``raw.raw_events`` in the graph but ``raw__raw_events`` in SQL."""
    return name.replace(".", "__")


def graph_name(key: str) -> str:
    return key.replace("__", ".", 1) if "__" in key else key


@dataclass
class ColumnLineage:
    columns: list[str] = field(default_factory=list)
    derives: dict[str, set[tuple[str, str]]] = field(default_factory=dict)  # col -> {(table, col)}
    error: str | None = None


EPHEMERAL_PREFIX = "__dbt__cte__"


def parse_model(sql: str, dialect: str = "duckdb") -> exp.Expr:
    """Parse model SQL. dbt's compiled SQL inlines an ephemeral model as a ``__dbt__cte__<name>`` CTE; those CTEs are
    dropped and their references point at ``<name>`` again, so lineage stops at the ephemeral model as it does for raw SQL."""
    expr = sqlglot.parse_one(sql, read=dialect)
    for cte in list(expr.find_all(exp.CTE)):
        if cte.alias.startswith(EPHEMERAL_PREFIX):
            cte.pop()
    for t in expr.find_all(exp.Table):
        if t.name.startswith(EPHEMERAL_PREFIX):
            t.set("this", exp.to_identifier(t.name[len(EPHEMERAL_PREFIX):]))
    with_ = expr.args.get("with") or expr.args.get("with_")
    if isinstance(with_, exp.With) and not with_.expressions:
        with_.pop()
    return expr


def tables_schema(expr: exp.Expr, schema: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    """Only the tables the query reads: sqlglot normalises the whole schema on every call, which is quadratic on a large project.
    Column types are left out (``UNKNOWN``): lineage does not need them, and an empty or warehouse-specific type string
    makes sqlglot's type annotation fail for the whole query."""
    names = {t.name for t in expr.find_all(exp.Table)}
    return {k: dict.fromkeys(v, "UNKNOWN") for k, v in schema.items() if k in names or k.lower() in names}


def output_columns(sql: str, schema: dict[str, dict[str, str]], dialect: str = "duckdb") -> list[str]:
    expr = parse_model(sql, dialect)
    # an unnamed null in the final select is a Jinja expression standing for columns (`{{ dbt_utils.star(...) }}`):
    # the model's columns are unknown, which is reported rather than read as a model without that column
    if any(isinstance(s, exp.Null) for s in getattr(expr, "selects", [])):
        raise ParseError("a selected expression is Jinja Orion does not expand, so the model's columns are not known")
    with contextlib.suppress(SqlglotError):
        expr = qualify(expr, schema=tables_schema(expr, schema), dialect=dialect, validate_qualify_columns=False)
    return [c for c in expr.named_selects if c]


def _leaves(node) -> set[tuple[str, str]]:
    leaves: set[tuple[str, str]] = set()
    for n in node.walk():
        if n.downstream:
            continue
        if isinstance(n.source, exp.Table):
            leaves.add((graph_name(n.source.name), n.name.split(".")[-1]))
    return leaves


def column_lineage(sql: str, schema: dict[str, dict[str, str]], dialect: str = "duckdb") -> ColumnLineage:
    out = ColumnLineage()
    try:
        expr = parse_model(sql, dialect)
        out.columns = output_columns(sql, schema, dialect)
    except SqlglotError as e:
        out.error = f"parse: {e}"
        return out
    sub = tables_schema(expr, schema)
    try:
        # One qualification for every output column, with work shared across columns.
        nodes = lineage(None, expr, schema=sub, dialect=dialect)
    except SqlglotError:
        nodes = None
    if isinstance(nodes, dict):
        for col in out.columns:
            if col in nodes:
                out.derives[col] = _leaves(nodes[col])
        return out
    for col in out.columns:
        try:
            node = lineage(col, expr, schema=sub, dialect=dialect)
        except SqlglotError as e:
            out.error = f"lineage {col}: {e}"
            continue
        out.derives[col] = _leaves(node)
    return out
