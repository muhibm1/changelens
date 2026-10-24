# Replay eval: `claude-code:claude-sonnet-5-5`, 26 merged changes

| metric | value |
| --- | --- |
| changes | 26 |
| errors | 12 |
| changes warned | 3 |
| warns | 6 |
| warns per change | 0.23 |
| confirmed | 1 |
| plausible | 4 |
| noise | 1 |
| unjudged | 0 |
| setup | 0 |
| confirmed recall | 1/1 |
| tokens in | 458 |
| tokens out | 80273 |

| repo | commit | change | severity | warnings |
| --- | --- | --- | --- | --- |
| httpx | `1d73150` | Cleanup response.json() method (#2911) | warn | **plausible** `httpx/_models.py:761` json() ignores the declared charset and no longer tolerates invalid UTF-8 |
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
| hono | `7075369e` | Revert fix(etag) incremental hashing (#5199) | warn | **confirmed** `src/middleware/etag/digest.ts:33` Revert restores chained hashing, so the ETag depends on chunk boundaries |
| hono | `8a5852d4` | fix(etag): incremental hashing for chunked responses (#5199) | inform | none |
| hono | `a1946287` | fix(pattern-router/linear-router): prefix overmatch (#5252) | inform | none |
| hono | `a1e4ac7d` | perf(cors): pre-join static array header options (#5210) | inform | none |
| hono | `c409d855` | fix(request): serialize cached JSON body in cloneRawRequest (#5288) | inform | none |
| cobra | `24ada7f` | Remove the default completion cmd if it is alone (#1559) | inform | none |
| cobra | `5bef9d8` | Fix --version help and output for plugins (#2180) | inform | none |
| cobra | `6c3c116` | feat: add CompletionFunc type (#2220) | inform | none |
| cobra | `8607918` | feat: make InitDefaultCompletionCmd public (#1467) | inform | none |
| cobra | `a0a6ae0` | Improve API to get flag completion function (#2063) | inform | none |
| cobra | `a4ab3fa` | powershell completion with custom comp (#1208) | warn | **plausible** `powershell_completions.go:95` Completion re-evaluates the typed command line with Invoke-Expression; **plausible** `powershell_completions.go:99` Empty program output is not handled before reading the directive; **plausible** `powershell_completions.go:166` Prefix filter treats the typed word as a wildcard pattern; **noise** `powershell_completions.go:201` Single-completion check uses .Length on a value that is a bare hashtable, not an array |
| cobra | `b84ef40` | Rename BashCompDirectives to ShellCompDirectives (#1082) | inform | none |
| cobra | `fce8d8a` | Expose ValidateRequiredFlags and ValidateFlagGroups (#1760) | inform | none |
| calitp | `008d78a8` | Remove non-RBAP Payments Tables (#5651) | - | error |
| calitp | `2eff9b46` | De-duplicate fct_payments_settlements rows (#5586) | - | error |
| calitp | `2f53a705` | SUM instead of COUNT for total arrivals (#5679) | - | error |
| calitp | `3127f574` | Add _feed_valid_from to daily GTFS summary tables (#5656) | - | error |
| calitp | `34716dcc` | Fix route-direction summary trip counting (#5767) | - | error |
| calitp | `5b9885a0` | Incrementally build vehicle position trip summaries (#5642) | - | error |
| calitp | `6a6df64e` | Add Public/Conditional Operating column to dim tables (#5674) | - | error |
| calitp | `6f1cc4b7` | Enghouse transactions cleanup (#5763) | - | error |
| calitp | `ad497afc` | De-duplicate Littlepay customers and rides (#5761) | - | error |
| calitp | `b0b73b32` | fix refs (#5768) | - | error |
| calitp | `e636f6e2` | Type datetimes in fct_payments_aggregations (#5583) | - | error |
| calitp | `fbe45897` | Remove legacy stg_rt models (#5633) | - | error |
