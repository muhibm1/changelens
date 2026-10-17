from pathlib import Path

from orion.constraints import load_constraints
from orion.providers import StubProvider
from orion.risk import RiskModel
from pipelinebench.review_eval import load_cases, run_eval

ROOT = Path(__file__).resolve().parents[1]
CASES = ROOT / "pipelinebench" / "review" / "cases.yaml"


def test_every_case_loads_and_every_marker_resolves():
    cases = load_cases(CASES, ROOT)
    assert len(cases) >= 20
    assert len({c.id for c in cases}) == len(cases)
    assert sum(not c.expect for c in cases) >= 5, "enough correct changes to count false alarms"
    for c in cases:
        assert c.diff().startswith(f"diff --git a/{c.file} b/{c.file}\n")
        assert all(e.line > 0 for e in c.expect)


def test_grading_counts_catches_inform_and_false_alarms(synced_store):
    cases = {c.id: c for c in load_cases(CASES, ROOT)}
    picked = [cases["py-sql-injection"], cases["py-off-by-one-total"], cases["clean-py-refactor"]]
    inj, obo = picked[0], picked[1]

    def respond(system, user):
        if inj.file in user:
            return {"findings": [{"path": inj.file, "line": inj.expect[0].line, "severity": "block", "category": "security", "title": "injection", "body": "b", "evidence": [f"hunk:{inj.file}:{inj.expect[0].line}"]}]}
        if obo.file in user:
            return {"findings": [{"path": obo.file, "line": obo.expect[0].line, "severity": "inform", "category": "correctness", "title": "loop start", "body": "b", "evidence": [f"hunk:{obo.file}:{obo.expect[0].line}"]}]}
        return {"findings": [{"path": "app/pricing.py", "line": 6, "severity": "warn", "category": "maintainability", "title": "nit", "body": "b", "evidence": ["hunk:app/pricing.py:6"]}]}

    cs, _ = load_constraints(ROOT / "fixture" / "orion" / "constraints")
    res = run_eval(picked, StubProvider(respond), store=synced_store, repo_root=ROOT, constraints=cs, risk_model=RiskModel.load(None))
    s = res["summary"]
    assert s["recall"] == 0.5
    assert s["inform_only"] == 1
    assert s["false_alarms"] == 1 and s["clean_cases_flagged"] == "1/1"
    assert s["precision"] == 0.5 and s["duplicate_findings"] == 0
    assert s["missed"] == [f"py-off-by-one-total@{obo.expect[0].line}"]
