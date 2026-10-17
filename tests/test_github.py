"""Posting a review to GitHub: inline comments for new findings, memory across pushes, one summary."""

from __future__ import annotations

import json

from orion.github import GitHubClient, find_summary, finding_id, match_previous, parse_state, post_review


def _f(path, line, title, severity="warn", source="model", category="correctness"):
    return {"source": source, "path": path, "line": line, "end_line": None, "severity": severity, "category": category, "title": title, "body": "why", "evidence": [f"hunk:{path}:{line}"], "status": "valid", "notes": []}


class FakeGitHub(GitHubClient):
    """Records calls; serves the issue comments and review comments it was given."""

    def __init__(self, comments=None, fail_review=False):
        super().__init__("o/r", token="t")
        self.comments = comments or []
        self.calls = []
        self.fail_review = fail_review
        self.fail_readback = False
        self._next = 900

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and path.startswith("issues/"):
            return self.comments
        if method == "POST" and path.endswith("/reviews"):
            if self.fail_review:
                raise RuntimeError("GitHub POST pulls/1/reviews: HTTP 422 line must be part of the diff")
            self._review = body
            return {"id": 77}
        if method == "GET" and "/reviews/77/comments" in path:
            if self.fail_readback:
                raise RuntimeError("GitHub GET: HTTP 502")
            return [{"id": self._next + i, "body": c["body"]} for i, c in enumerate(self._review["comments"])]
        if method == "POST" and path.startswith("issues/"):
            return {"id": 5}
        return {}


REVIEW = {"severity": "warn", "findings": [_f("app/a.py", 10, "Null deref"), _f("app/b.py", 3, "CON-031 columns_locked fires", "block", "deterministic", "data_contract"), {**_f(None, None, "No location"), "evidence": ["node:x"]}]}


def test_first_push_posts_inline_comments_and_a_summary_with_state():
    gh = FakeGitHub()
    out = post_review(gh, 1, "abcdef1234", REVIEW, "**Orion review: WARN**\n- stuff\n")
    assert out == {"new": 3, "carried": 0, "resolved": 0, "inline_posted": 2, "replies": 0}
    review_call = next(b for m, p, b in gh.calls if p == "pulls/1/reviews")
    assert review_call["event"] == "COMMENT" and review_call["commit_id"] == "abcdef1234"
    assert [(c["path"], c["line"], c["side"]) for c in review_call["comments"]] == [("app/b.py", 3, "RIGHT"), ("app/a.py", 10, "RIGHT")], "block before warn"
    assert "<!-- orion:" in review_call["comments"][1]["body"] and "`hunk:app/a.py:10`" in review_call["comments"][1]["body"]
    summary = next(b for m, p, b in gh.calls if m == "POST" and p == "issues/1/comments")["body"]
    assert summary.startswith("<!-- orion-review -->\n**Orion review: WARN**") and "Reviewed `abcdef1`: 3 new, 0 unchanged since the last push, 0 no longer raised" in summary
    state = parse_state(summary)
    assert [s["title"] for s in state] == ["Null deref", "CON-031 columns_locked fires", "No location"]
    assert state[0]["comment_id"] == 901 and state[1]["comment_id"] == 900 and state[2]["comment_id"] is None


def test_second_push_keeps_unchanged_findings_quiet_and_replies_on_resolved_ones():
    first = FakeGitHub()
    post_review(first, 1, "aaaaaaa", REVIEW, "md")
    sticky = next(b for m, p, b in first.calls if m == "POST" and p == "issues/1/comments")["body"]
    gh = FakeGitHub(comments=[{"id": 5, "user": {"login": "github-actions[bot]"}, "body": sticky}, {"id": 6, "user": {"login": "someone"}, "body": "<!-- orion-review --> not ours"}])
    # Same bug reworded two lines down, the constraint finding gone, one new finding.
    later = {"severity": "warn", "findings": [_f("app/a.py", 12, "Possible null dereference of user"), _f("app/c.py", 1, "Shell injection", "block", category="security")]}
    out = post_review(gh, 1, "bbbbbbb", later, "md2")
    assert out == {"new": 1, "carried": 1, "resolved": 2, "inline_posted": 1, "replies": 1}
    review_call = next(b for m, p, b in gh.calls if p == "pulls/1/reviews")
    assert [c["path"] for c in review_call["comments"]] == ["app/c.py"], "the reworded finding gets no second comment"
    assert ("POST", "pulls/1/comments/900/replies", {"body": "Not raised on `bbbbbbb`: the line changed or the finding no longer applies."}) in gh.calls
    patch = next(b for m, p, b in gh.calls if m == "PATCH")
    assert "1 new, 1 unchanged since the last push, 2 no longer raised" in patch["body"]
    state = parse_state(patch["body"])
    assert state[0]["id"] == parse_state(sticky)[0]["id"] and state[0]["comment_id"] == 901, "the carried finding keeps its id and its comment"
    assert ("PATCH", "issues/comments/5", patch) in gh.calls


def test_summary_is_found_by_marker_and_state_whatever_the_login():
    first = FakeGitHub()
    post_review(first, 1, "aaaaaaa", REVIEW, "md")
    sticky = next(b for m, p, b in first.calls if m == "POST" and p == "issues/1/comments")["body"]
    # Posted with a PAT, so the author is a person, and someone else pasted the marker without state.
    gh = FakeGitHub(comments=[{"id": 6, "user": {"login": "someone"}, "body": "<!-- orion-review --> not ours"}, {"id": 7, "user": {"login": "muhibm1"}, "body": sticky}])
    assert find_summary(gh, 1)["id"] == 7
    out = post_review(gh, 1, "bbbbbbb", REVIEW, "md2")
    assert out["carried"] == 3 and ("PATCH", "issues/comments/7", next(b for m, p, b in gh.calls if m == "PATCH")) in gh.calls
    assert find_summary(FakeGitHub(comments=[{"id": 6, "user": {"login": "github-actions[bot]"}, "body": "<!-- orion-review --> no state"}]), 1) is None


def test_match_previous_prefers_exact_id_then_nearby_line():
    prev = [{"id": finding_id(_f("a.py", 1, "Same title")), "source": "model", "path": "a.py", "line": 1, "category": "correctness", "title": "Same title", "comment_id": 1},
            {"id": "deadbeef00", "source": "model", "path": "a.py", "line": 40, "category": "correctness", "title": "Other", "comment_id": 2}]
    cur = [_f("a.py", 30, "Same title"), _f("a.py", 42, "Reworded"), _f("a.py", 42, "Far", category="security")]
    new, carried, resolved = match_previous(cur, prev)
    assert [f["id"] for f in carried] == [prev[0]["id"], "deadbeef00"]
    assert [f["title"] for f in new] == ["Far"] and resolved == []


def test_omitted_files_are_named_to_the_model(synced_store):
    from pathlib import Path

    from orion.constraints import load_constraints
    from orion.providers import StubProvider
    from orion.review import review
    from orion.risk import RiskModel
    from pipelinebench.review_eval import Change

    root = Path(__file__).resolve().parents[1]
    cs, _ = load_constraints(root / "fixture" / "orion" / "constraints")
    huge = "".join(f"x{i} = {i}\n" for i in range(20000))
    diff = Change("app/huge.py", "", huge).diff() + Change("app/tiny.py", "", "t = 1\n").diff()
    stub = StubProvider()
    r = review(synced_store, root, stub, constraints=cs, risk_model=RiskModel.load(None), diff_text=diff, new_files={"app/huge.py": huge, "app/tiny.py": "t = 1\n"})
    assert r["omitted"] == ["app/huge.py"]
    assert "too large to show, so they are not under review here: app/huge.py" in stub.calls[0][1]


def test_inline_failure_falls_back_to_the_summary():
    gh = FakeGitHub(fail_review=True)
    out = post_review(gh, 1, "abc", REVIEW, "md")
    assert out["inline_posted"] == 0 and out["new"] == 3
    summary = next(b for m, p, b in gh.calls if m == "POST" and p == "issues/1/comments")["body"]
    assert "Inline comments could not be posted" in summary and json.loads(summary.split("<!-- orion-state ")[1].rstrip(" ->\n"))


def test_a_failed_readback_of_comment_ids_does_not_claim_the_comments_were_not_posted():
    gh = FakeGitHub()
    gh.fail_readback = True
    out = post_review(gh, 1, "abc", REVIEW, "md")
    assert out["inline_posted"] == 2
    summary = next(b for m, p, b in gh.calls if m == "POST" and p == "issues/1/comments")["body"]
    assert "could not be posted" not in summary and all(s["comment_id"] is None for s in parse_state(summary))
