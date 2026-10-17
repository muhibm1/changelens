# Review eval: `anthropic:claude-sonnet-5-5`, 9 cases x 3, whole-file context on

| metric | value |
| --- | --- |
| recall (bugs raised at warn or block) | 1.0 |
| bugs raised only as inform | 0 |
| precision (caught bugs / (caught bugs + false alarms)) | 0.875 |
| extra warn/block findings on an already-caught bug | 31 |
| false alarms at warn/block | 3 |
| correct changes flagged | 0/6 |
| provider errors | 0 |
| tokens in / out | 60150 / 10448 |

| case | run | caught | false alarms | uncertain | rejected |
| --- | --- | --- | --- | --- | --- |
| dbt-percent-as-amount | 0 | 1/1 | 0 | 0 | 0 |
| dbt-drop-locked-column | 0 | 1/1 | 0 | 0 | 0 |
| dbt-left-to-inner-join | 0 | 1/1 | 0 | 0 | 0 |
| dbt-revenue-filter-widened | 0 | 1/1 | 0 | 0 | 0 |
| clean-dbt-comment | 0 | 0/0 | 0 | 0 | 0 |
| xf-dbt-renamed-column-still-read | 0 | 1/1 | warn fixture/dbt_project/models/staging/stg_customers.sql:6 Column rename region -> country changes staging contract | 0 | 0 |
| clean-xf-dbt-rename-with-consumer | 0 | 0/0 | 0 | 0 | 0 |
| hidden-dbt-column-dropped | 0 | 1/1 | 0 | 0 | 0 |
| hidden-dbt-column-renamed | 0 | 1/1 | 0 | 0 | 0 |
| dbt-percent-as-amount | 1 | 1/1 | 0 | 0 | 0 |
| dbt-drop-locked-column | 1 | 1/1 | 0 | 0 | 0 |
| dbt-left-to-inner-join | 1 | 1/1 | 0 | 0 | 0 |
| dbt-revenue-filter-widened | 1 | 1/1 | 0 | 0 | 0 |
| clean-dbt-comment | 1 | 0/0 | 0 | 0 | 0 |
| xf-dbt-renamed-column-still-read | 1 | 1/1 | warn fixture/dbt_project/models/staging/stg_customers.sql:6 Column rename in staging model not verified against downstream consumers | 0 | 0 |
| clean-xf-dbt-rename-with-consumer | 1 | 0/0 | 0 | 0 | 0 |
| hidden-dbt-column-dropped | 1 | 1/1 | 0 | 0 | 0 |
| hidden-dbt-column-renamed | 1 | 1/1 | 0 | 0 | 0 |
| dbt-percent-as-amount | 2 | 1/1 | 0 | 0 | 0 |
| dbt-drop-locked-column | 2 | 1/1 | 0 | 0 | 0 |
| dbt-left-to-inner-join | 2 | 1/1 | 0 | 0 | 0 |
| dbt-revenue-filter-widened | 2 | 1/1 | 0 | 0 | 0 |
| clean-dbt-comment | 2 | 0/0 | 0 | 0 | 0 |
| xf-dbt-renamed-column-still-read | 2 | 1/1 | warn fixture/dbt_project/models/staging/stg_customers.sql:6 Column rename breaks the staging contract for downstream assets | 0 | 0 |
| clean-xf-dbt-rename-with-consumer | 2 | 0/0 | 0 | 0 | 0 |
| hidden-dbt-column-dropped | 2 | 1/1 | 0 | 0 | 0 |
| hidden-dbt-column-renamed | 2 | 1/1 | 0 | 0 | 0 |
