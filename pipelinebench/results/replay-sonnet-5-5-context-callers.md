# Replay eval: `anthropic`, 26 merged changes

| metric | value |
| --- | --- |
| changes | 26 |
| errors | 0 |
| changes warned | 2 |
| warns | 4 |
| warns per change | 0.15 |
| confirmed | 0 |
| plausible | 4 |
| noise | 0 |
| unjudged | 0 |
| setup | 0 |
| confirmed recall | 0/1 |
| tokens in | 373208 |
| tokens out | 7349 |

| repo | commit | change | severity | warnings |
| --- | --- | --- | --- | --- |
| httpx | `1d73150` | Cleanup response.json() method (#2911) | warn | **plausible** `httpx/_models.py:761` Response.json() now ignores the declared charset in Content-Type |
| httpx | `359f77d` | Clean up URL signature (#3245) | inform | none |
| httpx | `41597ad` | Move remaining utility functions to _models.py (#3387) | inform | none |
| httpx | `6212e8f` | Move utility functions to _multipart.py (#3388) | inform | none |
| httpx | `75a13a1` | Remove typeshed iscoroutine workaround (#2466) | inform | none |
| httpx | `83a8518` | Move normalize header functions to _models.py (#3382) | inform | none |
| httpx | `8752e26` | Drop .read/.aread from SyncByteStream/AsyncByteStream (#2407) | inform | none |
| httpx | `9baf3a6` | Drop RawURL (#2241) | inform | none |
| httpx | `b97c059` | Streaming multipart support (#2382) | inform | none |
| httpx | `ba2e512` | Review urlescape percent-safe set (#3373) | inform | none |
| hono | `3a67f7f3` | Merge commit from fork | inform | none |
| hono | `4b44abe1` | perf(router): share null object creation (#5267) | inform | none |
| hono | `6cadf753` | fix(lambda-edge): descriptive error on a malformed event (#5358) | inform | none |
| hono | `7075369e` | Revert fix(etag) incremental hashing (#5199) | inform | none |
| hono | `8a5852d4` | fix(etag): incremental hashing for chunked responses (#5199) | inform | none |
| hono | `a1946287` | fix(pattern-router/linear-router): prefix overmatch (#5252) | inform | none |
| hono | `a1e4ac7d` | perf(cors): pre-join static array header options (#5210) | inform | none |
| hono | `c409d855` | fix(request): serialize cached JSON body in cloneRawRequest (#5288) | inform | none |
| cobra | `24ada7f` | Remove the default completion cmd if it is alone (#1559) | inform | none |
| cobra | `5bef9d8` | Fix --version help and output for plugins (#2180) | inform | none |
| cobra | `6c3c116` | feat: add CompletionFunc type (#2220) | inform | none |
| cobra | `8607918` | feat: make InitDefaultCompletionCmd public (#1467) | inform | none |
| cobra | `a0a6ae0` | Improve API to get flag completion function (#2063) | inform | none |
| cobra | `a4ab3fa` | powershell completion with custom comp (#1208) | warn | **plausible** `powershell_completions.go:71` WordToComplete is taken by splitting arguments on spaces, so quoted arguments break; **plausible** `powershell_completions.go:99` Empty output makes $Out[-1] null and the directive parse throws; **plausible** `powershell_completions.go:107` Removing the directive line by value also removes valid completions equal to it |
| cobra | `b84ef40` | Rename BashCompDirectives to ShellCompDirectives (#1082) | inform | none |
| cobra | `fce8d8a` | Expose ValidateRequiredFlags and ValidateFlagGroups (#1760) | inform | none |
