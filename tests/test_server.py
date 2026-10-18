import asyncio
import json
from pathlib import Path

from orion.config import load_config
from orion.server import build_server

ROOT = Path(__file__).resolve().parents[1]


def test_server_exposes_nine_tools(synced_store):
    cfg = load_config(ROOT / "orion.yaml")
    cfg.db = str(synced_store.path)
    cfg.root = ROOT
    server = build_server(cfg)
    tools = asyncio.run(server.list_tools())
    assert {t.name for t in tools} == {"preflight", "explain", "history", "record_decision", "propose_constraint", "ask", "review", "search_record", "read_record"}


def test_review_tool_runs_a_diff(synced_store):
    from orion.providers import StubProvider

    cfg = load_config(ROOT / "orion.yaml")
    cfg.db = str(synced_store.path)
    cfg.root = ROOT
    fct = "fixture/dbt_project/models/marts/fct_orders.sql"
    stub = StubProvider({"findings": [{"path": fct, "line": 8, "severity": "warn", "category": "correctness", "title": "net_amount reads a dropped column", "body": "b", "failure": "a caller passing the old value gets the wrong result", "evidence": [f"hunk:{fct}:8"]}]})
    server = build_server(cfg, review_provider=stub)
    diff = (ROOT / fct).read_text().replace("    discount_pct,\n", "")
    import difflib

    old = (ROOT / fct).read_text().splitlines(keepends=True)
    text = "".join(difflib.unified_diff(old, diff.splitlines(keepends=True), f"a/{fct}", f"b/{fct}"))
    text = f"diff --git a/{fct} b/{fct}\n" + text
    result = asyncio.run(server.call_tool("review", {"diff": text}))
    r = json.loads(result.content[0].text) if hasattr(result, "content") else result
    titles = [f["title"] for f in r["findings"]]
    assert titles[0].startswith("CON-031"), "deterministic finding from the platform first"
    assert "net_amount reads a dropped column" in titles, "model finding survives validation"
    asyncio.run(server.call_tool("review", {"diff": text, "intent": "Drop discount_pct"}))
    assert "<<<INTENT\nDrop discount_pct\nINTENT>>>" in stub.calls[-1][1]
    missing = asyncio.run(server.call_tool("review", {}))
    assert "give base_ref or diff" in str(missing)
    bad_ref = asyncio.run(server.call_tool("review", {"base_ref": "no-such-ref"}))
    assert "review failed" in str(bad_ref)


def test_ask_is_graph_scoped(synced_store):
    from orion.retrieval import ask

    out = ask(synced_store, "why is events partitioned by event_date", ["source_table:raw.raw_events"])
    assert out["answer"] != "not recorded"
    assert any("INC-214" in c["source"] or "CON-014" in c["chunk"] for c in out["chunks"])
    assert all("[" in s for s in out["answer"].split("] ") if s)
    nothing = ask(synced_store, "what colour is the finance office carpet", ["model:fct_revenue"])
    assert nothing["answer"] == "not recorded" or nothing["chunks"]


def test_rankers_agree_on_the_obvious_question(synced_store):
    from orion.retrieval import HashEmbedder, ask

    q = "why was raw_events repartitioned and what went wrong"
    scope = ["source_table:raw.raw_events"]
    lexical = ask(synced_store, q, scope, ranker="bm25")
    hybrid = ask(synced_store, q, scope, ranker="hybrid", embedder=HashEmbedder())
    dense = ask(synced_store, q, scope, ranker="embedding", embedder=HashEmbedder())
    for out in (lexical, hybrid, dense):
        assert out["answer"] != "not recorded"
        assert any("INC-214" in c["source"] for c in out["chunks"][:3])
    assert hybrid["ranker"] == "hybrid"
    cached = synced_store.con.execute("SELECT count(*) FROM chunk_vectors WHERE model = 'hash-512'").fetchone()[0]
    assert cached > 0
