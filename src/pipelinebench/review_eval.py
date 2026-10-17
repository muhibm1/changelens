"""Review eval: does ``orion review`` catch planted bugs without raising false alarms?

PipelineBench scores the pre-flight verdict; this scores code review. Each case
in ``pipelinebench/review/cases.yaml`` is a change to one or more files with
hand-written expectations: the bugs a reviewer must raise, each located by a
marker string in a new file. Correct changes (``expect: []``) count false alarms.

A finding catches an expected bug when it is valid (shown to the user), on the
bug's path, and within ``WINDOW`` lines of the marker. Severity ``warn`` or
``block`` is what a reviewer acts on, so recall and precision are reported at
that bar, per bug rather than per finding; an ``inform`` that lands on a bug is
counted separately.
"""

from __future__ import annotations

import difflib
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from orion.constraints import Constraint
from orion.providers import Provider
from orion.review import review
from orion.risk import RiskModel
from orion.store import Store

WINDOW = 3
ACTED_ON = ("warn", "block")


@dataclass
class Expected:
    marker: str
    category: str | None = None
    line: int = 0
    file: str = ""


@dataclass
class Change:
    file: str
    before: str
    after: str

    def diff(self) -> str:
        old, new = self.before.splitlines(keepends=True), self.after.splitlines(keepends=True)
        a = "/dev/null" if not self.before else f"a/{self.file}"
        body = "".join(difflib.unified_diff(old, new, a, f"b/{self.file}"))
        head = f"diff --git a/{self.file} b/{self.file}\n" + ("new file mode 100644\n" if not self.before else "")
        return head + body


@dataclass
class Case:
    id: str
    changes: list[Change]
    expect: list[Expected] = field(default_factory=list)
    repo_files: dict[str, str] = field(default_factory=dict)  # files the change leaves alone but a reviewer could search

    @property
    def files(self) -> list[str]:
        return [c.file for c in self.changes]

    def diff(self) -> str:
        return "".join(c.diff() for c in self.changes)


def _change(case_id: str, raw: dict[str, Any], repo_root: Path) -> Change:
    before = (repo_root / raw["file"]).read_text() if raw.get("before_from_repo") else raw.get("before", "")
    after = raw.get("after", before)
    for e in raw.get("edits", []):
        if after.count(e["old"]) != 1:
            raise ValueError(f"{case_id}: edit target must occur exactly once in {raw['file']}: {e['old']!r}")
        after = after.replace(e["old"], e["new"])
    after += raw.get("append", "")
    if after == before:
        raise ValueError(f"{case_id}: {raw['file']} is not changed")
    return Change(raw["file"], before, after)


def load_cases(path: Path, repo_root: Path) -> list[Case]:
    """Load cases. A case is one file (``file`` plus its fields) or several (``files``); ``repo_files`` are unchanged files to search."""
    cases = []
    for raw in yaml.safe_load(path.read_text()):
        changes = [_change(raw["id"], f, repo_root) for f in raw.get("files") or [raw]]
        by_file = {c.file: c for c in changes}
        if len(by_file) != len(changes):
            raise ValueError(f"{raw['id']}: a file appears twice")
        expect = []
        for x in raw.get("expect") or []:
            file = x.get("file") or (changes[0].file if len(changes) == 1 else None)
            if file not in by_file:
                raise ValueError(f"{raw['id']}: marker {x['marker']!r} needs a `file` that the case changes")
            lines = [i for i, line in enumerate(by_file[file].after.splitlines(), 1) if x["marker"] in line]
            if len(lines) != 1:
                raise ValueError(f"{raw['id']}: marker {x['marker']!r} must match exactly one line of {file}, matched {len(lines)}")
            expect.append(Expected(x["marker"], x.get("category"), lines[0], file))
        repo_files = dict(raw.get("repo_files") or {})
        if any(not isinstance(k, str) or not isinstance(v, str) for k, v in repo_files.items()):
            raise ValueError(f"{raw['id']}: repo_files maps paths to file text")
        if set(repo_files) & set(by_file):
            raise ValueError(f"{raw['id']}: a repo_files entry is also changed by the case")
        cases.append(Case(raw["id"], changes, expect, repo_files))
    return cases


def _near(f: dict[str, Any], path: str, line: int) -> bool:
    if f["path"] != path or f["line"] is None:
        return False
    hi = f.get("end_line") or f["line"]
    return f["line"] - WINDOW <= line <= hi + WINDOW


def grade(case: Case, r: dict[str, Any]) -> dict[str, Any]:
    shown = r["findings"]
    acted = [f for f in shown if f["severity"] in ACTED_ON]
    per_bug = []
    for e in case.expect:
        hits = [f for f in shown if _near(f, e.file, e.line)]
        per_bug.append({
            "file": e.file,
            "marker": e.marker,
            "line": e.line,
            "caught": any(f["severity"] in ACTED_ON for f in hits),
            "inform_only": bool(hits) and not any(f["severity"] in ACTED_ON for f in hits),
            "by": sorted({f["source"] for f in hits}),
        })
    false_alarms = [f for f in acted if not any(_near(f, e.file, e.line) for e in case.expect)]
    return {
        "case": case.id,
        "expected": len(case.expect),
        "caught": sum(b["caught"] for b in per_bug),
        "bugs": per_bug,
        "acted_on": len(acted),
        "false_alarms": [{"path": f["path"], "line": f["line"], "severity": f["severity"], "title": f["title"], "source": f["source"]} for f in false_alarms],
        "uncertain": len(r["uncertain"]),
        "rejected": len(r["rejected"]),
        "provider_error": r["provider_error"],
        "usage": r.get("usage"),
    }


def run_eval(cases: list[Case], provider: Provider, *, store: Store, repo_root: Path, constraints: list[Constraint], risk_model: RiskModel, scope: list[str] | None = None, ignore: list[str] | None = None, repeat: int = 1, file_context: bool = True, progress=None) -> dict[str, Any]:
    rows = []
    # Reference searches run against an empty repository plus each case's own files, so the eval
    # never finds the symbol it planted mentioned in this repository's tests or results.
    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(["git", "init", "-q", tmp], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError) as e:
            raise RuntimeError("the review eval needs git on PATH for its reference search") from e
        search_root = Path(tmp)
        for n in range(repeat):
            for c in cases:
                r = review(store, repo_root, provider, constraints=constraints, risk_model=risk_model, diff_text=c.diff(), scope=scope, ignore=ignore, new_files={ch.file: ch.after for ch in c.changes}, repo_files=c.repo_files, search_root=search_root, file_context=file_context)
                g = grade(c, r) | {"run": n}
                rows.append(g)
                if progress:
                    progress(g)
    return {"provider": provider.name, "file_context": file_context, "repeat": repeat, "cases": len(cases), "rows": rows, "summary": summarize(rows)}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    expected = sum(r["expected"] for r in rows)
    caught = sum(r["caught"] for r in rows)
    acted = sum(r["acted_on"] for r in rows)
    alarms = sum(len(r["false_alarms"]) for r in rows)
    clean = [r for r in rows if r["expected"] == 0]
    tokens_in = sum((r["usage"] or {}).get("input_tokens", (r["usage"] or {}).get("prompt_tokens", 0)) for r in rows)
    tokens_out = sum((r["usage"] or {}).get("output_tokens", (r["usage"] or {}).get("completion_tokens", 0)) for r in rows)
    return {
        "recall": round(caught / expected, 3) if expected else None,
        "inform_only": sum(b["inform_only"] for r in rows for b in r["bugs"]),
        # Per bug, not per finding, so three findings on one bug do not count three times.
        "precision": round(caught / (caught + alarms), 3) if caught + alarms else None,
        "duplicate_findings": acted - alarms - caught,
        "false_alarms": alarms,
        "clean_cases_flagged": f"{sum(bool(r['false_alarms']) for r in clean)}/{len(clean)}",
        "missed": sorted({f"{r['case']}@{b['line']}" for r in rows for b in r["bugs"] if not b["caught"]}),
        "provider_errors": sum(bool(r["provider_error"]) for r in rows),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }


def to_markdown(res: dict[str, Any]) -> str:
    s = {k: ("n/a" if v is None else v) for k, v in res["summary"].items()}
    lines = [
        f"# Review eval: `{res['provider']}`, {res['cases']} cases x {res['repeat']}, whole-file context {'on' if res.get('file_context', True) else 'off'}",
        "",
        "| metric | value |",
        "| --- | --- |",
        f"| recall (bugs raised at warn or block) | {s['recall']} |",
        f"| bugs raised only as inform | {s['inform_only']} |",
        f"| precision (caught bugs / (caught bugs + false alarms)) | {s['precision']} |",
        f"| extra warn/block findings on an already-caught bug | {s['duplicate_findings']} |",
        f"| false alarms at warn/block | {s['false_alarms']} |",
        f"| correct changes flagged | {s['clean_cases_flagged']} |",
        f"| provider errors | {s['provider_errors']} |",
        f"| tokens in / out | {s['tokens_in']} / {s['tokens_out']} |",
        "",
        "| case | run | caught | false alarms | uncertain | rejected |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in res["rows"]:
        fa = "; ".join(f"{f['severity']} {f['path']}:{f['line']} {f['title']}" for f in r["false_alarms"]) or "0"
        lines.append(f"| {r['case']} | {r['run']} | {r['caught']}/{r['expected']} | {fa} | {r['uncertain']} | {r['rejected']} |")
    if s["missed"]:
        lines += ["", "Missed: " + ", ".join(f"`{m}`" for m in s["missed"])]
    return "\n".join(lines) + "\n"
