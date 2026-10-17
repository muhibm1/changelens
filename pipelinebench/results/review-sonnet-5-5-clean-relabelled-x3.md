# Review eval: `anthropic:claude-sonnet-5-5`, 2 cases x 3, whole-file context on

| metric | value |
| --- | --- |
| recall (bugs raised at warn or block) | None |
| bugs raised only as inform | 0 |
| precision (caught bugs / (caught bugs + false alarms)) | 0.0 |
| extra warn/block findings on an already-caught bug | 0 |
| false alarms at warn/block | 1 |
| correct changes flagged | 1/6 |
| provider errors | 0 |
| tokens in / out | 7749 / 615 |

| case | run | caught | false alarms | uncertain | rejected |
| --- | --- | --- | --- | --- | --- |
| clean-sql-index | 0 | 0/0 | 0 | 0 | 0 |
| clean-ctx-helper-defined-above | 0 | 0/0 | 0 | 0 | 0 |
| clean-sql-index | 1 | 0/0 | 0 | 0 | 0 |
| clean-ctx-helper-defined-above | 1 | 0/0 | warn app/report.py:16 line() output format changed from raw cents to dollar string | 0 | 0 |
| clean-sql-index | 2 | 0/0 | 0 | 0 | 0 |
| clean-ctx-helper-defined-above | 2 | 0/0 | 0 | 0 | 0 |
