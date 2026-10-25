"""Run agents x conditions x scenarios, grade, and write a results file."""

from __future__ import annotations

import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fathom.store import Store

from .agents import Agent, Prompt
from .conditions import CONDITIONS, build_context, load_platform
from .grader import aggregate, grade
from .scenarios import Scenario, known_ids


def run(
    scenarios: list[Scenario],
    agents: list[Agent],
    conditions: list[str],
    *,
    repo_root: Path,
    store: Store | None,
    seeds: int = 1,
    progress=None,
) -> dict[str, Any]:
    constraints, risk = load_platform(repo_root, store) if store else ([], None)
    ids = known_ids(repo_root / "fixture")
    started = time.time()
    cells: dict[str, dict[str, Any]] = {}
    details: list[dict[str, Any]] = []
    for cond in conditions:
        if cond not in CONDITIONS:
            raise ValueError(f"unknown condition {cond}")
        contexts = {s.id: build_context(cond, s, repo_root=repo_root, store=store, constraints=constraints, risk=risk) for s in scenarios}
        for agent in agents:
            rows: list[dict[str, Any]] = []
            for seed in range(seeds):
                for s in scenarios:
                    ctx = contexts[s.id]
                    prompt = Prompt(s, cond, ctx["text"], {k: v for k, v in ctx.items() if k != "text"})
                    d = agent.decide(prompt)
                    g = grade(s, d, ids, ctx["text"])
                    g.update({"agent": agent.name, "condition": cond, "seed": seed, "cited": d.cited, "affected": d.affected, "approvers": d.approvers, "rationale": d.rationale[:300]})
                    rows.append(g)
                    details.append(g)
                    if progress:
                        progress(agent.name, cond, s.id, g)
            cells[f"{agent.name}|{cond}"] = {"agent": agent.name, "condition": cond, **aggregate(rows)}
    usage = {a.name: dict(a.usage) for a in agents if getattr(a, "usage", None)}
    return {
        "benchmark": "PipelineBench",
        "version": "0.1.0",
        "ran_at": datetime.now(UTC).isoformat(),
        "seconds": round(time.time() - started, 1),
        "platform": platform.platform(),
        "scenarios": len(scenarios),
        "seeds": seeds,
        "cells": list(cells.values()),
        "usage": usage,
        "details": details,
    }


def report(results: dict[str, Any]) -> str:
    """A markdown table: one row per agent x condition."""
    lines = [
        f"PipelineBench {results['version']}: {results['scenarios']} scenarios, {results['seeds']} seed(s), ran {results['ran_at'][:19]}Z",
        *[f"{name}: {u.get('input_tokens', 0)} tokens in, {u.get('output_tokens', 0)} out, {u.get('errors', 0)} provider errors (each graded as proceed)" for name, u in (results.get("usage") or {}).items()],
        "",
        "| agent | condition | accuracy | unsafe | cite recall | name recall | approvers | fabricated | score |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for c in results["cells"]:
        lines.append(f"| {c['agent']} | {c['condition']} | {c['accuracy']:.2f} | {c['unsafe_rate']:.2f} | {c['cite_recall']:.2f} | {c['name_recall']:.2f} | {c['approvers_ok']:.2f} | {c['fabrication_rate']:.2f} | {c['score']:.2f} |")
    fam: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for d in results["details"]:
        fam.setdefault((d["agent"], d["condition"], d["family"]), []).append(d)
    lines += ["", "Accuracy by family:", "", "| agent | condition | " + " | ".join(sorted({k[2] for k in fam})) + " |"]
    families = sorted({k[2] for k in fam})
    lines.append("| --- | --- | " + " | ".join("---:" for _ in families) + " |")
    for c in results["cells"]:
        cells = []
        for f in families:
            rows = fam.get((c["agent"], c["condition"], f), [])
            cells.append(f"{sum(r['decision_correct'] for r in rows) / len(rows):.2f}" if rows else "")
        lines.append(f"| {c['agent']} | {c['condition']} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def write_results(results: dict[str, Any], out_dir: Path, name: str | None = None) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    name = name or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    j = out_dir / f"{name}.json"
    m = out_dir / f"{name}.md"
    j.write_text(json.dumps(results, indent=1))
    m.write_text(report(results))
    return j, m


def check_ground_truth(scenarios: list[Scenario], *, repo_root: Path, store: Store) -> list[dict[str, Any]]:
    """Fathom's verdict against each scenario's hand-written expectation. Disagreements are listed, not hidden."""
    constraints, risk = load_platform(repo_root, store)
    out = []
    for s in scenarios:
        ctx = build_context("fathom", s, repo_root=repo_root, store=store, constraints=constraints, risk=risk)
        v = ctx["verdict"]
        fired = {c["id"] for c in v["constraints"] if c["status"] == "fires"}
        hist = {h["ref"] for h in v["history"] if h["ref"]}
        affected = {a["asset"].split(":", 1)[-1] for a in v["affected"]}
        missing_cites = [c for c in s.expected.must_cite if c not in fired | hist]
        missing_names = [n for n in s.expected.must_name if n not in affected]
        out.append({
            "scenario": s.id,
            "title": s.title,
            "expected": s.expected.severity,
            "fathom": v["severity"],
            "agree": v["severity"] == s.expected.severity and not missing_cites and not missing_names,
            "missing_cites": missing_cites,
            "missing_names": missing_names,
            "fired": sorted(fired),
            "unresolved": v["change"]["unresolved"],
        })
    return out
