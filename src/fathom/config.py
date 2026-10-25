"""``fathom.yaml`` at the repository root tells Fathom where things live."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field


class DbtConfig(BaseModel):
    project: str


class AirflowConfig(BaseModel):
    dags: str


class GitConfig(BaseModel):
    paths: list[str] = Field(default_factory=list)
    max_commits: int = 500


class IncidentsConfig(BaseModel):
    dir: str


class RunsConfig(BaseModel):
    """Run history: an Airflow run export (JSONL), dbt run_results.json files, Iceberg table metadata."""

    airflow: str | None = None
    dbt: list[str] = Field(default_factory=list)
    iceberg: str | None = None


class RetrievalConfig(BaseModel):
    ranker: Literal["bm25", "hybrid", "embedding"] = "bm25"
    model: str | None = None  # sentence-transformers model name; None uses the built-in hashed embedder


class ReviewConfig(BaseModel):
    provider: Literal["stub", "anthropic", "openai", "command", "claude-code"] = "stub"
    model: str | None = None
    command: list[str] | None = None  # the `command` provider's argument list (`{schema}`, `{system}`, `{model}`, `{effort}` filled per call)
    areas: str | None = None  # sensitive areas of the code (fathom.codectx), a YAML or JSON file; `file#key` picks one list from a file keyed by project
    base_url: str | None = None
    api_key_env: str | None = None  # name of the environment variable holding the key; never the key itself
    second_reading: bool = True  # check the model's warn/block findings with a second call
    verify_model: str | None = None  # model for that second call (same provider); default: ``model``
    verify_effort: str | None = "high"  # effort for the second reading (Anthropic); None means ``effort``
    effort: str | None = None  # how hard an Anthropic model thinks: low | medium | high | xhigh | max (default medium)
    escalation: Literal["evidence", "platform", "model"] = "platform"  # evidence: model warn/block needs recorded system context behind it; platform: only on files mapped to a data-platform asset; model: keep the model's severity
    warehouse: str | None = None  # a DuckDB warehouse file; model warnings on data models get their failing case run against it
    complex_model: str | None = None  # lens passes on a complex diff use this model (same provider); e.g. a cheap `model` with a strong one here
    complex_lines: int = 400  # changed lines (added + removed) from which a diff counts as complex
    lenses: list[str] | None = None  # focused review passes (fathom.review.LENSES); default all, [] for one general pass
    agent: bool = False  # the model investigates with read-only tools before it reports (fathom.review_agent)
    agent_steps: int | None = Field(None, ge=1, le=30)  # turns per agent run (default fathom.review_agent.MAX_STEPS); bounded like --agent-steps


class Adapters(BaseModel):
    dbt: DbtConfig | None = None
    airflow: AirflowConfig | None = None
    git: GitConfig | None = None
    incidents: IncidentsConfig | None = None
    runs: RunsConfig | None = None


class FathomConfig(BaseModel):
    db: str = ".fathom/graph.duckdb"
    constraints: str | None = None
    risk: str | None = None
    adapters: Adapters = Field(default_factory=Adapters)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    postflight_runs: int = 3
    root: Path = Path(".")

    def path(self, rel: str) -> Path:
        return (self.root / rel).resolve()


def repo_root(start: Path) -> Path:
    try:
        top = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"], check=True, capture_output=True, text=True
        ).stdout.strip()
        return Path(top)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return start.resolve()


def load_config(path: str | Path | None = None, cwd: Path | None = None) -> FathomConfig:
    cwd = cwd or Path.cwd()
    if path is None:
        for cand in [cwd / "fathom.yaml", repo_root(cwd) / "fathom.yaml"]:
            if cand.exists():
                path = cand
                break
    if path is None:
        raise FileNotFoundError("fathom.yaml not found; create one or pass --config")
    path = Path(path)
    data: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    cfg = FathomConfig.model_validate(data)
    cfg.root = path.parent.resolve()
    return cfg
