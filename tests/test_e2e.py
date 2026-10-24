"""End to end: ingest a small engineering record and two repositories, sync the code graph across them, build the
retrieval index, then call the agent tools over MCP the way a coding agent does, on a sample change.

No model, no network, the hash embedder: fast and the same on every run. What it holds the slices to together:
answers cite ids that read back, the change's blast radius crosses into the other repository, every answer fits its
token budget, a repeated call is served from the cache, and a server that is running never locks out a re-index."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from orion import agent

pytest.importorskip("tree_sitter_language_pack")

SVC = {
    "pyproject.toml": '[project]\nname = "acme-svc"\ndependencies = ["fastapi>=0.100"]\n\n[tool.hatch.build.targets.wheel]\npackages = ["src/acme_svc"]\n',
    "src/acme_svc/__init__.py": "from .core import compute\n",
    "src/acme_svc/core.py": "import os\n\n\ndef compute(x):\n    return helper(x) * 2\n\n\ndef helper(x):\n    return x + int(os.environ.get(\"SCALE\", \"1\"))\n",
    "src/acme_svc/api.py": 'from fastapi import APIRouter\nfrom .core import compute\n\nrouter = APIRouter(prefix="/api")\n\n\n@router.get("/orders/{oid}")\ndef get_order(oid):\n    return compute(oid)\n',
    "README.md": "# acme svc\n\n## Pricing\n\nThe compute function doubles the helper result for every order total.\n",
    "docs/decisions.md": "# Decisions\n\n## 2026-09-01 Keep compute pure\n\nWe decided compute must never read the database: the nightly worker calls it in a hot loop, and a query per call took the batch from minutes to hours.\n",
}
WORKER = {
    "pyproject.toml": '[project]\nname = "worker"\ndependencies = ["acme-svc"]\n',
    "worker/__init__.py": "",
    "worker/jobs.py": "from acme_svc.core import compute\n\n\ndef nightly(orders):\n    return [compute(o) for o in orders]\n",
    "README.md": "# worker\n\n## Nightly job\n\nThe nightly job calls acme_svc compute once for every open order.\n",
}
CHANGE = "    return helper(x) * 3\n"  # the sample change: compute triples instead of doubling


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), "-c", "user.email=dana@example.com", "-c", "user.name=Dana", *args], check=True, capture_output=True, text=True).stdout


def _repo(root: Path, files: dict[str, str], origin: str) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    _git(root, "remote", "add", "origin", origin)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "Initial import")
    return root


def _call(server, tool: str, args: dict) -> dict:
    res = asyncio.run(server.call_tool(tool, {**args, "format": "json"}))
    content = res.content if hasattr(res, "content") else res[0] if isinstance(res, tuple) else res
    return json.loads(content[0].text)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """Two repositories with history, a record file naming both, and every Orion store under tmp_path."""
    for var, rel in (("ORION_DOCS", "docs.sqlite"), ("ORION_RAG", "rag.duckdb"), ("ORION_RECORD", "record.toml"), ("ORION_RECORD_CACHE", "record-cache"), ("XDG_CACHE_HOME", "xdg")):
        monkeypatch.setenv(var, str(tmp_path / rel))
    monkeypatch.setenv("ORION_RAG_EMBEDDER", "hash")
    monkeypatch.setenv("ORION_RAG_RERANKER", "none")
    svc = _repo(tmp_path / "svc", SVC, "https://github.com/acme/svc.git")
    (svc / "src/acme_svc/core.py").write_text(SVC["src/acme_svc/core.py"].replace("+ int(", "+ 0 + int("))
    _git(svc, "commit", "-qam", "fix: compute dropped the SCALE offset for zero totals")
    worker = _repo(tmp_path / "worker", WORKER, "https://github.com/acme/worker.git")
    (tmp_path / "record.toml").write_text(
        f'[ingest]\nsessions = []\ngithub = []\n\n[[repo]]\nname = "svc"\npath = "{svc}"\ninclude = ["README.md", "docs/**/*.md"]\n\n'
        f'[[repo]]\nname = "worker"\npath = "{worker}"\ninclude = ["README.md"]\n'
    )
    agent._cache.clear()
    yield {"tmp": tmp_path, "svc": svc, "worker": worker}
    agent._cache.clear()
    with agent._work_lock:
        agent._rag_close()
    agent._rag_held.clear()


def _build(world) -> dict:
    """The pipeline an install runs: ingest the record, sync the code graph, index code and record for retrieval."""
    from orion import codegraph
    from orion.rag import RagIndex
    from orion.record.documents import DocStore
    from orion.record.ingest import run as ingest
    from orion.store import Store

    svc, worker = world["svc"], world["worker"]
    with DocStore.open() as docs:
        reports = ingest(world["tmp"] / "record.toml", docs)
        assert not any(r.incomplete for r in reports), [r.notes for r in reports]
        counts = docs.counts()
        # Where `orion codegraph sync --repo` puts it, so the record server finds it without an orion.yaml.
        store = Store(svc / ".orion" / "graph.duckdb")
        graph = codegraph.sync(store, [codegraph.RepoSpec("svc", svc), codegraph.RepoSpec("worker", worker)], cache_dir=world["tmp"] / "cg")
        store.con.close()
        idx = RagIndex.open()
        indexed = [idx.index_checkout(svc), idx.index_checkout(worker), idx.sync_record(docs)]
        idx.close()
    return {"counts": counts, "graph": graph, "indexed": indexed}


def test_the_full_path_answers_cited_scoped_and_budgeted(world):
    from orion.server import build_record_server

    built = _build(world)
    assert built["counts"]["git"]["live"] == 3 and built["counts"]["markdown"]["live"] == 3
    assert built["graph"]["repos"] == 2 and not built["graph"]["skipped"]
    assert all(r.get("chunks") for r in built["indexed"])
    svc = world["svc"]
    assert subprocess.run(["git", "-C", str(svc), "status", "--porcelain"], capture_output=True, text=True).stdout == "", "Orion's own folder is ignored, never part of a change"

    core = svc / "src/acme_svc/core.py"
    core.write_text(core.read_text().replace("    return helper(x) * 2\n", CHANGE))
    diff = _git(svc, "diff")
    server = build_record_server(svc)
    held: dict[str, dict] = {}

    # context: what to know before starting, from the record, the code and the history.
    ctx = held["context"] = _call(server, "context", {"task": "change how compute doubles order totals", "paths": ["src/acme_svc/core.py"]})
    ids = [i["id"] for i in ctx["items"]]
    assert ctx["repo"] == "svc" and ctx["platform"] is False
    assert any(i.startswith("md:svc:docs/decisions.md") for i in ids), ids  # the decision behind the code
    assert any(i.startswith("git:svc@") for i in ids)
    assert len({i["id"] for i in ctx["items"]}) == len(ctx["items"])

    # impact: the change's blast radius crosses into the worker repository, from parsed calls, not text matches.
    imp = held["impact"] = _call(server, "impact", {"diff": diff})
    assert imp["changed"] == ["src/acme_svc/core.py"]
    deps = {i["id"]: i for i in imp["items"] if i["kind"] == "dependent"}
    assert "symbol:worker/worker/jobs.py::nightly" in deps, list(deps)
    assert deps["symbol:worker/worker/jobs.py::nightly"].get("cross_repo") is True
    assert "symbol:svc/src/acme_svc/api.py::get_order" in deps
    assert any(i.startswith("route:svc") for i in deps)
    assert not [i for i in imp["items"] if i["id"].startswith("ref:") and i["kind"] == "dependent"], "graph-covered files skip the text match"
    assert any(i["kind"] == "fix" and "SCALE" in i["title"] for i in imp["items"])  # past fixes on the file
    assert all(isinstance(u, str) and not u.startswith("{") for u in imp["unresolved"])
    # The same change given as a base ref reaches the same dependants.
    by_ref = _call(server, "impact", {"base_ref": "HEAD"})
    assert by_ref["changed"] == ["src/acme_svc/core.py"]
    assert {i["id"] for i in by_ref["items"] if i["kind"] == "dependent"} == set(deps)

    # search: over everything, one passage per document, the same passage once whichever index holds it.
    found = held["search"] = _call(server, "search", {"query": "why must compute never read the database"})
    assert found["items"][0]["id"].startswith("md:svc:docs/decisions.md"), [i["id"] for i in found["items"]]
    assert "rag" in found["sources"]
    # The record section index and the retrieval index both read docs/decisions.md; the commit that added it is its own item.
    prose = [i for i in found["items"] if i["kind"] in ("doc", "record") and "hot loop" in i.get("text", "")]
    assert len(prose) == 1, [i["id"] for i in found["items"]]
    only_worker = _call(server, "search", {"query": "nightly job compute", "repo": "worker"})
    assert only_worker["items"] and all((i.get("repo") or "worker") == "worker" for i in only_worker["items"])

    # file_history: fixes first, and the record passages about the file.
    hist = held["file_history"] = _call(server, "file_history", {"path": "src/acme_svc/core.py"})
    assert hist["items"][0]["kind"] == "commit" and "fix:" in hist["items"][0]["title"]

    # read: every id any tool cited reads back, from whichever slice holds it.
    for name, r in held.items():
        for item in r["items"]:
            got = _call(server, "read", {"id": item["id"]})
            assert "error" not in got and got["items"], (name, item["id"], got)
    sym = _call(server, "read", {"id": "symbol:svc/src/acme_svc/core.py::compute"})["items"][0]
    assert sym["kind"] == "code" and "dependant" in sym["title"] and "nightly" in sym["text"]  # the code graph's view
    doc = _call(server, "read", {"id": "md:svc:docs/decisions.md"})["items"][0]
    assert doc["kind"] == "doc" and "hot loop" in doc["text"]  # the ingested record, by its DocStore id
    assert "not recorded" in _call(server, "read", {"id": "md:svc:docs/missing.md"})["error"]

    # Every answer fits its budget; a small budget cuts and lists what it left out instead of overrunning.
    for name, r in held.items():
        assert r["budget"]["used"] <= r["budget"]["tokens"] == agent.DEFAULT_BUDGET[name], (name, r["budget"])
        assert agent.estimate_tokens({k: v for k, v in r.items() if k not in ("elapsed_ms", "cached")}) <= r["budget"]["tokens"] + 50
    small = _call(server, "impact", {"diff": diff, "budget_tokens": 300})
    assert small["budget"]["used"] <= 300 and (small["omitted"] or any(i.get("truncated") for i in small["items"]))


def test_cold_then_warm_calls_are_cached_and_cost_the_same_tokens(world):
    from orion.server import build_record_server

    _build(world)
    server = build_record_server(world["svc"])
    calls = [
        ("context", {"task": "change how compute doubles order totals", "paths": ["src/acme_svc/core.py"]}),
        ("impact", {"paths": ["src/acme_svc/core.py"], "intent": "triple order totals"}),
        ("search", {"query": "nightly job compute"}),
        ("file_history", {"path": "src/acme_svc/core.py"}),
    ]
    for tool, args in calls:
        cold = _call(server, tool, args)
        warm = _call(server, tool, args)
        assert "cached" not in cold and warm["cached"] is True, tool
        assert warm["elapsed_ms"] < max(cold["elapsed_ms"], 50), (tool, cold["elapsed_ms"], warm["elapsed_ms"])
        assert warm["elapsed_ms"] < 250, (tool, warm["elapsed_ms"])  # a lookup: two git calls and a stat
        strip = lambda r: {k: v for k, v in r.items() if k not in ("elapsed_ms", "cached")}  # noqa: E731
        assert strip(warm) == strip(cold), tool  # same answer, same tokens, no work redone
    # An edit to the checkout is a new key: the next call is answered fresh.
    core = world["svc"] / "src/acme_svc/core.py"
    core.write_text(core.read_text() + "\n# touched\n")
    assert "cached" not in _call(server, "file_history", {"path": "src/acme_svc/core.py"})


def test_a_running_server_never_locks_out_a_reindex(world, monkeypatch):
    from orion.server import build_record_server

    monkeypatch.setattr(agent, "RAG_IDLE_S", 0.2)  # the index is let go this long after the last call

    _build(world)
    server = build_record_server(world["svc"])
    assert _call(server, "impact", {"paths": ["src/acme_svc/core.py"]})["items"]
    assert _call(server, "search", {"query": "compute"})["items"]
    time.sleep(0.5)
    # Another process (`orion rag index`, `orion codegraph sync --repo`) writes both files while the server is up.
    script = (
        "import sys, duckdb\n"
        "for p in sys.argv[1:]:\n"
        "    c = duckdb.connect(p); c.execute('CREATE TABLE IF NOT EXISTS lock_probe (x INT)'); c.close()\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", script, str(world["tmp"] / "rag.duckdb"), str(world["svc"] / ".orion" / "graph.duckdb")], capture_output=True, text=True)
    assert out.stdout.strip() == "ok", out.stderr[-500:]
    # The written index is a new key, and the server answers from it on the next call.
    agent._cache.clear()
    assert _call(server, "search", {"query": "compute"})["items"]
