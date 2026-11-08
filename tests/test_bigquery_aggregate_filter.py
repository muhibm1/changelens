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
