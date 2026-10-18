"""Post a review to a GitHub pull request: inline comments on the diff lines, one summary, and memory across pushes.

Orion advises; it never approves or requests changes, so every review it
posts is a ``COMMENT`` review. What makes the comments bearable push after
push is the state kept inside the summary comment: the findings of the last
run, each with a stable id. On the next run a finding on the same file,
category and nearby line is the same finding and gets no new comment; a
finding that is no longer raised gets one reply saying so; only a new
finding gets a new inline comment. The summary always shows the current
list in full.

HTTP is plain ``urllib`` against the REST API with the token GitHub Actions
provides, and every call goes through ``GitHubClient`` so tests can replace it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

MARKER = "<!-- orion-review -->"
STATE_RE = re.compile(r"<!-- orion-state (.*?) -->", re.S)
NEAR = 3  # lines: a finding this close to a previous one of the same file and category is the same finding
MAX_INLINE = 50  # inline comments per push; the summary carries the rest


@dataclass
class GitHubClient:
    repo: str  # owner/name
    token: str | None = None
    api: str = "https://api.github.com"
    bot_login: str = "github-actions[bot]"

    def __post_init__(self) -> None:
        self.token = self.token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        self.api = os.environ.get("GITHUB_API_URL", self.api).rstrip("/")

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        url = f"{self.api}/repos/{self.repo}/{path.lstrip('/')}" if not path.startswith("http") else path
        data = json.dumps(body).encode() if body is not None else None
        headers = {"accept": "application/vnd.github+json", "x-github-api-version": "2022-11-28"}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        if data is not None:
            headers["content-type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"GitHub {method} {path}: HTTP {e.code} {e.read().decode(errors='replace')[:300]}") from e

    def issue_comments(self, pr: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self.call("GET", f"issues/{pr}/comments?per_page=100&page={page}") or []
            out.extend(batch)
            if len(batch) < 100:
                return out
            page += 1


def finding_id(f: dict[str, Any]) -> str:
    key = "|".join([f["source"], f["path"] or "", f["category"], re.sub(r"[^a-z0-9]+", " ", f["title"].lower()).strip()])
    return hashlib.sha1(key.encode()).hexdigest()[:10]


def match_previous(findings: list[dict[str, Any]], previous: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(new, carried, resolved). A current finding takes a previous one's id when it is the same finding; ids are added in place."""
    unmatched = list(previous)
    new, carried = [], []
    for f in findings:
        same = next((p for p in unmatched if p["id"] == finding_id(f)), None) or next(
            (p for p in unmatched if p["source"] == f["source"] and p["path"] == f["path"] and p["category"] == f["category"] and p.get("line") is not None and f.get("line") is not None and abs(p["line"] - f["line"]) <= NEAR),
            None,
        )
        if same:
            unmatched.remove(same)
            f["id"] = same["id"]
            f["comment_id"] = same.get("comment_id")
            carried.append(f)
        else:
            f["id"] = finding_id(f)
            new.append(f)
    return new, carried, unmatched


def _state_of(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": f["id"], "source": f["source"], "path": f["path"], "line": f["line"], "category": f["category"], "title": f["title"], "comment_id": f.get("comment_id")} for f in findings]


def parse_state(body: str) -> list[dict[str, Any]]:
    m = STATE_RE.search(body or "")
    if not m:
        return []
    try:
        state = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    return [s for s in state if isinstance(s, dict) and "id" in s]


def _fails(f: dict[str, Any]) -> str:
    return f"Fails when: {f['failure']}\n\n" if f.get("failure") else ""


def inline_body(f: dict[str, Any]) -> str:
    tag = "deterministic" if f["source"] == "deterministic" else "model"
    return f"**{f['severity']}** {f['title']} _({tag}, {f['category']})_\n\n{f['body']}\n\n{_fails(f)}Evidence: {', '.join(f'`{e}`' for e in f['evidence'])}\n<!-- orion:{f['id']} -->"


def summary_body(review: dict[str, Any], markdown: str, new: list, carried: list, resolved: list, head_sha: str, state: list[dict[str, Any]], note: str = "", unchecked: list | None = None) -> str:
    counts = f"{len(new)} new, {len(carried)} unchanged since the last push, {len(resolved)} no longer raised"
    if unchecked:
        counts += f", {len(unchecked)} earlier model finding{'s' if len(unchecked) != 1 else ''} not re-checked (model unavailable): " + "; ".join((f"`{u['path']}:{u['line']}` " if u.get("path") else "") + u["title"] for u in unchecked)
    body = f"{MARKER}\n{markdown.rstrip()}\n\n_Reviewed `{head_sha[:7]}`: {counts}._"
    if note:
        body += f"\n\n{note}"
    return body + f"\n<!-- orion-state {json.dumps(state, separators=(',', ':'))} -->"


def find_summary(client: GitHubClient, pr: int) -> dict[str, Any] | None:
    """Orion's own summary comment on ``pr``: the latest one carrying the marker and a state block. The bot login is a
    preference, not a requirement, because the token may be a PAT or an App token rather than ``github-actions[bot]``."""
    ours = [c for c in client.issue_comments(pr) if (c.get("body") or "").startswith(MARKER) and STATE_RE.search(c.get("body") or "")]
    by_bot = [c for c in ours if (c.get("user") or {}).get("login") == client.bot_login]
    return (by_bot or ours or [None])[-1]


def post_review(client: GitHubClient, pr: int, head_sha: str, review: dict[str, Any], markdown: str, *, note: str = "") -> dict[str, Any]:
    """Post or update Orion's review of ``pr`` at ``head_sha``. Returns what was posted, for the log."""
    previous_comment = find_summary(client, pr)
    previous = parse_state(previous_comment["body"]) if previous_comment else []
    findings = [dict(f) for f in review["findings"]]
    new, carried, resolved = match_previous(findings, previous)
    # With the model unavailable its earlier findings were not re-checked, so they stay open rather than being called resolved.
    unchecked = [p for p in resolved if p.get("source") == "model"] if review.get("provider_error") else []
    resolved = [p for p in resolved if p not in unchecked]

    rank = {"block": 0, "warn": 1, "inform": 2}
    placeable = sorted([f for f in new if f["path"] and isinstance(f.get("line"), int)], key=lambda f: rank.get(f["severity"], 3))[:MAX_INLINE]
    posted = 0
    if placeable:
        comments = [{"path": f["path"], "line": f["line"], "side": "RIGHT", "body": inline_body(f)} for f in placeable]
        try:
            out = client.call("POST", f"pulls/{pr}/reviews", {"commit_id": head_sha, "event": "COMMENT", "body": f"Orion review: {len(placeable)} new finding{'s' if len(placeable) != 1 else ''} on this push.", "comments": comments})
            posted = len(comments)
        except RuntimeError as e:
            # A line GitHub will not accept (outside its view of the diff) fails the whole review; fall back to the summary only.
            note = (note + "\n\n" if note else "") + f"Inline comments could not be posted ({e}); the findings are listed above."
        else:
            # The comments were posted; a failure reading their ids back only costs the replies on resolution next push.
            try:
                for c in client.call("GET", f"pulls/{pr}/reviews/{out['id']}/comments?per_page=100") or []:
                    m = re.search(r"<!-- orion:([0-9a-f]+) -->", c.get("body") or "")
                    if m:
                        for f in placeable:
                            if f["id"] == m.group(1):
                                f["comment_id"] = c["id"]
            except RuntimeError:
                pass
    replied = 0
    for p in resolved:
        if p.get("comment_id"):
            try:
                client.call("POST", f"pulls/{pr}/comments/{p['comment_id']}/replies", {"body": f"Not raised on `{head_sha[:7]}`: the line changed or the finding no longer applies."})
                replied += 1
            except RuntimeError:
                pass
    body = summary_body(review, markdown, new, carried, resolved, head_sha, _state_of(findings) + unchecked, note, unchecked)
    if previous_comment:
        client.call("PATCH", f"issues/comments/{previous_comment['id']}", {"body": body})
    else:
        client.call("POST", f"issues/{pr}/comments", {"body": body})
    out = {"new": len(new), "carried": len(carried), "resolved": len(resolved), "inline_posted": posted, "replies": replied}
    return {**out, "unchecked": len(unchecked)} if unchecked else out


__all__ = ["GitHubClient", "post_review", "match_previous", "finding_id", "parse_state", "find_summary"]
