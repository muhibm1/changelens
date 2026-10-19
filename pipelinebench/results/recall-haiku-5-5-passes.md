# Recall eval: `anthropic:claude-haiku-5-5`, 50 bugs at the commit that introduced them, judged by `claude-haiku-5-5` reading each label

| metric | value |
| --- | --- |
| cases | 50 |
| errors | 0 |
| caught | 21 |
| in file | 14 |
| inform only | 1 |
| missed | 14 |
| recall | 0.42 |
| recall in file | 0.7 |
| warns per case | 2.06 |
| tokens in | 584559 |
| tokens cache write | 677435 |
| tokens cache read | 2539829 |
| tokens out | 520971 |

| repo | commit | bug | outcome | finding |
| --- | --- | --- | --- | --- |
| httpx | `1805ee0d22` | verify=False returns before the client certificate is loaded, so cert= is ignored | **in_file** | httpx/_config.py:26 verify=True no longer honours SSLKEYLOGFILE; httpx/_config.py:33 Non-bool, non-context verify values are returned unchecked and fail later inside httpcore |
| httpx | `fd3b69d2aa` | RFC 2069 digest (no qop) hashes HA1 twice: response = H(HA1:HA1:nonce:HA2) | **missed** |  |
| httpx | `15d09a3bbc` | a NO_PROXY entry with a scheme (http://host) is treated as a hostname and never matches | **in_file** | httpx/_utils.py:266 CIDR entries in NO_PROXY are stored as literal host patterns and never match; httpx/_utils.py:267 Bracketed IPv6 entries in NO_PROXY are not recognised as IPv6; httpx/_utils.py:268 IPv6 CIDR entries produce a malformed bracketed mount key |
| httpx | `27df5e49c7` | TextChunker.decode returns [''] for empty content, so iter_text yields an empty string | **in_file** | httpx/_models.py:1173 iter_bytes on an already-read empty body raises ValueError; httpx/_models.py:1173 Negative or zero chunk_size silently drops content or errors on the _content path |
| httpx | `b481166481` | with raise_app_exceptions=False an exception before the response completes is still raised | **caught** | warn `httpx/_transports/asgi.py:151` Error path now re-raises app exceptions with raise_app_exceptions=False, which the refactor intent does not mention |
| httpx | `59914c7690` | NetRCAuth(file: Optional[str]) has no default, so NetRCAuth() is a TypeError | **missed** |  |
| httpx | `c725387b2d` | Headers.update assigns each raw pair, so a repeated header in the update keeps only its last value | **caught** | block `httpx/_models.py:663` Headers.update drops earlier values of repeated keys |
| httpx | `6f5865f860` | hasattr(stream, 'read') also matches SyncByteStream subclasses, whose read() takes no size argument | **caught** | warn `httpx/_content.py:53` Iterables with a read() that takes no size argument now fail on upload |
| hono | `bfc190f725` | the style replacement regex has no s flag, so multiline CSS is not matched | **in_file** | src/helper/css/index.ts:92 Script nonce is only known if the Style callback has already run for this context; src/helper/css/index.ts:164 Style nonce is interpolated into HTML without escaping |
| hono | `09a81e4f01` | undefined children become [undefined], so a void element is cloned with a child | **in_file** | src/jsx/base.ts:430 cloneElement now ignores children passed in props; src/jsx/base.ts:436 Explicit props.children is silently discarded when no extra children are passed |
| hono | `9a6e52d734` | some() drops a Response returned by a short-circuiting middleware, so the context is never finalized | **in_file** | src/middleware/combine/index.ts:36 some() with no middleware succeeds silently; src/middleware/combine/index.ts:40 some() swallows downstream errors when a condition calls next(); src/middleware/combine/index.ts:52 some() drops falsy thrown values; src/middleware/combine/index.ts:88 every() with a Condition returning true never runs the remaining middleware |
| hono | `f356702efc` | retainedHeaders are compared lower-cased against the option's values as given, so a mixed-case option never matches | **caught** | warn `src/middleware/etag/index.ts:52` retainedHeaders entries are compared case-sensitively against lowercased names |
| hono | `8b0f189a3f` | c.res.json() on an empty or invalid JSON body throws, turning the response into a 500 | **caught** | warn `src/middleware/pretty-json/index.ts:12` Pretty-JSON middleware throws on any application/json response whose body is not valid JSON |
| hono | `1a1de75296` | an empty path segment in the middle of a path (/u//posts) matches a :param with an empty value | **in_file** | src/router/linear-router/router.ts:54 Wildcard route matches a path that only shares a prefix with it; src/router/linear-router/router.ts:71 Literal text after a label is silently dropped from the route; src/router/linear-router/router.ts:90 Label regex is matched as a prefix, not as the whole segment; src/router/linear-router/router.ts:110 Empty param value bypasses the duplicate-name check |
| hono | `28a9c12891` | media types and language tags are compared case-sensitively | **in_file** | src/helper/accepts/accepts.ts:56 Media types with q=0 are still selected |
| hono | `32ba5ba836` | rebuilding a cached body with new Response(body) loses the Content-Type, so formData() after text() throws | **in_file** | src/request.ts:218 json() and other body readers throw after parseBody() has run |
| hono | `8b4392fa36` | streamToNodeStream ignores write() backpressure and resolves before the writable finishes | **caught** | warn `src/adapter/aws-lambda/handler.ts:73` Write return value ignored, so backpressure is never applied |
| hono | `2d3bc55954` | abort subscribers are called bare, so a throwing or rejecting listener stops the rest or is unhandled | **missed** |  |
| hono | `d7964503c9` | content-type is dropped for a re-serialized FormData but the stale content-length is kept | **caught** | warn `src/request.ts:490` Stale Content-Length is kept when the FormData body is re-serialized |
| hono | `350d904ad0` | defaultMatch only accepts exact type matches, so */* and type/* never match | **missed** |  |
| hono | `366f760a30` | an undefined entry in a form array is appended as the string 'undefined' | **in_file** | src/client/client.ts:71 Empty array form values are now dropped instead of being sent |
| hono | `091ecb7e86` | If-None-Match is split on /,\s*/, so whitespace before a comma leaves a trailing space on the tag | **missed** |  |
| hono | `b143c4ed8b` | OPTIONS is not a safe method, so CORS preflights are rejected by the CSRF check | **in_file** | src/middleware/csrf/index.ts:37 Content-type check is case-sensitive, so cross-origin form-type requests can bypass the CSRF check |
| hono | `d9b8b4b73b` | Vary is set rather than appended on OPTIONS, overwriting a Vary another middleware set | **inform_only** | inform `src/middleware/cors/index.ts:119` Preflight Vary set overwrites Vary values from earlier middleware |
| hono | `331b3d8a96` | String.replace with a string replacement expands $ tokens in the param value | **caught** | warn `src/client/utils.ts:13` Param values are used as a String.replace replacement pattern |
| hono | `ff34b1ba9a` | /\?/ matches a ? inside a regex quantifier, so :p{\d+?} is read as optional | **caught** | warn `src/utils/url.ts:138` Regex quantifier '?' inside a parameter is treated as an optional marker |
| hono | `cdb36b80af` | request cookies are joined with ',' and serialized with path=/; a Cookie header is '; '-joined name=value pairs | **caught** | block `src/client/client.ts:100` Multiple cookies are joined with ',' instead of '; ', so all but the first are lost or corrupted |
| hono | `31f0605209` | pipe() releases the writer lock and re-acquires it only after pipeTo succeeds, so a throw leaves no writer | **caught** | warn `src/utils/stream.ts:35` pipe() leaves the writer released if the source stream errors |
| hono | `7ce0ddd350` | m[0].match(/\//) without the g flag counts at most one slash | **caught** | block `src/router/trie-router/node.ts:180` Slash count for greedy match is always 0 or 1, so the child is resumed at the wrong path segment |
| hono | `cd35af7655` | the subscription effect has no deps, so a changed subscribe or store is never picked up | **missed** |  |
| hono | `8130a67da1` | CSP callbacks run for both Content-Security-Policy and its Report-Only header | **caught** | block `src/middleware/secure-headers/secure-headers.ts:269` Nonce callbacks of one CSP policy are applied to the other policy's header, corrupting the enforced CSP |
| hono | `1ce18c26dc` | truthiness checks drop retry: 0 and id: '' | **caught** | warn `src/helper/streaming/sse.ts:29` retry: 0 is silently dropped |
| hono | `12806614b9` | a FormData body is re-serialized with a new boundary while the old Content-Type header is kept | **caught** | warn `src/request.ts:475` Original Content-Type header is copied onto a re-encoded FormData body |
| hono | `9822548bf7` | new Request with a stream body needs duplex: 'half' | **caught** | warn `src/middleware/method-override/index.ts:116` Query override forwards a streaming body without duplex, which fails in Node |
| hono | `6fe828ca0f` | existing Cache-Control directive names are compared case-sensitively | **caught** | warn `src/middleware/cache/index.ts:21` Existing Cache-Control names are compared case-sensitively |
| hono | `0edb243a74` | if (content) treats an empty file as not found | **missed** |  |
| hono | `71cdcf40d8` | expandIPv6('::') removes one empty section, so a second empty slot is expanded again | **in_file** | src/middleware/ip-restriction/index.ts:92 Static-rule lookup compares the raw remote address against normalized IPv6 rules; src/middleware/ip-restriction/index.ts:157 IPv4-mapped IPv6 client addresses bypass IPv4 rules; src/utils/ipaddr.ts:109 convertIPv6BinaryToString returns ':' for the all-zero address |
| hono | `48dc87dfd5` | replace(/\\/, '/') converts only the first backslash | **caught** | warn `src/utils/filepath.ts:26` Only the first backslash is converted to a slash |
| hono | `e9830c6ba1` | (Date.now() / 1000) \| 0 truncates to 32 bits, so timestamps past 2038 wrap | **caught** | block `src/utils/jwt/jwt.ts:77` `\| 0` truncates the JWT clock to int32 and breaks token expiry checks from 2038 |
| hono | `1854e24145` | after splicing an array child in place the index still advances, skipping the next child | **in_file** | src/jsx/dom/intrinsic-element/components.ts:238 script/link/style now throw TypeError when called with falsy props; src/jsx/dom/render.ts:129 toAttributeName is gated on nameSpaceContext, which may be unset when attributes are applied; src/jsx/dom/render.ts:243 Components now receive the element's own props object and can mutate it; src/jsx/dom/render.ts:299 findInsertBefore loses its fallback to the next sibling; src/jsx/dom/render.ts:471 build flattens the caller's children array in place; src/jsx/dom/render.ts:545 nN sibling link is skipped when the previous sibling is skipped, so later inserts go to the wrong place |
| hono | `778ac10e84` | getPath stops at ? but not #, so a fragment ends up in the path | **in_file** | src/utils/url.ts:85 Fallback decoding leaves valid UTF-8 undecoded when it is adjacent to an invalid byte |
| hono | `66fc63d134` | the Bearer prefix is matched case-sensitively | **missed** |  |
| hono | `7768865e7a` | SSE data is split on \n only, so \r and \r\n line endings are not framed | **missed** |  |
| hono | `6d18f17e58` | app.fetch(request, evt) passes the FetchEvent as Env instead of as the execution context | **missed** |  |
| hono | `acb94b15fa` | safeEncodeURI decides by decodeURI(str) === str, which mis-handles a literal % and some encoded input | **caught** | warn `src/context.ts:750` Redirect locations with percent-encoded non-UTF-8 bytes are now double-encoded |
| hono | `a16bcda120` | decodeURIComponent on an invalid query value throws instead of returning the raw value | **missed** |  |
| cobra | `b80aeb17fc` | append on a sub-slice of args writes into the caller's backing array (os.Args) | **missed** |  |
| cobra | `36aee64abe` | argsMinusFirstX removes the first arg equal to the subcommand name even when it is a flag's value | **missed** |  |
| cobra | `21f39ca07e` | the read loop reuses $c, clobbering the global $c that *_custom_func reads | **missed** |  |
| cobra | `6fd8e29b07` | declare -F ___custom_func has three underscores, so the fallback to __custom_func never runs | **caught** | block `bash_completions.go:137` Unqualified fallback checks the wrong function name and never runs |
