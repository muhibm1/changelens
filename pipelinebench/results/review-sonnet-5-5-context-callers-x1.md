# Review eval: `anthropic:claude-sonnet-5-5`, 61 cases x 1, whole-file context on, second reading on

| metric | value |
| --- | --- |
| recall (bugs raised at warn or block) | 1.0 |
| bugs raised only as inform | 0 |
| precision (caught bugs / (caught bugs + false alarms)) | 1.0 |
| extra warn/block findings on an already-caught bug | 15 |
| false alarms at warn/block | 0 |
| correct changes flagged | 0/19 |
| provider errors | 0 |
| tokens in / out | 201544 / 18699 |

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
| hidden-py-removed-method-typed-caller | 0 | 1/1 | 0 | 0 | 0 |
| clean-py-removed-method-other-object-has-its-own | 0 | 0/0 | 0 | 0 | 0 |
| clean-intent-py-query-spaces-as-plus | 0 | 0/0 | 0 | 0 | 0 |
| intent-py-refactor-changes-default-timeout | 0 | 1/1 | 0 | 0 | 0 |
| clean-py-renamed-fn-every-caller-updated | 0 | 0/0 | 0 | 0 | 0 |
| hard-xf3-py-timeout-units | 0 | 1/1 | 0 | 0 | 0 |
| hard-xf-py-cache-key-ignores-new-arg | 0 | 1/1 | 0 | 0 | 0 |
| hard-py-discount-after-tax | 0 | 1/1 | 0 | 0 | 0 |
| hard-ts-has-more-never-true | 0 | 1/1 | 0 | 0 | 0 |
| hard-go-shadowed-err | 0 | 1/1 | 0 | 0 | 0 |
| hard-xf-dbt-fanout-then-sum | 0 | 1/1 | 0 | 0 | 0 |
| hard-xf-py-retry-returns-none | 0 | 1/1 | 0 | 0 | 0 |
| hard-py-dedupe-keeps-last-not-first | 0 | 1/1 | 0 | 0 | 0 |
| clean-hard-py-subprocess-list-args | 0 | 0/0 | 0 | 0 | 0 |
| clean-hard-py-literal-eval | 0 | 0/0 | 0 | 0 | 0 |
| clean-hard-ts-innerhtml-sanitized | 0 | 0/0 | 0 | 0 | 0 |
| clean-hard-py-bulk-delete-guarded | 0 | 0/0 | 0 | 0 | 0 |
| clean-hard-py-lock-narrowed | 0 | 0/0 | 0 | 0 | 0 |
