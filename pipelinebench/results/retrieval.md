# Retrieval eval at fee9ce8

42 hand-labelled queries, 3 runs per configuration (fresh index, shuffled ingestion order). Values are means over runs, with the min-max range when runs differ. Latency is per search on a warm process with the result cache empty; cold build is the full index build including embedding, for the first configuration that uses each embedder (later runs and configurations reuse that model's vectors, so theirs is index assembly only).

| config | recall@1 | recall@5 | recall@10 | MRR | faithfulness | tamper caught | p50 ms | p95 ms | cold build s |
|---|---|---|---|---|---|---|---|---|---|
| bm25 | 0.417 | 0.631 | 0.698 | 0.520 | 1.000 | 1.000 | 23 | 33 | 5.3 |
| dense-hash | 0.238 | 0.444 | 0.500 | 0.334 | 1.000 | 1.000 | 19 | 24 | 2.8 |
| hybrid-hash | 0.417 | 0.563 | 0.607 | 0.506 | 1.000 | 1.000 | 36 | 45 | 3.4 |
| hybrid-hash+graph | 0.393 | 0.647 | 0.722 | 0.530 | 1.000 | 1.000 | 38 | 103 | 3.3 |
| dense-bge-small | 0.508 | 0.734 | 0.806 | 0.645 | 1.000 | 1.000 | 50 | 66 | 220.7 |
| hybrid-bge-small | 0.464 | 0.778 | 0.837 | 0.613 | 1.000 | 1.000 | 73 | 99 | 3.1 |
| hybrid-bge-small+graph | 0.476 | 0.873 | 0.889 | 0.656 | 1.000 | 1.000 | 70 | 154 | 2.6 |
| hybrid-bge-small+graph+rerank | 0.452 | 0.706 | 0.813 | 0.611 | 1.000 | 1.000 | 2021 | 2241 | 2.7 |

## MRR by query family

| config | exact names | paraphrase | decisions and incidents | about a code change | about a data change | dev | heldout |
|---|---|---|---|---|---|---|---|
| bm25 | 1.000 | 0.345 | 0.750 | 0.520 | 0.000 | 0.495 | 0.545 |
| dense-hash | 1.000 | 0.185 | 0.389 | 0.037 | 0.031 | 0.290 | 0.378 |
| hybrid-hash | 1.000 | 0.338 | 0.764 | 0.413 | 0.031 | 0.496 | 0.517 |
| hybrid-hash+graph | 1.000 | 0.338 | 0.764 | 0.389 | 0.319 | 0.493 | 0.567 |
| dense-bge-small | 1.000 | 0.453 | 0.833 | 0.514 | 0.708 | 0.695 | 0.594 |
| hybrid-bge-small | 1.000 | 0.465 | 0.917 | 0.569 | 0.113 | 0.628 | 0.598 |
| hybrid-bge-small+graph | 1.000 | 0.465 | 0.917 | 0.569 | 0.562 | 0.683 | 0.629 |
| hybrid-bge-small+graph+rerank | 0.854 | 0.458 | 0.917 | 0.552 | 0.438 | 0.575 | 0.646 |

## Misses (no relevant hit in the top 10, first run)

- bm25: S2, S3, S5, S6, S7, S12, S16, G1, G2, G3, G4
- dense-hash: S1, S2, S3, S5, S7, S9, S11, S12, S15, S16, S18, D3, C1, C3, C5, C6, G1, G2, G3
- hybrid-hash: S2, S3, S5, S7, S9, S11, S12, S15, S16, S18, G1, G2, G3
- hybrid-hash+graph: S2, S3, S5, S7, S9, S11, S12, S15, S16, S18
- dense-bge-small: S2, S7, S8, S14, S17
- hybrid-bge-small: S2, S3, S7, G3
- hybrid-bge-small+graph: S2, S3, S7
- hybrid-bge-small+graph+rerank: S2, S3, S7, S9, S12, S14

## Reading

- **Default: hybrid with local bge-small embeddings and the graph** (`hybrid-bge-small+graph`): recall@5 0.873, recall@10 0.889, MRR 0.656. The previous round (PR #46) had 0.837, 0.853 and 0.625. Fusion weights are unchanged (lexical and graph weight 1.0, RRF k 60). RRF k from 10 to 60 and lexical weight from 0.5 to 1.0 were tried on the dev split only, and none beat the current setting there. The held-out split was run once, here.
- **The gain is on the dev split** (MRR 0.615 to 0.683). **Held-out MRR is 0.629, against 0.635 before**, so the changes do not generalise to it beyond noise; held-out recall@10 did not drop (the only misses are S2, S3 and S7, as before minus S9 and D5).
- **What changed: views.** Each chunk also carries extra embedded texts that are never shown: every definition's signature with the first sentence of its docstring or leading comment (or, for small neighbours merged into one chunk, the definition's own code), and each prose section's heading path. A chunk scores its best vector. This finds S9 (two findings are the same problem, merged with `_words` and `_jaccard`).
- **What changed: one chunk per prose section.** Short sibling sections are no longer merged; only a short title or preamble stays with its first subsection. This keeps ids stable and moved D5 (running the tests) from rank 11 to the top; decisions-and-incidents MRR is 0.917, from 0.722.
- **Still missed:** S2 (held-out, not examined), S3 (rate-limit wait: `_retry_after` is dense rank 5 but shares no word with the query, so fusion with BM25 leaves it below chunks that match both; its test and its caller `_post` are the top two hits) and S7 (receiver type: dense rank 14).
- **The hashed fallback lost on code-change questions** (hybrid-hash+graph code-change MRR 0.639 to 0.389): hashed vectors of short views match on shared tokens rather than meaning. It is the no-install fallback only.
- **The graph is what answers questions about a change**: data-change MRR is 0.113 with the changed path in the query text and 0.562 with the asset graph's blast radius as a ranking.
- **The cross-encoder stays off**: it loses exact names and costs about 2 s per search.
- **Citations survive re-indexing.** Prose chunk ids are the heading path (`md:orion:README.md#orion/running-the-tests`), not the section's position, so adding or editing a section leaves other sections' citations valid. The exceptions: a repeated heading is numbered by order (`~2`), so a new same-named section above it renumbers it, and renaming a parent heading renames its subsections. Diff chunks are named by the file from the patch's `+++` line. Code chunk ids stay line spans.
- **Latency:** p50 70 ms, p95 154 ms on this run; the machine was busier than the previous round's (BM25 alone went from 19 to 23 ms), so read these as tens of milliseconds. The views add about 60% more vectors to scan. A cold build with bge-small on CPU is about 3 to 4 minutes, once; later builds embed only changed chunks and views.
- **Faithfulness** is a check of the checker: the extractive answers score 1.0 and every answer with rotated citations was caught.
