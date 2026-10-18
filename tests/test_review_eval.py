from pathlib import Path

from orion.constraints import load_constraints
from orion.providers import StubProvider
from orion.risk import RiskModel
from pipelinebench.review_eval import load_cases, run_eval, to_markdown

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "pipelinebench" / "review" / "cases.yaml"


def test_every_case_loads_and_every_marker_resolves():
    cases = load_cases(CASES, ROOT)
    assert len(cases) >= 20
    assert len({c.id for c in cases}) == len(cases)
    assert sum(not c.expect for c in cases) >= 5, "enough correct changes to count false alarms"
    for c in cases:
        assert c.diff().startswith(f"diff --git a/{c.files[0]} b/{c.files[0]}\n")
        assert c.diff().count("diff --git ") == len(c.files)
        assert all(e.line > 0 for e in c.expect)
    assert cases[[c.id for c in cases].index("clean-intent-py-query-spaces-as-plus")].intent.startswith("Encode spaces")


def test_grading_counts_catches_inform_and_false_alarms(synced_store):
    cases = {c.id: c for c in load_cases(CASES, ROOT)}
    picked = [cases["py-sql-injection"], cases["py-off-by-one-total"], cases["clean-py-refactor"]]
    inj, obo = picked[0], picked[1]

    def respond(system, user):
        if inj.files[0] in user:
            return {"findings": [{"path": inj.files[0], "line": inj.expect[0].line, "severity": "block", "category": "security", "title": "injection", "body": "b", "evidence": [f"hunk:{inj.files[0]}:{inj.expect[0].line}"]}]}
        if obo.files[0] in user:
            return {"findings": [{"path": obo.files[0], "line": obo.expect[0].line, "severity": "inform", "category": "correctness", "title": "loop start", "body": "b", "evidence": [f"hunk:{obo.files[0]}:{obo.expect[0].line}"]}]}
        return {"findings": [{"path": "app/pricing.py", "line": 6, "severity": "warn", "category": "maintainability", "title": "nit", "body": "b", "evidence": ["hunk:app/pricing.py:6"]}]}

    cs, _ = load_constraints(ROOT / "fixture" / "orion" / "constraints")
    res = run_eval(picked, StubProvider(respond), store=synced_store, repo_root=ROOT, constraints=cs, risk_model=RiskModel.load(None))
    s = res["summary"]
    assert s["recall"] == 0.5
    assert s["inform_only"] == 1
    assert s["false_alarms"] == 1 and s["clean_cases_flagged"] == "1/1"
    assert s["precision"] == 0.5 and s["duplicate_findings"] == 0
    assert s["missed"] == [f"py-off-by-one-total@{obo.expect[0].line}"]


def test_multi_file_case_grades_against_the_bug_file(synced_store):
    case = {c.id: c for c in load_cases(CASES, ROOT)}["xf-py-kwarg-renamed"]
    assert case.files == ["app/http.py", "app/health.py"]
    bug = case.expect[0]
    assert bug.file == "app/health.py"

    def respond(system, user):
        assert "=== app/http.py (whole file after the change)" in user and "=== app/health.py (whole file after the change)" in user
        # Same line number, wrong file: a false alarm, not a catch.
        return {"findings": [{"path": "app/http.py", "line": 4, "severity": "warn", "category": "correctness", "title": "signature changed", "body": "b", "evidence": ["hunk:app/http.py:4"]}]}

    cs, _ = load_constraints(ROOT / "fixture" / "orion" / "constraints")
    s = run_eval([case], StubProvider(respond), store=synced_store, repo_root=ROOT, constraints=cs, risk_model=RiskModel.load(None))["summary"]
    assert s["recall"] == 0.0 and s["false_alarms"] == 1


def test_marker_in_a_multi_file_case_must_name_its_file(tmp_path):
    import pytest

    bad = tmp_path / "cases.yaml"
    bad.write_text("- id: x\n  files:\n    - {file: a.py, before: '', after: 'a = 1\\n'}\n    - {file: b.py, before: '', after: 'b = 1\\n'}\n  expect:\n    - marker: 'b = 1'\n")
    with pytest.raises(ValueError, match="needs a `file`"):
        load_cases(bad, tmp_path)


def test_markdown_shows_na_when_a_metric_has_no_denominator():
    res = {"provider": "stub", "cases": 1, "repeat": 1, "summary": {"recall": None, "inform_only": 0, "precision": None, "duplicate_findings": 0, "false_alarms": 0, "clean_cases_flagged": 0, "provider_errors": 0, "tokens_in": 0, "tokens_out": 0, "missed": []}, "rows": []}
    md = to_markdown(res)
    assert "| recall (bugs raised at warn or block) | n/a |" in md and "| precision (caught bugs / (caught bugs + false alarms)) | n/a |" in md


def test_a_bug_may_be_raised_at_an_alternative_location(tmp_path):
    import yaml

    from pipelinebench.review_eval import grade

    case_yaml = [{"id": "alt", "files": [{"file": "a.py", "before": "", "after": "def f():\n    return None\n"}, {"file": "b.py", "before": "", "after": "x = f()\ndelete(x)\n"}],
                  "expect": [{"file": "a.py", "marker": "return None", "also": [{"file": "b.py", "marker": "delete(x)"}]}]}]
    (tmp_path / "cases.yaml").write_text(yaml.safe_dump(case_yaml))
    [case] = load_cases(tmp_path / "cases.yaml", tmp_path)
    assert case.expect[0].locations == [("a.py", 2), ("b.py", 2)]
    finding = {"source": "model", "path": "b.py", "line": 2, "end_line": None, "severity": "block", "category": "correctness", "title": "t", "body": "b", "evidence": ["hunk:b.py:2"], "status": "valid", "notes": []}
    g = grade(case, {"findings": [finding], "uncertain": [], "rejected": [], "provider_error": None})
    assert g["caught"] == 1 and g["false_alarms"] == []
