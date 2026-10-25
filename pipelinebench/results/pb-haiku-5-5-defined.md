PipelineBench 0.1.0: 50 scenarios, 1 seed(s), ran 2026-10-18T16:45:54Z
claude:claude-haiku-5-5: 713290 tokens in, 117419 out, 0 provider errors (each graded as proceed)

| agent | condition | accuracy | unsafe | cite recall | name recall | approvers | fabricated | score |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| claude:claude-haiku-5-5 | none | 0.36 | 0.08 | 0.46 | 0.56 | 0.46 | 0.00 | 0.39 |
| claude:claude-haiku-5-5 | repo | 0.52 | 0.10 | 1.00 | 1.00 | 1.00 | 0.00 | 0.75 |
| claude:claude-haiku-5-5 | rag | 0.44 | 0.18 | 0.93 | 0.84 | 0.86 | 0.00 | 0.60 |
| claude:claude-haiku-5-5 | fathom | 0.46 | 0.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.78 |

Accuracy by family:

| agent | condition | additive | additive_upstream | approval | backfill | deprecated | distractor | locked_add | locked_direct | locked_via_lineage | multi | partition | schedule | tier1_unlocked | unlocked_column |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| claude:claude-haiku-5-5 | none | 0.00 | 0.00 | 0.67 | 0.00 | 0.50 | 1.00 | 0.00 | 0.62 | 0.80 | 0.50 | 0.25 | 0.20 | 1.00 | 0.00 |
| claude:claude-haiku-5-5 | repo | 0.00 | 0.00 | 0.00 | 0.50 | 1.00 | 1.00 | 0.00 | 1.00 | 1.00 | 0.50 | 0.75 | 0.00 | 1.00 | 0.00 |
| claude:claude-haiku-5-5 | rag | 0.00 | 1.00 | 0.00 | 0.50 | 0.00 | 1.00 | 0.00 | 1.00 | 0.60 | 0.50 | 0.50 | 0.00 | 1.00 | 0.00 |
| claude:claude-haiku-5-5 | fathom | 0.00 | 0.00 | 0.00 | 0.50 | 0.00 | 0.00 | 0.00 | 1.00 | 1.00 | 0.50 | 0.75 | 0.00 | 1.00 | 0.00 |
