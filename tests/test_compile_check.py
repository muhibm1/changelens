"""The render-and-parse step: template branches, the parent as the baseline, and the dialect the file already used."""

from __future__ import annotations

from changelens import compile_check as cc
from changelens import warehouse_agent as wa


def test_sqlrender_branches_are_each_rendered() -> None:
    t = "SELECT a FROM t WHERE x = 1 {@flag} ? {AND y = @val} : {AND z = 2}"
    assert cc.render_sqlrender(t, {"@flag": True}).strip().endswith("AND y = p_val")
    assert cc.render_sqlrender(t, {"@flag": False}).strip().endswith("AND z = 2")
    assert len(cc.render_variants("q.sql", t)) == 2


def test_a_branch_that_renders_to_broken_sql_is_found_only_when_the_parent_parsed() -> None:
    old = "SELECT a FROM t WHERE x = 1 {@f} ? {AND y = 2}"
    new = "SELECT a FROM t WHERE x = 1 {@f} ? {ANDy = 2 2}"
    fail = cc.check_file("inst/sql/sql_server/q.sql", new, old)
    assert fail is not None and fail.dialect == "tsql" and "@f=T" in fail.variant
    assert cc.check_file("inst/sql/sql_server/q.sql", new, new) is None


def test_hive_variables_and_a_trailing_comma() -> None:
    old = "SELECT a, COUNT(1) FROM ${db}.t GROUP BY a"
    assert cc.check_file("q.hql", old, old) is None
    fail = cc.check_file("q.hql", "SELECT a, COUNT(1) FROM ${db}.t GROUP BY a,\nLIMIT 10", old)
    assert fail is not None and fail.dialect == "hive"


def test_non_sql_files_and_unparseable_parents_are_skipped() -> None:
    assert cc.check_file("m.yml", "a: [", "a: 1") is None
    assert cc.check_file("m.sql", "SELECT ((", "SELECT ((( FROM") is None


def test_cause_is_only_held_to_files_the_verifier_saw_or_the_commit_left_alone() -> None:
    changed = {"models/a.sql": ({3}, False)}
    assert wa._cause_checkable({"path": "models/a.sql", "line": 9, "removed": False}, changed, {"models/a.sql"})
    assert wa._cause_checkable({"path": "ddl/t.sql", "line": 9, "removed": False}, changed, {"models/a.sql"})
    assert not wa._cause_checkable({"path": "jobs/q.hql", "line": 9, "removed": False}, changed, {"models/a.sql", "jobs/q.hql"})


def test_documentation_findings_are_capped() -> None:
    assert wa._is_doc("README.md") and wa._is_doc("docs/x.yml") and not wa._is_doc("models/x.yml")
