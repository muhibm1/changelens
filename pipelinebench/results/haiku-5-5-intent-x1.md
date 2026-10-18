# Review eval: `anthropic:claude-haiku-5-5`, 11 cases x 1, whole-file context on

| metric | value |
| --- | --- |
| recall (bugs raised at warn or block) | 1.0 |
| bugs raised only as inform | 0 |
| precision (caught bugs / (caught bugs + false alarms)) | 1.0 |
| extra warn/block findings on an already-caught bug | 2 |
| false alarms at warn/block | 0 |
| correct changes flagged | 0/4 |
| provider errors | 0 |
| tokens in / out | 19180 / 3789 |

| case | run | caught | false alarms | uncertain | rejected |
| --- | --- | --- | --- | --- | --- |
| xf-py-kwarg-renamed | 0 | 1/1 | 0 | 0 | 0 |
| xf-py-cents-vs-dollars | 0 | 1/1 | 0 | 0 | 0 |
| xf-ts-status-value-renamed | 0 | 1/1 | 0 | 0 | 0 |
| clean-xf-py-rename-with-caller | 0 | 0/0 | 0 | 0 | 0 |
| hidden-py-renamed-fn-still-called | 0 | 1/1 | 0 | 0 | 0 |
| hidden-py-kwarg-renamed-still-called | 0 | 1/1 | 0 | 0 | 0 |
| hidden-py-removed-method-typed-caller | 0 | 1/1 | 0 | 0 | 0 |
| clean-py-removed-method-other-object-has-its-own | 0 | 0/0 | 0 | 0 | 0 |
| clean-intent-py-query-spaces-as-plus | 0 | 0/0 | 0 | 0 | 0 |
| intent-py-refactor-changes-default-timeout | 0 | 1/1 | 0 | 0 | 0 |
| clean-py-renamed-fn-every-caller-updated | 0 | 0/0 | 0 | 0 | 0 |
