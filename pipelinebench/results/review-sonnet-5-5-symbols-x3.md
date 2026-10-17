# Review eval: `anthropic:claude-sonnet-5-5`, 11 cases x 3, whole-file context on

| metric | value |
| --- | --- |
| recall (bugs raised at warn or block) | 1.0 |
| bugs raised only as inform | 0 |
| precision (caught bugs / (caught bugs + false alarms)) | 0.75 |
| extra warn/block findings on an already-caught bug | 6 |
| false alarms at warn/block | 6 |
| correct changes flagged | 5/15 |
| provider errors | 0 |
| tokens in / out | 48099 / 6727 |

| case | run | caught | false alarms | uncertain | rejected |
| --- | --- | --- | --- | --- | --- |
| py-secret-in-code | 0 | 1/1 | 0 | 0 | 0 |
| clean-py-refactor | 0 | 0/0 | 0 | 0 | 0 |
| clean-sql-index | 0 | 0/0 | warn migrations/0043_orders_created_idx.sql:1 CREATE INDEX CONCURRENTLY may fail inside a transactional migration runner | 0 | 0 |
| clean-ctx-helper-defined-above | 0 | 0/0 | 0 | 0 | 0 |
| xf-py-kwarg-renamed | 0 | 1/1 | 0 | 0 | 0 |
| xf-py-cents-vs-dollars | 0 | 1/1 | 0 | 0 | 0 |
| xf-ts-status-value-renamed | 0 | 1/1 | 0 | 0 | 0 |
| clean-xf-py-rename-with-caller | 0 | 0/0 | 0 | 0 | 0 |
| hidden-py-renamed-fn-still-called | 0 | 1/1 | 0 | 0 | 0 |
| hidden-py-kwarg-renamed-still-called | 0 | 1/1 | 0 | 0 | 0 |
| clean-py-renamed-fn-every-caller-updated | 0 | 0/0 | 0 | 0 | 0 |
| py-secret-in-code | 1 | 1/1 | 0 | 0 | 0 |
| clean-py-refactor | 1 | 0/0 | 0 | 0 | 0 |
| clean-sql-index | 1 | 0/0 | warn migrations/0043_orders_created_idx.sql:1 CREATE INDEX CONCURRENTLY may fail inside a transactional migration runner | 0 | 0 |
| clean-ctx-helper-defined-above | 1 | 0/0 | warn app/report.py:15 fmt_money mishandles negative cents | 0 | 0 |
| xf-py-kwarg-renamed | 1 | 1/1 | 0 | 0 | 0 |
| xf-py-cents-vs-dollars | 1 | 1/1 | warn app/prices.py:1 Price unit changed from dollars to cents without migrating the table source | 0 | 0 |
| xf-ts-status-value-renamed | 1 | 1/1 | 0 | 0 | 0 |
| clean-xf-py-rename-with-caller | 1 | 0/0 | 0 | 0 | 0 |
| hidden-py-renamed-fn-still-called | 1 | 1/1 | 0 | 0 | 0 |
| hidden-py-kwarg-renamed-still-called | 1 | 1/1 | 0 | 0 | 0 |
| clean-py-renamed-fn-every-caller-updated | 1 | 0/0 | 0 | 0 | 0 |
| py-secret-in-code | 2 | 1/1 | 0 | 0 | 0 |
| clean-py-refactor | 2 | 0/0 | 0 | 0 | 0 |
| clean-sql-index | 2 | 0/0 | warn migrations/0043_orders_created_idx.sql:1 CREATE INDEX CONCURRENTLY may fail inside a transactional migration runner, and a failed run leaves an invalid index | 0 | 0 |
| clean-ctx-helper-defined-above | 2 | 0/0 | warn app/report.py:15 fmt_money mishandles negative cents | 0 | 0 |
| xf-py-kwarg-renamed | 2 | 1/1 | 0 | 0 | 0 |
| xf-py-cents-vs-dollars | 2 | 1/1 | 0 | 0 | 0 |
| xf-ts-status-value-renamed | 2 | 1/1 | 0 | 0 | 0 |
| clean-xf-py-rename-with-caller | 2 | 0/0 | 0 | 0 | 0 |
| hidden-py-renamed-fn-still-called | 2 | 1/1 | 0 | 0 | 0 |
| hidden-py-kwarg-renamed-still-called | 2 | 1/1 | 0 | 0 | 0 |
| clean-py-renamed-fn-every-caller-updated | 2 | 0/0 | 0 | 0 | 0 |
