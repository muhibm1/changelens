"""Recall eval: would ``orion review`` have caught real bugs when they were introduced?

The replay eval (``replay_eval.py``) measures noise on correct changes. This one measures the other
side. ``pipelinebench/review/recall.yaml`` lists commits from public repositories that introduced a bug
a later fix commit removed, found by blaming the fixed lines (``introducers.py``) and then read and
labelled by hand. Each case names the introducing commit, the fix, and where in the introducing commit
the bug sits (``expect``: the lines the fix later touched, in the introducing commit's numbering).

Orion reviews the introducing commit against its parent with the commit message as intent, as a team
would have seen it. A case is **caught** when a valid ``warn`` or ``block`` finding sits on the expected
path within ``WINDOW`` lines of the expected span and uses one of the case's ``must_mention`` words
(so a different warning that lands nearby does not count); **in file** when a warning is on the path but further
away; **inform only** when a finding within the window stopped at ``inform``; otherwise **missed**.

Labels are written by hand from the upstream history and must never be derived from Orion's output.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .replay_eval import _head, prepare, review_commit

WINDOW = 12
ACTED_ON = ("warn", "block")


@dataclass
class Span:
    path: str
    line: int
    end_line: int | None = None

    def near(self, path: str, line: int) -> bool:
        return path == self.path and self.line - WINDOW <= line <= (self.end_line or self.line) + WINDOW


@dataclass
class Case:
    repo: str
    commit: str
    title: str = ""
    fix: str = ""
    fix_title: str = ""
    bug: str = ""  # one line, by hand: what the bug was
    expect: list[Span] = field(default_factory=list)
    must_mention: list[str] = field(default_factory=list)  # a finding about this bug uses one of these words (lower-cased substring of title, body or failure)
    skip: str = ""  # why the case is not run (the bug needs knowledge no reviewer of the diff has)


def load(path: Path) -> tuple[dict[str, dict[str, Any]], list[Case]]:
    doc = yaml.safe_load(path.read_text())
    cases = [Case(c["repo"], str(c["commit"]), c.get("title", ""), str(c.get("fix", "")), c.get("fix_title", ""), c.get("bug", ""), [Span(**s) for s in c.get("expect") or []], [str(w).lower() for w in c.get("must_mention") or []], c.get("skip", "")) for c in doc["cases"]]
    return doc["repos"], cases


def judge(case: Case, result: dict[str, Any]) -> dict[str, Any]:
    findings = [f for f in result.get("findings", []) if f.get("path")]
    warns = [f for f in findings if f["severity"] in ACTED_ON]

    def about(f: dict[str, Any]) -> bool:
        text = " ".join(str(f.get(k) or "") for k in ("title", "body", "failure")).lower()
        return not case.must_mention or any(w in text for w in case.must_mention)

    near_warn = [f for f in warns if about(f) and any(s.near(f["path"], f.get("line") or 0) for s in case.expect)]
    near_inform = [f for f in findings if f["severity"] == "inform" and about(f) and any(s.near(f["path"], f.get("line") or 0) for s in case.expect)]
    in_file = [f for f in warns if any(f["path"] == s.path for s in case.expect)]
    outcome = "caught" if near_warn else "in_file" if in_file else "inform_only" if near_inform else "missed"
    if result.get("error"):
        outcome = "error"
    return {
        "repo": case.repo,
        "commit": case.commit,
        "title": case.title,
        "bug": case.bug,
        "outcome": outcome,
        "matched": [{"severity": f["severity"], "source": f["source"], "path": f["path"], "line": f.get("line"), "title": f["title"], "failure": f.get("failure", ""), "notes": f.get("notes", [])} for f in near_warn + near_inform][:4],
        "warns": len(warns),
        "warn_titles": [f"{f['path']}:{f.get('line')} {f['title']}" for f in warns][:6],
        "severity": result.get("severity"),
        "error": result.get("error"),
        "provider_error": result.get("provider_error"),
        "usage": result.get("usage") or {},
    }


def run(cases: list[Case], repos: dict[str, dict[str, Any]], workdir: Path, *, orion: str, provider: str, model: str | None, progress=None, extra_args: list[str] | None = None, raw_dir: Path | None = None) -> dict[str, Any]:
    """``raw_dir``: where each case's full review JSON is kept, so a miss can be read without another model run."""
    todo = [c for c in cases if not c.skip]
    clones = {name: prepare(name, repos[name], workdir) for name in sorted({c.repo for c in todo})}
    heads = {name: _head(clone) for name, clone in clones.items()}
    rows = []
    for c in todo:
        result = review_commit(orion, clones[c.repo], c.commit, provider, model, extra_args=extra_args)
        if raw_dir is not None:
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / f"{c.repo}-{c.commit}.json").write_text(json.dumps(result, indent=1, default=str))
        row = judge(c, result)
        rows.append(row)
        if progress:
            progress(row)
    for name, clone in clones.items():
        subprocess.run(["git", "-C", str(clone), "checkout", "-q", "-f", heads[name]], capture_output=True)
    return {"provider": provider, "model": model, "args": extra_args or [], "cases": rows, "skipped": [{"repo": c.repo, "commit": c.commit, "why": c.skip} for c in cases if c.skip], "summary": summarise(rows)}


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r["outcome"] != "error"]
    n = len(ok)
    count = {k: sum(1 for r in ok if r["outcome"] == k) for k in ("caught", "in_file", "inform_only", "missed")}
    return {
        "cases": n,
        "errors": len(rows) - n,
        **count,
        "recall": round(count["caught"] / n, 3) if n else 0,
        "recall_in_file": round((count["caught"] + count["in_file"]) / n, 3) if n else 0,
        "warns_per_case": round(sum(r["warns"] for r in ok) / n, 2) if n else 0,
        "tokens_in": sum(r["usage"].get("input_tokens", 0) for r in ok),
        "tokens_out": sum(r["usage"].get("output_tokens", 0) for r in ok),
    }


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def to_markdown(res: dict[str, Any]) -> str:
    s = res["summary"]
    label = f"`{res['provider']}{':' + res['model'] if res['model'] else ''}`" + (f" with `{' '.join(res['args'])}`" if res.get("args") else "")
    lines = [f"# Recall eval: {label}, {s['cases']} bugs at the commit that introduced them", "", "| metric | value |", "| --- | --- |"]
    for k in ("cases", "errors", "caught", "in_file", "inform_only", "missed", "recall", "recall_in_file", "warns_per_case", "tokens_in", "tokens_out"):
        lines.append(f"| {k.replace('_', ' ')} | {s[k]} |")
    lines += ["", "| repo | commit | bug | outcome | finding |", "| --- | --- | --- | --- | --- |"]
    for r in res["cases"]:
        m = r["matched"][0] if r["matched"] else None
        found = f"{m['severity']} `{m['path']}:{m['line']}` {_cell(m['title'])}" if m else ("; ".join(_cell(t) for t in r["warn_titles"]) if r["outcome"] == "in_file" else "")
        lines.append(f"| {r['repo']} | `{r['commit']}` | {_cell(r['bug'] or r['title'])} | **{r['outcome']}** | {found} |")
    if res.get("skipped"):
        lines += ["", "Skipped: " + "; ".join(f"{k['repo']} `{k['commit']}` ({k['why']})" for k in res["skipped"])]
    return "\n".join(lines) + "\n"
