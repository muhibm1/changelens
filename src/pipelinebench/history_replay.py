"""History replay: every change a team merged over a window, reviewed as it landed.

This is the evidence a design partner asks for: "over the last six months, Orion would have flagged N of the M bugs
you later fixed, with about K false alarms a week". ``replay_eval.py`` replays a hand-picked set of commits; this
replays all of them. Each first-parent commit in the window that touches the given paths is reviewed against its
parent, as CI would have seen it. A labels file, written by hand from the upstream history, says

- ``bugs``: commits that introduced a bug a later commit fixed (``recall_eval.Case`` fields: commit, bug, expect, fix).
  A bug is **flagged** when a warn or block finding about it sits near the expected lines (the recall eval's judge).
- ``warnings``: verdicts for warnings already read (``replay_eval.Label`` fields, keyed by commit): confirmed,
  plausible, noise, setup. Noise is a false alarm. A warning with no label is ``unjudged`` and listed for a person.

Labels must never be derived from Orion's output.
"""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from .recall_eval import Case as BugCase
from .recall_eval import Span, commit_id, judge, judge_one
from .replay_eval import ACTED_ON, Label, _head, review_commit


def commits(clone: Path, since: str, until: str | None, paths: list[str]) -> list[dict[str, str]]:
    """First-parent commits in the window touching ``paths``, oldest first."""
    args = ["git", "-C", str(clone), "log", "--first-parent", "--reverse", f"--since={since}", "--format=%H%x09%ad%x09%s", "--date=short"] + ([f"--until={until}"] if until else []) + ["--", *paths]
    out = subprocess.run(args, capture_output=True, text=True, check=True).stdout
    return [dict(zip(("commit", "date", "title"), line.split("\t", 2), strict=True)) for line in out.splitlines() if line.strip()]


def load_labels(path: Path | None) -> tuple[list[BugCase], dict[str, list[Label]]]:
    if path is None or not path.exists():
        return [], {}
    doc = yaml.safe_load(path.read_text()) or {}
    bugs = [BugCase(doc.get("repo", ""), commit_id(b["commit"]), b.get("title", ""), commit_id(b["fix"]) if b.get("fix") else "", b.get("fix_title", ""), b.get("bug", ""), [Span(**s) for s in b.get("expect") or []], [str(w).lower() for w in b.get("must_mention") or []], b.get("skip", "")) for b in doc.get("bugs") or []]
    warnings = {commit_id(k): [Label(**lb) for lb in v] for k, v in (doc.get("warnings") or {}).items()}
    return bugs, warnings


def _labels_for(labels: dict[str, list[Label]], commit: str) -> list[Label]:
    return next((v for k, v in labels.items() if commit.startswith(k)), [])


def _verdict(labels: list[Label], f: dict[str, Any]) -> str:
    line = f.get("line") or 0
    near = sorted((lb.distance(line), lb.verdict) for lb in labels if lb.matches(f["path"] or "", line))
    return near[0][1] if near else "unjudged"


POLICIES = ("model", "evidence", "platform")
GAP_TITLE = "not mapped to any asset"


def _lowered(f: dict[str, Any], mapped: set[str], policy: str) -> bool:
    """Whether ``policy`` would have lowered this warning of a ``--escalation model`` review to inform: a model warning
    nothing recorded backs (on a file mapped to an asset, for ``platform``), or the deterministic unmapped-path gap."""
    if policy == "model":
        return False
    if f["source"] == "deterministic":
        return GAP_TITLE in f.get("title", "")
    return "backing" in f and not f["backing"] and (policy == "evidence" or f.get("path") in mapped)


def policy_view(result: dict[str, Any], policy: str) -> dict[str, Any]:
    """A ``--escalation model`` review as ``policy`` would have returned it, from the ``backing`` each warning records."""
    out = json.loads(json.dumps(result))
    mapped = {x["path"] for x in out.get("files", []) if x.get("mapped")}
    for f in out.get("findings", []):
        if f["severity"] in ACTED_ON and _lowered(f, mapped, policy):
            f["severity"] = "inform"
    return out


def _slots(clone: Path, jobs: int) -> list[Path]:
    """Checkouts that can review different commits at once, each with its own copy of the graph (DuckDB has one writer)."""
    out = [clone]
    for i in range(1, jobs):
        wt = clone.with_name(f"{clone.name}.wt{i}")
        if not (wt / ".git").exists():
            subprocess.run(["git", "-C", str(clone), "worktree", "add", "-q", "--detach", "-f", str(wt)], check=True, capture_output=True)
        for name in ("orion.yaml",):
            if (clone / name).exists():
                shutil.copy(clone / name, wt / name)
        if (clone / ".orion").is_dir() and not (wt / ".orion").exists():
            shutil.copytree(clone / ".orion", wt / ".orion")
        out.append(wt)
    return out


def run(clone: Path, picked: list[dict[str, str]], *, orion: str, provider: str, model: str | None, extra_args: list[str] | None = None, labels: Path | None = None, raw_dir: Path | None = None, jobs: int = 1, judge_provider: Any = None, progress=None) -> dict[str, Any]:
    bugs, warn_labels = load_labels(labels)
    by_commit = {b.commit: b for b in bugs}
    free: queue.Queue[Path] = queue.Queue()
    slots = _slots(clone, max(1, jobs))
    heads = {slot: _head(slot) for slot in slots}
    for slot in slots:
        free.put(slot)

    def one(c: dict[str, str]) -> dict[str, Any]:
        slot = free.get()
        try:
            result = review_commit(orion, slot, c["commit"], provider, model, extra_args=extra_args)
        except Exception as e:  # noqa: BLE001 - one commit that cannot be checked out or read is an error row, not the end of the run
            result = {"error": f"{type(e).__name__}: {e}"[:2000]}
        finally:
            free.put(slot)
        if raw_dir is not None:
            raw_dir.mkdir(parents=True, exist_ok=True)
            (raw_dir / f"{c['commit'][:12]}.json").write_text(json.dumps(result, indent=1, default=str))
        mine = _labels_for(warn_labels, c["commit"])
        mapped = {x["path"] for x in result.get("files", []) if x.get("mapped")}
        warns = [{"source": f["source"], "path": f["path"], "line": f.get("line"), "title": f["title"], "verdict": _verdict(mine, f), "escalation": f.get("escalation", []), **({"backing": f["backing"]} if "backing" in f else {}), "lowered_by": [p for p in POLICIES if _lowered(f, mapped, p)]} for f in result.get("findings", []) if f["severity"] in ACTED_ON]
        bug = next((b for k, b in by_commit.items() if c["commit"].startswith(k)), None)
        row = {**c, "week": date.fromisoformat(c["date"]).isocalendar()[:2], "severity": result.get("severity"), "error": result.get("error") or result.get("provider_error"), "warns": warns, "timing": result.get("timing"), "usage": result.get("usage") or {}}
        if bug is not None and not bug.skip:
            try:
                judged = judge_one(bug, result, judge_provider)
                row["bug"], row["bug_detected"] = judged["outcome"], judged.get("detected", judged["outcome"] in ("caught", "inform_only"))
            except Exception as e:  # noqa: BLE001 - a judge failure on one bug is recorded, not the end of a long replay
                row["bug"], row["bug_detected"], row["judge_error"] = "error", False, f"{type(e).__name__}: {e}"[:500]
            else:
                about = {tuple(a) for a in judged.get("about", [])}
                for p in POLICIES[1:]:  # the same judged matches, at the severity each policy leaves them (``judge`` makes no call)
                    view = policy_view(result, p)
                    ids = {id(f) for f in view.get("findings", []) if (f.get("path"), f.get("line"), f.get("title")) in about}
                    row.setdefault("bug_by_policy", {})[p] = judge(bug, view, ids if judged.get("judge") == "model" else None)["outcome"]
        if progress:
            progress(row)
        return row

    try:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
            rows = list(pool.map(one, picked))
    finally:
        for slot, head in heads.items():
            subprocess.run(["git", "-C", str(slot), "checkout", "-q", "-f", head], capture_output=True)
    missing = [b.commit for b in bugs if not b.skip and not any(r["commit"].startswith(b.commit) for r in rows)]
    picked_window = [r for r in rows if r.get("in_window", True)]
    rows = picked_window + [r for r in rows if not r.get("in_window", True)]
    return {"provider": provider, "model": model, "args": extra_args or [], "clone": clone.name, "commits": rows, "bugs_outside_window": missing, "summary": summarise(rows)}


def with_bugs(clone: Path, window: list[dict[str, str]], labels: Path | None) -> list[dict[str, str]]:
    """The window's commits, then each labelled bug-introducing commit from before it (marked ``in_window: False``), so a
    bug introduced earlier and fixed later is still reviewed; only in-window commits count toward warnings per week."""
    bugs, _ = load_labels(labels)
    have = {c["commit"] for c in window}
    extra = []
    for b in bugs:
        if b.skip or any(h.startswith(b.commit) for h in have):
            continue
        out = subprocess.run(["git", "-C", str(clone), "log", "-1", "--format=%H%x09%ad%x09%s", "--date=short", b.commit, "--"], capture_output=True, text=True).stdout.strip()
        if out.count("\t") < 2:
            continue  # not in the clone; run() reports it under bugs_outside_window
        extra.append({**dict(zip(("commit", "date", "title"), out.split("\t", 2), strict=True)), "in_window": False})
    return [{**c, "in_window": True} for c in window] + extra


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    bug_rows = [r for r in rows if "bug" in r]  # a bug whose review failed stays in the count: it was not flagged
    rows = [r for r in rows if r.get("in_window", True)]
    ok = [r for r in rows if not r["error"]]
    weeks = len({tuple(r["week"]) for r in rows}) or 1
    if rows:
        first, last = date.fromisoformat(rows[0]["date"]), date.fromisoformat(rows[-1]["date"])
        weeks = max(1, round(((last - first).days + 1) / 7))
    v = Counter(w["verdict"] for r in ok for w in r["warns"])
    src = Counter(w["source"] for r in ok for w in r["warns"])
    flagged = sum(1 for r in bug_rows if r["bug"] == "caught")
    detected = sum(1 for r in bug_rows if r.get("bug_detected", r["bug"] in ("caught", "inform_only")))
    times = sorted(r["timing"]["total_s"] for r in ok if r.get("timing"))
    warns = sum(v.values()) - v["setup"]
    return {
        "commits": len(ok),
        "errors": len(rows) - len(ok),
        "weeks": weeks,
        "commits_warned": sum(1 for r in ok if any(w["verdict"] != "setup" for w in r["warns"])),
        "warns": warns,
        "warns_per_week": round(warns / weeks, 2),
        "deterministic_warns": src["deterministic"],
        "model_warns": src["model"],
        **{k: v[k] for k in ("confirmed", "plausible", "noise", "unjudged", "setup")},
        "false_alarms_per_week": round(v["noise"] / weeks, 2),
        "bugs_flagged": f"{flagged}/{len(bug_rows)}",
        "bugs_surfaced_any_severity": f"{detected}/{len(bug_rows)}",
        "median_review_s": times[len(times) // 2] if times else None,
        "p90_review_s": times[int(len(times) * 0.9)] if times else None,
        "policies": {p: _policy_summary(ok, bug_rows, weeks, p) for p in POLICIES} if any("backing" in w for r in ok for w in r["warns"]) else None,
        "tokens_in": sum(r["usage"].get("input_tokens", 0) + r["usage"].get("cache_creation_input_tokens", 0) + r["usage"].get("cache_read_input_tokens", 0) for r in ok),
        "tokens_out": sum(r["usage"].get("output_tokens", 0) for r in ok),
    }


def _policy_summary(ok: list[dict[str, Any]], bug_rows: list[dict[str, Any]], weeks: int, policy: str) -> dict[str, Any]:
    """Warnings, false alarms and bugs flagged had the replayed reviews been run under ``policy``."""
    v = Counter(w["verdict"] for r in ok for w in r["warns"] if policy not in w.get("lowered_by", []))
    flagged = sum(1 for r in bug_rows if (r["bug"] if policy == "model" else r.get("bug_by_policy", {}).get(policy, r["bug"])) == "caught")
    warns = sum(v.values()) - v["setup"]
    return {"warns": warns, "warns_per_week": round(warns / weeks, 2), "noise": v["noise"], "unjudged": v["unjudged"], "false_alarms_per_week": round(v["noise"] / weeks, 2), "bugs_flagged": f"{flagged}/{len(bug_rows)}"}


def to_markdown(res: dict[str, Any]) -> str:
    s = res["summary"]
    label = f"`{res['provider']}{':' + res['model'] if res['model'] else ''}`" + (f" with `{' '.join(res['args'])}`" if res.get("args") else "")
    lines = [f"# History replay: {res['clone']}, {s['commits']} merged changes over {s['weeks']} weeks, {label}", "", "| metric | value |", "| --- | --- |"]
    lines += [f"| {k.replace('_', ' ')} | {val} |" for k, val in s.items() if k != "policies"]
    if s.get("policies"):
        cols = list(next(iter(s["policies"].values())))
        lines += ["", "Had the same reviews been run under each escalation policy:", "", "| policy | " + " | ".join(c.replace("_", " ") for c in cols) + " |", "| --- |" + " ---: |" * len(cols)]
        lines += [f"| {p} | " + " | ".join(str(row[c]) for c in cols) + " |" for p, row in s["policies"].items()]
    lines += ["", "| date | commit | change | bug | warnings |", "| --- | --- | --- | --- | --- |"]
    for r in res["commits"]:
        ws = "; ".join(f"**{w['verdict']}** {w['source']} `{w['path']}:{w['line']}` {w['title']}".replace("|", "\\|") for w in r["warns"]) or ("error: " + str(r["error"])[:80] if r["error"] else "none")
        lines.append(f"| {r['date']} | `{r['commit'][:8]}` | {r['title'].replace('|', '/')} | {r.get('bug', '')} | {ws} |")
    if res.get("bugs_outside_window"):
        lines += ["", "Labelled bugs not in the window: " + ", ".join(res["bugs_outside_window"])]
    return "\n".join(lines) + "\n"
