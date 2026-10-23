"""Executed confirmation: a warning's failing case run as a read-only query against the warehouse."""

import duckdb
import pytest

from orion.confirm import Refused, Warehouse, confirm, safe_select, tables_read
from orion.providers import StubProvider


@pytest.fixture()
def warehouse(tmp_path):
    path = tmp_path / "wh.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE orders AS SELECT * FROM (VALUES (1, 'paid', 'USD'), (2, 'test', 'USD'), (3, 'paid', NULL)) t(id, status, currency)")
    con.close()
    wh = Warehouse(path)
    yield wh
    wh.close()


@pytest.mark.parametrize("sql", [
    "DELETE FROM orders",
    "SELECT 1; DROP TABLE orders",
    "CREATE TABLE x AS SELECT 1",
    "SELECT * FROM read_csv('/etc/passwd')",
    "SELECT * FROM '/etc/passwd'",
    "COPY orders TO '/tmp/out.csv'",
    "SELECT getenv('HOME')",
    "PRAGMA database_list",
])
def test_only_a_single_select_over_tables_is_run(sql):
    with pytest.raises(Refused):
        safe_select(sql)


def test_the_connection_itself_cannot_write_or_reach_files(warehouse, tmp_path):
    with pytest.raises(duckdb.Error):
        warehouse.con.execute("CREATE TABLE x (a INT)")
    (tmp_path / "secret.csv").write_text("a\n1\n")
    with pytest.raises(duckdb.Error):
        warehouse.run(f"SELECT * FROM read_csv('{tmp_path / 'secret.csv'}')")
    with pytest.raises(duckdb.Error):
        warehouse.con.execute("SET enable_external_access = true")


def test_confirm_runs_each_check_and_records_what_came_back(warehouse):
    findings = [
        {"path": "m.sql", "line": 3, "title": "null currency", "body": "b", "failure": "an order with no currency"},
        {"path": "m.sql", "line": 4, "title": "refunds", "body": "b", "failure": "a refunded order"},
        {"path": "m.sql", "line": 5, "title": "bad", "body": "b", "failure": "x"},
        {"path": "m.sql", "line": 6, "title": "vague", "body": "b", "failure": "y"},
    ]
    seen = []

    def respond(system, user):
        seen.append(user)
        return {"checks": [
            {"index": 0, "sql": "select id from main.orders where currency is null", "note": ""},
            {"index": 1, "sql": "select id from main.orders where status = 'refunded'", "note": ""},
            {"index": 2, "sql": "drop table orders", "note": ""},
            {"index": 3, "sql": "", "note": "needs tomorrow's data"},
        ]}

    confirm(StubProvider(respond), warehouse, findings, tables={"orders"})
    assert "main.orders(id INTEGER, status VARCHAR, currency VARCHAR)" in seen[0]
    assert findings[0]["confirmation"]["status"] == "confirmed" and findings[0]["confirmation"]["rows"] == 1 and findings[0]["confirmation"]["sample"] == [["3"]]
    assert findings[1]["confirmation"]["status"] == "not_confirmed"
    assert findings[2]["confirmation"]["status"] == "unchecked" and "refused" in findings[2]["confirmation"]["note"]
    assert findings[3]["confirmation"] == {"status": "unchecked", "note": "needs tomorrow's data"}


def test_gate_uses_confirmation(warehouse):
    from orion.diffparse import parse
    from orion.review import ReviewContext, gate_severity

    diff = "diff --git a/m.sql b/m.sql\n--- a/m.sql\n+++ b/m.sql\n@@ -1,2 +1,2 @@\n select id\n-from orders where status != 'test'\n+from orders\n"
    ctx = ReviewContext(parse(diff), None)
    base = {"source": "model", "status": "valid", "severity": "warn", "path": "m.sql", "line": 2, "title": "t", "body": "b", "failure": "f", "evidence": ["hunk:m.sql:2"], "notes": []}
    yes = {**base, "notes": [], "confirmation": {"status": "confirmed", "sql": "SELECT id FROM orders WHERE status = 'test'"}}
    no = {**base, "notes": [], "evidence": ["hunk:m.sql:2"], "confirmation": {"status": "not_confirmed", "sql": "SELECT 1 WHERE FALSE"}}
    gate_severity(ctx, [yes, no])
    assert yes["severity"] == "warn" and yes["escalation"][0].startswith("exec:")
    assert no["severity"] == "inform" and "returned no rows" in no["notes"][0]


def test_a_query_that_does_not_tokenize_is_refused_not_raised():
    with pytest.raises(Refused):
        safe_select("SELECT id FROM orders WHERE note = 'unterminated")


def test_a_provider_answer_off_schema_leaves_findings_unchecked(warehouse):
    findings = [{"path": "m.sql", "line": 1, "title": "t", "body": "b", "failure": "f"}]
    confirm(StubProvider(["not", "a", "dict"]), warehouse, findings)
    assert findings[0]["confirmation"]["status"] == "unchecked"


def test_a_check_that_reads_no_table_confirms_nothing():
    assert tables_read("SELECT 1") == set()
    assert tables_read("WITH t AS (SELECT 1 AS a) SELECT a FROM t") == set(), "a CTE is not a warehouse table"
    assert tables_read("WITH t AS (SELECT * FROM stg_orders) SELECT * FROM t") == {"stg_orders"}
    assert tables_read("SELECT * FROM main.stg_orders o JOIN fct_orders USING (order_id)") == {"stg_orders", "fct_orders"}
