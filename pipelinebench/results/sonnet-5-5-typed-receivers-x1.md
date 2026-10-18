# Review eval: `anthropic:claude-sonnet-5-5`, 2 cases x 1, whole-file context on

| metric | value |
| --- | --- |
| recall (bugs raised at warn or block) | 1.0 |
| bugs raised only as inform | 0 |
| precision (caught bugs / (caught bugs + false alarms)) | 1.0 |
| extra warn/block findings on an already-caught bug | 1 |
| false alarms at warn/block | 0 |
| correct changes flagged | 0/1 |
| provider errors | 0 |
| tokens in / out | 2773 / 204 |

| case | run | caught | false alarms | uncertain | rejected |
| --- | --- | --- | --- | --- | --- |
| hidden-py-removed-method-typed-caller | 0 | 1/1 | 0 | 0 | 0 |
| clean-py-removed-method-other-object-has-its-own | 0 | 0/0 | 0 | 0 | 0 |
