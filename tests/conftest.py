from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from changelens.config import load_config
from changelens.store import Store
from changelens.sync import run_sync

ROOT = Path(__file__).resolve().parents[1]
DBT_PROJECT = ROOT / "fixture" / "dbt_project"


@pytest.fixture(scope="session")
def manifest() -> Path:
    """The fixture's dbt manifest; parsed on demand so tests need no warehouse."""
    target = DBT_PROJECT / "target" / "manifest.json"
    if not target.exists():
        env = {**os.environ, "PIPELINEBENCH_DB": str(DBT_PROJECT / "test_warehouse.duckdb")}
        subprocess.run(["dbt", "parse", "--profiles-dir", ".", "-q"], cwd=DBT_PROJECT, check=True, env=env)
    return target


@pytest.fixture(scope="session")
def synced_store(manifest: Path, tmp_path_factory: pytest.TempPathFactory) -> Store:
    cfg = load_config(ROOT / "changelens.yaml")
    store = Store(tmp_path_factory.mktemp("changelens") / "graph.duckdb")
    run_sync(cfg, store)
    return store


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """No test can spend API credit: real keys are removed, and the faked Anthropic calls tests make run under a
    one-dollar cap kept in a temporary ledger, never the user's."""
    for name in ("ANTHROPIC_API_KEY", "CHANGELENS_ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CHANGELENS_API_CAP", "1")
    monkeypatch.setenv("CHANGELENS_API_LEDGER", str(tmp_path_factory.mktemp("changelens-api") / "api-ledger.jsonl"))


@pytest.fixture(autouse=True)
def _usage_log_in_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """The MCP tools log to the usage log; in tests that log is a temporary file, never the user's."""
    home = tmp_path_factory.mktemp("changelens-home")
    monkeypatch.setenv("CHANGELENS_HOME", str(home))
    monkeypatch.setenv("CHANGELENS_USAGE", str(home / "usage.jsonl"))
