from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orion.rag import RagIndex, Scope, SourceDoc
from orion.rag.chunking import chunk, terms
from orion.rag.cite import check, extractive_answer
from orion.rag.code import import_edges, resolve_import
from orion.syntax import available

needs_syntax = pytest.mark.skipif(not available(), reason="tree-sitter (the syntax extra) is not installed")

STORE_PY = '''"""Storage."""

import json


class Store:
    """Holds things."""

    def __init__(self, path):
        self.path = path

    def walk(self, start, hops=3):
        """Breadth-first walk over consumers."""
        return [start] * hops


def helper():
    return json.dumps({})
'''

CLI_PY = '''from .store import Store


def main():
    return Store("x").walk("a")
'''

TEST_PY = '''from app.cli import main


def test_main():
    assert main()
'''

README = """# App

## Running the tests

Run `pytest -q` from the repository root.

## Decisions

We partition events by event_date, never by ingest time, because late arrivals double count.
"""


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "app-repo"
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "app" / "__init__.py").write_text("")
    (root / "app" / "store.py").write_text(STORE_PY)
    (root / "app" / "cli.py").write_text(CLI_PY)
    (root / "tests" / "test_cli.py").write_text(TEST_PY)
    (root / "README.md").write_text(README)
    git = ["git", "-C", str(root), "-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    return root


def test_terms_split_code_names():
    assert {"walk", "downstream", "walkdownstream", "http", "server"} <= set(terms("walkDownstream HTTPServer"))
    assert terms("running")[0] == terms("run")[0]


@needs_syntax
def test_code_is_cut_by_definition_with_line_spans():
    doc = SourceDoc(id="code:app/app/store.py", text=STORE_PY, kind="code", repo="app", path="app/store.py")
    chunks = chunk(doc)
    syms = [c.symbol for c in chunks]
    assert any("Store" in s for s in syms) and any("helper" in s for s in syms)
    for c in chunks:
        lines = STORE_PY.splitlines()[c.start_line - 1 : c.end_line]
        assert "\n".join(lines).strip() == c.text.strip()
        assert c.id == f"code:app/app/store.py#L{c.start_line}-{c.end_line}"


def test_markdown_is_cut_at_headings_and_long_runs_are_split():
    para = " ".join(["word"] * 80)
    text = f"# App\n\n## Running the tests\n\n{para}\n\n## Decisions\n\n{para}\n\n## Changes\n\n" + "\n".join(f"- item {i} " + "x " * 20 for i in range(60))
    doc = SourceDoc(id="md:app:README.md", text=text, kind="doc", repo="app", path="README.md", language="markdown")
    chunks = chunk(doc)
    heads = [c.symbol for c in chunks]
    assert any("Decisions" in h for h in heads) and len({h for h in heads}) >= 3
    assert sum("Changes" in h for h in heads) >= 2  # a bullet run with no blank line is still split


def test_diff_is_cut_per_file():
    text = "Fix the walk\n\nLonger message.\n\ndiff --git a/a.py b/a.py\n+x\ndiff --git a/b.py b/b.py\n+y\n"
    doc = SourceDoc(id="git:app@abc", text=text, kind="commit", repo="app", language="diff")
    syms = [c.symbol for c in chunk(doc)]
    assert "a.py" in syms and "b.py" in syms


@needs_syntax
def test_python_imports_resolve_within_the_repository():
    files = {"app/__init__.py", "app/store.py", "app/cli.py", "tests/test_cli.py"}
    assert resolve_import("app/cli.py", ".store", files) == ["app/store.py"]
    assert resolve_import("tests/test_cli.py", "app.cli", files) == ["app/cli.py"]
    assert resolve_import("app/cli.py", "json", files) == []
    # A one-word import never matches a local file by suffix: ``import logging`` is the standard library.
    assert resolve_import("app/cli.py", "logging", files | {"app/logging.py"}) == []
    assert resolve_import("main.go", "errors", {"pkg/errors/e.go"}) == []
    docs = [
        SourceDoc(id="code:app/app/cli.py", text=CLI_PY, kind="code", repo="app", path="app/cli.py"),
        SourceDoc(id="code:app/app/store.py", text=STORE_PY, kind="code", repo="app", path="app/store.py"),
    ]
    assert ("app", "app/cli.py", "app/store.py") in import_edges(docs)


@pytest.fixture()
def index(tmp_path: Path) -> tuple[RagIndex, Path]:
    root = _repo(tmp_path)
    idx = RagIndex(tmp_path / "rag.duckdb", embedder="hash")
    idx.index_checkout(root, "app")
    return idx, root


@needs_syntax
def test_search_finds_code_and_prose(index):
    idx, _ = index
    hits = idx.search("breadth-first walk", k=3)
    assert hits and hits[0].path == "app/store.py" and "Breadth-first walk" in hits[0].text
    assert hits[0].signals.get("bm25_rank") and hits[0].why
    prose = idx.search("why partition events by event_date", k=3)
    assert prose[0].path == "README.md"
    assert idx.search("breadth-first walk", k=3, mode="bm25")[0].path == "app/store.py"
    assert idx.search("breadth-first walk", k=3, scope=Scope(kinds=["doc"]))[0].kind == "doc"
    assert idx.search("breadth-first walk", k=0) == []


def test_a_batch_naming_one_id_twice_keeps_the_last(tmp_path: Path):
    idx = RagIndex(tmp_path / "rag.duckdb", embedder="hash")
    idx.add([SourceDoc(id="d", text="alpha beta", kind="doc"), SourceDoc(id="d", text="gamma delta", kind="doc")])
    assert idx.con.execute("SELECT count(*) FROM rag_postings WHERE term = 'alpha'").fetchone()[0] == 0
    assert idx.con.execute("SELECT count(*) FROM rag_postings WHERE term = 'gamma'").fetchone()[0] == 1


def test_form_feeds_do_not_shift_line_spans():
    text = "import os\n\x0c\ndef a():\n    return 1\n\n\ndef b():\n    return 2\n"
    for c in chunk(SourceDoc(id="code:r/x.py", text=text, kind="code", repo="r", path="x.py")):
        assert "\n".join(text.split("\n")[c.start_line - 1 : c.end_line]).strip() == c.text.strip()


@needs_syntax
def test_graph_scope_reaches_importers_and_tests(index):
    idx, _ = index
    hits = idx.search("tests for this", k=5, scope=Scope(paths=["app/store.py"], repo="app"))
    scoped = {h.path: h.signals.get("graph_hops") for h in hits if h.in_scope}
    assert scoped.get("app/cli.py") == 1
    assert scoped.get("tests/test_cli.py") == 2
    only = idx.search("tests for this", k=5, scope=Scope(paths=["./app/store.py"], repo="app", only_in_scope=True))
    assert only and all(h.in_scope for h in only)
    assert idx.search("tests for this", k=5, scope=Scope(paths=["app/missing.py"], repo="app", only_in_scope=True)) == []
    assert idx.scope_hops(Scope(paths=["app/store.py"], repo="APP")) == idx.scope_hops(Scope(paths=["app/store.py"], repo="app"))
    assert Scope(paths=["", "./a.py"]).paths == ["a.py"]


@needs_syntax
def test_reindex_is_incremental_and_skips_an_unchanged_checkout(index):
    idx, root = index
    assert idx.index_checkout(root, "app")["skipped"] is True
    first = idx.search("breadth-first walk", k=3)
    assert idx.search("breadth-first walk", k=3)[0].signals.get("cached")
    (root / "app" / "store.py").write_text(STORE_PY.replace("Breadth-first walk over consumers", "Depth-first crawl"))
    res = idx.index_checkout(root, "app")
    assert res["skipped"] is False and res["docs"] == 1  # only the changed file is re-chunked
    after = idx.search("breadth-first walk", k=3)
    assert not after[0].signals.get("cached") and after != first
    (root / "app" / "cli.py").unlink()
    assert idx.index_checkout(root, "app")["removed"] == 1
    assert idx.read("code:app/app/cli.py#L1-5")["error"].startswith("not recorded")


@needs_syntax
def test_read_and_citation_check(index):
    idx, _ = index
    hits = idx.search("breadth-first walk", k=3)
    got = idx.read(f"chunk:{hits[0].chunk_id}")
    assert got["path"] == "app/store.py" and "walk" in got["text"]
    answer = extractive_answer("breadth-first walk", hits, n=1)
    ok = check(answer, idx, retrieved={h.chunk_id for h in hits})
    assert ok["faithfulness"] == 1.0 and ok["validity"] == 1.0
    readme = next(h for h in idx.search("partition events event_date", k=5) if h.path == "README.md")
    wrong = check(f"Store keeps a breadth-first walk over consumers. [chunk:{readme.chunk_id}]", idx)
    assert wrong["unsupported"] and wrong["faithfulness"] == 0.0
    fake = check("It walks consumers. [chunk:code:app/nope.py#L1-2]", idx)
    assert fake["invalid"] == ["code:app/nope.py#L1-2"] and fake["validity"] == 0.0
    assert check("[chunk:code:app/nope.py#L1-2]", idx)["invalid"] == ["code:app/nope.py#L1-2"]  # a bare citation is checked too
    assert check("No citation here at all.", idx)["uncited"]


def test_from_record_skips_deleted_and_duplicates():
    from types import SimpleNamespace

    from orion.rag import from_record

    base = dict(id="gh:o/r#pr/1", text="body", kind="pr", repo="r", path="", title="PR", url="u", created_at="2026-01-01", updated_at=None, refs=["path:r/a.py"], meta={}, content_hash="h")
    assert from_record(SimpleNamespace(**base)).refs == ["path:r/a.py"]
    assert from_record(SimpleNamespace(**base, deleted=True)) is None
    assert from_record(SimpleNamespace(**base, duplicate_of="x")) is None


def test_record_refs_put_history_in_scope(tmp_path: Path):
    idx = RagIndex(tmp_path / "rag.duckdb", embedder="hash")
    idx.add([
        SourceDoc(id="gh:o/app#pr/7", text="## Summary\n\nReworked the store walk to stop at three hops.", kind="pr", repo="app", refs=["path:app/app/store.py"]),
        SourceDoc(id="gh:o/app#pr/8", text="## Summary\n\nReworked the store walk docs.", kind="pr", repo="app"),
    ])  # fmt: skip
    hits = idx.search("store walk", k=2, scope=Scope(paths=["app/store.py"], repo="app"))
    assert hits[0].doc_id == "gh:o/app#pr/7" and hits[0].in_scope


def test_data_graph_scope_reaches_incidents(synced_store, tmp_path: Path):
    idx = RagIndex(tmp_path / "rag.duckdb", embedder="hash")
    idx.sync_store(synced_store, root=Path(__file__).resolve().parents[1], repo="orion")
    scope = Scope.from_change(synced_store, ["fixture/dbt_project/models/marts/fct_orders.sql"], repo="orion")
    assert "model:fct_orders" in scope.asset_hops
    hits = idx.search("past incidents", k=5, scope=scope)
    assert any("INC-230" in h.path and h.in_scope for h in hits)
