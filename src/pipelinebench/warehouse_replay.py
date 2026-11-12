"""Replay of the deterministic warehouse rules over a repository's history, scored against hand-written bug labels.

For each labelled bug it takes the introducing commit's diff against its parent, runs ``changelens.warehouse_rules`` on
it (no model, no graph) and scores a hit when a finding sits on the lines the label expects or names a word the label
says a correct finding mentions. Then it runs the same rules on a seeded random sample of ordinary commits that touch
models, which is where the false alarms are counted: findings per commit and the share of commits with at least one.

Labels are written by hand from the upstream history (see ``pipelinebench/review/history-calitp.yaml`` and the
development set that goes with it) and are never derived from ChangeLens's output. Nothing in the rules knows a commit id,
a path or a title from the labels: the rules see only a diff and the repository.

Run: ``python -m pipelinebench.warehouse_replay --repo <clone> --labels dev.yaml history-calitp.yaml``.
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import yaml

from changelens.diffparse import parse
from changelens.warehouse_rules import RULES_VERSION, Finding, GitView, check_change_full

# history-calitp.yaml has no category field; these are read by hand from each entry's `bug` sentence there.
HISTORY_CATEGORIES = {
    "34716dcc27": "other",  # ref() replaced by hardcoded table names
    "b111f6f570": "nullability_or_cast",  # SAFE_CAST to TIMESTAMP yields NULL
    "f26b2970ab": "row_count_change",  # dedup partitions by the wrong columns
    "39cdaa1dd": "documented_column_not_selected",
    "6a9eae65a2": "other",  # a macro call lost its WHERE
    "0dfe3b65e7": "row_count_change",  # cross join fans out
}


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def load_bugs(paths: list[Path]) -> list[dict[str, Any]]:
    bugs: list[dict[str, Any]] = []
    for p in paths:
        doc = yaml.safe_load(p.read_text())
        for b in doc.get("bugs", []):
            b = dict(b)
            b.setdefault("category", HISTORY_CATEGORIES.get(str(b["commit"])[:10]) or HISTORY_CATEGORIES.get(str(b["commit"])[:9], "other"))
            b.setdefault("in_scope", True)
            b["must_mention"] = [str(w) for w in (b.get("must_mention") or []) if w]
            b["source"] = p.name
            bugs.append(b)
    return bugs


def review_commit(repo: Path, commit: str) -> tuple[list[Finding], float, list[dict[str, Any]]]:
    """Findings and unresolved notes for ``commit`` reviewed against its first parent, and the seconds that took."""
    t = time.monotonic()
    parent = git(repo, "rev-parse", f"{commit}^").strip()
    full = git(repo, "rev-parse", commit).strip()
    diff = git(repo, "diff", "-M", "--unified=3", "--no-color", parent, full, "--", "*.sql", "*.yml", "*.yaml")
    files = parse(diff)
    res = check_change_full(files, GitView(repo, parent, full))
    return res.findings, time.monotonic() - t, res.unresolved


def _overlaps(f: Finding, span: dict[str, Any]) -> bool:
    if f.path != span["path"] or f.line is None:
        return False
    lo, hi = span["line"], span.get("end_line") or span["line"]
    f_hi = f.end_line or f.line
    return f.line <= hi and f_hi >= lo


def score_bug(bug: dict[str, Any], findings: list[Finding]) -> dict[str, Any]:
    by_loc = [f for f in findings if any(_overlaps(f, s) for s in bug["expect"])]
    words = [w.lower() for w in bug["must_mention"]]
    by_word = [f for f in findings if f not in by_loc and any(w in (f.title + " " + f.body).lower() for w in words)]
    return {
        "commit": bug["commit"],
        "title": bug["title"],
        "category": bug["category"],
        "in_scope": bug["in_scope"],
        "hit_location": bool(by_loc),
        "hit_word": bool(by_word),
        "hit_location_warn": any(f.severity == "warn" for f in by_loc),
        "by_location": [f"{f.rule}:{f.severity}:{f.path}:{f.line}" for f in by_loc],
        "by_word": [f"{f.rule}:{f.severity}:{f.path}:{f.line}" for f in by_word],
        "findings": len(findings),
    }


def model_dirs(repo: Path) -> list[str]:
    """Where the repository's dbt projects keep models: ``<dir of every dbt_project.yml ever added>/models``, else ``models``."""
    out = git(repo, "log", "--all", "--format=", "--name-only", "--", "dbt_project.yml", "*/dbt_project.yml").split()
    roots = sorted({str(Path(p).parent) for p in out if "dbt_packages" not in p and "integration_tests" not in p and "/target/" not in p})
    dirs = [("" if r == "." else r + "/") + "models" for r in roots]
    return dirs or ["models"]


def ordinary_commits(repo: Path, exclude: set[str], n: int, seed: int) -> list[str]:
    """``n`` commits sampled with ``seed`` from the non-merge commits that touch model SQL or schema files of the repo's own dbt projects."""
    specs = [f"{d}/*{ext}" for d in model_dirs(repo) for ext in (".sql", ".yml")]
    out = git(repo, "log", "--no-merges", "--format=%H", "--", *specs).split()
    pool = sorted(c for c in out if not any(c.startswith(e) for e in exclude))
    return random.Random(seed).sample(pool, min(n, len(pool)))


def _one(args: tuple[Path, str]) -> tuple[list[Finding], float, list[dict[str, Any]]]:
    return review_commit(*args)


def run(repo: Path, labels: list[Path], sample: int, seed: int, out: Path | None, jobs: int = 1) -> dict[str, Any]:
    bugs = load_bugs(labels)
    exclude = {str(b["commit"]) for b in bugs} | {str(b["fix"]) for b in bugs if b.get("fix")}
    res: dict[str, Any] = {"rules": RULES_VERSION, "repo": repo.name, "bugs": [], "ordinary": []}
    with ProcessPoolExecutor(jobs) as pool:
        bug_results = list(pool.map(_one, [(repo, str(b["commit"])) for b in bugs]))
        commits = ordinary_commits(repo, exclude, sample, seed)
        ord_results = list(pool.map(_one, [(repo, c) for c in commits], chunksize=4))
    for b, (findings, secs, notes) in zip(bugs, bug_results, strict=True):
        row = score_bug(b, findings)
        row["hit_note"] = any(n.get("line") is not None and any(n["path"] == sp["path"] and sp["line"] - 1 <= n["line"] <= (sp.get("end_line") or sp["line"]) + 1 for sp in b["expect"]) for n in notes)
        row["seconds"] = round(secs, 2)
        row["all_findings"] = [f"{f.rule}:{f.severity}:{f.path}:{f.line}" for f in findings]
        res["bugs"].append(row)
    for c, (findings, secs, notes) in zip(commits, ord_results, strict=True):
        res["ordinary"].append({"commit": c[:10], "seconds": round(secs, 2), "notes": len(notes), "findings": [{"rule": f.rule, "severity": f.severity, "path": f.path, "line": f.line, "title": f.title} for f in findings]})
    res["summary"] = summarise(res)
    if out:
        out.mkdir(parents=True, exist_ok=True)
        (out / "warehouse-replay.json").write_text(json.dumps(res, indent=1))
    return res


def summarise(res: dict[str, Any]) -> dict[str, Any]:
    scope = [b for b in res["bugs"] if b["in_scope"]]
    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for b in scope:
        by_cat[b["category"]].append(b)

    def rate(rows: list[dict[str, Any]], key: str) -> str:
        return f"{sum(1 for r in rows if r[key])}/{len(rows)}"

    for b in scope:
        b["hit_any"] = b["hit_location"] or b["hit_word"]
        b["hit_with_notes"] = b["hit_any"] or b["hit_note"]
    ordinary = res["ordinary"]
    n = len(ordinary) or 1
    warn = [r for r in ordinary if any(f["severity"] == "warn" for f in r["findings"])]
    anyf = [r for r in ordinary if r["findings"]]
    per_rule = Counter(f["rule"] for r in ordinary for f in r["findings"])
    return {
        "in_scope_bugs": len(scope),
        "recall_location_or_word": rate(scope, "hit_any"),
        "recall_location_only": rate(scope, "hit_location"),
        "recall_with_unresolved_notes": rate(scope, "hit_with_notes"),
        "recall_location_warn_only": rate(scope, "hit_location_warn"),
        "by_category": {c: {"bugs": len(rows), "location_or_word": rate(rows, "hit_any"), "location_only": rate(rows, "hit_location")} for c, rows in sorted(by_cat.items())},
        "ordinary_commits": len(ordinary),
        "findings_total": sum(len(r["findings"]) for r in ordinary),
        "findings_per_commit": round(sum(len(r["findings"]) for r in ordinary) / n, 3),
        "warn_findings_per_commit": round(sum(1 for r in ordinary for f in r["findings"] if f["severity"] == "warn") / n, 3),
        "share_commits_with_a_finding": round(len(anyf) / n, 3),
        "share_commits_with_a_warn": round(len(warn) / n, 3),
        "findings_by_rule": dict(per_rule.most_common()),
        "median_seconds": sorted(r["seconds"] for r in ordinary)[len(ordinary) // 2] if ordinary else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--labels", type=Path, nargs="+", required=True)
    ap.add_argument("--sample", type=int, default=200)
    ap.add_argument("--seed", type=int, default=20261112)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--show", help="print the findings for one commit and stop")
    a = ap.parse_args()
    if a.show:
        findings, _, _ = review_commit(a.repo, a.show)
        for f in findings:
            print(f"[{f.severity}] {f.rule} {f.path}:{f.line}\n    {f.title}\n    {f.body[:400]}\n    unresolved={f.unresolved}")
        return
    res = run(a.repo, a.labels, a.sample, a.seed, a.out, a.jobs)
    print(json.dumps(res["summary"], indent=1))
    for b in res["bugs"]:
        mark = "HIT " if b["hit_location"] else ("word" if b["hit_word"] else "miss")
        print(f"{mark} {b['category']:32} {b['commit']:11} {b['title'][:60]}  loc={b['by_location']} word={b['by_word']} n={b['findings']}")


if __name__ == "__main__":
    main()
