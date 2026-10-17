# Review eval: `anthropic:claude-sonnet-5-5`, 8 cases x 3, whole-file context on

| metric | value |
| --- | --- |
| recall (bugs raised at warn or block) | 1.0 |
| bugs raised only as inform | 0 |
| precision (caught bugs / (caught bugs + false alarms)) | 0.545 |
| extra warn/block findings on an already-caught bug | 13 |
| false alarms at warn/block | 15 |
| correct changes flagged | 5/6 |
| provider errors | 0 |
| tokens in / out | 42792 / 11047 |

| case | run | caught | false alarms | uncertain | rejected |
| --- | --- | --- | --- | --- | --- |
| xf-py-kwarg-renamed | 0 | 1/1 | warn app/http.py:4 Public signature change breaks existing callers using `timeout` | 0 | 0 |
| xf-py-cents-vs-dollars | 0 | 1/1 | warn app/prices.py:1 unit_price contract changed from dollars to cents without verifying other callers | 0 | 0 |
| xf-ts-status-value-renamed | 0 | 1/1 | warn web/src/types.ts:1 UserStatus union drops "inactive" without a visible migration | 0 | 0 |
| xf-dbt-renamed-column-still-read | 0 | 1/1 | warn fixture/dbt_project/models/staging/stg_customers.sql:6 Column rename on staging model breaks the contract for downstream assets | 0 | 0 |
| clean-xf-py-rename-with-caller | 0 | 0/0 | warn app/http.py:4 Public fetch() signature changed: parameter renamed and made keyword-only | 0 | 0 |
| clean-xf-dbt-rename-with-consumer | 0 | 0/0 | warn fixture/dbt_project/models/staging/stg_customers.sql:6 Column rename on stg_customers reaches downstream assets outside this diff | 0 | 0 |
| hidden-dbt-column-dropped | 0 | 1/1 | 0 | 0 | 0 |
| hidden-dbt-column-renamed | 0 | 1/1 | 0 | 0 | 0 |
| xf-py-kwarg-renamed | 1 | 1/1 | 0 | 0 | 0 |
| xf-py-cents-vs-dollars | 1 | 1/1 | 0 | 0 | 0 |
| xf-ts-status-value-renamed | 1 | 1/1 | warn web/src/types.ts:1 Removing "inactive" from UserStatus breaks existing producers and consumers | 0 | 0 |
| xf-dbt-renamed-column-still-read | 1 | 1/1 | warn fixture/dbt_project/models/staging/stg_customers.sql:6 Column rename on staging model breaks downstream consumers | 0 | 0 |
| clean-xf-py-rename-with-caller | 1 | 0/0 | warn app/http.py:4 Public fetch() signature renamed and made keyword-only without a compatibility path | 0 | 0 |
| clean-xf-dbt-rename-with-consumer | 1 | 0/0 | warn fixture/dbt_project/models/staging/stg_customers.sql:6 Column rename on stg_customers reaches downstream assets outside this diff | 0 | 0 |
| hidden-dbt-column-dropped | 1 | 1/1 | 0 | 0 | 0 |
| hidden-dbt-column-renamed | 1 | 1/1 | 0 | 0 | 0 |
| xf-py-kwarg-renamed | 2 | 1/1 | warn app/http.py:4 Public fetch signature renamed and made keyword-only without updating all callers | 0 | 0 |
| xf-py-cents-vs-dollars | 2 | 1/1 | warn app/prices.py:1 unit_price contract changed from dollars to cents without verifying callers or table data | 0 | 0 |
| xf-ts-status-value-renamed | 2 | 1/1 | warn web/src/types.ts:1 Removing "inactive" from UserStatus is a breaking change for existing data and consumers | 0 | 0 |
| xf-dbt-renamed-column-still-read | 2 | 1/1 | warn fixture/dbt_project/models/staging/stg_customers.sql:6 Column rename in staging model breaks the contract for downstream consumers | 0 | 0 |
| clean-xf-py-rename-with-caller | 2 | 0/0 | 0 | 0 | 0 |
| clean-xf-dbt-rename-with-consumer | 2 | 0/0 | warn fixture/dbt_project/models/staging/stg_customers.sql:6 Column rename on stg_customers reaches downstream assets outside this diff | 0 | 0 |
| hidden-dbt-column-dropped | 2 | 1/1 | 0 | 0 | 0 |
| hidden-dbt-column-renamed | 2 | 1/1 | 0 | 0 | 0 |
