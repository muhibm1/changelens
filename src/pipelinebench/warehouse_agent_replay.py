"""Replay of the ChangeLens warehouse agent over real bugs and ordinary commits, scored against hand-written labels.

Same labels and same ordinary-commit sampling as ``warehouse_replay`` (the rules-only replay). For each labelled bug
the agent reviews the introducing commit; for each sampled ordinary commit it reviews that commit, which is where
false alarms are counted.

Scoring, per in-scope bug. Only ``bug`` findings (confirmed by the second, skeptical pass) count; ``risk`` notes never
count as a catch or as a false alarm.

- strict: a bug finding in one of the label's files that either overlaps the expected lines (within 3 lines) or
  names one of the label's must_mention words. This is the headline number.
- loose: the rules replay's own measure: a bug finding anywhere that overlaps the lines or names a must_mention word.

False alarms: the share of ordinary commits with at least one bug finding. Ordinary commits are unlabelled, so some
flagged ones may hold real bugs; the share is an upper bound on the false-alarm rate.

Results are cached per (repo, commit, agent version, model) in ``--cache`` so a rerun only reviews what is new.

Run: ``python -m pipelinebench.warehouse_agent_replay --repo <clone> --labels <yaml>... --sample 20 --out <dir>``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from changelens.warehouse_agent import AGENT_VERSION, AgentFinding, review
from pipelinebench.warehouse_replay import load_bugs, ordinary_commits

SLACK = 3
_lock = threading.Lock()
FIX_WORDS = ("fix", "revert", "hotfix", "wrong", "duplicate", "dedup", "fanout", "fan out", "missing", "incorrect", "bug", "broken", "null")

JUDGE_PROMPT = """A labelled bug in a SQL warehouse commit, and the findings a code reviewer filed on that commit.
Answer whether one of the findings identifies the labelled bug: the same defect (same model or column and the same
mechanism, for example the same join fanning out, the same dedup dropping rows, the same column missing). A finding
about a different defect in the same commit does not count, and neither does a vague mention of the same file.

Labelled bug: {bug}
Where: {where}

Findings:
{findings}

Return the index of the matching finding, or -1 if none matches."""
JUDGE_SCHEMA = {"type": "object", "properties": {"index": {"type": "integer"}, "reason": {"type": "string"}}, "required": ["index", "reason"]}


def judge(bug: dict[str, Any], findings: list[AgentFinding], model: str = "sonnet") -> dict[str, Any]:
    """Ask a model, with no tools, whether one of the findings is the labelled bug."""
    import json as _json
    import subprocess

    if not findings:
        return {"index": -1, "reason": "no findings"}
    listing = "\n".join(f"{i}: [{f.severity}] {f.path}:{f.line} {f.title}. {f.explanation}" for i, f in enumerate(findings))
    where = ", ".join(f"{s['path']}:{s['line']}" for s in bug["expect"])
    prompt = JUDGE_PROMPT.format(bug=bug["bug"], where=where, findings=listing)
    cmd = ["claude", "-p", "--model", model, "--tools", "", "--setting-sources", "", "--strict-mcp-config", "--no-session-persistence", "--output-format", "json", "--json-schema", _json.dumps(JUDGE_SCHEMA)]
    for _ in range(3):
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=300)
        try:
            out = _json.loads(p.stdout).get("structured_output")
        except _json.JSONDecodeError:
            out = None
        if isinstance(out, dict):
            return out
    return {"index": -2, "reason": "judge failed"}


def later_fixed(repo: Path, commit: str, f: AgentFinding, look: int = 8) -> str | None:
    """A later commit with a fix-like message that rewrote the flagged lines, if one exists among the next few that touched them."""
    import subprocess

    if f.line is None:
        return None
    hi = (f.end_line or f.line) + 1
    lo = max(1, f.line - 1)
    try:
        out = subprocess.run(["git", "-C", str(repo), "log", "--reverse", "--format=@@%h %s", "--no-patch", f"-L{lo},{hi}:{f.path}", f"{commit}..HEAD"], capture_output=True, text=True, timeout=120).stdout
    except subprocess.TimeoutExpired:
        return None
    msgs = [ln[2:] for ln in out.splitlines() if ln.startswith("@@")][:look]
    for m in msgs:
        if any(w in m.lower() for w in FIX_WORDS):
            return m[:120]
    return None


def _key(repo: Path, commit: str, model: str, verify: bool) -> str:
    return f"{repo.name}:{commit[:10]}:{AGENT_VERSION}:{model}:{'v' if verify else 'nv'}"


def load_cache(path: Path | None) -> dict[str, Any]:
    cache: dict[str, Any] = {}
    if path and path.exists():
        for ln in path.read_text().splitlines():
            if ln.strip():
                d = json.loads(ln)
                cache[d["key"]] = d["result"]
    return cache


def _findings(result: dict[str, Any]) -> list[AgentFinding]:
    return [AgentFinding(**f) for f in result.get("findings", [])]


def _near(f: AgentFinding, span: dict[str, Any]) -> bool:
    if f.line is None or not _same_path(f.path, span["path"]):
        return False
    lo, hi = span["line"] - SLACK, (span.get("end_line") or span["line"]) + SLACK
    f_hi = f.end_line or f.line
    return f.line <= hi and f_hi >= lo


def _same_path(a: str, b: str) -> bool:
    a, b = a.strip("/"), b.strip("/")
    return a == b or a.endswith("/" + b) or b.endswith("/" + a)


def score_bug(bug: dict[str, Any], findings: list[AgentFinding]) -> dict[str, Any]:
    bugs = [f for f in findings if f.severity == "bug"]
    words = [w.lower() for w in bug["must_mention"]]
    paths = [s["path"] for s in bug["expect"]]

    def says(f: AgentFinding) -> bool:
        text = (f.title + " " + f.explanation + " " + f.evidence).lower()
        return any(w in text for w in words)

    strict = [f for f in bugs if any(_near(f, s) for s in bug["expect"]) or (any(_same_path(f.path, p) for p in paths) and says(f))]
    loose = [f for f in bugs if any(_near(f, s) for s in bug["expect"]) or says(f)]
    risk_strict = [f for f in findings if f.severity == "risk" and (any(_near(f, s) for s in bug["expect"]) or (any(_same_path(f.path, p) for p in paths) and says(f)))]
    return {
        "commit": str(bug["commit"]),
        "title": bug["title"],
        "category": bug["category"],
        "in_scope": bug["in_scope"],
        "source": bug.get("source"),
        "hit_strict": bool(strict),
        "hit_loose": bool(loose),
        "hit_risk_only": bool(risk_strict) and not strict,
        "matched": [f"{f.path}:{f.line} {f.title}" for f in strict or loose],
        "findings": [f"[{f.severity}] {f.path}:{f.line} {f.title}" for f in findings],
    }


def run(repos: list[tuple[Path, list[Path]]], sample: int, seed: int, model: str, verify: bool, jobs: int, cache_path: Path | None, out: Path | None, effort: str | None, only_bugs: bool = False, use_judge: bool = True) -> dict[str, Any]:
    cache = load_cache(cache_path)
    work: list[tuple[str, Path, str, dict[str, Any] | None]] = []
    for repo, labels in repos:
        bugs = load_bugs(labels) if labels else []
        for b in bugs:
            work.append(("bug", repo, str(b["commit"]), b))
        if sample and not only_bugs:
            exclude = {str(b["commit"]) for b in bugs} | {str(b["fix"]) for b in bugs if b.get("fix")}
            for c in ordinary_commits(repo, exclude, sample, seed):
                work.append(("ordinary", repo, c, None))

    def one(item: tuple[str, Path, str, dict[str, Any] | None]) -> tuple[Any, dict[str, Any]]:
        kind, repo, commit, bug = item
        k = _key(repo, commit, model, verify)
        if k in cache and not cache[k].get("error"):
            return item, cache[k]
        try:
            r = review(repo, commit, model=model, verify=verify, effort=effort).to_json()
        except Exception as e:  # noqa: BLE001 - one broken commit is recorded as an error, never stops the replay
            r = {"findings": [], "seconds": 0.0, "error": f"{type(e).__name__}: {e}"[:500]}
        with _lock:
            cache[k] = r
            if cache_path:
                with cache_path.open("a") as fh:
                    fh.write(json.dumps({"key": k, "result": r}) + "\n")
        tag = "ERR" if r.get("error") else f"{sum(1 for f in r['findings'] if f['severity'] == 'bug')}b/{len(r['findings'])}"
        print(f"{kind:8} {repo.name:20} {commit[:10]} {tag} {r['seconds']}s", flush=True)
        return item, r

    with ThreadPoolExecutor(jobs) as pool:
        results = list(pool.map(one, work))

    res: dict[str, Any] = {"agent": AGENT_VERSION, "model": model, "verify": verify, "bugs": [], "ordinary": [], "errors": []}
    for (kind, repo, commit, bug), r in results:
        if r.get("error"):
            res["errors"].append({"repo": repo.name, "commit": commit[:10], "error": r["error"]})
        fs = _findings(r)
        if kind == "bug":
            row = score_bug(bug, fs)
            row["repo"] = repo.name
            row["error"] = r.get("error")
            row["_judge_args"] = (bug, fs)
            res["bugs"].append(row)
        else:
            bf = [f for f in fs if f.severity == "bug"]
            res["ordinary"].append({"repo": repo.name, "commit": commit[:10], "error": r.get("error"), "bug_findings": [f"[{f.category}] {f.path}:{f.line} {f.title} :: {f.explanation[:300]}" for f in bf], "later_fixed": [later_fixed(repo, commit, f) for f in bf], "risk_findings": sum(1 for f in fs if f.severity == "risk")})

    def judged(row: dict[str, Any]) -> None:
        bug, fs = row.pop("_judge_args")
        if not use_judge or row.get("error"):
            row["hit_judge"] = row["hit_strict"]
            row["hit_judge_any"] = row["hit_strict"] or row["hit_risk_only"]
            return
        jk = f"judge:{_key(Path(row['repo']), row['commit'], model, verify)}:{hashlib.sha256(bug['bug'].encode()).hexdigest()[:12]}"
        if jk in cache and cache[jk].get("index") != -2:
            j = cache[jk]
        else:
            j = judge(bug, fs)
            with _lock:
                if j.get("index") == -2:
                    row["judge"] = j
                    row["hit_judge"] = row["hit_judge_any"] = False
                    return
                cache[jk] = j
                if cache_path:
                    with cache_path.open("a") as fh:
                        fh.write(json.dumps({"key": jk, "result": j}) + "\n")
        i = j.get("index", -1)
        row["judge"] = j
        row["hit_judge"] = 0 <= i < len(fs) and fs[i].severity == "bug"
        row["hit_judge_any"] = 0 <= i < len(fs)

    with ThreadPoolExecutor(jobs) as pool:
        list(pool.map(judged, res["bugs"]))
    res["summary"] = summarise(res)
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "agent-replay.json").write_text(json.dumps(res, indent=1))
    return res


def summarise(res: dict[str, Any]) -> dict[str, Any]:
    scope = [b for b in res["bugs"] if b["in_scope"]]

    def rate(rows: list[dict[str, Any]], key: str) -> str:
        n = sum(1 for r in rows if r[key])
        return f"{n}/{len(rows)} ({100 * n / len(rows):.0f}%)" if rows else "0/0"

    by_repo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for b in scope:
        by_repo[b["repo"]].append(b)
        by_cat[b["category"]].append(b)
    ordn = [o for o in res["ordinary"] if not o["error"]]
    flagged = [o for o in ordn if o["bug_findings"]]
    ord_by_repo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for o in ordn:
        ord_by_repo[o["repo"]].append(o)
    return {
        "in_scope_bugs": len(scope),
        "caught_judge_bug": rate(scope, "hit_judge"),
        "caught_judge_bug_or_risk": rate(scope, "hit_judge_any"),
        "caught_strict": rate(scope, "hit_strict"),
        "caught_loose": rate(scope, "hit_loose"),
        "risk_note_only": rate(scope, "hit_risk_only"),
        "by_repo": {k: rate(v, "hit_judge") for k, v in sorted(by_repo.items())},
        "by_category": {k: rate(v, "hit_judge") for k, v in sorted(by_cat.items())},
        "ordinary_commits": len(ordn),
        "false_alarm_share": f"{len(flagged)}/{len(ordn)} ({100 * len(flagged) / len(ordn):.0f}%)" if ordn else "0/0",
        "false_alarm_share_excluding_later_fixed": (lambda fl: f"{len(fl)}/{len(ordn)} ({100 * len(fl) / len(ordn):.0f}%)" if ordn else "0/0")([o for o in flagged if not all(o["later_fixed"])]),
        "commits_with_bug_or_risk": f"{sum(1 for o in ordn if o['bug_findings'] or o['risk_findings'])}/{len(ordn)}",
        "false_alarm_by_repo": {k: f"{sum(1 for o in v if o['bug_findings'])}/{len(v)}" for k, v in sorted(ord_by_repo.items())},
        "errors": len(res["errors"]),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, action="append", required=True, help="a clone; repeat, one --labels group per --repo")
    ap.add_argument("--labels", type=Path, nargs="*", action="append", default=[])
    ap.add_argument("--sample", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20261117)
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--effort")
    ap.add_argument("--no-verify", action="store_true")
    ap.add_argument("--jobs", type=int, default=6)
    ap.add_argument("--cache", type=Path)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--only-bugs", action="store_true")
    ap.add_argument("--no-judge", action="store_true")
    a = ap.parse_args()
    labels = a.labels + [[]] * (len(a.repo) - len(a.labels))
    res = run(list(zip(a.repo, labels, strict=True)), a.sample, a.seed, a.model, not a.no_verify, a.jobs, a.cache, a.out, a.effort, a.only_bugs, not a.no_judge)
    print(json.dumps(res["summary"], indent=1))
    for b in res["bugs"]:
        mark = "HIT " if b["hit_judge"] else "jrisk" if b["hit_judge_any"] else ("loose" if b["hit_loose"] else ("risk" if b["hit_risk_only"] else "miss"))
        print(f"{mark:5} {b['repo']:20} {b['category']:30} {b['commit']:11} {b['title'][:50]}")


if __name__ == "__main__":
    main()
