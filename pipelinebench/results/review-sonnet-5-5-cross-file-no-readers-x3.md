# Review eval: `anthropic:claude-sonnet-5-5`, 8 cases x 3, whole-file context on

| metric | value |
| --- | --- |
| recall (bugs raised at warn or block) | 1.0 |
| bugs raised only as inform | 0 |
| precision (caught bugs / (caught bugs + false alarms)) | 0.529 |
| extra warn/block findings on an already-caught bug | 6 |
| false alarms at warn/block | 16 |
| correct changes flagged | 6/6 |
| provider errors | 0 |
| tokens in / out | 41685 / 11177 |

| case | run | caught | false alarms | uncertain | rejected |
| --- | --- | --- | --- | --- | --- |
| xf-py-kwarg-renamed | 0 | 1/1 | warn app/http.py:4 Public fetch signature changed in a breaking way | 0 | 0 |
| xf-py-cents-vs-dollars | 0 | 1/1 | warn app/prices.py:1 Return unit changed from dollars to cents without verifying other callers | 0 | 0 |
| xf-ts-status-value-renamed | 0 | 1/1 | warn web/src/types.ts:1 Removing "inactive" from UserStatus is a breaking change for other consumers | 0 | 0 |
| xf-dbt-renamed-column-still-read | 0 | 1/1 | 0 | 0 | 0 |
| clean-xf-py-rename-with-caller | 0 | 0/0 | warn app/http.py:4 Public fetch() signature renamed and made keyword-only; other callers may break | 0 | 0 |
| clean-xf-dbt-rename-with-consumer | 0 | 0/0 | warn fixture/dbt_project/models/staging/stg_customers.sql:6 Staging column region renamed to country; any other consumer of stg_customers.region will break | 0 | 0 |
| hidden-dbt-column-dropped | 0 | 1/1 | 0 | 0 | 0 |
| hidden-dbt-column-renamed | 0 | 1/1 | 0 | 0 | 0 |
| xf-py-kwarg-renamed | 1 | 1/1 | warn app/http.py:4 Renaming `timeout` to `timeout_s` and making it keyword-only breaks the public signature | 0 | 0 |
| xf-py-cents-vs-dollars | 1 | 1/1 | warn app/prices.py:1 Unit change from dollars to cents may break other callers and the price table | 0 | 0 |
| xf-ts-status-value-renamed | 1 | 1/1 | 0 | 0 | 0 |
| xf-dbt-renamed-column-still-read | 1 | 1/1 | warn fixture/dbt_project/models/staging/stg_customers.sql:6 Column rename breaks downstream consumers | 0 | 0 |
| clean-xf-py-rename-with-caller | 1 | 0/0 | warn app/http.py:4 Public fetch() signature change breaks callers using the old keyword or positional timeout | 0 | 0 |
| clean-xf-dbt-rename-with-consumer | 1 | 0/0 | warn fixture/dbt_project/models/staging/stg_customers.sql:6 Staging column renamed region -> country; any other consumers of stg_customers.region will break | 0 | 0 |
| hidden-dbt-column-dropped | 1 | 1/1 | 0 | 0 | 0 |
| hidden-dbt-column-renamed | 1 | 1/1 | 0 | 0 | 0 |
| xf-py-kwarg-renamed | 2 | 1/1 | warn app/http.py:4 Public fetch signature changed (rename plus keyword-only) | 0 | 0 |
| xf-py-cents-vs-dollars | 2 | 1/1 | warn app/prices.py:1 Price unit changed from dollars to cents without migrating table data | 0 | 0 |
| xf-ts-status-value-renamed | 2 | 1/1 | warn web/src/types.ts:1 Removing "inactive" from UserStatus is a breaking change | 0 | 0 |
| xf-dbt-renamed-column-still-read | 2 | 1/1 | warn fixture/dbt_project/models/staging/stg_customers.sql:6 Column rename region -> country breaks the staging contract | 0 | 0 |
| clean-xf-py-rename-with-caller | 2 | 0/0 | warn app/http.py:4 Public fetch() signature renamed and made keyword-only; other callers may break | 0 | 0 |
| clean-xf-dbt-rename-with-consumer | 2 | 0/0 | warn fixture/dbt_project/models/staging/stg_customers.sql:6 Staging column rename region -> country affects any consumer of stg_customers.region | 0 | 0 |
| hidden-dbt-column-dropped | 2 | 1/1 | 0 | 0 | 0 |
| hidden-dbt-column-renamed | 2 | 1/1 | 0 | 0 | 0 |
