> **The rag and orion rows of this run are void.** The run read a graph that had never been synced, so both conditions got no context (their cite recall equals `none`'s). They were re-run in `pb-sonnet-5-5-rag-orion.md`, and `pipelinebench run` now refuses an empty graph. The `none` and `repo` rows stand.

PipelineBench 0.1.0: 50 scenarios, 1 seed(s), ran 2026-10-18T16:12:41Z

| agent | condition | accuracy | unsafe | cite recall | name recall | approvers | fabricated | score |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| claude:claude-sonnet-5-5 | none | 0.48 | 0.16 | 0.46 | 0.56 | 0.46 | 0.00 | 0.39 |
| claude:claude-sonnet-5-5 | repo | 0.52 | 0.08 | 1.00 | 1.00 | 1.00 | 0.00 | 0.76 |
| claude:claude-sonnet-5-5 | rag | 0.46 | 0.14 | 0.46 | 0.56 | 0.46 | 0.00 | 0.40 |
| claude:claude-sonnet-5-5 | orion | 0.44 | 0.32 | 0.46 | 0.56 | 0.46 | 0.00 | 0.28 |

Accuracy by family:

| agent | condition | additive | additive_upstream | approval | backfill | deprecated | distractor | locked_add | locked_direct | locked_via_lineage | multi | partition | schedule | tier1_unlocked | unlocked_column |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| claude:claude-sonnet-5-5 | none | 0.00 | 0.00 | 0.33 | 0.25 | 0.50 | 1.00 | 0.00 | 1.00 | 1.00 | 0.50 | 0.75 | 0.20 | 0.00 | 0.00 |
| claude:claude-sonnet-5-5 | repo | 0.00 | 0.00 | 0.00 | 0.50 | 0.50 | 1.00 | 0.00 | 1.00 | 1.00 | 0.50 | 1.00 | 0.00 | 1.00 | 0.00 |
| claude:claude-sonnet-5-5 | rag | 0.00 | 0.00 | 0.33 | 0.12 | 0.50 | 1.00 | 0.00 | 1.00 | 1.00 | 0.50 | 0.75 | 0.20 | 0.00 | 0.00 |
| claude:claude-sonnet-5-5 | orion | 0.00 | 0.00 | 0.33 | 0.38 | 0.50 | 0.00 | 0.00 | 1.00 | 1.00 | 0.50 | 0.50 | 0.00 | 0.00 | 0.00 |
