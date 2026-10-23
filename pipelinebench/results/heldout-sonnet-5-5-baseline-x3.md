# Recall spread: Held-out bugs, Sonnet 5.5 via claude-code, model escalation (baseline), 3 runs of 45 bugs

| metric | mean | min | max | runs |
| --- | --- | --- | --- | --- |
| detection recall | 0.741 | 0.733 | 0.756 | 0.733 / 0.733 / 0.756 |
| escalation recall | 0.615 | 0.578 | 0.644 | 0.644 / 0.622 / 0.578 |
| recall in file | 0.719 | 0.689 | 0.756 | 0.756 / 0.711 / 0.689 |
| inform only | 5.0 | 4 | 6 | 4 / 5 / 6 |
| missed | 7.667 | 7 | 8 | 7 / 8 / 8 |
| warns per case | 1.613 | 1.51 | 1.69 | 1.64 / 1.69 / 1.51 |
| other warns per case | 0.993 | 0.91 | 1.07 | 1.0 / 1.07 / 0.91 |
| errors | 0.0 | 0 | 0 | 0 / 0 / 0 |

8 of 45 cases changed outcome between runs.

| repo | commit | outcomes |
| --- | --- | --- |
| starlette | `0aef1724cf` | missed, missed, caught |
| werkzeug | `9c92a5f904` | caught, missed, missed |
| echo | `c3887ebb13` | in_file, missed, inform_only |
| echo | `124825ee62` | caught, caught, missed |
| ky | `bf9eca6aff` | missed, inform_only, missed |
| h3 | `080bf53d69` | in_file, caught, in_file |
| h3 | `61b395e31b` | caught, in_file, in_file |
| h3 | `631249d0f4` | caught, caught, inform_only |
