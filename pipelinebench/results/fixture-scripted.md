PipelineBench 0.1.0: 60 scenarios, 1 seed(s), ran 2026-10-18T21:53:55Z

| agent | condition | accuracy | unsafe | cite recall | name recall | approvers | fabricated | score |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| scripted | none | 0.37 | 0.43 | 0.45 | 0.52 | 0.45 | 0.00 | 0.42 |
| scripted | repo | 0.53 | 0.33 | 0.89 | 0.71 | 0.83 | 0.00 | 0.58 |
| scripted | rag | 0.53 | 0.32 | 0.85 | 0.52 | 0.72 | 0.00 | 0.56 |
| scripted | fathom | 1.00 | 0.00 | 1.00 | 0.93 | 1.00 | 0.00 | 0.99 |

Accuracy by family:

| agent | condition | additive | additive_upstream | approval | backfill | deprecated | distractor | locked_add | locked_direct | locked_via_lineage | multi | partition | schedule | tier1_unlocked | unlocked_column |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| scripted | none | 1.00 | 1.00 | 0.00 | 0.40 | 0.00 | 1.00 | 1.00 | 0.00 | 0.00 | 0.50 | 0.20 | 0.60 | 0.00 | 1.00 |
| scripted | repo | 1.00 | 0.50 | 1.00 | 0.40 | 0.50 | 1.00 | 0.00 | 1.00 | 0.00 | 0.75 | 0.60 | 0.60 | 0.00 | 0.67 |
| scripted | rag | 1.00 | 1.00 | 1.00 | 0.40 | 0.50 | 1.00 | 0.00 | 1.00 | 0.00 | 0.25 | 0.40 | 0.60 | 0.00 | 1.00 |
| scripted | fathom | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 | 1.00 |
