"""The retrieval index: chunks, BM25 postings, dense vectors and import edges in one DuckDB file.

A search runs in five steps, each visible in a hit's ``signals``:

1. **Candidates.** BM25 over the postings table and cosine over the vectors (a chunk scores its
   best vector: its own text, or a view of it such as one definition's signature and doc
   sentence, or a section's heading path), each the top
   ``CANDIDATES`` over the whole index and again inside the graph scope, so a relevant chunk in
   the blast radius is never crowded out by a better-worded one elsewhere.
2. **Fusion.** Reciprocal rank fusion of the lexical and dense rankings. Rank fusion needs no
   score calibration between the two, which is why most production hybrid search uses it.
3. **Graph ranking.** Chunks in the scope form a third ranking, nearest first (the changed
   file, then its importers and the assets downstream of it), ties broken by the text ranking,
   fused in with weight ``GRAPH_WEIGHT``. A chunk whose symbol the query names exactly is
   multiplied by ``1 + SYMBOL_BOOST``.
4. **Rerank.** Optionally, a local cross-encoder rescores the top ``RERANK_DEPTH`` pairs.
5. **Diversity.** At most ``PER_DOC`` chunks of one document in the first ``k``, unless
   fewer documents than that match at all.

Everything except the embedding model and the reranker is deterministic code. The index never
calls a hosted model: embeddings are hashed vectors, a local sentence-transformers model or a
local Ollama, and the reranker is a local cross-encoder.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import subprocess
from collections import Counter, OrderedDict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import duckdb

from .chunking import Chunk, chunk, terms
from .docs import SourceDoc, repo_name
from .scope import Scope

K1 = 1.2
B = 0.75
HEADER_WEIGHT = 2  # header terms (path, symbol, heading) count this many extra times
CANDIDATES = 80
RRF_K = 60


def _weight(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return default
    return value if math.isfinite(value) and value >= 0 else default


GRAPH_WEIGHT = _weight("ORION_RAG_GRAPH_WEIGHT", 1.0)
LEXICAL_WEIGHT = _weight("ORION_RAG_LEXICAL_WEIGHT", 1.0)  # against dense at 1.0
SYMBOL_BOOST = 0.5
RERANK_DEPTH = 30
PER_DOC = 2
SCHEMA_VERSION = "1"
CHUNKER_VERSION = "3"  # bump when chunking, terms or posting weights change
CACHE_SIZE = 512

SCHEMA = """
CREATE TABLE IF NOT EXISTS rag_meta (key VARCHAR PRIMARY KEY, value VARCHAR);
CREATE TABLE IF NOT EXISTS rag_docs (
    doc_id VARCHAR PRIMARY KEY, kind VARCHAR, repo VARCHAR, path VARCHAR, title VARCHAR, url VARCHAR,
    date VARCHAR, content_hash VARCHAR, refs VARCHAR[], asset_ids VARCHAR[]
);
CREATE TABLE IF NOT EXISTS rag_chunks (
    chunk_id VARCHAR PRIMARY KEY, doc_id VARCHAR, kind VARCHAR, repo VARCHAR, path VARCHAR, title VARCHAR,
    url VARCHAR, date VARCHAR, symbol VARCHAR, start_line INTEGER, end_line INTEGER, header VARCHAR, text VARCHAR,
    text_hash VARCHAR, n_terms INTEGER, refs VARCHAR[], asset_ids VARCHAR[]
);
CREATE TABLE IF NOT EXISTS rag_views (chunk_id VARCHAR, text_hash VARCHAR, text VARCHAR);
CREATE TABLE IF NOT EXISTS rag_postings (term VARCHAR, chunk_id VARCHAR, tf INTEGER);
CREATE TABLE IF NOT EXISTS rag_vectors (text_hash VARCHAR, model VARCHAR, vec FLOAT[], PRIMARY KEY (text_hash, model));
CREATE TABLE IF NOT EXISTS rag_edges (repo VARCHAR, src VARCHAR, dst VARCHAR);
"""


def default_path() -> Path:
    """``ORION_RAG``, else ``~/.cache/orion/rag.duckdb`` (never inside a repository)."""
    configured = os.environ.get("ORION_RAG")
    if configured:
        return Path(configured)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "orion" / "rag.duckdb"


def default_embedder() -> str:
    """``ORION_RAG_EMBEDDER``, else a local sentence-transformers model when the ``embed`` extra is installed, else hashed vectors."""
    configured = os.environ.get("ORION_RAG_EMBEDDER")
    if configured:
        return configured
    try:
        import sentence_transformers  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return "hash"
    return "st:BAAI/bge-small-en-v1.5"


@dataclass
class Hit:
    chunk_id: str
    doc_id: str
    kind: str
    repo: str
    path: str
    start_line: int
    end_line: int
    title: str
    symbol: str
    text: str
    url: str
    date: str | None
    score: float
    signals: dict[str, Any] = field(default_factory=dict)
    why: str = ""
    in_scope: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rrf_ranks(scores: dict[str, float]) -> dict[str, int]:
    return {cid: i + 1 for i, (cid, _) in enumerate(sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])))}


class RagIndex:
    """One retrieval index file. ``open`` it, ``add`` documents (or ``index_checkout`` / ``sync_record``), then ``search``."""

    def __init__(self, path: Path | str, embedder: str | None = None, reranker: str | None = None, read_only: bool = False):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = duckdb.connect(str(path), read_only=read_only)
        if not read_only:
            self.con.execute(SCHEMA)
        if not read_only and self.meta("chunker") != CHUNKER_VERSION:
            # A new chunker or posting weights must reach unchanged files too: forget every content hash so the next add re-cuts them.
            self.con.execute("UPDATE rag_docs SET content_hash = ''")
            self.con.execute("DELETE FROM rag_meta WHERE key LIKE 'checkout:%' OR key = 'record_seq'")
            self.set_meta("chunker", CHUNKER_VERSION)
        held = self.meta("embedder")
        self.embedder_name = embedder or held or default_embedder()
        if not read_only and held != self.embedder_name:
            self.set_meta("embedder", self.embedder_name)
        self._embedder: Any = None
        self.reranker_name = reranker if reranker is not None else os.environ.get("ORION_RAG_RERANKER", "none")
        self._reranker: Any = None
        self._cache: OrderedDict[tuple, list[Hit]] = OrderedDict()
        self._qvec: OrderedDict[str, list[float]] = OrderedDict()

    @classmethod
    def open(cls, path: Path | str | None = None, *, embedder: str | None = None, reranker: str | None = None, read_only: bool = False) -> RagIndex:
        """The index at ``path``. DuckDB lets one process hold a file; when another one does, this fails at once with a clear error rather than waiting."""
        target = path or default_path()
        try:
            return cls(target, embedder=embedder, reranker=reranker, read_only=read_only)
        except duckdb.IOException as e:
            if "lock" not in str(e).lower():
                raise
            raise RuntimeError(f"the retrieval index {target} is in use by another process; point ORION_RAG at another file or stop that process") from e

    def close(self) -> None:
        self.con.close()

    # ------------------------------------------------------------ meta
    def meta(self, key: str) -> str | None:
        try:
            row = self.con.execute("SELECT value FROM rag_meta WHERE key = ?", [key]).fetchone()
        except duckdb.CatalogException:
            return None
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.con.execute("INSERT OR REPLACE INTO rag_meta VALUES (?, ?)", [key, value])

    @property
    def embedder(self) -> Any:
        if self._embedder is None:
            from orion.retrieval import make_embedder

            self._embedder = make_embedder(self.embedder_name)
        return self._embedder

    @property
    def reranker(self) -> Any:
        if self._reranker is None and self.reranker_name not in ("", "none"):
            from .rerank import make_reranker

            self._reranker = make_reranker(self.reranker_name)
        return self._reranker

    def stats(self) -> dict[str, Any]:
        out: dict[str, Any] = {t: self.con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("rag_docs", "rag_chunks", "rag_views", "rag_vectors", "rag_edges")}
        out["embedder"] = self.embedder_name
        out["reranker"] = self.reranker_name
        out["by_kind"] = dict(self.con.execute("SELECT kind, count(*) FROM rag_chunks GROUP BY kind ORDER BY kind").fetchall())
        return out

    # ------------------------------------------------------------ writing
    def hashes(self, prefix: str | None = None) -> dict[str, str]:
        sql, args = "SELECT doc_id, content_hash FROM rag_docs", []
        if prefix:
            sql, args = sql + " WHERE starts_with(doc_id, ?)", [prefix]
        return dict(self.con.execute(sql, args).fetchall())

    def generation(self) -> int:
        """A counter bumped by every write; cached searches are keyed on it, so a write invalidates them."""
        return int(self.meta("generation") or 0)

    def _bump(self) -> None:
        self.set_meta("generation", str(self.generation() + 1))
        self._cache.clear()

    def remove(self, doc_ids: Iterable[str]) -> int:
        ids = list(doc_ids)
        if not ids:
            return 0
        self._bump()
        self.con.execute("CREATE OR REPLACE TEMP TABLE _gone AS SELECT unnest(?::VARCHAR[]) AS doc_id", [ids])
        for table in ("rag_postings", "rag_views"):
            self.con.execute(f"DELETE FROM {table} WHERE chunk_id IN (SELECT chunk_id FROM rag_chunks WHERE doc_id IN (SELECT doc_id FROM _gone))")
        self.con.execute("DELETE FROM rag_chunks WHERE doc_id IN (SELECT doc_id FROM _gone)")
        self.con.execute("DELETE FROM rag_docs WHERE doc_id IN (SELECT doc_id FROM _gone)")
        return len(ids)

    def add(self, docs: Iterable[SourceDoc], *, embed: bool = True) -> dict[str, int]:
        """Index documents, replacing any held under the same id; a document whose content hash is unchanged is skipped."""
        held = self.hashes()
        latest = {d.id: d for d in docs}  # a batch that names one id twice keeps the last version
        fresh = [d for d in latest.values() if held.get(d.id) != d.content_hash]
        if not fresh:
            # Nothing new, but an earlier embedding pass may have failed after its chunks were written.
            return {"docs": 0, "chunks": 0, "embedded": self.embed_missing() if embed else 0}
        self.remove([d.id for d in fresh if d.id in held])
        self._bump()
        doc_rows, chunk_rows, posting_rows, view_rows = [], [], [], []
        for d in fresh:
            doc_rows.append((d.id, d.kind, d.repo, d.path, d.title, d.url, d.date, d.content_hash, d.refs, d.asset_ids))
            for c in chunk(d):
                counts = Counter(terms(c.text))
                for t in terms(c.header):
                    counts[t] += HEADER_WEIGHT
                th = hashlib.sha1(c.indexed_text.encode("utf-8", errors="replace")).hexdigest()
                chunk_rows.append((c.id, d.id, d.kind, d.repo, d.path, d.title, d.url, d.date, c.symbol, c.start_line, c.end_line, c.header, c.text, th, sum(counts.values()), d.refs, d.asset_ids))
                posting_rows.extend((t, c.id, n) for t, n in counts.items())
                view_rows.extend((c.id, hashlib.sha1(v.encode("utf-8", errors="replace")).hexdigest(), v) for v in c.views)
        self._bulk("rag_docs", doc_rows, "VARCHAR VARCHAR VARCHAR VARCHAR VARCHAR VARCHAR VARCHAR VARCHAR VARCHAR[] VARCHAR[]")
        self._bulk("rag_chunks", chunk_rows, "VARCHAR " * 9 + "INTEGER INTEGER VARCHAR VARCHAR VARCHAR INTEGER VARCHAR[] VARCHAR[]")
        self._bulk("rag_postings", posting_rows, "VARCHAR VARCHAR INTEGER")
        self._bulk("rag_views", view_rows, "VARCHAR VARCHAR VARCHAR")
        embedded = self.embed_missing() if embed else 0
        return {"docs": len(doc_rows), "chunks": len(chunk_rows), "embedded": embedded}

    def embed_missing(self, batch: int = 256) -> int:
        """Embed every chunk text and view the current embedder has no vector for; vectors are keyed by text, so a re-chunk reuses them."""
        rows = self.con.execute(
            "SELECT DISTINCT ON (t.text_hash) t.text_hash, t.text"
            " FROM (SELECT text_hash, header || chr(10) || text AS text FROM rag_chunks UNION ALL SELECT text_hash, text FROM rag_views) t"
            " LEFT JOIN rag_vectors v ON v.text_hash = t.text_hash AND v.model = ? WHERE v.text_hash IS NULL",
            [self.embedder_name],
        ).fetchall()
        for i in range(0, len(rows), batch):
            part = rows[i : i + batch]
            vecs = self.embedder.embed([t[:4000] for _, t in part])
            self._bulk("rag_vectors", [(h, self.embedder_name, v) for (h, _), v in zip(part, vecs, strict=True)], "VARCHAR VARCHAR FLOAT[]")
        if rows:
            self._bump()  # cached searches ranked without these vectors
        return len(rows)

    def _bulk(self, table: str, rows: list[tuple], types: str) -> None:
        """Insert rows column-wise in one statement (``executemany`` is row-at-a-time in DuckDB and far slower)."""
        if not rows:
            return
        cols = list(zip(*rows, strict=True))
        kinds = types.split()
        select = ", ".join(f"unnest(?::{t}[])" for t in kinds)
        self.con.execute(f"INSERT OR REPLACE INTO {table} SELECT {select}" if table not in ("rag_postings", "rag_views") else f"INSERT INTO {table} SELECT {select}", [list(c) for c in cols])

    def index_checkout(self, root: Path | str, repo: str | None = None, *, force: bool = False) -> dict[str, Any]:
        """Index a checkout's tracked files incrementally: unchanged files (same blob) are skipped, deleted ones dropped.

        When HEAD and the working tree are as they were at the last call, nothing is read at all,
        which is what makes a per-diff review cheap."""
        from .code import _tracked, checkout_documents, import_edges, wanted

        root = Path(root)
        repo = repo or repo_name(root)
        state = _tree_state(root)
        if not force and state and self.meta(f"checkout:{repo}") == state:
            # The checkout is as it was, but the embedder may be new: give its chunks vectors for this model.
            return {"repo": repo, "skipped": True, "embedded": self.embed_missing()}
        known = {**self.hashes(f"code:{repo}/"), **self.hashes(f"md:{repo}:")}
        docs = checkout_documents(root, repo, known=known)
        present = {rel for rel, _ in _tracked(root) if wanted(rel) and (root / rel).is_file()}
        gone = [doc_id for doc_id in known if _doc_path(doc_id, repo) not in present]
        self.remove(gone)
        counts = self.add(docs)
        code_files = {rel for rel in present if not rel.endswith((".md", ".mdx", ".rst", ".txt"))}
        changed = [d for d in docs if d.kind == "code"]
        self._bump()
        if any(d.id not in known for d in changed) or not known:
            # A new file can resolve imports in files that did not change, so edges are rebuilt whole.
            self.con.execute("DELETE FROM rag_edges WHERE repo = ?", [repo])
            edges = import_edges(checkout_documents(root, repo, only=code_files))
        else:
            # Only the edited files' imports can have moved; edges into a deleted file go with it.
            stale = sorted({d.path for d in changed} | {_doc_path(g, repo) for g in gone})
            self.con.execute("DELETE FROM rag_edges WHERE repo = ? AND (list_contains(?, src) OR list_contains(?, dst))", [repo, stale, [_doc_path(g, repo) for g in gone]])
            edges = import_edges(changed, files=code_files)
        if edges:
            self.con.executemany("INSERT INTO rag_edges VALUES (?, ?, ?)", edges)
        if state:
            self.set_meta(f"checkout:{repo}", state)
        return {"repo": repo, "skipped": False, "removed": len(gone), "edges": len(edges), **counts}

    def sync_record(self, docstore: Any) -> dict[str, int]:
        """Bring in what the ingestion ``DocStore`` added, changed or deleted since the last sync (by its sequence number)."""
        from .docs import from_record

        last = int(self.meta("record_seq") or 0)
        upto = docstore.last_seq()  # read first: a document written during the sync is picked up next time
        changed = docstore.changed_since(last) if last else list(docstore.iter_documents())
        gone = [d.id for d in changed if getattr(d, "deleted", False) or getattr(d, "duplicate_of", None)]
        self.remove(gone)
        docs = [sd for sd in (from_record(d) for d in changed) if sd is not None]
        counts = self.add(docs)
        self.set_meta("record_seq", str(upto))
        return {"removed": len(gone), **counts}

    def sync_store(self, store: Any, root: Path | str | None = None, repo: str = "") -> dict[str, int]:
        """Index the prose an Orion graph store holds (postmortems, asset descriptions, constraint reasons), attached to its assets.

        With ``root`` (the platform's checkout), each chunk carries the repository-relative file it came from."""
        rows = store.con.execute("SELECT id, text, source, asset_ids FROM chunks").fetchall()
        docs = []
        for cid, text, source, assets in rows:
            path = source.split(" (")[0]
            if root is not None and Path(path).is_absolute():
                with contextlib.suppress(ValueError):
                    path = Path(path).relative_to(Path(root).resolve()).as_posix()
            docs.append(SourceDoc(id=f"graph:{cid}", text=text, kind="graph", repo=repo or "graph", path=path, title=source, asset_ids=list(assets or []), language="markdown"))
        held = [d for d in self.hashes("graph:") if d not in {x.id for x in docs}]
        self.remove(held)
        return self.add(docs)

    # ------------------------------------------------------------ reading
    def read(self, chunk_id: str) -> dict[str, Any]:
        """One chunk in full, or an error naming an id the index does not hold."""
        row = self.con.execute(
            "SELECT chunk_id, doc_id, kind, repo, path, title, url, date, symbol, start_line, end_line, text FROM rag_chunks WHERE chunk_id = ?",
            [chunk_id.removeprefix("chunk:")],
        ).fetchone()
        if row is None:
            return {"error": f"not recorded: no chunk with id {chunk_id}"}
        keys = ("chunk_id", "doc_id", "kind", "repo", "path", "title", "url", "date", "symbol", "start_line", "end_line", "text")
        return dict(zip(keys, row, strict=True))

    # ------------------------------------------------------------ search
    def _filters(self, scope: Scope) -> tuple[str, list[Any]]:
        where, args = [], []
        if scope.repos:
            where.append("list_contains(?, lower(c.repo))")
            args.append([r.lower() for r in scope.repos])
        if scope.kinds:
            where.append("list_contains(?, c.kind)")
            args.append([k.lower() for k in scope.kinds])
        if scope.since:
            where.append("(c.date IS NULL OR c.date >= ?)")
            args.append(scope.since)
        if scope.path_prefixes:
            where.append("(" + " OR ".join("starts_with(c.path, ?)" for _ in scope.path_prefixes) + ")")
            args.extend(scope.path_prefixes)
        return (" AND " + " AND ".join(where)) if where else "", args

    def scope_hops(self, scope: Scope) -> dict[str, tuple[int, str]]:
        """chunk id -> (hops, reason) for every chunk the scope reaches: seeds, their import neighbourhood and the graph's blast radius."""
        paths: dict[tuple[str, str], tuple[int, str]] = {}

        def put(repo: str, path: str, hops: int, reason: str) -> None:
            key = (repo, path)
            if key not in paths or paths[key][0] > hops:
                paths[key] = (hops, reason)

        stored = {r.lower(): r for (r,) in self.con.execute("SELECT DISTINCT repo FROM rag_docs WHERE repo IS NOT NULL").fetchall()}

        def fix(repo: str) -> str:  # 'MyRepo' names the repository the index stores as 'myrepo'
            return stored.get(repo.lower(), repo) if repo else repo

        seeds = [(fix(scope.repo_of(p)), p) for p in scope.paths]
        for repo, p in seeds:
            put(repo, p, 0, "changed file")
        for p, (h, why) in scope.path_hops.items():
            put(fix(scope.repo_of(p)), p, h, why)
        # Import edges: importers fade over two hops, a seed's own imports sit at one.
        frontier = list(seeds)
        for hop in (1, 2):
            nxt = []
            for repo, p in frontier:
                sql = "SELECT repo, src FROM rag_edges WHERE dst = ?" + (" AND repo = ?" if repo else "")
                for r, src in self.con.execute(sql, [p, repo] if repo else [p]).fetchall():
                    if (r, src) not in paths:
                        put(r, src, hop, f"imports {Path(p).name}" if hop == 1 else f"imports an importer of {Path(seeds[0][1]).name}")
                        nxt.append((r, src))
            frontier = nxt
        for repo, p in seeds:
            sql = "SELECT repo, dst FROM rag_edges WHERE src = ?" + (" AND repo = ?" if repo else "")
            for r, dst in self.con.execute(sql, [p, repo] if repo else [p]).fetchall():
                put(r, dst, 1, f"imported by {Path(p).name}")
        out: dict[str, tuple[int, str]] = {}

        def take(cid: str, hops: int, reason: str) -> None:
            if cid not in out or out[cid][0] > hops:
                out[cid] = (hops, reason)

        if paths:
            self.con.execute("CREATE OR REPLACE TEMP TABLE _sp (repo VARCHAR, path VARCHAR, hops INTEGER, reason VARCHAR)")
            self.con.executemany("INSERT INTO _sp VALUES (?, ?, ?, ?)", [(r, p, h, why) for (r, p), (h, why) in paths.items()])
            for cid, h, why in self.con.execute("SELECT c.chunk_id, s.hops, s.reason FROM rag_chunks c JOIN _sp s ON c.path = s.path AND (s.repo = '' OR c.repo = s.repo)").fetchall():
                take(cid, h, why)
            # Records that reference a file in scope (a PR or commit that touched it) sit one hop out.
            for cid, h, why in self.con.execute(
                "SELECT c.chunk_id, s.hops, s.reason FROM rag_chunks c, unnest(c.refs) AS r(ref) JOIN _sp s"
                " ON (s.repo <> '' AND r.ref = 'path:' || s.repo || '/' || s.path) OR (s.repo = '' AND ends_with(r.ref, '/' || s.path))"
            ).fetchall():
                take(cid, h + 1, f"history of a file in scope ({why})")
        assets = {a: (0, "asset in scope") for a in scope.assets}
        for a, (h, why) in scope.asset_hops.items():
            if a not in assets or assets[a][0] > h:
                assets[a] = (h, why)
        if assets:
            self.con.execute("CREATE OR REPLACE TEMP TABLE _sa (asset VARCHAR, hops INTEGER, reason VARCHAR)")
            self.con.executemany("INSERT INTO _sa VALUES (?, ?, ?)", [(a, h, why) for a, (h, why) in assets.items()])
            for cid, h, why in self.con.execute("SELECT c.chunk_id, s.hops, s.reason FROM rag_chunks c, unnest(c.asset_ids) AS x(asset) JOIN _sa s ON x.asset = s.asset").fetchall():
                take(cid, h, why)
        return out

    def _bm25(self, q: list[str], where: str, args: list[Any], within: list[str] | None, limit: int) -> dict[str, float]:
        if not q:
            return {}
        n, avg = self.con.execute("SELECT count(*), coalesce(avg(n_terms), 1) FROM rag_chunks").fetchone()
        if not n:
            return {}
        scoped = " AND list_contains(?, c.chunk_id)" if within is not None else ""
        sql = f"""
            WITH df AS (SELECT term, count(*) AS df FROM rag_postings WHERE list_contains(?, term) GROUP BY term)
            SELECT p.chunk_id, sum(ln(1 + (? - df.df + 0.5) / (df.df + 0.5)) * p.tf * ({K1} + 1)
                                   / (p.tf + {K1} * (1 - {B} + {B} * c.n_terms / ?))) AS s
            FROM rag_postings p JOIN df USING (term) JOIN rag_chunks c USING (chunk_id)
            WHERE TRUE {where}{scoped}
            GROUP BY p.chunk_id ORDER BY s DESC, p.chunk_id LIMIT ?"""
        params = [q, n, avg, *args, *([within] if within is not None else []), limit]
        return dict(self.con.execute(sql, params).fetchall())

    def _dense(self, qv: list[float], where: str, args: list[Any], within: list[str] | None, limit: int) -> dict[str, float]:
        scoped = " AND list_contains(?, c.chunk_id)" if within is not None else ""
        # A chunk scores its best vector: its own text or any of its views (a definition's signature and doc, a heading).
        sql = f"""
            SELECT c.chunk_id, max(list_cosine_similarity(v.vec, ?::FLOAT[])) AS s
            FROM rag_chunks c
            JOIN (SELECT chunk_id, text_hash FROM rag_chunks UNION ALL SELECT chunk_id, text_hash FROM rag_views) t ON t.chunk_id = c.chunk_id
            JOIN rag_vectors v ON v.text_hash = t.text_hash AND v.model = ?
            WHERE TRUE {where}{scoped}
            GROUP BY c.chunk_id ORDER BY s DESC, c.chunk_id LIMIT ?"""
        params = [qv, self.embedder_name, *args, *([within] if within is not None else []), limit]
        return {cid: s for cid, s in self.con.execute(sql, params).fetchall() if s is not None and s > 0}

    def search(self, query: str, k: int = 10, scope: Scope | None = None, mode: str = "hybrid", rerank: bool | None = None) -> list[Hit]:
        """The ``k`` best chunks for ``query``. ``mode`` is ``hybrid``, ``bm25`` or ``dense``; ``scope`` filters and seeds the graph boost.

        Results are cached in this process under the query, scope, mode and the index's
        generation, so a repeated question on an unchanged index costs one small lookup."""
        if mode not in ("hybrid", "bm25", "dense"):
            raise ValueError(f"mode must be hybrid, bm25 or dense, not {mode!r}")
        if k < 1:
            return []
        scope = scope or Scope()
        key = (query, k, scope.key(), mode, rerank, self.generation(), self.embedder_name, self.reranker_name)
        if key in self._cache:
            self._cache.move_to_end(key)
            return [Hit(**{**h.as_dict(), "signals": {**h.signals, "cached": True}}) for h in self._cache[key]]
        hits = self._search(query, k, scope, mode, rerank)
        self._cache[key] = hits
        if len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return hits

    def _query_vector(self, query: str) -> list[float]:
        if query not in self._qvec:
            self._qvec[query] = self.embedder.embed([query])[0]
            if len(self._qvec) > CACHE_SIZE:
                self._qvec.popitem(last=False)
        return self._qvec[query]

    def _search(self, query: str, k: int, scope: Scope, mode: str, rerank: bool | None) -> list[Hit]:
        where, args = self._filters(scope)
        q = sorted(set(terms(query)))
        hops = self.scope_hops(scope) if scope.seeded else {}
        within = sorted(hops) if hops else None
        lexical: dict[str, float] = {}
        dense: dict[str, float] = {}
        if mode in ("hybrid", "bm25"):
            lexical = self._bm25(q, where, args, None, CANDIDATES)
            if within:
                # Inside the scope, the seed files' own names count as query words: a chunk that uses the changed module ranks first.
                seed_terms = sorted({t for p in scope.paths for t in terms(Path(p).stem)} - set(q))
                lexical.update(self._bm25(q + seed_terms, where, args, within, CANDIDATES))
        if mode in ("hybrid", "dense"):
            # A question about a change is about the changed files too, so their paths join the text that is embedded.
            qv = self._query_vector(f"{query} {' '.join(scope.paths)}" if scope.paths else query)
            dense = self._dense(qv, where, args, None, CANDIDATES)
            if within:
                dense.update(self._dense(qv, where, args, within, CANDIDATES))
        lex_rank, den_rank = _rrf_ranks(lexical), _rrf_ranks(dense)
        cands = set(lexical) | set(dense)
        if scope.only_in_scope and scope.seeded:
            cands &= set(hops)  # a scope the index holds nothing of yields nothing, not the whole index
        if not cands:
            return []
        rows = {
            r[0]: r
            for r in self.con.execute(
                "SELECT chunk_id, doc_id, kind, repo, path, start_line, end_line, title, symbol, text, url, date FROM rag_chunks WHERE list_contains(?, chunk_id)",
                [sorted(cands)],
            ).fetchall()
        }
        named = {w.lower() for w in _identifiers(query)}
        scored: dict[str, float] = {}
        signals: dict[str, dict[str, Any]] = {}
        text_fused = {cid: sum(w / (RRF_K + r[cid]) for r, w in ((lex_rank, LEXICAL_WEIGHT), (den_rank, 1.0)) if cid in r) for cid in cands}
        # The graph is a third ranking: chunks in scope, nearest first, ties broken by the text ranking.
        graph_rank = {cid: i + 1 for i, cid in enumerate(sorted((c for c in cands if c in hops), key=lambda c: (hops[c][0], -text_fused[c], c)))}
        for cid in cands:
            fused = text_fused[cid] + (GRAPH_WEIGHT / (RRF_K + graph_rank[cid]) if cid in graph_rank else 0.0)
            sig: dict[str, Any] = {"bm25_rank": lex_rank.get(cid), "dense_rank": den_rank.get(cid)}
            mult = 1.0
            if cid in hops:
                sig["graph_hops"] = hops[cid][0]
                sig["graph_rank"] = graph_rank[cid]
            syms = {s.strip().split(".")[-1].lower() for s in (rows[cid][8] or "").split(",") if s.strip()}
            if named and syms & named:
                mult *= 1 + SYMBOL_BOOST
                sig["symbol_match"] = True
            sig["fused"] = round(fused, 6)
            scored[cid] = fused * mult
            signals[cid] = sig
        order = sorted(scored, key=lambda c: (-scored[c], c))
        use_rerank = self.reranker is not None if rerank is None else (rerank and self.reranker is not None)
        if use_rerank:
            top = order[:RERANK_DEPTH]
            ce = self.reranker.score(query, [f"{rows[c][7] or ''} {rows[c][8] or ''}\n{rows[c][9]}"[:2000] for c in top])
            for c, s in zip(top, ce, strict=True):
                p = 1 / (1 + math.exp(-max(-50.0, min(50.0, s))))
                signals[c]["rerank"] = round(p, 4)
                h = hops.get(c, (None, ""))[0]
                scored[c] = p * (1 + GRAPH_WEIGHT / (2 + h) if h is not None else 1) + 1.0  # reranked rows stay above the rest
            order = sorted(scored, key=lambda c: (-scored[c], c))
        picked: list[str] = []
        per_doc: Counter[str] = Counter()
        for cid in order:
            if per_doc[rows[cid][1]] < PER_DOC:
                picked.append(cid)
                per_doc[rows[cid][1]] += 1
            if len(picked) == k:
                break
        if len(picked) < k:
            picked += [c for c in order if c not in picked][: k - len(picked)]
        hits = []
        for cid in picked:
            r = rows[cid]
            sig = {key: v for key, v in signals[cid].items() if v is not None}
            hits.append(
                Hit(
                    chunk_id=cid, doc_id=r[1], kind=r[2], repo=r[3], path=r[4], start_line=r[5], end_line=r[6], title=r[7], symbol=r[8],
                    text=r[9], url=r[10], date=r[11], score=round(scored[cid], 6), signals=sig, why=_why(sig, hops.get(cid)), in_scope=cid in hops,
                )
            )  # fmt: skip
        return hits


def _identifiers(query: str) -> list[str]:
    """Words in a query that look like code names: ``snake_case``, ``camelCase``, ``Dotted.name`` or a backticked word."""
    import re

    out = re.findall(r"`([^`]+)`", query)
    out += [w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", query) if "_" in w or "." in w.strip(".") or re.search(r"[a-z][A-Z]", w)]
    return [w.split(".")[-1].strip("()") for w in out]


def _why(sig: dict[str, Any], scoped: tuple[int, str] | None) -> str:
    parts = []
    if "bm25_rank" in sig:
        parts.append(f"keyword #{sig['bm25_rank']}")
    if "dense_rank" in sig:
        parts.append(f"meaning #{sig['dense_rank']}")
    if scoped:
        parts.append(f"{scoped[1]} ({scoped[0]} hop{'s' if scoped[0] != 1 else ''})")
    if sig.get("symbol_match"):
        parts.append("names the symbol")
    return ", ".join(parts)


def _doc_path(doc_id: str, repo: str) -> str:
    if doc_id.startswith(f"code:{repo}/"):
        return doc_id[len(f"code:{repo}/") :]
    return doc_id[len(f"md:{repo}:") :]


def _tree_state(root: Path) -> str | None:
    """HEAD plus a digest of uncommitted changes, or None when the folder is not a git checkout."""
    try:
        head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=30, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1", "-z", "--untracked-files=no"], capture_output=True, timeout=60, check=True).stdout
        diff = subprocess.run(["git", "-C", str(root), "diff", "HEAD"], capture_output=True, timeout=60, check=True).stdout if dirty else b""
    except (OSError, subprocess.SubprocessError):
        return None
    return json.dumps([SCHEMA_VERSION, head, hashlib.sha256(dirty + diff).hexdigest()])


__all__ = ["Chunk", "Hit", "RagIndex", "default_path"]
