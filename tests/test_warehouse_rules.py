"""The deterministic dbt rules: each fires on the change it describes and stays quiet on a harmless one."""

from __future__ import annotations

import difflib

from changelens.diffparse import parse
from changelens.warehouse_rules import TextView, check_change, check_change_full
from changelens.warehouse_schema import build_index

M = "models/m.sql"
SCHEMA = "models/schema.yml"


def _diff(old: dict[str, str], new: dict[str, str]) -> str:
    out = []
    for path in sorted(set(old) | set(new)):
        a, b = old.get(path, ""), new.get(path, "")
        if a == b:
            continue
        head = f"diff --git a/{path} b/{path}\n"
        if path not in old:
            head += "new file mode 100644\n"
        out.append(head + "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), f"a/{path}", f"b/{path}", n=3)))
    return "".join(out)


def run(old: dict[str, str], new: dict[str, str]):
    files = parse(_diff(old, new))
    return check_change(files, TextView({**old, **new}, old))


def rules(findings, severity: str | None = None) -> set[str]:
    return {f.rule for f in findings if severity is None or f.severity == severity}


ORDERS = """select o.order_id, o.amount, c.region
from {{ ref('orders') }} as o
left join {{ ref('customers') }} as c on o.customer_id = c.customer_id
"""

SCHEMA_UNIQUE = """models:
  - name: customers
    columns:
      - name: customer_id
        tests: [unique, not_null]
  - name: customers_history
    columns:
      - name: customer_id
"""


# ---- schema index ---------------------------------------------------------------------------------------
def test_schema_index_reads_columns_tests_and_keys():
    idx = build_index({SCHEMA: SCHEMA_UNIQUE})
    cust = idx.get("customers")
    assert cust is not None and cust.columns["customer_id"].not_null
    assert frozenset(["customer_id"]) in cust.unique_keys
    assert idx.get("customers_history").unique_keys == []


def test_schema_index_marks_unreadable_yaml():
    idx = build_index({SCHEMA: "models:\n  - name: {{ x }}\n   bad: ["})
    assert idx.unreadable == [SCHEMA]


# ---- row_count_change -----------------------------------------------------------------------------------
def test_join_on_declared_unique_key_is_quiet():
    new = {M: ORDERS, SCHEMA: SCHEMA_UNIQUE}
    assert not rules(run({SCHEMA: SCHEMA_UNIQUE}, new), "warn")


def test_join_key_compared_to_itself_warns():
    new = {M: ORDERS.replace("o.customer_id = c.customer_id", "c.customer_id = c.customer_id"), SCHEMA: SCHEMA_UNIQUE}
    assert "join_key_compared_to_itself" in rules(run({SCHEMA: SCHEMA_UNIQUE}, new), "warn")


def test_filter_removed_warns_and_other_filter_edit_does_not():
    old = {M: "select a, b, is_current from {{ ref('t') }} where is_current and b > 0\n"}
    gone = run(old, {M: "select a, b, is_current from {{ ref('t') }} where b > 0\n"})
    assert "filter_removed" in rules(gone)
    kept = run(old, {M: "select a, b, is_current from {{ ref('t') }} where is_current and b > 1\n"})
    assert "filter_removed" not in rules(kept)


def test_distinct_removed_flags_and_unrelated_change_does_not():
    old = {M: "select distinct a, b from {{ ref('t') }}\n"}
    assert "distinct_removed" in rules(run(old, {M: "select a, b from {{ ref('t') }}\n"}))
    assert "distinct_removed" not in rules(run(old, {M: "select distinct a, b, c from {{ ref('t') }}\n"}))


def test_dedup_removed_and_key_changed():
    old = {M: "select * from {{ ref('t') }}\nqualify row_number() over (partition by id order by ts desc) = 1\n"}
    assert "dedup_removed" in rules(run(old, {M: "select * from {{ ref('t') }}\n"}))
    changed = run(old, {M: "select * from {{ ref('t') }}\nqualify row_number() over (partition by id, region order by ts desc) = 1\n"})
    assert "dedup_key_changed" in rules(changed)
    same = run(old, {M: "select * from {{ ref('t') }}\nqualify row_number() over (partition by id order by ts asc) = 1\n"})
    assert not rules(same) & {"dedup_removed", "dedup_key_changed"}


def test_group_by_key_changed_when_declared_key_not_covered():
    schema = "models:\n  - name: m\n    columns:\n      - name: a\n        tests: [unique]\n"
    old = {M: "select a, b, count(*) n from {{ ref('t') }} group by a, b\n", SCHEMA: schema}
    new = {M: "select a, b, count(*) n from {{ ref('t') }} group by b\n", SCHEMA: schema}
    assert "group_by_key_changed" in rules(run(old, new))
    assert "group_by_key_changed" not in rules(run(old, old))


def test_join_to_multiplied_relation_warns():
    old = {M: "select 1 as x\n"}
    sql = (
        "with pairs as (\n  select a.id, b.id as other\n  from {{ ref('a') }} a, {{ ref('b') }} b\n  where a.k = b.k\n)\n"
        "select p.id, t.v\nfrom {{ ref('t') }} t\nleft join pairs p on t.id = p.id\n"
    )
    assert "join_to_multiplied_relation" in rules(run(old, {M: sql}), "warn")
    safe = sql.replace("from {{ ref('a') }} a, {{ ref('b') }} b\n  where a.k = b.k", "from {{ ref('a') }} a\n  group by a.id")
    assert "join_to_multiplied_relation" not in rules(run(old, {M: safe}))


def test_join_type_changed_left_to_inner_warns():
    old = {M: ORDERS, SCHEMA: SCHEMA_UNIQUE}
    new = {M: ORDERS.replace("left join", "inner join"), SCHEMA: SCHEMA_UNIQUE}
    assert "join_type_changed" in rules(run(old, new), "warn")
    assert "join_type_changed" not in rules(run(old, old))


# ---- nullability_or_cast --------------------------------------------------------------------------------
def test_cast_changed_flags_a_new_type_only():
    old = {M: "select cast(x as int) as x from {{ ref('t') }}\n"}
    assert "cast_changed" in rules(run(old, {M: "select cast(x as string) as x from {{ ref('t') }}\n"}))
    assert "cast_changed" not in rules(run(old, {M: "select cast(x as int) as x, y from {{ ref('t') }}\n"}))


def test_coalesce_removed_flags_and_kept_coalesce_does_not():
    old = {M: "select coalesce(a, 0) as a from {{ ref('t') }}\n"}
    assert "coalesce_removed" in rules(run(old, {M: "select a from {{ ref('t') }}\n"}))
    assert "coalesce_removed" not in rules(run(old, {M: "select coalesce(a, 1) as a from {{ ref('t') }}\n"}))


def test_safe_cast_to_timestamp_is_flagged_as_inform():
    old = {M: "select ts from {{ ref('t') }}\n"}
    found = run(old, {M: "select safe_cast(ts as timestamp) as ts from {{ ref('t') }}\n"})
    assert "safe_cast_hides_failures" in rules(found, "inform")
    assert "safe_cast_hides_failures" not in rules(run(old, {M: "select cast(ts as timestamp) as ts from {{ ref('t') }}\n"}))


# ---- documented_column_not_selected ---------------------------------------------------------------------
DOC = "models:\n  - name: m\n    columns:\n      - name: a\n        description: first\n      - name: b\n        description: second\n        tests: [not_null]\n"


def test_documented_column_dropped_from_select_is_flagged():
    old = {M: "select a, b from {{ ref('t') }}\n", SCHEMA: DOC}
    new = {M: "select a from {{ ref('t') }}\n", SCHEMA: DOC}
    found = [f for f in run(old, new) if f.rule == "documented_column_not_selected"]
    assert found and "b" in (found[0].title + found[0].body)


def test_documented_columns_all_selected_is_quiet():
    old = {M: "select a, b from {{ ref('t') }}\n", SCHEMA: DOC}
    new = {M: "select a, b, 1 as c from {{ ref('t') }}\n", SCHEMA: DOC}
    assert "documented_column_not_selected" not in rules(run(old, new))


def test_dropped_column_still_read_by_another_model():
    old = {M: "select a, b from {{ ref('t') }}\n", "models/n.sql": "select b from {{ ref('m') }}\n"}
    new = {M: "select a from {{ ref('t') }}\n", "models/n.sql": "select b from {{ ref('m') }}\n"}
    assert "dropped_column_still_read" in rules(run(old, new))
    old2 = {M: "select a, b from {{ ref('t') }}\n", "models/n.sql": "select a from {{ ref('m') }}\n"}
    new2 = {M: "select a from {{ ref('t') }}\n", "models/n.sql": "select a from {{ ref('m') }}\n"}
    assert "dropped_column_still_read" not in rules(run(old2, new2))


# ---- the rest -------------------------------------------------------------------------------------------
def test_hardcoded_relation_only_for_three_part_names():
    old = {M: "select a from {{ ref('t') }}\n"}
    assert "hardcoded_relation" in rules(run(old, {M: "select a from `proj.ds.t`\n"}))
    assert "hardcoded_relation" not in rules(run(old, {M: "select a from ds.t\n"}))


def test_dangling_alias_is_flagged():
    old = {M: "select a from {{ ref('t') }}\n"}
    assert "dangling_alias" in rules(run(old, {M: "select a as, b from {{ ref('t') }}\n"}))


def test_jinja_does_not_break_parsing():
    sql = "{{ config(post_hook=[\"{{ foo() }}\"]) }}\nselect a from {{ ref('t') }}\n{% if is_incremental() %}where a > 1{% endif %}\n"
    assert "sql_stopped_parsing" not in rules(run({M: "select a from {{ ref('t') }}\n"}, {M: sql}))


def test_unresolved_notes_are_reported_not_findings():
    old = {M: "select 1 as a\n"}
    new = {M: "select o.a from {{ ref('orders') }} o join {{ ref('mystery') }} m on o.k = m.k\n"}
    res = check_change_full(parse(_diff(old, new)), TextView(new, old))
    assert res.unresolved
    assert all(set(n) >= {"path", "rule", "reason"} for n in res.unresolved)


def test_finding_converts_to_review_shape():
    old = {M: "select distinct a from {{ ref('t') }}\n"}
    f = run(old, {M: "select a from {{ ref('t') }}\n"})[0].to_review()
    assert f["source"] == "deterministic" and f["path"] == M and f["evidence"]


# ---- refactors and moves stay silent ----------------------------------------------------------------------
def test_dedup_moved_to_another_file_is_silent():
    q = "qualify row_number() over (partition by id order by ts desc) = 1\n"
    old = {"models/a.sql": f"select * from {{{{ ref('t') }}}}\n{q}", "models/b.sql": "select * from {{ ref('a') }}\n"}
    new = {"models/a.sql": "select * from {{ ref('t') }}\n", "models/b.sql": f"select * from {{{{ ref('a') }}}}\n{q}"}
    assert not rules(run(old, new)) & {"dedup_removed", "dedup_added"}
    assert "dedup_removed" in rules(run(old, {**new, "models/b.sql": new["models/b.sql"].replace("id order", "k order")}))


def test_content_hash_dedup_is_not_a_finding():
    old = {M: "select * from {{ ref('t') }}\n"}
    new = {M: "select * from {{ ref('t') }}\nqualify row_number() over (partition by _content_hash order by ts desc) = 1\n"}
    assert "dedup_added" not in rules(run(old, new))


def test_rewritten_file_does_not_report_a_moved_filter():
    body = "".join(f"  c{i},\n" for i in range(10))
    old = {M: f"select\n{body}  a\nfrom {{{{ ref('t') }}}}\nwhere is_current\n"}
    new = {M: "select a, is_current from {{ ref('u') }}\n"}
    assert "filter_removed" not in rules(run(old, new))


def test_coalesce_inside_a_case_condition_is_not_a_removed_default():
    old = {M: "select case when coalesce(a, 0) > 0 then 'x' end as k from {{ ref('t') }}\n"}
    new = {M: "select case when a > 0 then 'x' end as k from {{ ref('t') }}\n"}
    assert "coalesce_removed" not in rules(run(old, new))


# ---- v3: references, casts, joins ------------------------------------------------------------------------
def test_column_missing_from_a_referenced_model_warns():
    old = {"models/home.sql": "select claim_id from {{ ref('base') }}\n", M: "select 1 as x\n"}
    sql = "select s.claim_id from {{ ref('base') }} s\nwhere not exists (select 1 from {{ ref('home') }} h where h.claim_id2 = s.claim_id)\n"
    found = run(old, {**old, M: sql})
    assert "unresolved_column" in rules(found, "warn")
    ok = run(old, {**old, M: sql.replace("claim_id2", "claim_id")})
    assert "unresolved_column" not in rules(ok)


def test_alias_not_in_scope_warns_but_struct_access_on_a_source_does_not():
    base = {"models/base.sql": "select claim_id, code from {{ source('a', 'b') }}\n"}
    bad = {**base, M: "select s.claim_id from {{ ref('base') }} s\nwhere med.code in ('1')\n"}
    assert "unresolved_alias" in rules(run(base, bad), "warn")
    struct = {M: "select header.timestamp from {{ source('ext', 'raw') }}\n"}
    assert "unresolved_alias" not in rules(run({}, struct))


def test_cast_of_a_text_literal_to_a_number_warns():
    old = {M: "select a from {{ ref('t') }}\n"}
    assert "cast_of_text_literal" in rules(run(old, {M: "select a from {{ ref('t') }} where try_cast('hcpcs_code' as integer) is not null\n"}), "warn")
    assert "cast_of_text_literal" not in rules(run(old, {M: "select a from {{ ref('t') }} where try_cast('12' as integer) is not null\n"}))


def test_cast_in_join_condition_and_concat_key():
    old = {M: "select a from {{ ref('t') }}\n"}
    sql = "select a from {{ ref('t') }} x join {{ ref('u') }} y on cast(x.code as numeric) = cast(y.code as numeric)\n"
    assert "cast_in_join_condition" in rules(run(old, {M: sql}), "inform")
    assert "cast_in_join_condition" not in rules(run(old, {M: sql.replace("cast(x.code as numeric) = cast(y.code as numeric)", "x.code = y.code")}))
    key = "select a || b || c as k from {{ ref('t') }}\n"
    assert "concat_key_with_nulls" in rules(run(old, {M: key}), "inform")
    assert "concat_key_with_nulls" not in rules(run(old, {M: "select concat(a, b, c) as k from {{ ref('t') }}\n"}))


def test_inner_join_added_on_a_left_joined_table():
    old = {M: "select 1 as x\n"}
    sql = "select a.id from {{ ref('a') }} a\nleft join {{ ref('b') }} b on a.id = b.id\ninner join {{ ref('c') }} c on b.cid = c.id\n"
    assert "inner_join_after_left_join" in rules(run(old, {M: sql}), "inform")
    assert "inner_join_after_left_join" not in rules(run(old, {M: sql.replace("left join", "inner join")}))


def test_join_on_columns_outside_a_declared_key_informs():
    schema = "models:\n  - name: dim\n    columns:\n      - name: dim_pk\n        tests: [unique]\n"
    old = {SCHEMA: schema, M: "select 1 as x\n"}
    sql = "select f.id from {{ ref('fact') }} f left join {{ ref('dim') }} d on f.block_id = d.block_id\n"
    new = {**old, M: sql, "models/dim.sql": "select 1 as dim_pk, 2 as block_id\n"}
    assert "join_on_non_key" in rules(run(old, new), "inform")
    assert "join_on_non_key" not in rules(run(old, {**new, M: sql.replace("f.block_id = d.block_id", "f.block_id = d.dim_pk")}))


def test_group_by_gaining_a_column_informs():
    old = {M: "with g as (select a, count(*) n from {{ ref('t') }} group by a)\nselect * from g\n"}
    new = {M: "with g as (select a, b, count(*) n from {{ ref('t') }} group by a, b)\nselect * from g\n"}
    assert "group_by_key_changed" in rules(run(old, new), "inform")


def test_jinja_if_else_branches_still_parse():
    sql = "select a from {{ ref('t') }}\n{% if target.type == 'duckdb' %}\nwhere try_cast('x' as integer) is not null\n{% else %}\nwhere {{ safe_cast('x', 'int') }} is not null\n{% endif %}\n"
    assert "cast_of_text_literal" in rules(run({M: "select a from {{ ref('t') }}\n"}, {M: sql}))


def test_same_rule_in_many_files_is_a_sweep_and_listed_once():
    old = {f"models/m{i}.sql": "select distinct a from {{ ref('t') }}\n" for i in range(6)}
    new = {k: "select a from {{ ref('t') }}\n" for k in old}
    files = parse(_diff(old, new))
    res = check_change_full(files, TextView(new, old))
    assert len([f for f in res.findings if f.rule == "distinct_removed"]) == 1
    assert any(n["rule"] == "distinct_removed" for n in res.unresolved)
