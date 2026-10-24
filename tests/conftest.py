from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from orion.config import load_config
from orion.store import Store
from orion.sync import run_sync

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
    cfg = load_config(ROOT / "orion.yaml")
    store = Store(tmp_path_factory.mktemp("orion") / "graph.duckdb")
    run_sync(cfg, store)
    return store


@pytest.fixture(autouse=True)
def _no_background_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Servers built in tests do not refresh indexes behind the test's back; tests of the refresh turn it on."""
    monkeypatch.setenv("ORION_REFRESH", "off")
