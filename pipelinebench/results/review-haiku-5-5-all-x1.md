# Review eval: `anthropic:claude-haiku-5-5`, 44 cases x 1, whole-file context on

| metric | value |
| --- | --- |
| recall (bugs raised at warn or block) | 1.0 |
| bugs raised only as inform | 0 |
| precision (caught bugs / (caught bugs + false alarms)) | 1.0 |
| extra warn/block findings on an already-caught bug | 16 |
| false alarms at warn/block | 0 |
| correct changes flagged | 0/12 |
| provider errors | 0 |
| tokens in / out | 68946 / 16463 |

| case | run | caught | false alarms | uncertain | rejected |
| --- | --- | --- | --- | --- | --- |
| py-sql-injection | 0 | 1/1 | 0 | 0 | 0 |
| py-shell-injection | 0 | 1/1 | 0 | 0 | 0 |
| py-off-by-one-total | 0 | 1/1 | 0 | 0 | 0 |
| py-mutable-default | 0 | 1/1 | 0 | 0 | 0 |
| py-swallowed-exception | 0 | 1/1 | 0 | 0 | 0 |
| py-missing-await | 0 | 1/1 | 0 | 0 | 0 |
| py-identity-compare | 0 | 1/1 | 0 | 0 | 0 |
| py-timezone-naive | 0 | 1/1 | 0 | 0 | 0 |
| py-secret-in-code | 0 | 1/1 | 0 | 0 | 0 |
| py-auth-check-removed | 0 | 1/1 | 0 | 0 | 0 |
| ts-xss-innerhtml | 0 | 1/1 | 0 | 0 | 0 |
| ts-unawaited-save | 0 | 1/1 | 0 | 0 | 0 |
| sql-migration-not-null | 0 | 1/1 | 0 | 0 | 0 |
| go-unchecked-error | 0 | 1/1 | 0 | 0 | 0 |
| dbt-percent-as-amount | 0 | 1/1 | 0 | 0 | 0 |
| dbt-drop-locked-column | 0 | 1/1 | 0 | 0 | 0 |
| dbt-left-to-inner-join | 0 | 1/1 | 0 | 0 | 0 |
| dbt-revenue-filter-widened | 0 | 1/1 | 0 | 0 | 0 |
| airflow-retries-zeroed | 0 | 1/1 | 0 | 0 | 0 |
| clean-py-refactor | 0 | 0/0 | 0 | 0 | 0 |
| clean-py-docstring-and-types | 0 | 0/0 | 0 | 0 | 0 |
| clean-py-test-added | 0 | 0/0 | 0 | 0 | 0 |
| clean-ts-rename | 0 | 0/0 | 0 | 0 | 0 |
| clean-dbt-comment | 0 | 0/0 | 0 | 0 | 0 |
| clean-sql-index | 0 | 0/0 | 0 | 0 | 0 |
| ctx-swapped-transfer-args | 0 | 1/1 | 0 | 0 | 0 |
| ctx-units-mismatch | 0 | 1/1 | 0 | 0 | 0 |
| ctx-lock-bypassed | 0 | 1/1 | 0 | 0 | 0 |
| ctx-timeout-milliseconds | 0 | 1/1 | 0 | 0 | 0 |
| ctx-null-guard-removed | 0 | 1/1 | 0 | 0 | 0 |
| clean-ctx-helper-defined-above | 0 | 0/0 | 0 | 0 | 0 |
| clean-ctx-lock-held-by-caller | 0 | 0/0 | 0 | 0 | 0 |
| clean-ctx-validated-earlier | 0 | 0/0 | 0 | 0 | 0 |
| xf-py-kwarg-renamed | 0 | 1/1 | 0 | 0 | 0 |
| xf-py-cents-vs-dollars | 0 | 1/1 | 0 | 0 | 0 |
| xf-ts-status-value-renamed | 0 | 1/1 | 0 | 0 | 0 |
| xf-dbt-renamed-column-still-read | 0 | 1/1 | 0 | 0 | 0 |
| clean-xf-py-rename-with-caller | 0 | 0/0 | 0 | 0 | 0 |
| clean-xf-dbt-rename-with-consumer | 0 | 0/0 | 0 | 0 | 0 |
| hidden-dbt-column-dropped | 0 | 1/1 | 0 | 0 | 0 |
| hidden-dbt-column-renamed | 0 | 1/1 | 0 | 0 | 0 |
| hidden-py-renamed-fn-still-called | 0 | 1/1 | 0 | 0 | 0 |
| hidden-py-kwarg-renamed-still-called | 0 | 1/1 | 0 | 0 | 0 |
| clean-py-renamed-fn-every-caller-updated | 0 | 0/0 | 0 | 0 | 0 |
