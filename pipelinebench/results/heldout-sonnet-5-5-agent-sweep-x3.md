# Recall spread: Held-out bugs, Sonnet 5.5 via claude-code, --agent sweep, model escalation, 3 runs of 45 bugs

| metric | mean | min | max | runs |
| --- | --- | --- | --- | --- |
| detection recall | 0.763 | 0.711 | 0.8 | 0.778 / 0.8 / 0.711 |
| escalation recall | 0.637 | 0.622 | 0.644 | 0.644 / 0.622 / 0.644 |
| recall in file | 0.771 | 0.756 | 0.778 | 0.778 / 0.756 / 0.778 |
| inform only | 4.333 | 3 | 5 | 5 / 5 / 3 |
| missed | 6.0 | 5 | 7 | 5 / 6 / 7 |
| warns per case | 1.71 | 1.69 | 1.73 | 1.73 / 1.71 / 1.69 |
| other warns per case | 1.067 | 1.04 | 1.09 | 1.07 / 1.09 / 1.04 |
| errors | 0.0 | 0 | 0 | 0 / 0 / 0 |

11 of 45 cases changed outcome between runs.

| repo | commit | outcomes |
| --- | --- | --- |
| starlette | `94a22b865e` | caught, inform_only, caught |
| starlette | `0aef1724cf` | caught, missed, caught |
| gin | `32065bbd42` | inform_only, inform_only, missed |
| gin | `2ae8a25fc9` | in_file, in_file, caught |
| echo | `c3887ebb13` | inform_only, in_file, in_file |
| echo | `124825ee62` | caught, caught, missed |
| echo | `626f13e338` | inform_only, caught, inform_only |
| ky | `2b0a10071d` | caught, inform_only, caught |
| h3 | `080bf53d69` | in_file, caught, in_file |
| h3 | `61b395e31b` | in_file, in_file, caught |
| express | `18e5985b8a` | caught, caught, in_file |
