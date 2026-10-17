# Review eval: `anthropic:claude-sonnet-5-5`, 25 cases x 1

| metric | value |
| --- | --- |
| recall (bugs raised at warn or block) | 1.0 |
| bugs raised only as inform | 0 |
| precision (caught bugs / (caught bugs + false alarms)) | 0.905 |
| extra warn/block findings on an already-caught bug | 11 |
| false alarms at warn/block | 2 |
| correct changes flagged | 2/6 |
| provider errors | 0 |
| tokens in / out | 29576 / 7894 |

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
| clean-py-refactor | 0 | 0/0 | block app/pricing.py:6 Call to price_with_tax which is not defined or imported in this diff | 0 | 0 |
| clean-py-docstring-and-types | 0 | 0/0 | 0 | 0 | 0 |
| clean-py-test-added | 0 | 0/0 | 0 | 0 | 0 |
| clean-ts-rename | 0 | 0/0 | 0 | 0 | 0 |
| clean-dbt-comment | 0 | 0/0 | 0 | 0 | 0 |
| clean-sql-index | 0 | 0/0 | warn migrations/0043_orders_created_idx.sql:1 CREATE INDEX CONCURRENTLY depends on how the migration runner handles transactions | 0 | 0 |
