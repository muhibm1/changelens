"""The four context conditions. Each builds the context string an agent sees for a scenario.

* ``none``: the task and nothing else.
* ``repo``: the task plus the repository's own files, the way a code-context tool would hand them over:
  the files the change touches, dbt schema and source yml, the DAGs, CODEOWNERS, the constraint files and
  the postmortems. Everything is there; nothing is connected.
* ``rag``: the task plus the top-k chunks of the same corpus by lexical similarity, with no graph scoping.
* ``fathom``: the task plus Fathom's verdict for the change.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from fathom.changes import collect_changes
from fathom.constraints import load_constraints
from fathom.retrieval import ask as ask_corpus
from fathom.risk import RiskModel
from fathom.store import Store
from fathom.verdict import preflight, to_markdown

from .scenarios import Scenario

CONDITIONS = ("none", "repo", "rag", "fathom")
REPO_CAP = 60_000


def _jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=lambda o: o.isoformat() if isinstance(o, datetime) else str(o)))


def repo_files(repo_root: Path, scenario: Scenario) -> list[Path]:
    fx = repo_root / "fixture"
    files = [repo_root / f for f in scenario.change.files]
    files += sorted((fx / "dbt_project" / "models").rglob("*.yml"))
    files += sorted((fx / "dags").glob("*.py"))
    files += [fx / "CODEOWNERS"]
    files += sorted((fx / "fathom" / "constraints").glob("*.yaml"))
    files += sorted((fx / "fathom" / "incidents").glob("*.md"))
    seen: list[Path] = []
    for f in files:
        if f.exists() and f not in seen:
            seen.append(f)
    return seen


def build_context(condition: str, scenario: Scenario, *, repo_root: Path, store: Store | None, constraints=None, risk: RiskModel | None = None, rag_k: int = 8) -> dict[str, Any]:
    """Returns {"text": ..., "verdict": ... (fathom only), "files": [...] (repo only)}."""
    if condition == "none":
        return {"text": ""}
    if condition == "repo":
        parts = []
        used = 0
        files = repo_files(repo_root, scenario)
        for f in files:
            body = f.read_text()
            rel = str(f.relative_to(repo_root))
            chunk = f"### file: {rel}\n{body}\n"
            if used + len(chunk) > REPO_CAP:
                break
            parts.append(chunk)
            used += len(chunk)
        return {"text": "\n".join(parts), "files": [str(f.relative_to(repo_root)) for f in files]}
    if store is None:
        raise ValueError(f"condition {condition} needs a synced Fathom store")
    if condition == "rag":
        question = scenario.task + " " + " ".join(f"{o.get('asset')} {o.get('column') or ''}" for o in scenario.change.ops)
        out = ask_corpus(store, question, scope=[], limit=rag_k)
        parts = [f"### chunk: {c['chunk']} (source {c['source']})\n{c['text']}\n" for c in out["chunks"]]
        return {"text": "\n".join(parts), "chunks": [c["chunk"] for c in out["chunks"]]}
    if condition == "fathom":
        cs = collect_changes(store, repo_root, ops=scenario.change.ops or None, intent=scenario.change.intent, scope=["fixture"], ignore=["fixture/fathom"])
        v = preflight(store, cs, constraints or [], risk or RiskModel.load(None), record=False)
        return {"text": to_markdown(v) + "\n\n```json\n" + json.dumps(_jsonable(v), indent=1) + "\n```", "verdict": _jsonable(v)}
    raise ValueError(f"unknown condition {condition}")


def load_platform(repo_root: Path, store: Store):
    constraints, _ = load_constraints(repo_root / "fixture" / "fathom" / "constraints")
    return constraints, RiskModel.load(None)
