"""BigQuery accepts a filter inside an aggregate call, ``COUNT(DISTINCT x WHERE cond)``. sqlglot does not parse it, which
left two of the 14 models changed in cal-itp/data-infra #5827 as a bare "modify" (``pipelinebench/results/calitp-shadow-2026-11.md``).
The excerpts below are shortened from those models."""

from fathom.changes import ops_from_sql_change
from fathom.lineage import bigquery_aggregate_filter, column_lineage, normalize_sql, parse_model

OLD = """with trips as (
    select feed_key, service_date, count(distinct trip_instance_key) as n_trips
    from {{ ref('fct_scheduled_trips') }}
    group by 1, 2
)
select feed_key, service_date, n_trips from trips
"""
NEW = """with trips as (
    select
        feed_key,
        service_date,
        count(distinct trip_instance_key) as n_trips,
        COALESCE(COUNT(DISTINCT trip_instance_key WHERE is_gtfs_flex_trip = True), 0) AS n_flex_trips
    from {{ ref('fct_scheduled_trips') }}
    group by 1, 2
)
select feed_key, service_date, n_trips, COALESCE(trips.n_flex_trips, 0) AS n_flex_trips from trips
"""
SCHEMA = {"fct_scheduled_trips": {"feed_key": "", "service_date": "", "trip_instance_key": "", "is_gtfs_flex_trip": ""}}


def test_aggregate_filter_change_yields_typed_ops_not_a_parse_note():
    ops = ops_from_sql_change("model:m", "m.sql", OLD, NEW, SCHEMA, dialect="bigquery")
    assert not [o for o in ops if "did not parse" in str(o.details.get("note", ""))]
    assert [(o.kind, o.column) for o in ops if o.kind == "add_column"] == [("add_column", "n_flex_trips")]
    assert not [o for o in ops if o.kind == "change_filter"]  # the in-call WHERE is not a row filter


def test_aggregate_filter_lineage_includes_counted_and_filter_columns():
    cl = column_lineage(NEW.replace("{{ ref('fct_scheduled_trips') }}", "fct_scheduled_trips"), SCHEMA, dialect="bigquery")
    assert cl.error is None and "n_flex_trips" in cl.columns
    assert {("fct_scheduled_trips", "trip_instance_key"), ("fct_scheduled_trips", "is_gtfs_flex_trip")} <= cl.derives["n_flex_trips"]


def test_rewrite_is_paren_aware_and_leaves_other_sql_alone():
    assert bigquery_aggregate_filter("select count(distinct a where b = 'x where y') from t") == "select count(distinct IF(b = 'x where y', a, NULL)) from t"
    assert bigquery_aggregate_filter("select sum(coalesce(a, 0) where f(b) > 1) from t") == "select sum(IF(f(b) > 1, coalesce(a, 0), NULL)) from t"
    plain = "select a from t where a in (select b from u where b > 1) and exists (select 1 from v where v.k = t.k)"
    assert bigquery_aggregate_filter(plain) == plain


def test_other_dialects_are_untouched():
    sql = "select count(distinct a where b) from t"
    assert normalize_sql(sql, "duckdb") == sql
    assert normalize_sql(sql, "bigquery") != sql
    parse_model(normalize_sql(sql, "bigquery"), "bigquery")


FILTER_OLD = """with base as (
    select feed_key, trip_instance_key, is_flex from {{ ref('fct_scheduled_trips') }}
    where service_date < current_date()
),
agg as (
    select feed_key, count(distinct trip_instance_key) as n_trips
    from base
    group by 1
)
select feed_key, n_trips from agg
"""
FILTER_NEW = FILTER_OLD.replace(
    "count(distinct trip_instance_key) as n_trips",
    "count(distinct trip_instance_key) as n_trips, count(distinct trip_instance_key where is_flex = true) as n_flex",
).replace("select feed_key, n_trips from agg", "select feed_key, n_trips, n_flex from agg")


def test_row_filters_unchanged_means_no_change_filter_when_only_select_columns_change():
    ops = ops_from_sql_change("model:m", "m.sql", FILTER_OLD, FILTER_NEW, {}, dialect="bigquery")
    assert [o.column for o in ops if o.kind == "add_column"] == ["n_flex"]
    assert not [o for o in ops if o.kind == "change_filter"]


def test_a_real_row_filter_change_is_still_a_change_filter():
    changed = FILTER_NEW.replace("service_date < current_date()", "service_date < date '2026-01-01'")
    assert [o for o in ops_from_sql_change("model:m", "m.sql", FILTER_NEW, changed, {}, dialect="bigquery") if o.kind == "change_filter"]


def test_comments_are_skipped_not_rewritten_or_read_as_strings():
    assert bigquery_aggregate_filter("select count(a /* where */) from t") == "select count(a /* where */) from t"
    sql = "select 1 -- don't\n, count(a where b) from t"
    assert bigquery_aggregate_filter(sql) == "select 1 -- don't\n, count(IF(b, a, NULL)) from t"


def test_count_star_with_filter_counts_rows():
    assert bigquery_aggregate_filter("select count(* where b) from t") == "select count(IF(b, 1, NULL)) from t"
    assert bigquery_aggregate_filter("select count(distinct * where b) from t") == "select count(distinct IF(b, 1, NULL)) from t"


def test_nulls_modifiers_order_by_and_limit_are_left_as_written():
    for sql in ("select array_agg(x ignore nulls where c) from t", "select array_agg(x respect nulls where c) from t", "select array_agg(x order by y where c) from t", "select array_agg(x where c limit 2) from t"):
        assert bigquery_aggregate_filter(sql) == sql


def test_line_comments_in_operand_or_condition_do_not_swallow_the_rewrite():
    import sqlglot

    for sql in (
        "select count(a -- note\n where b) from t",
        "select count(a where b -- x\n) from t",
        "select count(a # note\n where b) from t",
        "select count(a where b # x\n) from t",
    ):
        out = bigquery_aggregate_filter(sql)
        expr = sqlglot.parse_one(out, read="bigquery")
        assert {c.name for c in expr.find_all(sqlglot.exp.Column)} == {"a", "b"}, out
