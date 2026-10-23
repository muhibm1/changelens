"""Replay eval: what does ``orion review`` say about changes that real maintainers merged?

The review eval (``review_eval.py``) plants bugs in small hand-written diffs. This one replays merged
commits from public repositories, where nearly every change is correct, so it measures what a team
would actually see: how many warnings per change, and how many of them hold up.

``pipelinebench/review/replay.yaml`` names each repository (URL, an ``orion.yaml`` to write into the
clone) and each commit, with hand labels for warnings already judged against the upstream history:

- ``confirmed``: upstream fixed exactly this later. A run that misses it loses recall.
- ``plausible``: specific and true of the code, never fixed upstream. A fair question to the author.
- ``noise``: wrong, or an announced change reported as a bug.
- ``setup``: an artifact of replaying against a graph synced after the change (a model the change deletes
  is already gone from it). Counted apart, neither as signal nor as noise.

A warning that matches no label is ``unjudged`` and listed for a person to label. Labels are written by
hand from the upstream history and must never be derived from Orion's output.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

WINDOW = 12  # lines; the model may anchor the same finding a few lines apart from run to run
ACTED_ON = ("warn", "block")


@dataclass
class Label:
    path: str
    line: int
    verdict: str  # confirmed | plausible | noise | setup
    note: str = ""
    end_line: int | None = None  # a finding anywhere in line..end_line matches, for a problem that spans a block

    def matches(self, path: str, line: int) -> bool:
        if path != self.path:
            return False
        return self.line - WINDOW <= line <= (self.end_line or self.line) + WINDOW

    def distance(self, line: int) -> int:
        return max(self.line - line, line - (self.end_line or self.line), 0)


@dataclass
class Case:
    repo: str
    commit: str
    title: str = ""
    labels: list[Label] = field(default_factory=list)


def load(path: Path) -> tuple[dict[str, dict[str, Any]], list[Case]]:
    doc = yaml.safe_load(path.read_text())
    cases = [Case(c["repo"], str(c["commit"]), c.get("title", ""), [Label(**lb) for lb in c.get("labels") or []]) for c in doc["cases"]]
    return doc["repos"], cases


def prepare(name: str, spec: dict[str, Any], workdir: Path) -> Path:
    """Clone once, write the repository's orion.yaml (untracked), and sync when the config has a platform."""
    clone = workdir / name
    if not (clone / ".git").exists():
        subprocess.run(["git", "clone", "-q", "--filter=blob:none", spec["url"], str(clone)], check=True)
    cfg = clone / "orion.yaml"
    if not cfg.exists():
        cfg.write_text(spec.get("config") or "db: .orion/graph.duckdb\n")
    # Keep the config and the graph out of the diff under review: `orion review --git` adds untracked files.
    exclude = clone / ".git" / "info" / "exclude"
    if exclude.parent.is_dir():
        have = {ln.strip() for ln in (exclude.read_text().splitlines() if exclude.exists() else [])}
        missing = [p for p in ("orion.yaml", ".orion/") if p not in have]
        if missing:
            with exclude.open("a") as fh:
                fh.write("".join(f"\n{p}" for p in missing) + "\n")
    return clone


def review_commit(orion: str, clone: Path, commit: str, provider: str, model: str | None, extra_args: list[str] | None = None) -> dict[str, Any]:
    subprocess.run(["git", "-C", str(clone), "checkout", "-q", "-f", commit], check=True)
    cmd = [orion, "review", "--git", f"{commit}^", "--provider", provider, "--format", "json"] + (["--model", model] if model else []) + list(extra_args or [])
    # Every eval review is a fresh sample: a cached answer would freeze one run's result into the next.
    p = subprocess.run(cmd, cwd=clone, capture_output=True, text=True, env={**os.environ, "ORION_REVIEW_CACHE": "0"})
    if p.returncode not in (0, 1):  # 1 is a block verdict
        return {"error": (p.stderr or p.stdout)[-2000:]}
    return json.loads(p.stdout)


def judge(case: Case, result: dict[str, Any]) -> dict[str, Any]:
    warns = [f for f in result.get("findings", []) if f["severity"] in ACTED_ON]
    rows = []
    hit: set[int] = set()
    for f in warns:
        line = f.get("line") or 0
        near = sorted((lb.distance(line), i) for i, lb in enumerate(case.labels) if lb.matches(f["path"], line))
        match = near[0][1] if near else None
        verdict = case.labels[match].verdict if match is not None else "unjudged"
        if match is not None:
            hit.add(match)
        rows.append({"source": f["source"], "path": f["path"], "line": f.get("line"), "title": f["title"], "verdict": verdict})
    confirmed = [i for i, lb in enumerate(case.labels) if lb.verdict == "confirmed"]
    return {
        "repo": case.repo,
        "commit": case.commit,
        "title": case.title,
        "severity": result.get("severity"),
        "error": result.get("error"),
        "provider_error": result.get("provider_error"),
        "warns": rows,
        "confirmed_expected": len(confirmed),
        "confirmed_caught": sum(1 for i in confirmed if i in hit),
        "usage": result.get("usage") or {},
    }


def _head(clone: Path) -> str:
    """The ref to come back to after the run: the branch name, or the commit when detached (`checkout -` would land on the previous case)."""
    r = subprocess.run(["git", "-C", str(clone), "symbolic-ref", "-q", "--short", "HEAD"], capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    return subprocess.run(["git", "-C", str(clone), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def run(cases: list[Case], repos: dict[str, dict[str, Any]], workdir: Path, *, orion: str, provider: str, model: str | None, progress=None) -> dict[str, Any]:
    clones = {name: prepare(name, repos[name], workdir) for name in sorted({c.repo for c in cases})}
    heads = {name: _head(clone) for name, clone in clones.items()}
    unsynced = {n for n, clone in clones.items() if repos[n].get("needs_sync") and not (clone / ".orion" / "graph.duckdb").exists()}
    rows = []
    for c in cases:
        if c.repo in unsynced:
            result = {"error": f"{c.repo} needs a compiled manifest and `orion sync` in {clones[c.repo]} first"}
        else:
            result = review_commit(orion, clones[c.repo], c.commit, provider, model)
        row = judge(c, result)
        rows.append(row)
        if progress:
            progress(row)
    for name, clone in clones.items():
        subprocess.run(["git", "-C", str(clone), "checkout", "-q", "-f", heads[name]], capture_output=True)
    return {"provider": provider, "model": model, "cases": rows, "summary": summarise(rows)}


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if not r["error"]]
    count = {v: sum(1 for r in ok for w in r["warns"] if w["verdict"] == v) for v in ("confirmed", "plausible", "noise", "unjudged", "setup")}
    warns = sum(count.values()) - count["setup"]
    return {
        "changes": len(ok),
        "errors": len(rows) - len(ok),
        "changes_warned": sum(1 for r in ok if any(w["verdict"] != "setup" for w in r["warns"])),
        "warns": warns,
        "warns_per_change": round(warns / len(ok), 2) if ok else 0,
        **count,
        "confirmed_recall": f"{sum(r['confirmed_caught'] for r in ok)}/{sum(r['confirmed_expected'] for r in ok)}",
        "tokens_in": sum(r["usage"].get("input_tokens", 0) for r in ok),
        "tokens_out": sum(r["usage"].get("output_tokens", 0) for r in ok),
    }


def to_markdown(res: dict[str, Any]) -> str:
    s = res["summary"]
    lines = [
        f"# Replay eval: `{res['provider']}{':' + res['model'] if res['model'] else ''}`, {s['changes']} merged changes",
        "",
        "| metric | value |",
        "| --- | --- |",
    ]
    for k in ("changes", "errors", "changes_warned", "warns", "warns_per_change", "confirmed", "plausible", "noise", "unjudged", "setup", "confirmed_recall", "tokens_in", "tokens_out"):
        lines.append(f"| {k.replace('_', ' ')} | {s[k]} |")
    lines += ["", "| repo | commit | change | severity | warnings |", "| --- | --- | --- | --- | --- |"]
    for r in res["cases"]:
        ws = "; ".join(f"**{w['verdict']}** `{w['path']}:{w['line']}` {w['title']}" for w in r["warns"]) or ("error" if r["error"] else "none")
        lines.append(f"| {r['repo']} | `{r['commit']}` | {r['title']} | {r['severity'] or '-'} | {ws} |")
    return "\n".join(lines) + "\n"
