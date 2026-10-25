"""Executed confirmation: run the failing case a warning names, against the warehouse, before it may stand at warn.

A model warning must name a concrete failing case (``failure``). For a change to a data model that case is usually a
condition in the data: rows with a null key, a status the new filter drops, a currency the join does not cover. Fathom
asks the model for one read-only query that returns rows exactly when that condition exists, checks the query in
code (a single SELECT, no file, network or extension functions), runs it on a read-only connection with external
access off and a time limit, and records what came back. Rows mean the case is real in today's data (``confirmed``);
no rows mean it is not (``not_confirmed``); a query that does not parse, is refused or fails is ``unchecked``.

The model only writes the query. Whether it returned rows is decided by running it, and the query, its row count and
a sample are kept on the finding so a reader can run it again.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import duckdb
import sqlglot
from sqlglot import exp

MAX_TABLES = 60
SAMPLE_ROWS = 3
TIMEOUT_S = 10.0
# Table functions and scalar functions that reach outside the database file.
BLOCKED_FUNCS = {"sniff_csv", "read_xlsx", "st_read", "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "read_json_objects", "read_text", "read_blob", "read_ndjson", "glob", "parquet_scan", "parquet_metadata", "parquet_schema", "sqlite_scan", "postgres_scan", "mysql_scan", "iceberg_scan", "delta_scan", "query_table", "query", "getenv", "load_extension", "install_extension"}

CONFIRM_SYSTEM = """You turn a code reviewer's warnings about a change to data models into checks against the current warehouse.
For each warning, write one read-only DuckDB SELECT over the tables listed that returns at least one row exactly when the failing case the warning names exists in today's data (for example, the rows the changed filter would now drop or let through, or the keys the changed join would lose).
Use only the listed tables and columns. No DDL, no writes, no file or network functions. When the case cannot be expressed as such a query over these tables, return an empty `sql` and say why in `note`."""

CONFIRM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"index": {"type": "integer"}, "sql": {"type": "string"}, "note": {"type": "string"}},
                "required": ["index", "sql", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["checks"],
    "additionalProperties": False,
}


def _index(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class Refused(ValueError):
    pass


def tables_read(sql: str) -> set[str]:
    """The bare names of the tables a checked query reads."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}  # a CTE is the query's own rows, not the warehouse's
    return {t.name.lower() for t in tree.find_all(exp.Table) if t.name and not (not t.db and t.name.lower() in ctes)}


def safe_select(sql: str) -> str:
    """``sql`` as one SELECT that reads only database tables, or Refused saying why."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except sqlglot.errors.SqlglotError as e:  # parse and tokenizer errors alike
        raise Refused(f"does not parse: {str(e)[:200]}") from e
    if len(statements) != 1:
        raise Refused(f"expected one statement, got {len(statements)}")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise Refused(f"not a SELECT ({type(tree).__name__})")
    for node in tree.walk():
        if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Copy, exp.Pragma, exp.Set)):
            raise Refused(f"contains {type(node).__name__}")
        if isinstance(node, exp.Func):
            name = (node.sql_name() if not isinstance(node, exp.Anonymous) else node.name).lower()
            if name in BLOCKED_FUNCS:
                raise Refused(f"uses {name}, which reads outside the warehouse")
        if isinstance(node, exp.Table) and (isinstance(node.this, exp.Literal) or any(ch in node.name for ch in "/\\.:*")):
            raise Refused("reads a file path as a table")
    return tree.sql(dialect="duckdb")


class Warehouse:
    """A DuckDB warehouse file opened read-only, with external access and extension loading off."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.con = duckdb.connect(str(self.path), read_only=True, config={"enable_external_access": False, "autoinstall_known_extensions": False, "autoload_known_extensions": False})
        self._lock = threading.Lock()
        try:
            self.con.execute("SET lock_configuration = true")
        except duckdb.Error:
            self.con.close()
            raise

    def tables(self, names: set[str] | None = None) -> list[str]:
        """``schema.table(col type, ...)`` lines, the ones named in ``names`` first."""
        rows = self.con.execute("SELECT table_schema, table_name, column_name, data_type FROM information_schema.columns ORDER BY table_schema, table_name, ordinal_position").fetchall()
        cols: dict[str, list[str]] = {}
        for schema, table, col, typ in rows:
            cols.setdefault(f"{schema}.{table}", []).append(f"{col} {typ}")
        order = sorted(cols, key=lambda t: (not names or t.split(".", 1)[1] not in names, t))
        return [f"{t}({', '.join(cols[t])})" for t in order[:MAX_TABLES]]

    def run(self, sql: str, timeout: float = TIMEOUT_S) -> tuple[int, list[list[Any]]]:
        """Row count (capped at ``SAMPLE_ROWS`` + 1 read) and a sample; raises duckdb.Error on failure or timeout."""
        done = threading.Event()

        def stop() -> None:
            # The check and the interrupt hold the lock that marks the query done, so a late timer interrupts this
            # query or an idle connection, never the next query.
            with self._lock:
                if not done.is_set():
                    self.con.interrupt()

        timer = threading.Timer(timeout, stop)
        timer.start()
        try:
            rows = self.con.execute(f"SELECT * FROM ({sql}) AS fathom_check LIMIT {SAMPLE_ROWS + 1}").fetchall()
        finally:
            with self._lock:
                done.set()
            timer.cancel()
        return len(rows), [[str(v) for v in r] for r in rows[:SAMPLE_ROWS]]

    def close(self) -> None:
        self.con.close()


def confirm(provider: Any, warehouse: Warehouse, findings: list[dict[str, Any]], *, tables: set[str] | None = None, facts: str = "") -> dict[str, Any] | None:
    """Ask for and run one check per finding; each gets ``confirmation`` {status, sql, rows, sample, note}. Returns the
    model call's token usage. A provider error leaves every finding ``unchecked``."""
    if not findings:
        return None
    try:
        tables = warehouse.tables(tables)
    except duckdb.Error as e:
        for f in findings:
            f["confirmation"] = {"status": "unchecked", "note": f"warehouse tables not read: {str(e)[:200]}"}
        return None
    listed = "\n\n".join(f"[{i}] {f['path']}:{f['line']} {f['title']}\n{f['body']}\nFails when: {f['failure']}" for i, f in enumerate(findings))
    user = "Warehouse tables:\n" + "\n".join(tables) + (f"\n\nSystem facts:\n{facts}" if facts else "") + f"\n\nWarnings:\n{listed}"
    try:
        out = provider.complete_json(CONFIRM_SYSTEM, user, CONFIRM_SCHEMA, schema_name="report_checks")
    except Exception as e:  # noqa: BLE001 - any failure of the model call (ProviderError, a transport error, an off-schema answer) leaves them unchecked
        for f in findings:
            f["confirmation"] = {"status": "unchecked", "note": f"no check written: {str(e)[:200]}"}
        return None
    by_index = {_index(c.get("index")): c for c in ((out.get("checks") if isinstance(out, dict) else None) or []) if isinstance(c, dict)}
    for i, f in enumerate(findings):
        c = by_index.get(i) or {}
        sql, note = str(c.get("sql") or "").strip(), str(c.get("note") or "").strip()[:300]
        if not sql:
            f["confirmation"] = {"status": "unchecked", "note": note or "no check written"}
            continue
        try:
            checked = safe_select(sql)
            # A query that reads no table cannot show the case exists in the data (``SELECT 1`` always returns a row).
            # It need not read the changed model itself: the new version is not built yet, so checks read its inputs.
            if not tables_read(checked):
                raise Refused("reads no table")
            n, sample = warehouse.run(checked)
        except Refused as e:
            f["confirmation"] = {"status": "unchecked", "sql": sql[:2000], "note": f"refused: {e}"}
            continue
        except duckdb.Error as e:
            f["confirmation"] = {"status": "unchecked", "sql": sql[:2000], "note": f"failed: {str(e)[:200]}"}
            continue
        f["confirmation"] = {"status": "confirmed" if n else "not_confirmed", "sql": checked, "rows": n if n <= SAMPLE_ROWS else f">{SAMPLE_ROWS}", "sample": sample, "note": note}
    return getattr(provider, "last_usage", None)
