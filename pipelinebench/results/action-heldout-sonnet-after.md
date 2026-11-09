PipelineBench 0.1.0: 42 scenarios, 1 seed(s), ran 2026-11-09T05:53:10Z
claude-code:claude-sonnet-5-5: 3505647 tokens in, 92451 out, 0 provider errors (each graded as proceed)

| agent | condition | accuracy | unsafe | cite recall | name recall | approvers | fabricated | score |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| claude-code:claude-sonnet-5-5 | none | 0.33 | 0.10 | 0.50 | 0.52 | 0.50 | 0.00 | 0.43 |
| claude-code:claude-sonnet-5-5 | repo | 0.86 | 0.00 | 1.00 | 1.00 | 1.00 | 0.00 | 0.94 |
| claude-code:claude-sonnet-5-5 | rag | 0.64 | 0.07 | 0.88 | 0.71 | 0.81 | 0.00 | 0.74 |
| claude-code:claude-sonnet-5-5 | changelens | 1.00 | 0.00 | 0.98 | 1.00 | 1.00 | 0.00 | 0.99 |

Accuracy by family:

| agent | condition | additive | additive_upstream | approval | backfill | deprecated | distractor | locked_direct | locked_via_lineage | multi | partition | schedule | tier1_unlocked | unlocked_column |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| claude-code:claude-sonnet-5-5 | none | 1.00 | 1.00 | 0.33 | 0.12 | 0.50 | 1.00 | 0.00 | 0.00 | 0.33 | 0.33 | 0.50 | 1.00 | 0.25 |
| claude-code:claude-sonnet-5-5 | repo | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 0.20 | 1.00 | 1.00 | 1.00 | 1.00 | 0.50 |
| claude-code:claude-sonnet-5-5 | rag | 1.00 | 1.00 | 1.00 | 0.62 | 1.00 | 1.00 | 1.00 | 0.00 | 0.67 | 0.67 | 0.75 | 0.67 | 0.25 |
| claude-code:claude-sonnet-5-5 | changelens | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
