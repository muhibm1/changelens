"""Graph-scoped retrieval over the prose Fathom holds.

The corpus is text humans wrote and committed: postmortem bodies, model and
column descriptions, constraint reasons, commit subjects. Each chunk is
attached to graph assets; a question is answered from chunks in scope first,
and only widens to the whole corpus when the scoped set is thin. Ranking is
lexical (BM25-style) in this slice; an embedding index can replace the ranker
behind the same ``chunks`` table.

Three rankers, chosen in ``fathom.yaml`` under ``retrieval.ranker``:

* ``bm25`` (default): lexical, no model, no vectors.
* ``hybrid``: BM25 fused with cosine over dense vectors by reciprocal rank;
  robust to wording that the lexical index misses.
* ``embedding``: cosine over dense vectors only.

Vectors come from an ``Embedder``. The built-in one hashes tokens and
character trigrams into a fixed-width vector, so it needs no download and is
deterministic; set ``retrieval.model`` to a sentence-transformers model name
to use real embeddings (the ``embed`` extra). Vectors are cached in the
``chunk_vectors`` table per model name and rebuilt when the chunks are.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Any, Protocol

from .store import Store

TOKEN_RE = re.compile(r"[a-z0-9_]+")
STOP = set(["the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "by", "with", "is", "are", "was", "were", "be", "been", "it", "this", "that", "from", "as", "at", "which", "who", "what", "why", "how", "does", "do", "did"])


def tokens(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOP]


def rebuild_chunks(store: Store) -> int:
    """Derive the chunks table from what the store already holds."""
    store.con.execute("DELETE FROM chunks")
    store.con.execute("DELETE FROM chunk_vectors")
    rows: list[tuple[str, str, str, list[str]]] = []
    for ev in store.con.execute("SELECT id, kind, source, source_ref, assets, payload FROM events WHERE kind = 'postmortem_filed'").fetchall():
        eid, _kind, source, ref, assets, payload = ev
        import json

        p = json.loads(payload)
        a = json.loads(assets)
        body = p.get("body") or ""
        title = p.get("title") or ref
        for i, section in enumerate(re.split(r"\n(?=## )", body)):
            section = section.strip()
            if section:
                rows.append((f"{eid}#s{i}", f"{title}\n\n{section}", f"{source} ({ref})", a))
    for n in store.find_nodes():
        if n["kind"] == "column":
            continue
        desc = (n["properties"].get("description") or "").strip()
        if desc:
            rows.append((f"{n['id']}#description", f"{n['name']}: {desc}", n["properties"].get("path") or n["id"], [n["id"]]))
    for cid, text, source, assets in rows:
        store.con.execute(
            "INSERT INTO chunks (id, text, source, asset_ids, tokens) VALUES (?, ?, ?, ?, ?)",
            [cid, text, source, assets, " ".join(tokens(text))],
        )
    return len(rows)


def add_constraint_chunks(store: Store, constraints, resolve) -> int:
    n = 0
    for c in constraints:
        assets = [resolve(a) for a in c.scope.assets]
        text = f"{c.id} ({c.rule.kind}, {c.severity}): {c.reason}" + (f" Source: {c.source}." if c.source else "") + (f" Approvers: {', '.join(c.approvers)}." if c.approvers else "")
        store.con.execute(
            "INSERT OR REPLACE INTO chunks (id, text, source, asset_ids, tokens) VALUES (?, ?, ?, ?, ?)",
            [f"constraint:{c.id}", text, c.path or c.id, assets, " ".join(tokens(text))],
        )
        n += 1
    return n


def _bm25(query: list[str], docs: list[tuple[str, list[str]]], k1: float = 1.5, b: float = 0.75) -> dict[str, float]:
    n = len(docs)
    if n == 0:
        return {}
    avgdl = sum(len(t) for _, t in docs) / n
    df: Counter[str] = Counter()
    for _, t in docs:
        for term in set(t):
            df[term] += 1
    scores: dict[str, float] = {}
    for cid, t in docs:
        tf = Counter(t)
        s = 0.0
        for q in query:
            if q not in tf:
                continue
            idf = math.log(1 + (n - df[q] + 0.5) / (df[q] + 0.5))
            s += idf * (tf[q] * (k1 + 1)) / (tf[q] + k1 * (1 - b + b * len(t) / avgdl))
        if s > 0:
            scores[cid] = s
    return scores


# ---------------------------------------------------------------- embedders
class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    """Deterministic, dependency-free vectors: hashed tokens and character trigrams, L2-normalised."""

    def __init__(self, dims: int = 512):
        self.dims = dims
        self.name = f"hash-{dims}"

    def _features(self, text: str) -> list[str]:
        toks = tokens(text)
        feats = list(toks)
        for t in toks:
            padded = f"#{t}#"
            feats += [padded[i : i + 3] for i in range(len(padded) - 2)]
        return feats

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            v = [0.0] * self.dims
            for f in self._features(text):
                h = int(hashlib.blake2b(f.encode(), digest_size=8).hexdigest(), 16)
                v[h % self.dims] += 1.0 if (h >> 63) else -1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


class SentenceTransformerEmbedder:
    """Real embeddings via sentence-transformers; optional (``pip install fathom[embed]``)."""

    def __init__(self, model: str):
        from sentence_transformers import SentenceTransformer  # type: ignore[import-not-found]

        self.name = f"st:{model}"
        self._m = SentenceTransformer(model)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self._m.encode(texts, normalize_embeddings=True, batch_size=64)]


class OllamaEmbedder:
    """Embeddings from a local Ollama server (``ollama:<model>``), over HTTP with the standard library."""

    def __init__(self, model: str, url: str | None = None):
        import os

        self.name = f"ollama:{model}"
        self.model = model
        host = url or os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
        self.url = (host if "://" in host else f"http://{host}").rstrip("/")  # Ollama's own OLLAMA_HOST is host:port

    def embed(self, texts: list[str]) -> list[list[float]]:
        import json
        import urllib.request

        out: list[list[float]] = []
        for i in range(0, len(texts), 32):
            body = json.dumps({"model": self.model, "input": texts[i : i + 32]}).encode()
            req = urllib.request.Request(f"{self.url}/api/embed", data=body, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                for v in json.loads(resp.read())["embeddings"]:
                    norm = math.sqrt(sum(x * x for x in v)) or 1.0
                    out.append([x / norm for x in v])
        return out


def make_embedder(model: str | None) -> Embedder:
    """``None`` or ``hash`` for the built-in hashed vectors, ``ollama:<model>`` for a local Ollama, ``st:<model>`` or a bare name for sentence-transformers."""
    if not model or model == "hash":
        return HashEmbedder()
    if model.startswith("hash-") and model[5:].isdigit() and int(model[5:]) > 0:
        return HashEmbedder(int(model[5:]))
    if model.startswith("ollama:"):
        return OllamaEmbedder(model.split(":", 1)[1])
    return SentenceTransformerEmbedder(model.removeprefix("st:"))


def vectors_for(store: Store, embedder: Embedder, rows: list[tuple]) -> dict[str, list[float]]:
    """Vectors for the given chunk rows, computed once per model and cached in ``chunk_vectors``."""
    have = {cid: list(vec) for cid, vec in store.con.execute("SELECT chunk_id, vec FROM chunk_vectors WHERE model = ?", [embedder.name]).fetchall()}
    missing = [r for r in rows if r[0] not in have]
    if missing:
        vecs = embedder.embed([r[1] for r in missing])
        for r, v in zip(missing, vecs, strict=True):
            store.con.execute("INSERT OR REPLACE INTO chunk_vectors (chunk_id, model, vec) VALUES (?, ?, ?)", [r[0], embedder.name, v])
            have[r[0]] = v
    return have


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _rrf(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    fused: dict[str, float] = {}
    for ranking in rankings:
        for i, cid in enumerate(ranking):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (k + i + 1)
    return fused


def rank(store: Store, question: str, pool: list[tuple], ranker: str = "bm25", embedder: Embedder | None = None) -> dict[str, float]:
    """Score chunk ids in ``pool`` for ``question`` with the chosen ranker; absent ids scored zero."""
    lexical = _bm25(tokens(question), [(r[0], r[4].split()) for r in pool])
    if ranker == "bm25":
        return lexical
    embedder = embedder or HashEmbedder()
    vecs = vectors_for(store, embedder, pool)
    qv = embedder.embed([question])[0]
    dense = {r[0]: _cosine(qv, vecs[r[0]]) for r in pool}
    dense = {k: v for k, v in dense.items() if v > 0}
    if ranker == "embedding":
        return dense
    lex_rank = [cid for cid, _ in sorted(lexical.items(), key=lambda kv: kv[1], reverse=True)]
    den_rank = [cid for cid, _ in sorted(dense.items(), key=lambda kv: kv[1], reverse=True)]
    return _rrf([lex_rank, den_rank])


def ask(store: Store, question: str, scope: list[str], limit: int = 5, ranker: str = "bm25", embedder: Embedder | None = None) -> dict[str, Any]:
    all_rows = store.con.execute("SELECT id, text, source, asset_ids, tokens FROM chunks").fetchall()
    if not all_rows:
        return {"answer": "not recorded", "chunks": [], "scope": scope, "note": "the retrieval index is empty; run `fathom sync`"}
    scope_set = set(scope)
    if scope_set:
        expanded = set(scope_set)
        for s in scope_set:
            expanded |= set(store.walk([s], "downstream", max_hops=3, always_include_tier=None))
            expanded |= set(store.walk([s], "upstream", max_hops=1, always_include_tier=None))
        scoped = [r for r in all_rows if set(r[3]) & expanded]
    else:
        scoped = []
    widened = False
    pool = scoped
    if len(pool) < 3:
        pool = all_rows
        widened = bool(scope_set)
    scores = rank(store, question, pool, ranker, embedder)
    ranked = sorted(pool, key=lambda r: scores.get(r[0], 0), reverse=True)
    hits = [r for r in ranked if scores.get(r[0], 0) > 0][:limit]
    chunks = [{"chunk": r[0], "score": round(scores[r[0]], 4), "source": r[2], "assets": r[3], "text": r[1][:800]} for r in hits]
    if not chunks:
        return {"answer": "not recorded", "chunks": [], "scope": sorted(scope_set), "widened": widened, "ranker": ranker}
    sentences = []
    for c in chunks[:3]:
        body = c["text"].split("\n", 1)[1] if "\n" in c["text"] else c["text"]
        body = re.sub(r"^#+ .*$", "", body, flags=re.M).replace("\n", " ").strip()
        first = re.split(r"(?<=[.!?])\s+", body) or [c["text"]]
        pick = next((s for s in first if len(s) > 30), first[0] if first and first[0] else c["text"][:200])
        sentences.append(f"{pick.strip()} [{c['chunk']}]")
    return {"answer": " ".join(sentences), "chunks": chunks, "scope": sorted(scope_set), "widened": widened, "ranker": ranker}
