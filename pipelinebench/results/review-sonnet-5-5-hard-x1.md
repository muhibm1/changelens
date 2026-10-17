# Review eval: `anthropic:claude-sonnet-5-5`, 13 cases x 1, whole-file context on

| metric | value |
| --- | --- |
| recall (bugs raised at warn or block) | 1.0 |
| bugs raised only as inform | 0 |
| precision (caught bugs / (caught bugs + false alarms)) | 0.889 |
| extra warn/block findings on an already-caught bug | 1 |
| false alarms at warn/block | 1 |
| correct changes flagged | 1/5 |
| provider errors | 0 |
| tokens in / out | 20809 / 3196 |

| case | run | caught | false alarms | uncertain | rejected |
| --- | --- | --- | --- | --- | --- |
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
| clean-hard-ts-innerhtml-sanitized | 0 | 0/0 | warn web/src/render.ts:4 Comment rendering switched from textContent to sanitized innerHTML | 0 | 0 |
| clean-hard-py-bulk-delete-guarded | 0 | 0/0 | 0 | 0 | 0 |
| clean-hard-py-lock-narrowed | 0 | 0/0 | 0 | 0 | 0 |
