"""The warehouse agent's plumbing, with a stand-in for the model: snapshot, numbered diff, merge, verdicts, scoring."""

from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from changelens import warehouse_agent as wa
from pipelinebench import warehouse_agent_replay as rep

STUB = r'''
import json, os, sys
prompt = sys.stdin.read()
assert not os.path.exists(".git"), "the snapshot must carry no git history"
assert os.path.exists("models/orders.sql")
if "Reviewers filed the candidate findings" in prompt:
    out = {"verdicts": [{"index": 0, "verdict": "bug", "reason": "customers is one row per customer per region"},
                        {"index": 1, "verdict": "reject", "reason": "speculation"}]}
elif "Your area: ROW COUNT" in prompt:
    out = {"audit": [], "findings": [
        {"path": "models/orders.sql", "line": 3, "severity": "bug", "category": "row_count_change",
         "title": "orders joined to customers on customer_id fans out", "explanation": "customers has a row per region",
         "evidence": "models/customers.sql:1"},
        {"path": "models/orders.sql", "line": 1, "severity": "risk", "category": "other",
         "title": "style", "explanation": "x", "evidence": ""}]}
elif "Your area: VALUES" in prompt:
    out = {"findings": [{"path": "models/orders.sql", "line": 4, "severity": "bug", "category": "row_count_change",
         "title": "duplicate of the join finding", "explanation": "same", "evidence": ""}]}
else:
    out = {"findings": []}
print(json.dumps({"structured_output": out, "num_turns": 2}))
'''


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "wh"
    (r / "models").mkdir(parents=True)
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "models" / "orders.sql").write_text("select o.id\nfrom {{ ref('raw_orders') }} o\n")
    (r / "models" / "customers.sql").write_text("select customer_id, region from {{ ref('raw_customers') }}\n")
    _git(r, "add", ".")
    _git(r, "commit", "-qm", "base")
    (r / "models" / "orders.sql").write_text("select o.id, c.region\nfrom {{ ref('raw_orders') }} o\njoin {{ ref('customers') }} c\n  on o.customer_id = c.customer_id\n")
    _git(r, "commit", "-qam", "add region to orders")
    return r


@pytest.fixture()
def stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "fake-claude"
    script.write_text(f"#!{sys.executable}\n{STUB}")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("CHANGELENS_CLAUDE_BIN", str(script))


def test_number_diff_prefixes_new_side_lines() -> None:
    diff = "diff --git a/m.sql b/m.sql\n--- a/m.sql\n+++ b/m.sql\n@@ -1,2 +10,2 @@\n select 1\n-from a\n+from b\n"
    out = wa._number_diff(diff).splitlines()
    assert out[4].startswith("   10  select 1")
    assert out[5].strip() == "-from a"
    assert out[6].startswith("   11 +from b")


def test_snapshot_has_tree_and_no_history(repo: Path, tmp_path: Path) -> None:
    dest = tmp_path / "snap"
    dest.mkdir()
    wa.snapshot(repo, "HEAD", dest)
    assert (dest / "models" / "orders.sql").read_text().count("customer_id") == 2
    assert not (dest / ".git").exists()


def test_review_merges_passes_and_applies_verdicts(repo: Path, stub: None) -> None:
    res = wa.review(repo, "HEAD", use_rules=False)
    assert res.error is None, res.error
    assert res.candidates == 3
    assert [f.title for f in res.findings] == ["orders joined to customers on customer_id fans out"]
    assert res.findings[0].severity == "bug" and res.findings[0].verified
    assert [f.title for f in res.rejected] == ["style"]


def test_cli_gate_exits_1_on_a_confirmed_bug(repo: Path, stub: None) -> None:
    from typer.testing import CliRunner

    from changelens.cli import app

    r = CliRunner().invoke(app, ["warehouse-review", "HEAD", "--repo", str(repo), "--format", "json"])
    assert r.exit_code == 1, r.output
    assert json.loads(r.output)["findings"][0]["severity"] == "bug"
    r = CliRunner().invoke(app, ["warehouse-review", "HEAD", "--repo", str(repo), "--fail-on", "never"])
    assert r.exit_code == 0 and "1 confirmed" in r.output


def test_review_records_failure_instead_of_a_clean_result(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHANGELENS_CLAUDE_BIN", "/bin/false")
    monkeypatch.setattr(wa.time, "sleep", lambda s: None)
    res = wa.review(repo, "HEAD", use_rules=False)
    assert res.error and not res.findings


def _f(**kw: object) -> wa.AgentFinding:
    base = {"path": "models/a.sql", "line": 10, "end_line": None, "severity": "bug", "category": "row_count_change", "title": "t", "explanation": "e"}
    base.update(kw)
    return wa.AgentFinding(**base)  # type: ignore[arg-type]


def test_score_strict_needs_the_labelled_file() -> None:
    bug = {"commit": "c", "title": "t", "category": "row_count_change", "in_scope": True, "must_mention": ["fan"], "expect": [{"path": "models/a.sql", "line": 40}]}
    assert rep.score_bug(bug, [_f(line=42)])["hit_strict"]
    assert rep.score_bug(bug, [_f(line=12, title="rows fan out")])["hit_strict"]
    assert not rep.score_bug(bug, [_f(path="models/b.sql", line=40, title="fan out")])["hit_strict"]
    assert rep.score_bug(bug, [_f(path="models/b.sql", title="fan out")])["hit_loose"]
    assert not rep.score_bug(bug, [_f(line=40, severity="risk")])["hit_strict"]
    assert rep.score_bug(bug, [_f(line=40, severity="risk")])["hit_risk_only"]


def test_large_commits_are_reviewed_in_chunks_without_dropping_files() -> None:
    d = "\n".join(f"diff --git a/f{i}.sql b/f{i}.sql\n--- a/f{i}.sql\n+++ b/f{i}.sql\n@@ -1 +1 @@\n    1 +x" for i in range(14))
    chunks = wa._chunks(d)
    assert [c.count("diff --git") for c in chunks] == [4, 4, 6]
    assert wa._chunks("diff --git a/a.sql b/a.sql\n+x") == ["diff --git a/a.sql b/a.sql\n+x"]


def test_merge_folds_near_duplicates() -> None:
    a, b, c = _f(line=10), _f(line=12), _f(line=12, category="other")
    assert rep._findings({"findings": []}) == []
    assert wa._merge([("grain", a), ("values", b), ("contract", c)]) == [a, c]


def test_replay_summary_counts_false_alarms() -> None:
    res = {"bugs": [{"in_scope": True, "repo": "r", "category": "x", "hit_judge": True, "hit_judge_any": True, "hit_strict": True, "hit_loose": True, "hit_risk_only": False}],
           "ordinary": [{"repo": "r", "error": None, "bug_findings": ["x"], "later_fixed": [None], "risk_findings": 0},
                        {"repo": "r", "error": None, "bug_findings": [], "later_fixed": [], "risk_findings": 1}],
           "errors": []}
    s = rep.summarise(res)
    assert s["caught_judge_bug"].startswith("1/1")
    assert s["false_alarm_share"].startswith("1/2")
    assert s["commits_with_bug_or_risk"] == "2/2"
    assert json.dumps(s)
