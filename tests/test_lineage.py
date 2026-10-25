from fathom.lineage import column_lineage, dejinja

SQL = """
{{ config(materialized='table') }}
select
    o.order_id,
    o.amount * (1 - o.discount_pct / 100.0) as net_amount,
    count(p.payment_id) as payment_count
from {{ ref('stg_orders') }} o
left join {{ source('raw', 'raw_payments') }} p on p.order_id = o.order_id
group by 1, 2
"""


def test_dejinja_substitutes_refs_and_sources():
    sql = dejinja(SQL)
    assert "config" not in sql
    assert "from stg_orders o" in sql
    assert "raw__raw_payments p" in sql


def test_column_lineage_reaches_leaf_columns():
    schema = {
        "stg_orders": {"order_id": "int", "amount": "double", "discount_pct": "double"},
        "raw__raw_payments": {"payment_id": "int", "order_id": "int"},
    }
    lin = column_lineage(dejinja(SQL), schema)
    assert lin.error is None
    assert lin.columns == ["order_id", "net_amount", "payment_count"]
    assert lin.derives["net_amount"] == {("stg_orders", "amount"), ("stg_orders", "discount_pct")}
    assert lin.derives["payment_count"] == {("raw.raw_payments", "payment_id")}


def test_dejinja_reads_versioned_and_package_refs_this_and_macros():
    sql = dejinja(
        """with spine as (
    {{ month_spine() }}
), recent as (
    select o.id, {{ dbt_utils.generate_surrogate_key(['o.id']) }} as k
    from {{ ref('shop', 'stg_orders') }} o
    join {{ ref("stg_customers", v=2) }} c using (id)
    {% if is_incremental() %}
    where o.d > (select max(d) from {{ this }}) and o.d < to_date('{{ var("through") }}')
    {% endif %}
)
select * from recent""",
        "fct_orders",
    )
    assert "spine as (\n    select null\n)" in sql
    assert "from stg_orders o" in sql and "join stg_customers c" in sql
    assert "from fct_orders)" in sql and "null as k" in sql
    lin = column_lineage(sql, {"stg_orders": {"id": "int", "d": "date"}, "stg_customers": {"id": "int"}, "fct_orders": {"d": "date"}})
    assert lin.error is None
    assert lin.columns == ["id", "k"]


def test_a_macro_standing_for_columns_is_reported_not_read_as_no_columns():
    sql = dejinja("select {{ dbt_utils.star(ref('stg_orders')) }}, 1 as n from {{ ref('stg_orders') }}")
    lin = column_lineage(sql, {"stg_orders": {"order_id": "int"}})
    assert lin.error and "Jinja" in lin.error and lin.columns == []
