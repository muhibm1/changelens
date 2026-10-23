# Recall eval: `claude-code:claude-sonnet-5-5` with `--agent`, 50 bugs at the commit that introduced them, judged by `claude-sonnet-5-5` reading each label

| metric | value |
| --- | --- |
| cases | 50 |
| errors | 0 |
| caught | 12 |
| in file | 11 |
| inform only | 5 |
| missed | 22 |
| recall | 0.24 |
| recall in file | 0.46 |
| warns per case | 0.92 |
| tokens in | 494 |
| tokens cache write | 2219344 |
| tokens cache read | 2494224 |
| tokens out | 91881 |

| repo | commit | bug | outcome | finding |
| --- | --- | --- | --- | --- |
| httpx | `1805ee0d22` | verify=False returns before the client certificate is loaded, so cert= is ignored | **in_file** | httpx/_config.py:33 Unsupported verify values are returned unvalidated as the SSL context |
| httpx | `fd3b69d2aa` | RFC 2069 digest (no qop) hashes HA1 twice: response = H(HA1:HA1:nonce:HA2) | **missed** |  |
| httpx | `15d09a3bbc` | a NO_PROXY entry with a scheme (http://host) is treated as a hostname and never matches | **in_file** | httpx/_utils.py:268 IPv6 CIDR in NO_PROXY builds an invalid bracketed URL; IPv4 CIDR is not a range match |
| httpx | `27df5e49c7` | TextChunker.decode returns [''] for empty content, so iter_text yields an empty string | **in_file** | httpx/_models.py:1173 iter_bytes() raises ValueError on an already-read empty response |
| httpx | `b481166481` | with raise_app_exceptions=False an exception before the response completes is still raised | **inform_only** | inform `httpx/_transports/asgi.py:151` Unmentioned behaviour change in exception handling with raise_app_exceptions=False |
| httpx | `59914c7690` | NetRCAuth(file: Optional[str]) has no default, so NetRCAuth() is a TypeError | **caught** | warn `httpx/_auth.py:150` NetRCAuth.__init__ requires `file`, but docs call `NetRCAuth()` with no argument |
| httpx | `c725387b2d` | Headers.update assigns each raw pair, so a repeated header in the update keeps only its last value | **caught** | warn `httpx/_models.py:662` Headers.update drops repeated keys in the updating headers |
| httpx | `6f5865f860` | hasattr(stream, 'read') also matches SyncByteStream subclasses, whose read() takes no size argument | **missed** |  |
| hono | `bfc190f725` | the style replacement regex has no s flag, so multiline CSS is not matched | **missed** |  |
| hono | `09a81e4f01` | undefined children become [undefined], so a void element is cloned with a child | **in_file** | src/jsx/base.ts:431 cloneElement ignores children passed via props argument |
| hono | `9a6e52d734` | some() drops a Response returned by a short-circuiting middleware, so the context is never finalized | **in_file** | src/middleware/combine/index.ts:87 every(): a Condition returning true never calls next, so the chain stops |
| hono | `f356702efc` | retainedHeaders are compared lower-cased against the option's values as given, so a mixed-case option never matches | **in_file** | src/middleware/etag/index.ts:51 Deleting headers while iterating with forEach can skip entries |
| hono | `8b0f189a3f` | c.res.json() on an empty or invalid JSON body throws, turning the response into a 500 | **caught** | warn `src/middleware/pretty-json/index.ts:12` Re-parsing the response body can throw or leave a stale Content-Length |
| hono | `1a1de75296` | an empty path segment in the middle of a path (/u//posts) matches a :param with an empty value | **in_file** | src/router/linear-router/router.ts:42 Trailing '/*' route matches paths that only share a string prefix |
| hono | `28a9c12891` | media types and language tags are compared case-sensitively | **missed** |  |
| hono | `32ba5ba836` | rebuilding a cached body with new Response(body) loses the Content-Type, so formData() after text() throws | **in_file** | src/request.ts:216 cachedBody no longer skips the `parsedBody` cache entry |
| hono | `8b4392fa36` | streamToNodeStream ignores write() backpressure and resolves before the writable finishes | **in_file** | inform `src/adapter/aws-lambda/handler.ts:71` No backpressure handling or reader cleanup when streaming |
| hono | `2d3bc55954` | abort subscribers are called bare, so a throwing or rejecting listener stops the rest or is unhandled | **missed** |  |
| hono | `d7964503c9` | content-type is dropped for a re-serialized FormData but the stale content-length is kept | **missed** |  |
| hono | `350d904ad0` | defaultMatch only accepts exact type matches, so */* and type/* never match | **missed** |  |
| hono | `366f760a30` | an undefined entry in a form array is appended as the string 'undefined' | **missed** |  |
| hono | `091ecb7e86` | If-None-Match is split on /,\s*/, so whitespace before a comma leaves a trailing space on the tag | **missed** |  |
| hono | `b143c4ed8b` | OPTIONS is not a safe method, so CORS preflights are rejected by the CSRF check | **in_file** | src/middleware/csrf/index.ts:11 CSRF content-type check is case-sensitive and can be bypassed |
| hono | `d9b8b4b73b` | Vary is set rather than appended on OPTIONS, overwriting a Vary another middleware set | **missed** |  |
| hono | `331b3d8a96` | String.replace with a string replacement expands $ tokens in the param value | **missed** |  |
| hono | `ff34b1ba9a` | /\?/ matches a ? inside a regex quantifier, so :p{\d+?} is read as optional | **caught** | warn `src/utils/url.ts:134` Segment-splitting mishandles custom regex params containing '/' or '?' |
| hono | `cdb36b80af` | request cookies are joined with ',' and serialized with path=/; a Cookie header is '; '-joined name=value pairs | **caught** | warn `src/client/client.ts:100` Cookie request header built with Set-Cookie serialization and ',' separator |
| hono | `31f0605209` | pipe() releases the writer lock and re-acquires it only after pipeTo succeeds, so a throw leaves no writer | **missed** |  |
| hono | `7ce0ddd350` | m[0].match(/\//) without the g flag counts at most one slash | **caught** | warn `src/router/trie-router/node.ts:180` componentCount counts at most one slash, so queued child nodes resume at the wrong path segment |
| hono | `cd35af7655` | the subscription effect has no deps, so a changed subscribe or store is never picked up | **missed** |  |
| hono | `8130a67da1` | CSP callbacks run for both Content-Security-Policy and its Report-Only header | **caught** | block `src/middleware/secure-headers/secure-headers.ts:271` CSP nonce callbacks are applied to the wrong header when both CSP and Report-Only are set |
| hono | `1ce18c26dc` | truthiness checks drop retry: 0 and id: '' | **inform_only** | inform `src/helper/streaming/sse.ts:29` retry: 0 is silently dropped by truthiness check |
| hono | `12806614b9` | a FormData body is re-serialized with a new boundary while the old Content-Type header is kept | **caught** | warn `src/request.ts:472` Rebuilt multipart request keeps the old Content-Type boundary |
| hono | `9822548bf7` | new Request with a stream body needs duplex: 'half' | **caught** | warn `src/middleware/method-override/index.ts:115` Query override rebuilds Request with a stream body and no duplex option |
| hono | `6fe828ca0f` | existing Cache-Control directive names are compared case-sensitively | **inform_only** | inform `src/middleware/cache/index.ts:21` Existing Cache-Control directive names are not lowercased before comparison |
| hono | `0edb243a74` | if (content) treats an empty file as not found | **missed** |  |
| hono | `71cdcf40d8` | expandIPv6('::') removes one empty section, so a second empty slot is expanded again | **missed** |  |
| hono | `48dc87dfd5` | replace(/\\/, '/') converts only the first backslash | **caught** | warn `src/utils/filepath.ts:26` Backslash normalization replaces only the first backslash |
| hono | `e9830c6ba1` | (Date.now() / 1000) \| 0 truncates to 32 bits, so timestamps past 2038 wrap | **inform_only** | inform `src/utils/jwt/jwt.ts:77` `\| 0` truncates to int32, so JWT time checks break after 2038-01-19 |
| hono | `1854e24145` | after splicing an array child in place the index still advances, skipping the next child | **in_file** | src/jsx/dom/render.ts:471 build splices flattened children into the caller's children array |
| hono | `778ac10e84` | getPath stops at ? but not #, so a fragment ends up in the path | **missed** |  |
| hono | `66fc63d134` | the Bearer prefix is matched case-sensitively | **missed** |  |
| hono | `7768865e7a` | SSE data is split on \n only, so \r and \r\n line endings are not framed | **missed** |  |
| hono | `6d18f17e58` | app.fetch(request, evt) passes the FetchEvent as Env instead of as the execution context | **missed** |  |
| hono | `acb94b15fa` | safeEncodeURI decides by decodeURI(str) === str, which mis-handles a literal % and some encoded input | **caught** | warn `src/utils/url.ts:323` safeEncodeURI double-encodes reserved escapes like %2F, %3F, %23, %26 |
| hono | `a16bcda120` | decodeURIComponent on an invalid query value throws instead of returning the raw value | **missed** |  |
| cobra | `b80aeb17fc` | append on a sub-slice of args writes into the caller's backing array (os.Args) | **missed** |  |
| cobra | `36aee64abe` | argsMinusFirstX removes the first arg equal to the subcommand name even when it is a flag's value | **missed** |  |
| cobra | `21f39ca07e` | the read loop reuses $c, clobbering the global $c that *_custom_func reads | **inform_only** | inform `bash_completions.go:75` Loop variable `c` overwrites the word-index variable used by the completion parser |
| cobra | `6fd8e29b07` | declare -F ___custom_func has three underscores, so the fallback to __custom_func never runs | **caught** | warn `bash_completions.go:137` Fallback checks `___custom_func` (three underscores), so the unqualified fallback never runs |
