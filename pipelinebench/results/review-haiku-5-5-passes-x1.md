# Review eval: `anthropic:claude-haiku-5-5`, 61 cases x 1, whole-file context on, second reading by `anthropic:claude-haiku-5-5`

| metric | value |
| --- | --- |
| recall (bugs raised at warn or block) | 0.976 |
| bugs raised only as inform | 1 |
| precision (caught bugs / (caught bugs + false alarms)) | 0.872 |
| extra warn/block findings on an already-caught bug | 28 |
| false alarms at warn/block | 6 |
| correct changes flagged | 4/19 |
| provider errors | 0 |
| tokens in / out | 121196 / 188095 |

| case | run | caught | false alarms | uncertain | rejected |
| --- | --- | --- | --- | --- | --- |
| py-sql-injection | 0 | 1/1 | 0 | 0 | 2 |
| py-shell-injection | 0 | 1/1 | 0 | 0 | 4 |
| py-off-by-one-total | 0 | 1/1 | 0 | 0 | 3 |
| py-mutable-default | 0 | 1/1 | 0 | 0 | 3 |
| py-swallowed-exception | 0 | 1/1 | 0 | 0 | 3 |
| py-missing-await | 0 | 1/1 | 0 | 0 | 3 |
| py-identity-compare | 0 | 1/1 | 0 | 0 | 2 |
| py-timezone-naive | 0 | 1/1 | 0 | 0 | 2 |
| py-secret-in-code | 0 | 1/1 | 0 | 0 | 3 |
| py-auth-check-removed | 0 | 1/1 | 0 | 0 | 2 |
| ts-xss-innerhtml | 0 | 1/1 | 0 | 0 | 1 |
| ts-unawaited-save | 0 | 1/1 | 0 | 0 | 3 |
| sql-migration-not-null | 0 | 1/1 | 0 | 0 | 2 |
| go-unchecked-error | 0 | 1/1 | 0 | 0 | 2 |
| dbt-percent-as-amount | 0 | 1/1 | 0 | 0 | 0 |
| dbt-drop-locked-column | 0 | 1/1 | 0 | 0 | 0 |
| dbt-left-to-inner-join | 0 | 1/1 | 0 | 0 | 2 |
| dbt-revenue-filter-widened | 0 | 1/1 | 0 | 0 | 2 |
| airflow-retries-zeroed | 0 | 0/1 | 0 | 0 | 3 |
| clean-py-refactor | 0 | 0/0 | 0 | 0 | 0 |
| clean-py-docstring-and-types | 0 | 0/0 | 0 | 0 | 0 |
| clean-py-test-added | 0 | 0/0 | 0 | 0 | 0 |
| clean-ts-rename | 0 | 0/0 | 0 | 0 | 0 |
| clean-dbt-comment | 0 | 0/0 | 0 | 0 | 0 |
| clean-sql-index | 0 | 0/0 | warn migrations/0043_orders_created_idx.sql:2 Retry after a failed concurrent build silently keeps an invalid index | 0 | 3 |
| ctx-swapped-transfer-args | 0 | 1/1 | 0 | 0 | 2 |
| ctx-units-mismatch | 0 | 1/1 | 0 | 0 | 5 |
| ctx-lock-bypassed | 0 | 1/1 | 0 | 0 | 3 |
| ctx-timeout-milliseconds | 0 | 1/1 | 0 | 0 | 3 |
| ctx-null-guard-removed | 0 | 1/1 | 0 | 0 | 3 |
| clean-ctx-helper-defined-above | 0 | 0/0 | 0 | 0 | 0 |
| clean-ctx-lock-held-by-caller | 0 | 0/0 | 0 | 0 | 2 |
| clean-ctx-validated-earlier | 0 | 0/0 | 0 | 0 | 0 |
| xf-py-kwarg-renamed | 0 | 1/1 | 0 | 0 | 1 |
| xf-py-cents-vs-dollars | 0 | 1/1 | 0 | 0 | 1 |
| xf-ts-status-value-renamed | 0 | 1/1 | 0 | 0 | 3 |
| xf-dbt-renamed-column-still-read | 0 | 1/1 | 0 | 0 | 2 |
| clean-xf-py-rename-with-caller | 0 | 0/0 | 0 | 0 | 1 |
| clean-xf-dbt-rename-with-consumer | 0 | 0/0 | warn fixture/dbt_project/models/intermediate/int_customer_orders.sql:4 region now carries country values for downstream readers | 0 | 1 |
| hidden-dbt-column-dropped | 0 | 1/1 | 0 | 0 | 2 |
| hidden-dbt-column-renamed | 0 | 1/1 | 0 | 0 | 0 |
| hidden-py-renamed-fn-still-called | 0 | 1/1 | 0 | 0 | 2 |
| hidden-py-kwarg-renamed-still-called | 0 | 1/1 | 0 | 0 | 2 |
| hidden-py-removed-method-typed-caller | 0 | 1/1 | 0 | 0 | 2 |
| clean-py-removed-method-other-object-has-its-own | 0 | 0/0 | 0 | 0 | 0 |
| clean-intent-py-query-spaces-as-plus | 0 | 0/0 | 0 | 0 | 0 |
| intent-py-refactor-changes-default-timeout | 0 | 1/1 | 0 | 0 | 2 |
| clean-py-renamed-fn-every-caller-updated | 0 | 0/0 | 0 | 0 | 1 |
| hard-xf3-py-timeout-units | 0 | 1/1 | 0 | 0 | 3 |
| hard-xf-py-cache-key-ignores-new-arg | 0 | 1/1 | warn app/pricing.py:11 Unknown or differently cased currency codes raise a bare KeyError | 0 | 3 |
| hard-py-discount-after-tax | 0 | 1/1 | 0 | 0 | 1 |
| hard-ts-has-more-never-true | 0 | 1/1 | 0 | 0 | 1 |
| hard-go-shadowed-err | 0 | 1/1 | 0 | 0 | 2 |
| hard-xf-dbt-fanout-then-sum | 0 | 1/1 | 0 | 0 | 5 |
| hard-xf-py-retry-returns-none | 0 | 1/1 | 0 | 0 | 1 |
| hard-py-dedupe-keeps-last-not-first | 0 | 1/1 | 0 | 0 | 2 |
| clean-hard-py-subprocess-list-args | 0 | 0/0 | 0 | 0 | 0 |
| clean-hard-py-literal-eval | 0 | 0/0 | warn app/settings.py:8 Deeply nested JSON escapes as RecursionError instead of ValueError | 0 | 0 |
| clean-hard-ts-innerhtml-sanitized | 0 | 0/0 | 0 | 0 | 1 |
| clean-hard-py-bulk-delete-guarded | 0 | 0/0 | warn app/cleanup.py:10 Negative limit silently drops the slice bound and deletes almost everything; warn app/cleanup.py:10 Limit is applied before deletion, so rows beyond the limit are reported but not all removed per call and the dry-run list can differ from what is deleted | 0 | 0 |
| clean-hard-py-lock-narrowed | 0 | 0/0 | 0 | 0 | 0 |

Missed: `airflow-retries-zeroed@6`
