# Recall eval: `anthropic:claude-haiku-5-5`, 50 bugs at the commit that introduced them, judged by `claude-haiku-5-5` reading each label

| metric | value |
| --- | --- |
| cases | 50 |
| errors | 0 |
| caught | 9 |
| in file | 12 |
| inform only | 3 |
| missed | 26 |
| recall | 0.18 |
| recall in file | 0.42 |
| warns per case | 0.9 |
| tokens in | 1083019 |
| tokens cache write | 0 |
| tokens cache read | 113100 |
| tokens out | 102710 |

| repo | commit | bug | outcome | finding |
| --- | --- | --- | --- | --- |
| httpx | `1805ee0d22` | verify=False returns before the client certificate is loaded, so cert= is ignored | **in_file** | tests/test_config.py:25 SSLKEYLOGFILE support was dropped from the default SSL context; httpx/_config.py:33 Non-bool, non-SSLContext verify values are returned unchanged |
| httpx | `fd3b69d2aa` | RFC 2069 digest (no qop) hashes HA1 twice: response = H(HA1:HA1:nonce:HA2) | **missed** |  |
| httpx | `15d09a3bbc` | a NO_PROXY entry with a scheme (http://host) is treated as a hostname and never matches | **in_file** | httpx/_utils.py:268 IPv6 CIDR entries in NO_PROXY produce a malformed bypass pattern |
| httpx | `27df5e49c7` | TextChunker.decode returns [''] for empty content, so iter_text yields an empty string | **in_file** | httpx/_models.py:1173 iter_bytes/aiter_bytes raise ValueError for an empty body that was already read |
| httpx | `b481166481` | with raise_app_exceptions=False an exception before the response completes is still raised | **inform_only** | inform `httpx/_transports/asgi.py:151` Exception handling condition changed beyond the stated refactor |
| httpx | `59914c7690` | NetRCAuth(file: Optional[str]) has no default, so NetRCAuth() is a TypeError | **missed** |  |
| httpx | `c725387b2d` | Headers.update assigns each raw pair, so a repeated header in the update keeps only its last value | **caught** | warn `httpx/_models.py:663` update() now overwrites repeated header values instead of joining them |
| httpx | `6f5865f860` | hasattr(stream, 'read') also matches SyncByteStream subclasses, whose read() takes no size argument | **missed** |  |
| hono | `bfc190f725` | the style replacement regex has no s flag, so multiline CSS is not matched | **missed** |  |
| hono | `09a81e4f01` | undefined children become [undefined], so a void element is cloned with a child | **in_file** | src/jsx/base.ts:430 Explicit children in props are ignored when no child arguments are passed |
| hono | `9a6e52d734` | some() drops a Response returned by a short-circuiting middleware, so the context is never finalized | **missed** |  |
| hono | `f356702efc` | retainedHeaders are compared lower-cased against the option's values as given, so a mixed-case option never matches | **in_file** | src/middleware/etag/index.ts:44 304 response is built from scratch, so retained upstream headers are never copied |
| hono | `8b0f189a3f` | c.res.json() on an empty or invalid JSON body throws, turning the response into a 500 | **caught** | warn `src/middleware/pretty-json/index.ts:12` Pretty-json middleware throws on JSON-typed responses with an empty body |
| hono | `1a1de75296` | an empty path segment in the middle of a path (/u//posts) matches a :param with an empty value | **in_file** | src/router/linear-router/router.ts:41 Wildcard route '/prefix/*' matches paths that only share the prefix string |
| hono | `28a9c12891` | media types and language tags are compared case-sensitively | **missed** |  |
| hono | `32ba5ba836` | rebuilding a cached body with new Response(body) loses the Content-Type, so formData() after text() throws | **in_file** | src/request.ts:218 Body read after parseBody crashes when parsedBody is the first cached key |
| hono | `8b4392fa36` | streamToNodeStream ignores write() backpressure and resolves before the writable finishes | **in_file** | runtime_tests/lambda/index.test.ts:484 streamHandle test asserts on its own input, not the handler output; src/adapter/aws-lambda/handler.ts:107 streamHandle drops the response status and headers |
| hono | `2d3bc55954` | abort subscribers are called bare, so a throwing or rejecting listener stops the rest or is unhandled | **missed** |  |
| hono | `d7964503c9` | content-type is dropped for a re-serialized FormData but the stale content-length is kept | **missed** |  |
| hono | `350d904ad0` | defaultMatch only accepts exact type matches, so */* and type/* never match | **missed** |  |
| hono | `366f760a30` | an undefined entry in a form array is appended as the string 'undefined' | **missed** |  |
| hono | `091ecb7e86` | If-None-Match is split on /,\s*/, so whitespace before a comma leaves a trailing space on the tag | **missed** |  |
| hono | `b143c4ed8b` | OPTIONS is not a safe method, so CORS preflights are rejected by the CSRF check | **missed** |  |
| hono | `d9b8b4b73b` | Vary is set rather than appended on OPTIONS, overwriting a Vary another middleware set | **missed** |  |
| hono | `331b3d8a96` | String.replace with a string replacement expands $ tokens in the param value | **missed** |  |
| hono | `ff34b1ba9a` | /\?/ matches a ? inside a regex quantifier, so :p{\d+?} is read as optional | **missed** |  |
| hono | `cdb36b80af` | request cookies are joined with ',' and serialized with path=/; a Cookie header is '; '-joined name=value pairs | **caught** | block `src/client/client.ts:100` Multiple cookies are joined with a comma instead of a semicolon |
| hono | `31f0605209` | pipe() releases the writer lock and re-acquires it only after pipeTo succeeds, so a throw leaves no writer | **inform_only** | inform `src/utils/stream.ts:33` pipe() leaves the writer released if the source errors |
| hono | `7ce0ddd350` | m[0].match(/\//) without the g flag counts at most one slash | **missed** |  |
| hono | `cd35af7655` | the subscription effect has no deps, so a changed subscribe or store is never picked up | **missed** |  |
| hono | `8130a67da1` | CSP callbacks run for both Content-Security-Policy and its Report-Only header | **caught** | block `src/middleware/secure-headers/secure-headers.ts:272` Nonce callbacks run on both CSP headers and crash when both policies use functions or one policy has no callbacks |
| hono | `1ce18c26dc` | truthiness checks drop retry: 0 and id: '' | **caught** | warn `src/helper/streaming/sse.ts:29` retry: 0 is silently dropped from the SSE message |
| hono | `12806614b9` | a FormData body is re-serialized with a new boundary while the old Content-Type header is kept | **in_file** | src/request.ts:472 Consumed-body clone re-serializes the parsed JSON object as the request body; src/request.ts:463 Cache key 'parsedBody' is not a method on HonoRequest, so the consumed-body path crashes |
| hono | `9822548bf7` | new Request with a stream body needs duplex: 'half' | **in_file** | src/middleware/method-override/index.ts:86 URL-encoded override keeps the original Content-Length after the body shrinks; src/middleware/method-override/index.ts:101 Unvalidated override header value is passed straight to the Request constructor; src/middleware/method-override/index.ts:115 Query override forwards the original body into a GET or HEAD request |
| hono | `6fe828ca0f` | existing Cache-Control directive names are compared case-sensitively | **caught** | warn `src/middleware/cache/index.ts:25` Existing Cache-Control directive names are compared case-sensitively |
| hono | `0edb243a74` | if (content) treats an empty file as not found | **missed** |  |
| hono | `71cdcf40d8` | expandIPv6('::') removes one empty section, so a second empty slot is expanded again | **missed** |  |
| hono | `48dc87dfd5` | replace(/\\/, '/') converts only the first backslash | **caught** | warn `src/utils/filepath.ts:26` Backslash normalization replaces only the first backslash |
| hono | `e9830c6ba1` | (Date.now() / 1000) \| 0 truncates to 32 bits, so timestamps past 2038 wrap | **caught** | warn `src/utils/jwt/jwt.ts:77` `\| 0` wraps the JWT clock to a negative value after 2038 |
| hono | `1854e24145` | after splicing an array child in place the index still advances, skipping the next child | **in_file** | src/jsx/dom/render.ts:243 Components without defaultProps now receive the element's own props object, which the style component mutates |
| hono | `778ac10e84` | getPath stops at ? but not #, so a fragment ends up in the path | **in_file** | src/utils/url.ts:85 Fallback decoding leaves a whole run of percent sequences undecoded when one byte is invalid |
| hono | `66fc63d134` | the Bearer prefix is matched case-sensitively | **missed** |  |
| hono | `7768865e7a` | SSE data is split on \n only, so \r and \r\n line endings are not framed | **missed** |  |
| hono | `6d18f17e58` | app.fetch(request, evt) passes the FetchEvent as Env instead of as the execution context | **missed** |  |
| hono | `acb94b15fa` | safeEncodeURI decides by decodeURI(str) === str, which mis-handles a literal % and some encoded input | **inform_only** | inform `src/context.ts:750` Redirect with a stray percent sign double-encodes already-encoded parts |
| hono | `a16bcda120` | decodeURIComponent on an invalid query value throws instead of returning the raw value | **missed** |  |
| cobra | `b80aeb17fc` | append on a sub-slice of args writes into the caller's backing array (os.Args) | **missed** |  |
| cobra | `36aee64abe` | argsMinusFirstX removes the first arg equal to the subcommand name even when it is a flag's value | **missed** |  |
| cobra | `21f39ca07e` | the read loop reuses $c, clobbering the global $c that *_custom_func reads | **missed** |  |
| cobra | `6fd8e29b07` | declare -F ___custom_func has three underscores, so the fallback to __custom_func never runs | **caught** | block `bash_completions.go:137` Unqualified fallback checks the wrong function name, so __custom_func is never called |
