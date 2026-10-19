# Replay eval: `anthropic:claude-haiku-5-5`, 26 merged changes

| metric | value |
| --- | --- |
| changes | 26 |
| errors | 0 |
| changes warned | 7 |
| warns | 13 |
| warns per change | 0.5 |
| confirmed | 0 |
| plausible | 4 |
| noise | 5 |
| unjudged | 4 |
| setup | 0 |
| confirmed recall | 0/1 |
| tokens in | 118701 |
| tokens out | 170547 |

| repo | commit | change | severity | warnings |
| --- | --- | --- | --- | --- |
| httpx | `1d73150` | Cleanup response.json() method (#2911) | inform | none |
| httpx | `359f77d` | Clean up URL signature (#3245) | inform | none |
| httpx | `41597ad` | Move remaining utility functions to _models.py (#3387) | inform | none |
| httpx | `6212e8f` | Move utility functions to _multipart.py (#3388) | inform | none |
| httpx | `75a13a1` | Remove typeshed iscoroutine workaround (#2466) | inform | none |
| httpx | `83a8518` | Move normalize header functions to _models.py (#3382) | inform | none |
| httpx | `8752e26` | Drop .read/.aread from SyncByteStream/AsyncByteStream (#2407) | warn | **noise** `httpx/_content.py:55` Removing the SyncByteStream guard makes a user stream's own read() be called; **unjudged** `httpx/_content.py:80` Removing the AsyncByteStream guard makes a user stream's own aread() be called |
| httpx | `9baf3a6` | Drop RawURL (#2241) | inform | none |
| httpx | `b97c059` | Streaming multipart support (#2382) | warn | **plausible** `httpx/_multipart.py:177` Silently swallowing seek failure can send a Content-Length that does not match the body; **plausible** `httpx/_multipart.py:181` Non-seekable file body is empty on a second send of the same stream |
| httpx | `ba2e512` | Review urlescape percent-safe set (#3373) | inform | none |
| hono | `3a67f7f3` | Merge commit from fork | warn | **unjudged** `src/helper/ssg/utils.ts:91` Single-letter-colon segments are treated as Windows drive roots for relative output paths; **unjudged** `src/helper/ssg/utils.ts:113` Windows trailing-dot or trailing-space parent segments bypass the containment check |
| hono | `4b44abe1` | perf(router): share null object creation (#5267) | inform | none |
| hono | `6cadf753` | fix(lambda-edge): descriptive error on a malformed event (#5358) | warn | **plausible** `src/adapter/lambda-edge/handler.ts:186` Missing config and host header yields a request to host 'undefined' instead of failing |
| hono | `7075369e` | Revert fix(etag) incremental hashing (#5199) | inform | none |
| hono | `8a5852d4` | fix(etag): incremental hashing for chunked responses (#5199) | inform | none |
| hono | `a1946287` | fix(pattern-router/linear-router): prefix overmatch (#5252) | inform | none |
| hono | `a1e4ac7d` | perf(cors): pre-join static array header options (#5210) | inform | none |
| hono | `c409d855` | fix(request): serialize cached JSON body in cloneRawRequest (#5288) | inform | none |
| cobra | `24ada7f` | Remove the default completion cmd if it is alone (#1559) | warn | **noise** `completions.go:776` Completion of 'completion' is skipped when a flag precedes it in the __complete path |
| cobra | `5bef9d8` | Fix --version help and output for plugins (#2180) | inform | none |
| cobra | `6c3c116` | feat: add CompletionFunc type (#2220) | inform | none |
| cobra | `8607918` | feat: make InitDefaultCompletionCmd public (#1467) | inform | none |
| cobra | `a0a6ae0` | Improve API to get flag completion function (#2063) | inform | none |
| cobra | `a4ab3fa` | powershell completion with custom comp (#1208) | warn | **plausible** `powershell_completions.go:71` Quoted arguments containing spaces are split and re-joined incorrectly; **noise** `powershell_completions.go:146` NoFileComp empty-result sentinel is unreachable; **noise** `powershell_completions.go:146` ShellCompDirectiveNoFileComp is checked before the prefix filter, so no-match results fall back to file completion; **noise** `powershell_completions.go:201` Single remaining candidate in Complete mode is never recognised |
| cobra | `b84ef40` | Rename BashCompDirectives to ShellCompDirectives (#1082) | warn | **unjudged** `custom_completions.go:14` Exported constant renamed beyond the stated intent |
| cobra | `fce8d8a` | Expose ValidateRequiredFlags and ValidateFlagGroups (#1760) | inform | none |
