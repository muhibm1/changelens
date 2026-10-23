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
            return {"findings": [{"path": inj.files[0], "line": inj.expect[0].line, "severity": "block", "category": "security", "title": "injection", "body": "b", "failure": "a caller passing the old value gets the wrong result", "evidence": [f"hunk:{inj.files[0]}:{inj.expect[0].line}"]}]}
        if obo.files[0] in user:
            return {"findings": [{"path": obo.files[0], "line": obo.expect[0].line, "severity": "inform", "category": "correctness", "title": "loop start", "body": "b", "evidence": [f"hunk:{obo.files[0]}:{obo.expect[0].line}"]}]}
        return {"findings": [{"path": "app/pricing.py", "line": 6, "severity": "warn", "category": "maintainability", "title": "nit", "body": "b", "failure": "a caller passing the old value gets the wrong result", "evidence": ["hunk:app/pricing.py:6"]}]}

    cs, _ = load_constraints(ROOT / "fixture" / "orion" / "constraints")
    res = run_eval(picked, StubProvider(respond), store=synced_store, repo_root=ROOT, constraints=cs, risk_model=RiskModel.load(None), scope=["fixture"], escalation="model")
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
        return {"findings": [{"path": "app/http.py", "line": 4, "severity": "warn", "category": "correctness", "title": "signature changed", "body": "b", "failure": "a caller passing the old value gets the wrong result", "evidence": ["hunk:app/http.py:4"]}]}

    cs, _ = load_constraints(ROOT / "fixture" / "orion" / "constraints")
    s = run_eval([case], StubProvider(respond), store=synced_store, repo_root=ROOT, constraints=cs, risk_model=RiskModel.load(None), scope=["fixture"])["summary"]
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
    finding = {"source": "model", "path": "b.py", "line": 2, "end_line": None, "severity": "block", "category": "correctness", "title": "t", "body": "b", "failure": "a caller passing the old value gets the wrong result", "evidence": ["hunk:b.py:2"], "status": "valid", "notes": []}
    g = grade(case, {"findings": [finding], "uncertain": [], "rejected": [], "provider_error": None})
    assert g["caught"] == 1 and g["false_alarms"] == []


def test_recall_model_judge_counts_only_findings_it_says_are_about_the_bug():
    from orion.providers import StubProvider
    from pipelinebench.recall_eval import Case, Span, judge_one

    case = Case("r", "abc", bug="Bearer prefix matched case-sensitively", expect=[Span("auth.ts", 10)], must_mention=["case"])
    result = {"findings": [
        {"path": "auth.ts", "line": 11, "severity": "warn", "source": "model", "title": "Empty token case", "body": "b", "failure": "f"},
        {"path": "auth.ts", "line": 12, "severity": "warn", "source": "model", "title": "Scheme compared case-sensitively", "body": "b", "failure": "f"},
    ]}
    seen = []

    def respond(system, user):
        seen.append(user)
        return {"matches": [1], "reason": "only [1] names the case rule"}

    row = judge_one(case, result, StubProvider(respond))
    assert row["outcome"] == "caught" and row["judge"] == "model" and [m["title"] for m in row["matched"]] == ["Scheme compared case-sensitively"]
    assert "Bearer prefix matched case-sensitively" in seen[0], "the judge reads the hand-written label"
    none = judge_one(case, result, StubProvider({"matches": [], "reason": "neither"}))
    assert none["outcome"] == "in_file", "a warning in the file about something else is not a catch"
    assert judge_one(case, result)["judge"] == "words"
    assert judge_one(case, {**result, "provider_error": "claude exited 1"})["outcome"] == "error", "a review that did not run in full is not a miss"
    from pipelinebench.recall_eval import summarise, to_markdown
    md = to_markdown({"provider": "stub", "model": None, "cases": [row], "summary": summarise([row])})
    assert "scored in this order" in md and "| r | `abc` |" in md, "the page says what each outcome means"


def test_recall_detection_counts_any_severity_and_escalation_only_warn():
    from pipelinebench.recall_eval import Case, Span, judge, summarise

    case = Case("r", "abc", bug="b", expect=[Span("a.py", 10)])
    inform = {"path": "a.py", "line": 10, "severity": "inform", "source": "model", "title": "t", "body": "b", "failure": "f"}
    other = {"path": "a.py", "line": 80, "severity": "warn", "source": "model", "title": "elsewhere", "body": "b", "failure": "f"}
    found_low = judge(case, {"findings": [inform, other]})
    assert found_low["outcome"] == "in_file" and found_low["detected"], "an inform about the bug is detected even when a warn elsewhere in the file sets the outcome"
    assert found_low["other_warns"] == 1
    caught = judge(case, {"findings": [{**inform, "severity": "warn"}]})
    missed = judge(case, {"findings": []})
    s = summarise([found_low, caught, missed])
    assert s["detection_recall"] == round(2 / 3, 3) and s["escalation_recall"] == round(1 / 3, 3) and s["recall"] == s["escalation_recall"]
    assert s["other_warns_per_case"] == round(1 / 3, 2)


def test_recall_spread_reports_range_and_unstable_cases():
    from pipelinebench.recall_eval import Case, Span, judge, spread, spread_markdown, summarise

    case = Case("r", "abc", bug="b", expect=[Span("a.py", 10)])
    warn = {"path": "a.py", "line": 10, "severity": "warn", "source": "model", "title": "t", "body": "b", "failure": "f"}
    runs = []
    for findings in ([warn], [{**warn, "severity": "inform"}], [warn]):
        rows = [judge(case, {"findings": findings})]
        runs.append({"provider": "stub", "model": None, "cases": rows, "summary": summarise(rows)})
    sp = spread(runs)
    assert sp["metrics"]["escalation_recall"] == {"mean": 0.667, "min": 0.0, "max": 1.0, "runs": [1.0, 0.0, 1.0]}
    assert sp["metrics"]["detection_recall"]["min"] == 1.0
    assert sp["unstable_cases"] == [{"repo": "r", "commit": "abc", "outcomes": ["caught", "inform_only", "caught"]}]
    old = {"provider": "stub", "model": None, "cases": [{k: v for k, v in runs[0]["cases"][0].items() if k not in ("detected", "other_warns")}], "summary": {"recall": 1.0}}
    assert spread([old])["metrics"]["detection_recall"]["mean"] == 1.0, "rows from before the split are summarised again"
    assert "1 of 1 cases changed outcome" in spread_markdown(sp)


def test_regate_lowers_kept_warnings_without_backing_and_reuses_the_judge(tmp_path):
    import json

    from pipelinebench.recall_eval import Case, Span, judge, rejudge

    case = Case("r", "abc", bug="b", expect=[Span("a.py", 10)])
    warn = {"path": "a.py", "line": 10, "severity": "warn", "source": "model", "status": "valid", "title": "t", "body": "b", "failure": "f", "evidence": ["hunk:a.py:10"], "notes": []}
    raw = {"findings": [warn], "symbols": [], "context": {"areas": [], "history": []}}
    (tmp_path / "r-abc.json").write_text(json.dumps(raw))
    first = judge(case, raw)
    assert first["outcome"] == "caught"
    (row,) = rejudge([case], tmp_path, gate=True, verdicts=[first])
    assert row["outcome"] == "inform_only" and row["detected"] and row["judge"] == "reused"
    raw["context"]["history"] = [{"commit": "c" * 40, "date": "2020-01-01", "subject": "Revert broken parser", "paths": ["a.py"]}]
    (tmp_path / "r-abc.json").write_text(json.dumps(raw))
    (row,) = rejudge([case], tmp_path, gate=True, verdicts=[first])
    assert row["outcome"] == "caught", "a revert on the file backs the warning"
