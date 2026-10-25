PipelineBench 0.1.0: 50 scenarios, 1 seed(s), ran 2026-10-18T16:33:21Z
claude:claude-haiku-5-5: 693690 tokens in, 122637 out, 0 provider errors (each graded as proceed)

| agent | condition | accuracy | unsafe | cite recall | name recall | approvers | fabricated | score |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| claude:claude-haiku-5-5 | none | 0.48 | 0.16 | 0.46 | 0.56 | 0.46 | 0.00 | 0.39 |
| claude:claude-haiku-5-5 | repo | 0.46 | 0.10 | 1.00 | 1.00 | 1.00 | 0.00 | 0.72 |
| claude:claude-haiku-5-5 | rag | 0.44 | 0.16 | 0.93 | 0.81 | 0.86 | 0.00 | 0.60 |
| claude:claude-haiku-5-5 | fathom | 0.58 | 0.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.83 |

Accuracy by family:

| agent | condition | additive | additive_upstream | approval | backfill | deprecated | distractor | locked_add | locked_direct | locked_via_lineage | multi | partition | schedule | tier1_unlocked | unlocked_column |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| claude:claude-haiku-5-5 | none | 0.00 | 0.00 | 0.33 | 0.25 | 1.00 | 1.00 | 0.00 | 1.00 | 1.00 | 0.50 | 0.50 | 0.20 | 0.00 | 0.00 |
| claude:claude-haiku-5-5 | repo | 0.00 | 0.00 | 0.33 | 0.50 | 0.50 | 0.00 | 0.00 | 1.00 | 1.00 | 0.50 | 0.50 | 0.00 | 0.00 | 0.00 |
| claude:claude-haiku-5-5 | rag | 0.00 | 0.00 | 0.00 | 0.50 | 0.00 | 0.00 | 0.00 | 1.00 | 1.00 | 0.50 | 0.50 | 0.00 | 1.00 | 0.00 |
| claude:claude-haiku-5-5 | fathom | 0.00 | 0.00 | 1.00 | 0.50 | 1.00 | 0.00 | 0.00 | 1.00 | 1.00 | 0.50 | 0.75 | 0.20 | 1.00 | 0.00 |
