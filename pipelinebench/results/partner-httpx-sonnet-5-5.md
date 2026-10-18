# Design partner run: httpx, `anthropic:claude-sonnet-5-5`, 10 merged changes x 1

Ten merged pull requests from [encode/httpx](https://github.com/encode/httpx), chosen because each removes,
moves or re-signs definitions (found by `changed_definitions` over the last 400 commits). Each was reviewed with
`orion review --git <commit>^` on a checkout of the commit, with an `orion.yaml` that names only a database, so
there is no platform graph: these are plain code reviews. All ten changes were merged by the httpx maintainers,
so a warning is at best a fair question to the author and at worst a false alarm.

| commit | change | changed definitions | deterministic warn | model warn, no intent | model warn, commit message as intent |
| --- | --- | --- | --- | --- | --- |
| `41597ad` | Move remaining utility functions from _utils.py to _models.py (#3387) | 7 | 0 | 0 | 0 |
| `6212e8f` | Move utility functions from _utils.py to _multipart.py (#3388) | 3 | 0 | 0 | 0 |
| `83a8518` | Move normalize header functions from _utils.py to _models.py (#3382) | 2 | 0 | 0 | 0 |
| `1d73150` | Cleanup response.json() method (#2911) | 1 | 0 | 1 | 1 |
| `8752e26` | Drop .read/.aread from SyncByteStream/AsyncByteStream (#2407) | 2 | 0 | 2 | 0 |
| `9baf3a6` | Drop RawURL (#2241) | 1 | 0 | 1 | 0 |
| `b97c059` | Streaming multipart support (#2382) | 3 | 0 | 1 | 1 |
| `359f77d` | Clean up URL signature. (#3245) | 3 | 0 | 0 | 0 |
| `ba2e512` | Review urlescape percent-safe set, and use + behavior for form spaces. | 3 | 0 | 1 | 1 |
| `75a13a1` | Remove typeshed iscoroutine workaround (#2466) | 1 | 0 | 0 | 0 |
| total | | | 0 | 6 | 3 |

Deterministic findings: none. Every removed definition either had no remaining reference, or its remaining
mentions were told apart from it by parsing: 49 `.read()` calls on response objects after `SyncByteStream.read`
went, 20 `.raw` accesses after `URL.raw` went, and two `urlencode` calls imported from `urllib.parse` after
httpx's own `urlencode` went.

Without intent the model warned on announced changes it could not see were announced (dropping `read`/`aread`
and `RawURL` as unannounced breaking changes). With the commit message as stated intent those went, and the
three warnings left are questions on behaviour the message does not fully cover (the last one partly restates the
announced `+` for spaces, but its point is the reserved characters left unescaped):

- `1d73150` `httpx/_models.py:761` json() now ignores the response's declared charset
- `b97c059` `httpx/_multipart.py:177` Swallowed seek failure on non-seekable file hides re-read problems
- `ba2e512` `httpx/_urls.py:622` Query string encoding changes for all params: space becomes '+', '/', ':', '?' etc. left unescaped

One intermediate prompt also flagged a constructor restructuring that keeps behaviour ("an unrequested
refactor"); the system prompt now says a restructuring the model judges to keep behaviour is not a finding.

To rerun: clone httpx, write `db: .orion/graph.duckdb` to an untracked `orion.yaml` in it, then for each commit
`git checkout <commit>` and `orion review --git <commit>^ --provider anthropic --model claude-sonnet-5-5 --format json`
(to review without the commit message, pass the same diff with `--diff-file` instead of `--git`).
