# Review eval: `anthropic:claude-haiku-5-5`, 7 cases x 1, whole-file context on

| metric | value |
| --- | --- |
| recall (bugs raised at warn or block) | 1.0 |
| bugs raised only as inform | 0 |
| precision (caught bugs / (caught bugs + false alarms)) | 1.0 |
| extra warn/block findings on an already-caught bug | 1 |
| false alarms at warn/block | 0 |
| correct changes flagged | 0/2 |
| provider errors | 0 |
| tokens in / out | 11477 / 2590 |

| case | run | caught | false alarms | uncertain | rejected |
| --- | --- | --- | --- | --- | --- |
| xf-py-kwarg-renamed | 0 | 1/1 | 0 | 0 | 0 |
| xf-py-cents-vs-dollars | 0 | 1/1 | 0 | 0 | 0 |
| xf-ts-status-value-renamed | 0 | 1/1 | 0 | 0 | 0 |
| clean-xf-py-rename-with-caller | 0 | 0/0 | 0 | 0 | 0 |
| hidden-py-renamed-fn-still-called | 0 | 1/1 | 0 | 0 | 0 |
| hidden-py-kwarg-renamed-still-called | 0 | 1/1 | 0 | 0 | 0 |
| clean-py-renamed-fn-every-caller-updated | 0 | 0/0 | 0 | 0 | 0 |
