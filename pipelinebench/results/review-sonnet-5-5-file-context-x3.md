# Review eval: `anthropic:claude-sonnet-5-5`, 33 cases x 3, whole-file context on

| metric | value |
| --- | --- |
| recall (bugs raised at warn or block) | 1.0 |
| bugs raised only as inform | 0 |
| precision (caught bugs / (caught bugs + false alarms)) | 0.935 |
| extra warn/block findings on an already-caught bug | 31 |
| false alarms at warn/block | 5 |
| correct changes flagged | 5/27 |
| provider errors | 0 |
| tokens in / out | 130737 / 27748 |

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
| clean-sql-index | 0 | 0/0 | warn migrations/0043_orders_created_idx.sql:1 CREATE INDEX CONCURRENTLY may fail inside a transactional migration runner | 0 | 0 |
| ctx-swapped-transfer-args | 0 | 1/1 | 0 | 0 | 0 |
| ctx-units-mismatch | 0 | 1/1 | 0 | 0 | 0 |
| ctx-lock-bypassed | 0 | 1/1 | 0 | 0 | 0 |
| ctx-timeout-milliseconds | 0 | 1/1 | 0 | 0 | 0 |
| ctx-null-guard-removed | 0 | 1/1 | 0 | 0 | 0 |
| clean-ctx-helper-defined-above | 0 | 0/0 | warn app/report.py:15 Negative amounts render incorrectly through fmt_money | 0 | 0 |
| clean-ctx-lock-held-by-caller | 0 | 0/0 | 0 | 0 | 0 |
| clean-ctx-validated-earlier | 0 | 0/0 | 0 | 0 | 0 |
| py-sql-injection | 1 | 1/1 | 0 | 0 | 0 |
| py-shell-injection | 1 | 1/1 | 0 | 0 | 0 |
| py-off-by-one-total | 1 | 1/1 | 0 | 0 | 0 |
| py-mutable-default | 1 | 1/1 | 0 | 0 | 0 |
| py-swallowed-exception | 1 | 1/1 | 0 | 0 | 0 |
| py-missing-await | 1 | 1/1 | 0 | 0 | 0 |
| py-identity-compare | 1 | 1/1 | 0 | 0 | 0 |
| py-timezone-naive | 1 | 1/1 | 0 | 0 | 0 |
| py-secret-in-code | 1 | 1/1 | 0 | 0 | 0 |
| py-auth-check-removed | 1 | 1/1 | 0 | 0 | 0 |
| ts-xss-innerhtml | 1 | 1/1 | 0 | 0 | 0 |
| ts-unawaited-save | 1 | 1/1 | 0 | 0 | 0 |
| sql-migration-not-null | 1 | 1/1 | 0 | 0 | 0 |
| go-unchecked-error | 1 | 1/1 | 0 | 0 | 0 |
| dbt-percent-as-amount | 1 | 1/1 | 0 | 0 | 0 |
| dbt-drop-locked-column | 1 | 1/1 | 0 | 0 | 0 |
| dbt-left-to-inner-join | 1 | 1/1 | 0 | 0 | 0 |
| dbt-revenue-filter-widened | 1 | 1/1 | 0 | 0 | 0 |
| airflow-retries-zeroed | 1 | 1/1 | 0 | 0 | 0 |
| clean-py-refactor | 1 | 0/0 | 0 | 0 | 0 |
| clean-py-docstring-and-types | 1 | 0/0 | 0 | 0 | 0 |
| clean-py-test-added | 1 | 0/0 | 0 | 0 | 0 |
| clean-ts-rename | 1 | 0/0 | 0 | 0 | 0 |
| clean-dbt-comment | 1 | 0/0 | 0 | 0 | 0 |
| clean-sql-index | 1 | 0/0 | warn migrations/0043_orders_created_idx.sql:1 CREATE INDEX CONCURRENTLY may fail inside a migration transaction and can leave an invalid index | 0 | 0 |
| ctx-swapped-transfer-args | 1 | 1/1 | 0 | 0 | 0 |
| ctx-units-mismatch | 1 | 1/1 | 0 | 0 | 0 |
| ctx-lock-bypassed | 1 | 1/1 | 0 | 0 | 0 |
| ctx-timeout-milliseconds | 1 | 1/1 | 0 | 0 | 0 |
| ctx-null-guard-removed | 1 | 1/1 | 0 | 0 | 0 |
| clean-ctx-helper-defined-above | 1 | 0/0 | 0 | 0 | 0 |
| clean-ctx-lock-held-by-caller | 1 | 0/0 | 0 | 0 | 0 |
| clean-ctx-validated-earlier | 1 | 0/0 | 0 | 0 | 0 |
| py-sql-injection | 2 | 1/1 | 0 | 0 | 0 |
| py-shell-injection | 2 | 1/1 | 0 | 0 | 0 |
| py-off-by-one-total | 2 | 1/1 | 0 | 0 | 0 |
| py-mutable-default | 2 | 1/1 | 0 | 0 | 0 |
| py-swallowed-exception | 2 | 1/1 | 0 | 0 | 0 |
| py-missing-await | 2 | 1/1 | 0 | 0 | 0 |
| py-identity-compare | 2 | 1/1 | 0 | 0 | 0 |
| py-timezone-naive | 2 | 1/1 | 0 | 0 | 0 |
| py-secret-in-code | 2 | 1/1 | 0 | 0 | 0 |
| py-auth-check-removed | 2 | 1/1 | 0 | 0 | 0 |
| ts-xss-innerhtml | 2 | 1/1 | 0 | 0 | 0 |
| ts-unawaited-save | 2 | 1/1 | 0 | 0 | 0 |
| sql-migration-not-null | 2 | 1/1 | 0 | 0 | 0 |
| go-unchecked-error | 2 | 1/1 | 0 | 0 | 0 |
| dbt-percent-as-amount | 2 | 1/1 | 0 | 0 | 0 |
| dbt-drop-locked-column | 2 | 1/1 | 0 | 0 | 0 |
| dbt-left-to-inner-join | 2 | 1/1 | 0 | 0 | 0 |
| dbt-revenue-filter-widened | 2 | 1/1 | 0 | 0 | 0 |
| airflow-retries-zeroed | 2 | 1/1 | 0 | 0 | 0 |
| clean-py-refactor | 2 | 0/0 | 0 | 0 | 0 |
| clean-py-docstring-and-types | 2 | 0/0 | 0 | 0 | 0 |
| clean-py-test-added | 2 | 0/0 | 0 | 0 | 0 |
| clean-ts-rename | 2 | 0/0 | 0 | 0 | 0 |
| clean-dbt-comment | 2 | 0/0 | 0 | 0 | 0 |
| clean-sql-index | 2 | 0/0 | warn migrations/0043_orders_created_idx.sql:1 CREATE INDEX CONCURRENTLY may fail inside a transactional migration, and a failed run leaves an invalid index | 0 | 0 |
| ctx-swapped-transfer-args | 2 | 1/1 | 0 | 0 | 0 |
| ctx-units-mismatch | 2 | 1/1 | 0 | 0 | 0 |
| ctx-lock-bypassed | 2 | 1/1 | 0 | 0 | 0 |
| ctx-timeout-milliseconds | 2 | 1/1 | 0 | 0 | 0 |
| ctx-null-guard-removed | 2 | 1/1 | 0 | 0 | 0 |
| clean-ctx-helper-defined-above | 2 | 0/0 | warn app/report.py:15 Negative amounts render incorrectly through fmt_money | 0 | 0 |
| clean-ctx-lock-held-by-caller | 2 | 0/0 | 0 | 0 | 0 |
| clean-ctx-validated-earlier | 2 | 0/0 | 0 | 0 | 0 |
