PipelineBench 0.1.0: 50 scenarios, 1 seed(s), ran 2026-10-18T16:21:25Z
claude:claude-sonnet-5-5: 359399 tokens in, 31749 out, 0 provider errors (each graded as proceed)

| agent | condition | accuracy | unsafe | cite recall | name recall | approvers | fabricated | score |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| claude:claude-sonnet-5-5 | rag | 0.42 | 0.18 | 0.93 | 0.82 | 0.82 | 0.00 | 0.58 |
| claude:claude-sonnet-5-5 | orion | 0.56 | 0.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.82 |

Accuracy by family:

| agent | condition | additive | additive_upstream | approval | backfill | deprecated | distractor | locked_add | locked_direct | locked_via_lineage | multi | partition | schedule | tier1_unlocked | unlocked_column |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| claude:claude-sonnet-5-5 | rag | 0.00 | 0.00 | 0.00 | 0.50 | 0.00 | 1.00 | 0.00 | 1.00 | 0.80 | 0.50 | 0.50 | 0.00 | 0.00 | 0.00 |
| claude:claude-sonnet-5-5 | orion | 0.00 | 0.00 | 1.00 | 0.50 | 1.00 | 0.00 | 0.00 | 1.00 | 1.00 | 0.50 | 0.75 | 0.00 | 1.00 | 0.00 |
