"""History replay: every merged change in a window, as the evidence a design partner reads."""

from pipelinebench.history_replay import load_labels, summarise, to_markdown


def _row(day, warns, **kw):
    return {"commit": f"{day:040d}", "date": f"2026-05-{day:02d}", "title": "t", "week": (2026, 18 + day // 7), "severity": "warn" if warns else "inform", "error": None, "warns": warns, "timing": {"total_s": float(day)}, "usage": {}, **kw}


def test_summary_counts_false_alarms_per_week_and_bugs_flagged():
    noise = {"source": "model", "path": "a.sql", "line": 1, "title": "x", "verdict": "noise", "escalation": []}
    det = {"source": "deterministic", "path": "b.sql", "line": 1, "title": "y", "verdict": "unjudged", "escalation": []}
    rows = [
        _row(1, [noise], bug="caught"),
        _row(8, [det]),
        _row(14, []),
        _row(2, [], bug="inform_only", in_window=False),  # a bug from before the window counts for recall only
    ]
    s = summarise(rows)
    assert s["commits"] == 3 and s["weeks"] == 2 and s["warns"] == 2 and s["warns_per_week"] == 1.0
    assert s["noise"] == 1 and s["false_alarms_per_week"] == 0.5
    assert s["model_warns"] == 1 and s["deterministic_warns"] == 1
    assert s["bugs_flagged"] == "1/2" and s["bugs_surfaced_any_severity"] == "2/2"
    md = to_markdown({"provider": "stub", "model": None, "clone": "calitp", "commits": rows, "summary": s})
    assert "3 merged changes over 2 weeks" in md and "**noise** model" in md


def test_an_unquoted_numeric_commit_label_is_refused(tmp_path):
    import pytest

    labels = tmp_path / "labels.yaml"
    labels.write_text("bugs:\n  - commit: 0123456\n")
    with pytest.raises(ValueError, match="quoted"):
        load_labels(labels)


def test_one_model_run_compares_the_escalation_policies():
    from pipelinebench.history_replay import policy_view

    result = {"files": [{"path": "models/a.sql", "mapped": True}, {"path": "app.py", "mapped": False}], "findings": [
        {"source": "model", "severity": "warn", "path": "models/a.sql", "line": 1, "title": "a", "backing": []},
        {"source": "model", "severity": "warn", "path": "models/a.sql", "line": 2, "title": "b", "backing": ["node:x"]},
        {"source": "model", "severity": "warn", "path": "app.py", "line": 1, "title": "c", "backing": []},
        {"source": "deterministic", "severity": "warn", "path": "x.py", "line": 1, "title": "1 changed path is not mapped to any asset"},
    ]}
    sev = {p: [f["severity"] for f in policy_view(result, p)["findings"]] for p in ("model", "evidence", "platform")}
    assert sev["model"] == ["warn"] * 4
    assert sev["evidence"] == ["inform", "warn", "inform", "inform"]
    assert sev["platform"] == ["inform", "warn", "warn", "inform"], "platform keeps the model's severity off mapped files"
    noise = {"source": "model", "path": "app.py", "line": 1, "title": "c", "verdict": "noise", "backing": [], "lowered_by": ["evidence"]}
    rows = [_row(1, [noise], bug="caught", bug_by_policy={"evidence": "inform_only", "platform": "caught"})]
    s = summarise(rows)["policies"]
    assert s["model"]["noise"] == 1 and s["evidence"]["noise"] == 0 and s["platform"]["noise"] == 1
    assert s["evidence"]["bugs_flagged"] == "0/1" and s["platform"]["bugs_flagged"] == "1/1"
    assert "| evidence |" in to_markdown({"provider": "stub", "model": None, "clone": "c", "commits": rows, "summary": summarise(rows)})


def test_relabel_scores_a_finished_replay_against_new_labels(tmp_path):
    from pipelinebench.history_replay import relabel

    labels = tmp_path / "labels.yaml"
    labels.write_text('warnings:\n  "00000000":\n    - {path: a.sql, line: 1, verdict: noise}\n')
    w = {"source": "model", "path": "a.sql", "line": 2, "title": "x", "verdict": "unjudged"}
    res = relabel({"commits": [_row(1, [w])]}, labels)
    assert res["commits"][0]["warns"][0]["verdict"] == "noise" and res["summary"]["noise"] == 1
