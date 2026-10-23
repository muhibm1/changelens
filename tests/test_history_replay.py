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
