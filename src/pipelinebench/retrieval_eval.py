"""Retrieval eval: recall@k, MRR, citation faithfulness and latency for each retrieval configuration.

The corpus is this repository at a pinned commit (``git archive``), so the labels in
``pipelinebench/retrieval/queries.yaml`` keep pointing at the right lines as the code moves on.
Queries asked about a change run two ways: as plain RAG would see them (the changed paths
appended to the question as text) and graph-scoped (the changed paths as seeds). Each
configuration is run ``runs`` times on a fresh index, documents shuffled with a different seed
each time, and the spread is reported. The search cache never carries over between runs; a
model's vectors do (they are deterministic), so only the first run's build time is cold.

Citation faithfulness is measured on an extractive answer built from each query's top hits:
every citation must name a chunk the index holds and the sentence must be supported by it.
As a control, the same answers with their citations rotated to another query's chunks must
fail the check; the share that does is reported as ``tamper_caught``.
"""

from __future__ import annotations

import random
import re
import statistics
import subprocess
import tarfile
import tempfile
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import yaml

from orion.rag import RagIndex, Scope
from orion.rag.cite import check, extractive_answer
from orion.rag.code import checkout_documents, import_edges

ROOT = Path(__file__).resolve().parents[2]
QUERIES = ROOT / "pipelinebench" / "retrieval" / "queries.yaml"
KS = (1, 5, 10)


@dataclass
class Config:
    name: str
    mode: str = "hybrid"
    embedder: str = "hash"
    graph: bool = True
    reranker: str = "none"


DEFAULT_CONFIGS = [
    Config("bm25", mode="bm25", graph=False),
    Config("dense-hash", mode="dense", graph=False),
    Config("hybrid-hash", graph=False),
    Config("hybrid-hash+graph"),
]
MODEL_CONFIGS = [
    Config("dense-bge-small", mode="dense", embedder="st:BAAI/bge-small-en-v1.5", graph=False),
    Config("hybrid-bge-small", embedder="st:BAAI/bge-small-en-v1.5", graph=False),
    Config("hybrid-bge-small+graph", embedder="st:BAAI/bge-small-en-v1.5"),
    Config("hybrid-bge-small+graph+rerank", embedder="st:BAAI/bge-small-en-v1.5", reranker="ce:cross-encoder/ms-marco-MiniLM-L-6-v2"),
]


def load_queries(path: Path = QUERIES) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def materialize(commit: str, dest: Path, repo_root: Path = ROOT) -> Path:
    """The repository's tracked files at ``commit``, extracted under ``dest``."""
    data = subprocess.run(["git", "-C", str(repo_root), "archive", "--format=tar", commit], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=BytesIO(data)) as tar:
        tar.extractall(dest, filter="data")
    return dest


def _def_line(text: str, symbol: str) -> int | None:
    """The 1-based line that defines ``symbol`` (a def, class, heading or YAML key), found by pattern, not by Orion."""
    name = re.escape(symbol.split(".")[-1])
    for pat in (rf"^[ \t]*(?:async[ \t]+)?(?:def|class|function)[ \t]+{name}\b", rf"^#+[ \t]+{name}[ \t]*\r?$", rf"^[ \t]*{name}[ \t]*[:=]"):
        m = re.search(pat, text, re.M)
        if m:
            return text[: m.start()].count("\n") + 1
    return None


def relevant(hit: Any, label: dict[str, Any], files: dict[str, str]) -> bool:
    if hit.path != label["path"]:
        return False
    sym = label.get("symbol")
    if not sym:
        return True
    if sym in [s.strip().split(".")[-1] for s in (hit.symbol or "").split(",")] or (hit.kind != "code" and sym in [h.strip() for h in (hit.symbol or "").split(">")]):
        return True
    line = _def_line(files.get(label["path"], ""), sym)
    return line is not None and hit.start_line <= line <= hit.end_line


def _store_for(config_path: Path | None) -> tuple[Any, Path] | None:
    from orion.config import load_config
    from orion.store import Store
    from orion.sync import run_sync

    cfg = load_config(config_path, cwd=ROOT)
    manifest = cfg.root / "fixture" / "dbt_project" / "target" / "manifest.json"
    if not manifest.exists():
        return None
    store = Store(":memory:")
    run_sync(cfg, store)
    return store, cfg.root


_VECTORS: dict[str, list[tuple]] = {}  # embedder -> vector rows, reused across runs and configs: a model's vectors are deterministic, the ranking is what is measured


def build_index(corpus: Path, repo: str, cfg: Config, seed: int, store: Any | None, store_root: Path | None) -> tuple[RagIndex, float]:
    t0 = time.perf_counter()
    idx = RagIndex(":memory:", embedder=cfg.embedder, reranker=cfg.reranker)
    if _VECTORS.get(cfg.embedder):
        idx._bulk("rag_vectors", _VECTORS[cfg.embedder], "VARCHAR VARCHAR FLOAT[]")
    docs = checkout_documents(corpus, repo)
    random.Random(seed).shuffle(docs)
    idx.add(docs)
    edges = import_edges(docs)
    if edges:
        idx.con.executemany("INSERT INTO rag_edges VALUES (?, ?, ?)", edges)
    if store is not None:
        idx.sync_store(store, root=store_root, repo=repo)
    if not _VECTORS.get(cfg.embedder):
        _VECTORS[cfg.embedder] = idx.con.execute("SELECT text_hash, model, vec FROM rag_vectors").fetchall()
    return idx, time.perf_counter() - t0


def run_once(idx: RagIndex, queries: list[dict[str, Any]], cfg: Config, files: dict[str, str], repo: str, store: Any | None) -> dict[str, Any]:
    per: list[dict[str, Any]] = []
    answers: list[tuple[str, str, set[str]]] = []
    for q in queries:
        changed = q.get("changed") or []
        changed = [changed] if isinstance(changed, str) else list(changed)
        text = q["query"]
        scope = None
        if changed and cfg.graph:
            scope = Scope.from_change(store if q.get("graph") else None, changed, repo=repo)
        elif changed:
            text = f"{text} {' '.join(changed)}"
        t0 = time.perf_counter()
        hits = idx.search(text, k=max(KS), scope=scope, mode=cfg.mode)
        ms = (time.perf_counter() - t0) * 1000
        labels = q["relevant"]
        if not labels:
            raise ValueError(f"query {q['id']} has no relevant labels")
        first = next((i + 1 for i, h in enumerate(hits) if any(relevant(h, lab, files) for lab in labels)), None)
        rec = {}
        for k in KS:
            found = sum(1 for lab in labels if any(relevant(h, lab, files) for h in hits[:k]))
            rec[f"recall@{k}"] = found / len(labels)
        answer = extractive_answer(q["query"], hits)
        faith = check(answer, idx, retrieved={h.chunk_id for h in hits})
        answers.append((q["id"], answer, {h.chunk_id for h in hits}))
        per.append({"id": q["id"], "split": q.get("split", "dev"), "family": q["id"][0], "mrr": 1 / first if first else 0.0, **rec,
                    "faithfulness": faith["faithfulness"], "validity": faith["validity"], "ms": ms, "top": [h.chunk_id for h in hits[:3]]})  # fmt: skip
    # Control: rotate citations to another query's chunks; a sound checker flags those answers.
    caught = total = 0
    for i, (_, answer, _) in enumerate(answers):
        _, other, other_ids = answers[(i + len(answers) // 2) % len(answers)]
        own_ids = re.findall(r"\[chunk:([^\]]+)\]", answer)
        swap_ids = [x for x in re.findall(r"\[chunk:([^\]]+)\]", other) if x not in own_ids]
        if not own_ids or not swap_ids:
            continue
        tampered = answer
        for j, cid in enumerate(own_ids):
            tampered = tampered.replace(f"[chunk:{cid}]", f"[chunk:{swap_ids[j % len(swap_ids)]}]")
        res = check(tampered, idx, retrieved=answers[i][2])
        total += 1
        caught += bool(res["unsupported"] or res["invalid"])
    return {"queries": per, "tamper_caught": caught / total if total else None}


def _summary(rows: list[dict[str, Any]]) -> dict[str, float]:
    out = {}
    for key in ("recall@1", "recall@5", "recall@10", "mrr", "faithfulness"):
        vals = [r[key] for r in rows if r[key] is not None]
        out[key] = statistics.fmean(vals) if vals else 0.0
    lat = sorted(r["ms"] for r in rows)
    out["p50_ms"] = lat[len(lat) // 2] if lat else 0.0
    out["p95_ms"] = lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else 0.0
    return out


def evaluate(configs: list[Config], runs: int = 3, queries_path: Path = QUERIES, config_path: Path | None = None, split: str | None = None) -> dict[str, Any]:
    spec = load_queries(queries_path)
    queries = [q for q in spec["queries"] if split in (None, q.get("split", "dev"))]
    repo, commit = spec["corpus"]["repo"], spec["corpus"]["commit"]
    with tempfile.TemporaryDirectory() as tmp:
        corpus = materialize(commit, Path(tmp) / repo)
        files = {p.relative_to(corpus).as_posix(): p.read_text(errors="replace") for p in corpus.rglob("*") if p.is_file() and p.stat().st_size < 400_000}
        graph = _store_for(config_path) if any(q.get("graph") for q in queries) else None
        store, store_root = graph if graph else (None, None)
        results: dict[str, Any] = {"commit": commit, "runs": runs, "queries": len(queries), "graph_loaded": store is not None, "configs": {}}
        for cfg in configs:
            per_run = []
            for seed in range(1, runs + 1):
                idx, build_s = build_index(corpus, repo, cfg, seed, store, store_root)
                try:
                    once = run_once(idx, queries, cfg, files, repo, store)
                finally:
                    idx.close()
                summary = {"all": _summary(once["queries"])}
                for fam in sorted({r["family"] for r in once["queries"]}):
                    summary[fam] = _summary([r for r in once["queries"] if r["family"] == fam])
                for sp in ("dev", "heldout"):
                    rows = [r for r in once["queries"] if r["split"] == sp]
                    if rows:
                        summary[sp] = _summary(rows)
                per_run.append({"seed": seed, "build_s": build_s, "tamper_caught": once["tamper_caught"], "summary": summary, "queries": once["queries"]})
            results["configs"][cfg.name] = {"config": cfg.__dict__, "runs": per_run}
    return results


def spread(values: list[float]) -> str:
    if not values:
        return "-"
    lo, hi, mean = min(values), max(values), statistics.fmean(values)
    return f"{mean:.3f}" if hi - lo < 5e-4 else f"{mean:.3f} ({lo:.3f}-{hi:.3f})"


def report(results: dict[str, Any]) -> str:
    lines = [
        f"# Retrieval eval at {results['commit']}",
        "",
        *([] if results.get("graph_loaded") else ["**The fixture's data graph was not loaded (no dbt manifest), so the data-change queries ran without it.**", ""]),
        f"{results['queries']} hand-labelled queries, {results['runs']} runs per configuration (fresh index, shuffled ingestion order). "
        "Values are means over runs, with the min-max range when runs differ. Latency is per search on a warm process with the result cache "
        "empty; cold build is the full index build including embedding, for the first configuration that uses each embedder (later runs and configurations reuse that model's vectors, so theirs is index assembly only).",
        "",
        "| config | recall@1 | recall@5 | recall@10 | MRR | faithfulness | tamper caught | p50 ms | p95 ms | cold build s |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, data in results["configs"].items():
        runs = data["runs"]
        col = lambda key, part="all", runs=runs: spread([r["summary"][part][key] for r in runs if part in r["summary"]])  # noqa: E731
        tc = [r["tamper_caught"] for r in runs if r["tamper_caught"] is not None]
        lines.append(
            f"| {name} | {col('recall@1')} | {col('recall@5')} | {col('recall@10')} | {col('mrr')} | {col('faithfulness')} | {spread(tc)} | "
            f"{statistics.fmean(r['summary']['all']['p50_ms'] for r in runs):.0f} | {statistics.fmean(r['summary']['all']['p95_ms'] for r in runs):.0f} | "
            f"{runs[0]['build_s']:.1f} |"
        )
    fams = {"L": "exact names", "S": "paraphrase", "D": "decisions and incidents", "C": "about a code change", "G": "about a data change"}
    lines += ["", "## MRR by query family", "", "| config | " + " | ".join(fams.values()) + " | dev | heldout |", "|---|" + "---|" * (len(fams) + 2)]
    for name, data in results["configs"].items():
        runs = data["runs"]
        cells = [spread([r["summary"][f]["mrr"] for r in runs if f in r["summary"]]) for f in [*fams, "dev", "heldout"]]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += ["", "## Misses (no relevant hit in the top 10, first run)", ""]
    for name, data in results["configs"].items():
        missed = [q["id"] for q in data["runs"][0]["queries"] if q["recall@10"] == 0]
        lines.append(f"- {name}: {', '.join(missed) or 'none'}")
    return "\n".join(lines) + "\n"
