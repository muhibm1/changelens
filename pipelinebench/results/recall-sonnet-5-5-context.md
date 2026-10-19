# Recall eval: `anthropic`, 50 bugs at the commit that introduced them

| metric | value |
| --- | --- |
| cases | 50 |
| errors | 0 |
| caught | 22 |
| in file | 4 |
| inform only | 8 |
| missed | 16 |
| recall | 0.44 |
| recall in file | 0.52 |
| warns per case | 1.24 |
| tokens in | 1300068 |
| tokens out | 36886 |

| repo | commit | bug | outcome | finding |
| --- | --- | --- | --- | --- |
| httpx | `1805ee0d22` | verify=False returns before the client certificate is loaded, so cert= is ignored | **caught** | warn `httpx/_config.py:25` Default verify=True context loses stricter flags and SSLKEYLOGFILE support |
| httpx | `fd3b69d2aa` | RFC 2069 digest (no qop) hashes HA1 twice: response = H(HA1:HA1:nonce:HA2) | **missed** |  |
| httpx | `15d09a3bbc` | a NO_PROXY entry with a scheme (http://host) is treated as a hostname and never matches | **caught** | warn `httpx/_utils.py:265` CIDR NO_PROXY entries are mounted as literal host patterns and never match |
| httpx | `27df5e49c7` | TextChunker.decode returns [''] for empty content, so iter_text yields an empty string | **in_file** | httpx/_models.py:1173 iter_bytes/aiter_bytes crash or misbehave on already-read content with edge-case chunk sizes |
| httpx | `b481166481` | with raise_app_exceptions=False an exception before the response completes is still raised | **inform_only** | inform `httpx/_transports/asgi.py:151` Exception-handling condition changed from truthiness to is_set() |
| httpx | `59914c7690` | NetRCAuth(file: Optional[str]) has no default, so NetRCAuth() is a TypeError | **caught** | warn `httpx/_auth.py:150` NetRCAuth `file` argument is required, contradicting the docs |
| httpx | `c725387b2d` | Headers.update assigns each raw pair, so a repeated header in the update keeps only its last value | **missed** |  |
| httpx | `6f5865f860` | hasattr(stream, 'read') also matches SyncByteStream subclasses, whose read() takes no size argument | **inform_only** | inform `httpx/_content.py:53` Any iterable with a `read` attribute is now read via read(CHUNK_SIZE), ignoring its iteration |
| hono | `bfc190f725` | the style replacement regex has no s flag, so multiline CSS is not matched | **missed** |  |
| hono | `09a81e4f01` | undefined children become [undefined], so a void element is cloned with a child | **caught** | warn `src/jsx/base.ts:431` Element without children is cloned with a spurious undefined child |
| hono | `9a6e52d734` | some() drops a Response returned by a short-circuiting middleware, so the context is never finalized | **caught** | warn `src/middleware/combine/index.ts:38` some() can call next() twice when a middleware calls next and then throws or returns false |
| hono | `f356702efc` | retainedHeaders are compared lower-cased against the option's values as given, so a mixed-case option never matches | **caught** | warn `src/middleware/etag/index.ts:51` Deleting headers while iterating Headers.forEach can skip entries |
| hono | `8b0f189a3f` | c.res.json() on an empty or invalid JSON body throws, turning the response into a 500 | **caught** | warn `src/middleware/pretty-json/index.ts:12` Pretty-print post-processing can throw on invalid JSON bodies and consumes body of shared response |
| hono | `1a1de75296` | an empty path segment in the middle of a path (/u//posts) matches a :param with an empty value | **in_file** | inform `src/router/linear-router/router.ts:90` Regexp label pattern is not anchored at the end and can leak across segments |
| hono | `28a9c12891` | media types and language tags are compared case-sensitively | **caught** | warn `src/helper/accepts/accepts.ts:29` Subtype wildcard matching applies to non-media headers and ignores q=0 |
| hono | `32ba5ba836` | rebuilding a cached body with new Response(body) loses the Content-Type, so formData() after text() throws | **caught** | warn `src/request.ts:216` Removed skip of 'parsedBody' key in cachedBody fallback |
| hono | `8b4392fa36` | streamToNodeStream ignores write() backpressure and resolves before the writable finishes | **caught** | warn `src/adapter/aws-lambda/handler.ts:73` Stream writes ignore backpressure and errors, and the stream is ended twice |
| hono | `2d3bc55954` | abort subscribers are called bare, so a throwing or rejecting listener stops the rest or is unhandled | **inform_only** | inform `src/utils/stream.ts:88` Async abort subscriber rejections are unhandled and sync throws stop other subscribers |
| hono | `d7964503c9` | content-type is dropped for a re-serialized FormData but the stale content-length is kept | **missed** |  |
| hono | `350d904ad0` | defaultMatch only accepts exact type matches, so */* and type/* never match | **missed** |  |
| hono | `366f760a30` | an undefined entry in a form array is appended as the string 'undefined' | **inform_only** | inform `src/client/client.ts:71` Array form values only handled at the top level; array elements typed as string only |
| hono | `091ecb7e86` | If-None-Match is split on /,\s*/, so whitespace before a comma leaves a trailing space on the tag | **missed** |  |
| hono | `b143c4ed8b` | OPTIONS is not a safe method, so CORS preflights are rejected by the CSRF check | **in_file** | src/middleware/csrf/index.ts:37 Content-Type check is case-sensitive, so mixed-case values bypass CSRF protection |
| hono | `d9b8b4b73b` | Vary is set rather than appended on OPTIONS, overwriting a Vary another middleware set | **missed** |  |
| hono | `331b3d8a96` | String.replace with a string replacement expands $ tokens in the param value | **caught** | warn `src/client/utils.ts:13` Falsy check drops empty-string params and removes required segments |
| hono | `ff34b1ba9a` | /\?/ matches a ? inside a regex quantifier, so :p{\d+?} is read as optional | **caught** | warn `src/utils/url.ts:128` Loose guard regex lets non-trailing optional segments through |
| hono | `cdb36b80af` | request cookies are joined with ',' and serialized with path=/; a Cookie header is '; '-joined name=value pairs | **caught** | warn `src/client/client.ts:100` Cookie header joined with commas instead of semicolons |
| hono | `31f0605209` | pipe() releases the writer lock and re-acquires it only after pipeTo succeeds, so a throw leaves no writer | **missed** |  |
| hono | `7ce0ddd350` | m[0].match(/\//) without the g flag counts at most one slash | **caught** | warn `src/router/trie-router/node.ts:180` Slash count uses match() without g flag, so it is always 0 or 1 |
| hono | `cd35af7655` | the subscription effect has no deps, so a changed subscribe or store is never picked up | **inform_only** | inform `src/jsx/hooks/index.ts:399` Throws when no build context, breaking string rendering without getServerSnapshot |
| hono | `8130a67da1` | CSP callbacks run for both Content-Security-Policy and its Report-Only header | **caught** | block `src/middleware/secure-headers/secure-headers.ts:271` CSP callbacks are applied to both CSP headers, corrupting or crashing when both are set |
| hono | `1ce18c26dc` | truthiness checks drop retry: 0 and id: '' | **missed** |  |
| hono | `12806614b9` | a FormData body is re-serialized with a new boundary while the old Content-Type header is kept | **caught** | warn `src/request.ts:463` Rebuilt body uses first cache key, which may be a re-serialized/lossy form |
| hono | `9822548bf7` | new Request with a stream body needs duplex: 'half' | **caught** | warn `src/middleware/method-override/index.ts:115` Query override builds Request with a body for the new method |
| hono | `6fe828ca0f` | existing Cache-Control directive names are compared case-sensitively | **caught** | warn `src/middleware/cache/index.ts:21` Existing directive names are not lowercased before comparison |
| hono | `0edb243a74` | if (content) treats an empty file as not found | **inform_only** | inform `src/middleware/serve-static/index.ts:110` Precompressed lookup now skipped for types the regex deems incompressible |
| hono | `71cdcf40d8` | expandIPv6('::') removes one empty section, so a second empty slot is expanded again | **missed** |  |
| hono | `48dc87dfd5` | replace(/\\/, '/') converts only the first backslash | **caught** | warn `src/utils/filepath.ts:26` Backslash normalization replaces only the first backslash |
| hono | `e9830c6ba1` | (Date.now() / 1000) \| 0 truncates to 32 bits, so timestamps past 2038 wrap | **missed** |  |
| hono | `1854e24145` | after splicing an array child in place the index still advances, skipping the next child | **in_file** | src/jsx/dom/render.ts:299 findInsertBefore loop skips portal subtree differently from original; src/jsx/dom/render.ts:545 nN links not updated when previous or current child is marked skip |
| hono | `778ac10e84` | getPath stops at ? but not #, so a fragment ends up in the path | **inform_only** | inform `src/utils/url.ts:107` Path decoded in getPath is decoded again by param(), and decodeURI leaves reserved chars encoded |
| hono | `66fc63d134` | the Bearer prefix is matched case-sensitively | **inform_only** | inform `src/middleware/bearer-auth/index.ts:67` Prefix null or other falsy values no longer default to Bearer |
| hono | `7768865e7a` | SSE data is split on \n only, so \r and \r\n line endings are not framed | **missed** |  |
| hono | `6d18f17e58` | app.fetch(request, evt) passes the FetchEvent as Env instead of as the execution context | **missed** |  |
| hono | `acb94b15fa` | safeEncodeURI decides by decodeURI(str) === str, which mis-handles a literal % and some encoded input | **caught** | warn `src/utils/url.ts:323` safeEncodeURI changes redirect targets that contain reserved escapes |
| hono | `a16bcda120` | decodeURIComponent on an invalid query value throws instead of returning the raw value | **missed** |  |
| cobra | `b80aeb17fc` | append on a sub-slice of args writes into the caller's backing array (os.Args) | **missed** |  |
| cobra | `36aee64abe` | argsMinusFirstX removes the first arg equal to the subcommand name even when it is a flag's value | **missed** |  |
| cobra | `21f39ca07e` | the read loop reuses $c, clobbering the global $c that *_custom_func reads | **caught** | warn `bash_completions.go:75` COMPREPLY now appended instead of assigned; stale entries retained and `c` leaks as global |
| cobra | `6fd8e29b07` | declare -F ___custom_func has three underscores, so the fallback to __custom_func never runs | **caught** | block `bash_completions.go:137` Fallback checks for `___custom_func` (three underscores) instead of `__custom_func` |
