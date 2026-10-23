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

Two recalls come out of that. **Detection recall** counts a bug surfaced at any severity (a finding about it near
the lines, warn or inform); **escalation recall** counts it only at the severity a reviewer acts on (``caught``).
A gap between them is a severity problem, not a finding problem. ``spread`` reads several runs of the same build
and reports each metric's mean and range, because one run of 45 bugs moves by a few cases on its own.

Labels are written by hand from the upstream history and must never be derived from Orion's output.
"""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from orion.providers import ProviderError

from .replay_eval import _head, prepare, review_commit

WINDOW = 12
ACTED_ON = ("warn", "block")


@dataclass
class Span:
    path: str
    line: int
    end_line: int | None = None

    def near(self, path: str, line: int | None) -> bool:
        """A finding with no line is about the whole file, so it is near no span."""
        return path == self.path and line is not None and self.line - WINDOW <= line <= (self.end_line or self.line) + WINDOW


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


def commit_id(value: Any) -> str:
    """A commit id from a labels file. YAML reads an unquoted all-digit prefix as a number (``0123`` as octal 83), which
    would silently name another commit, so such ids must be quoted."""
    if not isinstance(value, str):
        raise ValueError(f"commit {value!r} must be a quoted string in the labels file")
    return value


def load(path: Path) -> tuple[dict[str, dict[str, Any]], list[Case]]:
    doc = yaml.safe_load(path.read_text())
    cases = [Case(c["repo"], commit_id(c["commit"]), c.get("title", ""), commit_id(c["fix"]) if c.get("fix") else "", c.get("fix_title", ""), c.get("bug", ""), [Span(**s) for s in c.get("expect") or []], [str(w).lower() for w in c.get("must_mention") or []], c.get("skip", "")) for c in doc["cases"]]
    return doc["repos"], cases


JUDGE_SYSTEM = """You grade a code reviewer against a known bug. You are given the bug, written by hand from the later fix, and numbered review findings that sit near the buggy lines.
A finding counts only when it identifies this bug: the same root cause and the same kind of failing case, so that a developer acting on the finding would have fixed this bug. A different problem in the same code, a vaguer worry about the same lines, or a finding that names the right code but a different failure does not count.
Return the indexes of every finding that counts; an empty list when none does."""

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"matches": {"type": "array", "items": {"type": "integer"}}, "reason": {"type": "string"}},
    "required": ["matches", "reason"],
    "additionalProperties": False,
}


def model_about(case: Case, result: dict[str, Any], provider: Any) -> tuple[set[int], dict[str, Any] | None]:
    """Which findings near the expected span describe the labelled bug, by a model reading the hand-written label;
    the ids of the matching findings and the judge's token usage. The label, not Orion's output, is the reference."""
    near = [f for f in result.get("findings", []) if f.get("path") and any(s.near(f["path"], f.get("line")) for s in case.expect)]
    if not near:
        return set(), None
    listed = "\n\n".join(f"[{i}] {f['path']}:{f.get('line')} {f['title']}\n{f.get('body', '')}\nFails when: {f.get('failure', '')}" for i, f in enumerate(near))
    user = f"Bug (from the fix `{case.fix_title}`): {case.bug}\nWhere: " + ", ".join(f"{s.path}:{s.line}" + (f"-{s.end_line}" if s.end_line else "") for s in case.expect) + f"\n\nFindings:\n{listed}"
    out = provider.complete_json(JUDGE_SYSTEM, user, JUDGE_SCHEMA, schema_name="report_matches")
    picked = {i for i in (out.get("matches") or []) if isinstance(i, int) and 0 <= i < len(near)}
    return {id(near[i]) for i in picked}, getattr(provider, "last_usage", None)


def judge(case: Case, result: dict[str, Any], judged: set[int] | None = None) -> dict[str, Any]:
    """``judged``: ids of the findings a model judge said describe the bug (``model_about``); without it, a finding
    is about the bug when it uses one of the case's ``must_mention`` words."""
    findings = [f for f in result.get("findings", []) if f.get("path")]
    warns = [f for f in findings if f["severity"] in ACTED_ON]

    def about(f: dict[str, Any]) -> bool:
        if judged is not None:
            return id(f) in judged
        text = " ".join(str(f.get(k) or "") for k in ("title", "body", "failure")).lower()
        return not case.must_mention or any(w in text for w in case.must_mention)

    near_warn = [f for f in warns if about(f) and any(s.near(f["path"], f.get("line")) for s in case.expect)]
    near_inform = [f for f in findings if f["severity"] == "inform" and about(f) and any(s.near(f["path"], f.get("line")) for s in case.expect)]
    in_file = [f for f in warns if any(f["path"] == s.path for s in case.expect)]
    other_warns = [f for f in warns if not any(f is g for g in near_warn)]
    outcome = "caught" if near_warn else "in_file" if in_file else "inform_only" if near_inform else "missed"
    if result.get("error") or result.get("provider_error"):
        outcome = "error"  # a review that did not run in full measures nothing; it is not a miss
    return {
        "repo": case.repo,
        "commit": case.commit,
        "title": case.title,
        "bug": case.bug,
        "outcome": outcome,
        "detected": bool(near_warn or near_inform) and outcome != "error",
        "about": [[f["path"], f.get("line"), f["title"]] for f in near_warn + near_inform],  # every finding judged about the bug, for --reuse-judged
        "judge": "model" if judged is not None else "words",
        "matched": [{"severity": f["severity"], "source": f["source"], "path": f["path"], "line": f.get("line"), "title": f["title"], "failure": f.get("failure", ""), "notes": f.get("notes", [])} for f in near_warn + near_inform][:4],
        "warns": len(warns),
        "other_warns": len(other_warns),
        "warn_titles": [f"{f['path']}:{f.get('line')} {f['title']}" for f in warns][:6],
        "severity": result.get("severity"),
        "error": result.get("error"),
        "provider_error": result.get("provider_error"),
        "usage": result.get("usage") or {},
    }


def _slots(clone: Path, jobs: int) -> list[Path]:
    """``jobs`` checkouts of one clone that can hold different commits at once: the clone and detached worktrees beside it."""
    out = [clone]
    for i in range(1, jobs):
        wt = clone.with_name(f"{clone.name}.wt{i}")
        if not (wt / ".git").exists():
            subprocess.run(["git", "-C", str(clone), "worktree", "add", "-q", "--detach", "-f", str(wt)], check=True, capture_output=True)
        if (clone / "orion.yaml").exists() and not (wt / "orion.yaml").exists():
            shutil.copy(clone / "orion.yaml", wt / "orion.yaml")
        out.append(wt)
    return out


def run(cases: list[Case], repos: dict[str, dict[str, Any]], workdir: Path, *, orion: str, provider: str, model: str | None, progress=None, extra_args: list[str] | None = None, raw_dir: Path | None = None, jobs: int = 1, judge_provider: Any = None) -> dict[str, Any]:
    """``raw_dir``: where each case's full review JSON is kept, so a miss can be read without another model run.
    ``jobs``: cases reviewed at once, each in its own checkout (worktrees of the clone)."""
    todo = [c for c in cases if not c.skip]
    clones = {name: prepare(name, repos[name], workdir) for name in sorted({c.repo for c in todo})}
    heads = {name: _head(clone) for name, clone in clones.items()}
    free: dict[str, queue.Queue[Path]] = {}
    for name, clone in clones.items():
        free[name] = queue.Queue()
        for slot in _slots(clone, max(1, jobs)):
            free[name].put(slot)

    def one(c: Case) -> dict[str, Any]:
        slot = free[c.repo].get()
        try:
            result = review_commit(orion, slot, c.commit, provider, model, extra_args=extra_args)
        finally:
            free[c.repo].put(slot)
        if raw_dir is not None:
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / f"{c.repo}-{c.commit}.json").write_text(json.dumps(result, indent=1, default=str))
        row = judge_one(c, result, judge_provider)
        if progress:
            progress(row)
        return row

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        rows = list(pool.map(one, todo))
    for name, clone in clones.items():
        subprocess.run(["git", "-C", str(clone), "checkout", "-q", "-f", heads[name]], capture_output=True)
    return {"provider": provider, "model": model, "args": extra_args or [], "cases": rows, "skipped": [{"repo": c.repo, "commit": c.commit, "why": c.skip} for c in cases if c.skip], "summary": summarise(rows)}


_JUDGE_LOCK = threading.Lock()  # one judge provider is shared by the worker threads; its last_usage is per call


def judge_one(c: Case, result: dict[str, Any], judge_provider: Any = None) -> dict[str, Any]:
    """The case judged by the model when one is given; a judge that fails falls back to the words, and the row says so."""
    if judge_provider is None or result.get("error"):
        return judge(c, result)
    try:
        with _JUDGE_LOCK:
            ids, usage = model_about(c, result, judge_provider)
    except ProviderError as e:
        row = judge(c, result)
        row["judge_error"] = str(e)[:300]
        return row
    row = judge(c, result, ids)
    row["judge_usage"] = usage or {}
    return row


def regate(result: dict[str, Any]) -> dict[str, Any]:
    """A kept review as the evidence-gated build would have returned it, without another model call.

    The gate reads only what the review JSON records (changed symbols and their outside callers, sensitive areas,
    fix and revert history), and it only ever lowers, so applying it after the second reading leaves the same
    warnings as applying it before. It cannot see a data platform's verdict (readers, constraints, incidents), which
    the JSON does not keep, so use it on code-only reviews such as the recall sets."""
    from orion.codectx import Area, CodeContext
    from orion.review import ReviewContext, gate_severity
    from orion.symbols import SymbolChange

    out = json.loads(json.dumps(result))
    c = out.get("context") or {}
    code = CodeContext(areas=[{"area": Area(id=a["id"], reason=a.get("reason", ""), severity=a.get("severity", "inform")), "paths": a["paths"], "line": None} for a in c.get("areas", [])], history=c.get("history", []))
    symbols = [SymbolChange(s["name"], s["path"], s["kind"], s.get("line"), s.get("old", ""), s.get("new"), s.get("owner"), s.get("refs", [])) for s in out.get("symbols", [])]
    ctx = ReviewContext([], None, code=code, symbols=symbols)
    gate_severity(ctx, out.get("findings", []))
    out["escalation"] = {"policy": "evidence", "regated": True}
    return out


def _key(f: dict[str, Any]) -> tuple[Any, ...]:
    return (f.get("path"), f.get("line"), f.get("title"))


def rejudge(cases: list[Case], raw_dir: Path, judge_provider: Any = None, *, gate: bool = False, verdicts: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Judge kept raw reviews again (another judge, a changed label, or ``gate``: the evidence-gated severity) without reviewing anything.
    ``verdicts``: the rows of an earlier judged run of the same raw reviews; a finding it matched to the bug counts
    as about the bug again, so a severity-only change is scored without new judge calls (the judge never sees severity)."""
    earlier = {(r["repo"], r["commit"]): {tuple(a) for a in r["about"]} if "about" in r else {_key(m) for m in r["matched"]} for r in verdicts or []}
    rows = []
    for c in cases:
        p = raw_dir / f"{c.repo}-{c.commit}.json"
        if not c.skip and p.exists():
            result = json.loads(p.read_text())
            if gate:
                result = regate(result)
            if (c.repo, c.commit) in earlier:
                row = judge(c, result, {id(f) for f in result.get("findings", []) if _key(f) in earlier[(c.repo, c.commit)]})
                row["judge"] = "reused"
                rows.append(row)
            else:
                rows.append(judge_one(c, result, judge_provider))
    return rows


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in rows if r["outcome"] != "error"]
    n = len(ok)
    count = {k: sum(1 for r in ok if r["outcome"] == k) for k in ("caught", "in_file", "inform_only", "missed")}
    detected = sum(1 for r in ok if _detected(r))
    return {
        "cases": n,
        "errors": len(rows) - n,
        **count,
        "detected": detected,
        "detection_recall": round(detected / n, 3) if n else 0,
        "escalation_recall": round(count["caught"] / n, 3) if n else 0,
        "recall": round(count["caught"] / n, 3) if n else 0,
        "recall_in_file": round((count["caught"] + count["in_file"]) / n, 3) if n else 0,
        "warns_per_case": round(sum(r["warns"] for r in ok) / n, 2) if n else 0,
        "other_warns_per_case": round(sum(_other_warns(r) for r in ok) / n, 2) if n else 0,
        "tokens_in": sum(r["usage"].get("input_tokens", 0) for r in ok),
        "tokens_cache_write": sum(r["usage"].get("cache_creation_input_tokens", 0) for r in ok),
        "tokens_cache_read": sum(r["usage"].get("cache_read_input_tokens", 0) for r in ok),
        "tokens_out": sum(r["usage"].get("output_tokens", 0) for r in ok),
    }


def _detected(row: dict[str, Any]) -> bool:
    """Rows written before ``detected`` existed: caught or inform only means a finding about the bug was near it."""
    return row["detected"] if "detected" in row else row["outcome"] in ("caught", "inform_only")


def _other_warns(row: dict[str, Any]) -> int:
    return row["other_warns"] if "other_warns" in row else row["warns"] - (1 if row["outcome"] == "caught" else 0)


SPREAD_METRICS = ("detection_recall", "escalation_recall", "recall_in_file", "inform_only", "missed", "warns_per_case", "other_warns_per_case", "errors")


def spread(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Several runs of one build on one case set: each metric's mean, min and max, and the cases whose outcome
    changed between runs (the noise any single-run comparison has to clear)."""
    if not results:
        raise ValueError("no runs to compare")
    sums = [r["summary"] if "detection_recall" in r["summary"] else summarise(r["cases"]) for r in results]
    metrics = {}
    for k in SPREAD_METRICS:
        vals = [s[k] for s in sums]
        metrics[k] = {"mean": round(sum(vals) / len(vals), 3), "min": min(vals), "max": max(vals), "runs": vals}
    by_case: dict[tuple[str, str], list[str]] = {}
    for r in results:
        for row in r["cases"]:
            by_case.setdefault((row["repo"], row["commit"]), []).append(row["outcome"])
    # a run whose review errored measured nothing for that case, so it is not a change of outcome
    flips = [{"repo": k[0], "commit": k[1], "outcomes": v} for k, v in by_case.items() if len(set(v) - {"error"}) > 1]
    return {"runs": len(results), "provider": results[0].get("provider"), "model": results[0].get("model"), "cases": sums[0]["cases"], "metrics": metrics, "unstable_cases": flips}


def spread_markdown(sp: dict[str, Any], title: str = "") -> str:
    head = title or f"`{sp['provider']}{':' + sp['model'] if sp['model'] else ''}`"
    lines = [f"# Recall spread: {head}, {sp['runs']} runs of {sp['cases']} bugs", "", "| metric | mean | min | max | runs |", "| --- | --- | --- | --- | --- |"]
    for k, m in sp["metrics"].items():
        lines.append(f"| {k.replace('_', ' ')} | {m['mean']} | {m['min']} | {m['max']} | {' / '.join(str(v) for v in m['runs'])} |")
    lines += ["", f"{len(sp['unstable_cases'])} of {sp['cases']} cases changed outcome between runs."]
    if sp["unstable_cases"]:
        lines += ["", "| repo | commit | outcomes |", "| --- | --- | --- |"] + [f"| {u['repo']} | `{u['commit']}` | {', '.join(u['outcomes'])} |" for u in sp["unstable_cases"]]
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


LEGEND = (
    "Outcomes, scored in this order: **caught**, a warn or block finding near the labelled lines that names the bug; "
    "**in_file**, a warn or block finding in the bug's file about something else; **inform_only**, the bug named near its "
    "lines only at `inform`; **missed**, none of these. The finding column shows the first finding that names the bug, at "
    "its own severity, or the warnings of an in_file row that has none. A case with no finding near the labelled lines is "
    "not sent to the judge, so its `judge_usage` is empty. Finding text is capped (`failure` at 500 characters, a note's "
    "reason at 300), so a long one ends mid-word."
)


def to_markdown(res: dict[str, Any]) -> str:
    s = res["summary"]
    label = f"`{res['provider']}{':' + res['model'] if res['model'] else ''}`" + (f" with `{' '.join(res['args'])}`" if res.get("args") else "")
    judged = f", judged by `{res['judge_model']}` reading each label" if res.get("judge_model") else ", judged by `must_mention` words"
    lines = [f"# Recall eval: {label}, {s['cases']} bugs at the commit that introduced them{judged}", "", "| metric | value |", "| --- | --- |"]
    for k in ("cases", "errors", "caught", "in_file", "inform_only", "missed", "detection_recall", "escalation_recall", "recall_in_file", "warns_per_case", "other_warns_per_case", "tokens_in", "tokens_cache_write", "tokens_cache_read", "tokens_out"):
        if k in s:
            lines.append(f"| {k.replace('_', ' ')} | {s[k]} |")
    lines += ["", LEGEND, "", "| repo | commit | bug | outcome | finding |", "| --- | --- | --- | --- | --- |"]
    for r in res["cases"]:
        m = r["matched"][0] if r["matched"] else None
        found = f"{m['severity']} `{m['path']}:{m['line']}` {_cell(m['title'])}" if m else ("; ".join(_cell(t) for t in r["warn_titles"]) if r["outcome"] == "in_file" else "")
        lines.append(f"| {r['repo']} | `{r['commit']}` | {_cell(r['bug'] or r['title'])} | **{r['outcome']}** | {found} |")
    if res.get("skipped"):
        lines += ["", "Skipped: " + "; ".join(f"{k['repo']} `{k['commit']}` ({k['why']})" for k in res["skipped"])]
    return "\n".join(lines) + "\n"
