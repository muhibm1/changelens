# Recall spread: Held-out bugs, Sonnet 5.5 via claude-code, evidence-gated escalation (the same reviews, regated offline), 3 runs of 45 bugs

| metric | mean | min | max | runs |
| --- | --- | --- | --- | --- |
| detection recall | 0.741 | 0.733 | 0.756 | 0.733 / 0.733 / 0.756 |
| escalation recall | 0.148 | 0.133 | 0.156 | 0.156 / 0.156 / 0.133 |
| recall in file | 0.148 | 0.133 | 0.156 | 0.156 / 0.156 / 0.133 |
| inform only | 26.667 | 26 | 28 | 26 / 26 / 28 |
| missed | 11.667 | 11 | 12 | 12 / 12 / 11 |
| warns per case | 0.317 | 0.29 | 0.33 | 0.33 / 0.33 / 0.29 |
| other warns per case | 0.173 | 0.16 | 0.18 | 0.18 / 0.18 / 0.16 |
| errors | 0.0 | 0 | 0 | 0 / 0 / 0 |

9 of 45 cases changed outcome between runs.

| repo | commit | outcomes |
| --- | --- | --- |
| starlette | `097152be5a` | missed, missed, inform_only |
| starlette | `0aef1724cf` | missed, missed, inform_only |
| werkzeug | `9c92a5f904` | inform_only, missed, missed |
| echo | `c3887ebb13` | missed, missed, inform_only |
| echo | `124825ee62` | inform_only, inform_only, missed |
| ky | `bf9eca6aff` | missed, inform_only, missed |
| h3 | `080bf53d69` | missed, inform_only, missed |
| h3 | `61b395e31b` | inform_only, missed, inform_only |
| h3 | `631249d0f4` | caught, caught, inform_only |
