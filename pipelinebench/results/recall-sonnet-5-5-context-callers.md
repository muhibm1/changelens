# Recall eval: `anthropic`, 50 bugs at the commit that introduced them

| metric | value |
| --- | --- |
| cases | 50 |
| errors | 0 |
| caught | 21 |
| in file | 8 |
| inform only | 5 |
| missed | 16 |
| recall | 0.42 |
| recall in file | 0.58 |
| warns per case | 1.36 |
| tokens in | 1316983 |
| tokens out | 37342 |

| repo | commit | bug | outcome | finding |
| --- | --- | --- | --- | --- |
| httpx | `1805ee0d22` | verify=False returns before the client certificate is loaded, so cert= is ignored | **caught** | warn `httpx/_config.py:26` Default context drops SSLKEYLOGFILE support and the stricter verify flags |
| httpx | `fd3b69d2aa` | RFC 2069 digest (no qop) hashes HA1 twice: response = H(HA1:HA1:nonce:HA2) | **missed** |  |
| httpx | `15d09a3bbc` | a NO_PROXY entry with a scheme (http://host) is treated as a hostname and never matches | **caught** | warn `httpx/_utils.py:265` CIDR entries in NO_PROXY are mounted as literal URL patterns and never match |
| httpx | `27df5e49c7` | TextChunker.decode returns [''] for empty content, so iter_text yields an empty string | **in_file** | httpx/_models.py:1173 Empty cached content with chunk_size=None raises ValueError; also chunk_size=0 fails |
| httpx | `b481166481` | with raise_app_exceptions=False an exception before the response completes is still raised | **inform_only** | inform `httpx/_transports/asgi.py:151` Condition changed from truthiness of Event to is_set() |
| httpx | `59914c7690` | NetRCAuth(file: Optional[str]) has no default, so NetRCAuth() is a TypeError | **caught** | warn `httpx/_auth.py:150` NetRCAuth `file` argument is required, contradicting the documented default usage |
| httpx | `c725387b2d` | Headers.update assigns each raw pair, so a repeated header in the update keeps only its last value | **caught** | warn `httpx/_models.py:662` update() decodes raw bytes with headers.encoding, possibly inconsistent |
| httpx | `6f5865f860` | hasattr(stream, 'read') also matches SyncByteStream subclasses, whose read() takes no size argument | **inform_only** | inform `httpx/_content.py:53` Objects with a `read` attribute are read via read(CHUNK_SIZE) even if text-mode or non-file iterables |
| hono | `bfc190f725` | the style replacement regex has no s flag, so multiline CSS is not matched | **in_file** | src/helper/css/index.ts:164 Unescaped nonce interpolated into HTML attributes |
| hono | `09a81e4f01` | undefined children become [undefined], so a void element is cloned with a child | **caught** | warn `src/jsx/base.ts:431` Element with no children gets a spurious undefined child |
| hono | `9a6e52d734` | some() drops a Response returned by a short-circuiting middleware, so the context is never finalized | **in_file** | inform `src/middleware/combine/index.ts:39` Condition returning true inside every() leaves downstream chain unexecuted |
| hono | `f356702efc` | retainedHeaders are compared lower-cased against the option's values as given, so a mixed-case option never matches | **in_file** | deno_dist/middleware/etag/index.ts:51 Headers deleted while iterating the same Headers object (deno dist); src/middleware/etag/index.ts:51 Headers deleted while iterating the same Headers object |
| hono | `8b0f189a3f` | c.res.json() on an empty or invalid JSON body throws, turning the response into a 500 | **caught** | warn `src/middleware/pretty-json/index.ts:12` Middleware reads and re-serializes the response body unconditionally |
| hono | `1a1de75296` | an empty path segment in the middle of a path (/u//posts) matches a :param with an empty value | **caught** | warn `src/router/linear-router/router.ts:90` Custom regex pattern is unanchored and constructed on every match |
| hono | `28a9c12891` | media types and language tags are compared case-sensitively | **in_file** | inform `src/helper/accepts/accepts.ts:29` Subtype wildcard match is case-sensitive and ignores non-media headers |
| hono | `32ba5ba836` | rebuilding a cached body with new Response(body) loses the Content-Type, so formData() after text() throws | **inform_only** | inform `src/request.ts:216` parsedBody key now picked as fallback source in cachedBody |
| hono | `8b4392fa36` | streamToNodeStream ignores write() backpressure and resolves before the writable finishes | **caught** | warn `src/adapter/aws-lambda/handler.ts:73` Stream writes ignore backpressure and are not cleaned up on error |
| hono | `2d3bc55954` | abort subscribers are called bare, so a throwing or rejecting listener stops the rest or is unhandled | **caught** | warn `src/utils/stream.ts:88` Abort subscribers' errors and rejections are unhandled |
| hono | `d7964503c9` | content-type is dropped for a re-serialized FormData but the stale content-length is kept | **missed** |  |
| hono | `350d904ad0` | defaultMatch only accepts exact type matches, so */* and type/* never match | **missed** |  |
| hono | `366f760a30` | an undefined entry in a form array is appended as the string 'undefined' | **missed** |  |
| hono | `091ecb7e86` | If-None-Match is split on /,\s*/, so whitespace before a comma leaves a trailing space on the tag | **missed** |  |
| hono | `b143c4ed8b` | OPTIONS is not a safe method, so CORS preflights are rejected by the CSRF check | **in_file** | src/middleware/csrf/index.ts:12 Content-Type regex is case-sensitive, so it can be bypassed |
| hono | `d9b8b4b73b` | Vary is set rather than appended on OPTIONS, overwriting a Vary another middleware set | **missed** |  |
| hono | `331b3d8a96` | String.replace with a string replacement expands $ tokens in the param value | **caught** | warn `src/client/utils.ts:13` Falsy check drops empty-string params and removes required params |
| hono | `ff34b1ba9a` | /\?/ matches a ? inside a regex quantifier, so :p{\d+?} is read as optional | **caught** | warn `src/utils/url.ts:128` Loose optional-parameter guard accepts paths the old regex rejected and mishandles regex-constrained params |
| hono | `cdb36b80af` | request cookies are joined with ',' and serialized with path=/; a Cookie header is '; '-joined name=value pairs | **caught** | warn `src/client/client.ts:100` Cookie header joined with comma and each cookie serialized with Set-Cookie attributes |
| hono | `31f0605209` | pipe() releases the writer lock and re-acquires it only after pipeTo succeeds, so a throw leaves no writer | **caught** | warn `src/utils/stream.ts:33` pipe() loses the writer if pipeTo fails |
| hono | `7ce0ddd350` | m[0].match(/\//) without the g flag counts at most one slash | **caught** | warn `src/router/trie-router/node.ts:180` componentCount uses match() without global flag, so it is always 0 or 1 |
| hono | `cd35af7655` | the subscription effect has no deps, so a changed subscribe or store is never picked up | **inform_only** | inform `src/jsx/hooks/index.ts:400` useSyncExternalStore now throws during string rendering without getServerSnapshot |
| hono | `8130a67da1` | CSP callbacks run for both Content-Security-Policy and its Report-Only header | **caught** | block `src/middleware/secure-headers/secure-headers.ts:272` CSP callbacks are applied to both CSP headers, which throws or corrupts values |
| hono | `1ce18c26dc` | truthiness checks drop retry: 0 and id: '' | **inform_only** | inform `src/helper/streaming/sse.ts:29` retry: 0 is silently dropped by truthiness check |
| hono | `12806614b9` | a FormData body is re-serialized with a new boundary while the old Content-Type header is kept | **caught** | warn `src/request.ts:463` Rebuilt body uses first cached key, which may not be the original body or can be a parsed value |
| hono | `9822548bf7` | new Request with a stream body needs duplex: 'half' | **caught** | warn `src/middleware/method-override/index.ts:115` Query override reuses the body stream and may fail on Node |
| hono | `6fe828ca0f` | existing Cache-Control directive names are compared case-sensitively | **in_file** | src/middleware/cache/index.ts:23 Directive with '=' in value is truncated by split limit |
| hono | `0edb243a74` | if (content) treats an empty file as not found | **missed** |  |
| hono | `71cdcf40d8` | expandIPv6('::') removes one empty section, so a second empty slot is expanded again | **missed** |  |
| hono | `48dc87dfd5` | replace(/\\/, '/') converts only the first backslash | **caught** | warn `src/utils/filepath.ts:26` Backslash replacement lacks global flag, only first backslash converted |
| hono | `e9830c6ba1` | (Date.now() / 1000) \| 0 truncates to 32 bits, so timestamps past 2038 wrap | **missed** |  |
| hono | `1854e24145` | after splicing an array child in place the index still advances, skipping the next child | **missed** |  |
| hono | `778ac10e84` | getPath stops at ? but not #, so a fragment ends up in the path | **missed** |  |
| hono | `66fc63d134` | the Bearer prefix is matched case-sensitively | **in_file** | src/middleware/bearer-auth/index.ts:73 Prefix is interpolated into RegExp unescaped, now compiled once at setup |
| hono | `7768865e7a` | SSE data is split on \n only, so \r and \r\n line endings are not framed | **missed** |  |
| hono | `6d18f17e58` | app.fetch(request, evt) passes the FetchEvent as Env instead of as the execution context | **missed** |  |
| hono | `acb94b15fa` | safeEncodeURI decides by decodeURI(str) === str, which mis-handles a literal % and some encoded input | **caught** | warn `src/utils/url.ts:323` safeEncodeURI can corrupt URLs with reserved-character escapes such as %2F, %23, %3F |
| hono | `a16bcda120` | decodeURIComponent on an invalid query value throws instead of returning the raw value | **missed** |  |
| cobra | `b80aeb17fc` | append on a sub-slice of args writes into the caller's backing array (os.Args) | **missed** |  |
| cobra | `36aee64abe` | argsMinusFirstX removes the first arg equal to the subcommand name even when it is a flag's value | **missed** |  |
| cobra | `21f39ca07e` | the read loop reuses $c, clobbering the global $c that *_custom_func reads | **caught** | warn `bash_completions.go:75` COMPREPLY appended to rather than replaced; `c` leaks as global |
| cobra | `6fd8e29b07` | declare -F ___custom_func has three underscores, so the fallback to __custom_func never runs | **caught** | block `bash_completions.go:137` Fallback checks for nonexistent `___custom_func` (three underscores) |
