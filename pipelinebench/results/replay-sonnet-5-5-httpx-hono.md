# Replay eval: `anthropic`, 18 merged changes

| metric | value |
| --- | --- |
| changes | 18 |
| errors | 0 |
| changes warned | 2 |
| warns | 2 |
| warns per change | 0.11 |
| confirmed | 1 |
| plausible | 1 |
| noise | 0 |
| unjudged | 0 |
| setup | 0 |
| confirmed recall | 1/1 |
| tokens in | 189217 |
| tokens out | 4695 |

| repo | commit | change | severity | warnings |
| --- | --- | --- | --- | --- |
| httpx | `1d73150` | Cleanup response.json() method (#2911) | warn | **plausible** `httpx/_models.py:761` response.json() now ignores the declared Content-Type charset |
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
| hono | `7075369e` | Revert fix(etag) incremental hashing (#5199) | warn | **confirmed** `src/middleware/etag/digest.ts:33` Revert reintroduces wrong ETag for multi-chunk bodies |
| hono | `8a5852d4` | fix(etag): incremental hashing for chunked responses (#5199) | inform | none |
| hono | `a1946287` | fix(pattern-router/linear-router): prefix overmatch (#5252) | inform | none |
| hono | `a1e4ac7d` | perf(cors): pre-join static array header options (#5210) | inform | none |
| hono | `c409d855` | fix(request): serialize cached JSON body in cloneRawRequest (#5288) | inform | none |
