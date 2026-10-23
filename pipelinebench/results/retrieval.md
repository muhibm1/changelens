# Retrieval eval at fee9ce8

42 hand-labelled queries, 3 runs per configuration (fresh index, shuffled ingestion order). Values are means over runs, with the min-max range when runs differ. Latency is per search on a warm process with the result cache empty; cold build is the full index build including embedding, for the first configuration that uses each embedder (later runs and configurations reuse that model's vectors, so theirs is index assembly only).

| config | recall@1 | recall@5 | recall@10 | MRR | faithfulness | tamper caught | p50 ms | p95 ms | cold build s |
|---|---|---|---|---|---|---|---|---|---|
| bm25 | 0.417 | 0.607 | 0.675 | 0.520 | 1.000 | 1.000 | 21 | 28 | 3.9 |
| dense-hash | 0.214 | 0.369 | 0.464 | 0.300 | 1.000 | 1.000 | 11 | 17 | 2.5 |
| hybrid-hash | 0.357 | 0.532 | 0.615 | 0.464 | 1.000 | 1.000 | 27 | 35 | 2.0 |
| hybrid-hash+graph | 0.345 | 0.611 | 0.714 | 0.499 | 1.000 | 1.000 | 28 | 76 | 2.3 |
| dense-bge-small | 0.484 | 0.726 | 0.786 | 0.618 | 1.000 | 1.000 | 36 | 48 | 173.3 |
| hybrid-bge-small | 0.440 | 0.742 | 0.802 | 0.575 | 1.000 | 1.000 | 54 | 71 | 2.0 |
| hybrid-bge-small+graph | 0.452 | 0.837 | 0.853 | 0.625 | 1.000 | 1.000 | 64 | 131 | 2.3 |
| hybrid-bge-small+graph+rerank | 0.452 | 0.706 | 0.813 | 0.605 | 1.000 | 1.000 | 1821 | 2062 | 2.1 |

## MRR by query family

| config | exact names | paraphrase | decisions and incidents | about a code change | about a data change | dev | heldout |
|---|---|---|---|---|---|---|---|
| bm25 | 1.000 | 0.345 | 0.750 | 0.520 | 0.000 | 0.495 | 0.545 |
| dense-hash | 0.875 | 0.154 | 0.368 | 0.078 | 0.036 | 0.267 | 0.333 |
| hybrid-hash | 1.000 | 0.218 | 0.657 | 0.583 | 0.031 | 0.426 | 0.502 |
| hybrid-hash+graph | 1.000 | 0.218 | 0.657 | 0.639 | 0.319 | 0.431 | 0.567 |
| dense-bge-small | 1.000 | 0.469 | 0.625 | 0.486 | 0.708 | 0.641 | 0.594 |
| hybrid-bge-small | 1.000 | 0.468 | 0.722 | 0.497 | 0.100 | 0.558 | 0.591 |
| hybrid-bge-small+graph | 1.000 | 0.468 | 0.722 | 0.542 | 0.562 | 0.615 | 0.635 |
| hybrid-bge-small+graph+rerank | 0.854 | 0.458 | 0.917 | 0.524 | 0.425 | 0.573 | 0.638 |

## Misses (no relevant hit in the top 10, first run)

- bm25: S2, S3, S5, S6, S7, S12, S16, D5, G1, G2, G3, G4
- dense-hash: L2, S1, S2, S3, S4, S5, S7, S9, S11, S12, S15, S16, S18, D5, C1, C3, C6, G1, G2, G3
- hybrid-hash: S2, S3, S5, S7, S9, S11, S12, S15, S16, S18, G1, G2, G3
- hybrid-hash+graph: S2, S3, S5, S7, S9, S11, S12, S15, S16, S18
- dense-bge-small: S2, S7, S9, S14, D5, C4
- hybrid-bge-small: S2, S3, S7, S9, D5, G3
- hybrid-bge-small+graph: S2, S3, S7, S9, D5
- hybrid-bge-small+graph+rerank: S2, S3, S7, S9, S12, S14

## Reading

- **Default: hybrid with local bge-small embeddings and the graph** (`hybrid-bge-small+graph`). It has the best recall@5 (0.837), recall@10 (0.853) and MRR (0.625), and the best held-out MRR of the configurations without a reranker (0.635). Weights were chosen on the dev split only (lexical and graph weight 1.0 each; 0.3 to 2.0 were tried); the held-out split was run once, here.
- **The graph is what answers questions about a change.** For a data change, plain retrieval with the changed path in the query finds the right incident or constraint at MRR 0.100 (hybrid); with the asset graph's blast radius as a ranking it is 0.562. For code changes the import edges lift MRR from 0.497 to 0.542.
- **Local embeddings beat BM25 on paraphrase** (0.468 against 0.345 MRR) and BM25 keeps exact names perfect, which is why the default fuses both. The dependency-free hashed embedder is worse than BM25 alone and stays only as the no-install fallback.
- **The cross-encoder is not on by default.** It helps decisions and incidents (0.917) but loses exact names and costs about 1.8 s per search against 64 ms; `ORION_RAG_RERANKER=ce:<model>` turns it on.
- **Runs agree exactly.** Ranking is deterministic, so the three shuffled-ingestion runs give the same numbers and the table shows no min-max ranges.
- **Faithfulness here is a check of the checker.** The answers are extractive, so they score 1.0; every answer whose citations were rotated to another query's chunks was caught. Agent-written answers are checked the same way with `orion.rag.cite.check`.
- **Still missed by the default:** S2 (credential redaction phrased as "hide API keys"), S3 (rate-limit wait), S7 (receiver type), S9 (same-problem findings) and D5 (running the tests: the README heading is found at rank 11). These are paraphrases of short helpers whose names and docstrings share no words with the question.
- **Latency:** p50 64 ms and p95 131 ms per search on the warm in-process index (about 1,000 chunks) in this run; an earlier run of the same ranking on a quieter machine measured 42 ms and 98 ms, so read these as tens of milliseconds; a repeated search is about 1 ms from the cache, and refreshing an unchanged checkout is two git calls (about 15 ms). A cold build of this repository with bge-small on CPU is about 100 s, once; later builds re-embed only changed chunks.
