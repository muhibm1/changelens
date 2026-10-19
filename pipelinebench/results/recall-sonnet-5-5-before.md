# Recall eval: `anthropic`, 50 bugs at the commit that introduced them

| metric | value |
| --- | --- |
| cases | 50 |
| errors | 0 |
| caught | 25 |
| in file | 3 |
| inform only | 8 |
| missed | 14 |
| recall | 0.5 |
| recall in file | 0.56 |
| warns per case | 1.34 |
| tokens in | 1111150 |
| tokens out | 35895 |

| repo | commit | bug | outcome | finding |
| --- | --- | --- | --- | --- |
| httpx | `1805ee0d22` | verify=False returns before the client certificate is loaded, so cert= is ignored | **caught** | warn `httpx/_config.py:20` create_ssl_context imports certifi and ssl eagerly, and drops SSLKEYLOGFILE and strict verify flags |
| httpx | `fd3b69d2aa` | RFC 2069 digest (no qop) hashes HA1 twice: response = H(HA1:HA1:nonce:HA2) | **missed** |  |
| httpx | `15d09a3bbc` | a NO_PROXY entry with a scheme (http://host) is treated as a hostname and never matches | **caught** | warn `httpx/_utils.py:266` CIDR no_proxy entries are mounted as literal host patterns and will not match |
| httpx | `27df5e49c7` | TextChunker.decode returns [''] for empty content, so iter_text yields an empty string | **in_file** | inform `httpx/_models.py:1239` Chunker flush and close happen outside exception mapping and skip on early exit |
| httpx | `b481166481` | with raise_app_exceptions=False an exception before the response completes is still raised | **inform_only** | inform `httpx/_transports/asgi.py:151` Unannounced behaviour change in exception handling |
| httpx | `59914c7690` | NetRCAuth(file: Optional[str]) has no default, so NetRCAuth() is a TypeError | **caught** | warn `httpx/_auth.py:150` NetRCAuth requires `file` argument, contradicting docs |
| httpx | `c725387b2d` | Headers.update assigns each raw pair, so a repeated header in the update keeps only its last value | **missed** |  |
| httpx | `6f5865f860` | hasattr(stream, 'read') also matches SyncByteStream subclasses, whose read() takes no size argument | **inform_only** | inform `httpx/_content.py:53` Any object with a `read` attribute is now consumed via read(), bypassing its iterator |
| hono | `bfc190f725` | the style replacement regex has no s flag, so multiline CSS is not matched | **in_file** | src/helper/css/index.ts:164 Unescaped nonce interpolated into HTML attribute |
| hono | `09a81e4f01` | undefined children become [undefined], so a void element is cloned with a child | **caught** | warn `src/jsx/base.ts:431` Element without children gets a spurious undefined child |
| hono | `9a6e52d734` | some() drops a Response returned by a short-circuiting middleware, so the context is never finalized | **caught** | warn `src/middleware/combine/index.ts:38` some() runs next() inside a handler, then retries later handlers if downstream throws |
| hono | `f356702efc` | retainedHeaders are compared lower-cased against the option's values as given, so a mixed-case option never matches | **missed** |  |
| hono | `8b0f189a3f` | c.res.json() on an empty or invalid JSON body throws, turning the response into a 500 | **caught** | warn `src/middleware/pretty-json/index.ts:11` Pretty printing now applies to all JSON responses, and a body that is not valid JSON throws |
| hono | `1a1de75296` | an empty path segment in the middle of a path (/u//posts) matches a :param with an empty value | **caught** | warn `src/router/linear-router/router.ts:90` Regex 'd' flag and unanchored pattern compiled on every match |
| hono | `28a9c12891` | media types and language tags are compared case-sensitively | **caught** | warn `src/helper/accepts/accepts.ts:28` Wildcard subtype matching is applied to non-media headers and q=0 is not honored |
| hono | `32ba5ba836` | rebuilding a cached body with new Response(body) loses the Content-Type, so formData() after text() throws | **caught** | warn `src/request.ts:216` Cache fallback may pick the parsedBody key as the source body |
| hono | `8b4392fa36` | streamToNodeStream ignores write() backpressure and resolves before the writable finishes | **in_file** | runtime_tests/lambda/index.test.ts:484 Test does not verify handler output; src/adapter/aws-lambda/handler.ts:98 Lambda context passed under a different binding name than in handle(); src/adapter/aws-lambda/handler.ts:108 Streaming does not send status code or headers to the Lambda response stream; src/adapter/aws-lambda/handler.ts:112 Error path writes to a stream that may already be ended, and ignores backpressure |
| hono | `2d3bc55954` | abort subscribers are called bare, so a throwing or rejecting listener stops the rest or is unhandled | **caught** | warn `src/utils/stream.ts:88` abort() does not isolate subscriber errors or handle async rejections |
| hono | `d7964503c9` | content-type is dropped for a re-serialized FormData but the stale content-length is kept | **inform_only** | inform `src/request.ts:494` Content-Type deletion may be case-sensitive and miss the header |
| hono | `350d904ad0` | defaultMatch only accepts exact type matches, so */* and type/* never match | **missed** |  |
| hono | `366f760a30` | an undefined entry in a form array is appended as the string 'undefined' | **missed** |  |
| hono | `091ecb7e86` | If-None-Match is split on /,\s*/, so whitespace before a comma leaves a trailing space on the tag | **missed** |  |
| hono | `b143c4ed8b` | OPTIONS is not a safe method, so CORS preflights are rejected by the CSRF check | **caught** | warn `src/middleware/csrf/index.ts:12` Content-Type regex is case-sensitive, allowing CSRF bypass |
| hono | `d9b8b4b73b` | Vary is set rather than appended on OPTIONS, overwriting a Vary another middleware set | **caught** | warn `src/middleware/cors/index.ts:119` Preflight Vary: Origin is overwritten by the later append |
| hono | `331b3d8a96` | String.replace with a string replacement expands $ tokens in the param value | **caught** | warn `src/client/utils.ts:13` Falsy check drops empty-string params and removes required params silently |
| hono | `ff34b1ba9a` | /\?/ matches a ? inside a regex quantifier, so :p{\d+?} is read as optional | **caught** | warn `src/utils/url.ts:128` Loose guard accepts regex-constrained optional params and mangles them |
| hono | `cdb36b80af` | request cookies are joined with ',' and serialized with path=/; a Cookie header is '; '-joined name=value pairs | **caught** | warn `src/client/client.ts:100` Multiple cookies joined with comma instead of semicolon |
| hono | `31f0605209` | pipe() releases the writer lock and re-acquires it only after pipeTo succeeds, so a throw leaves no writer | **missed** |  |
| hono | `7ce0ddd350` | m[0].match(/\//) without the g flag counts at most one slash | **caught** | warn `src/router/trie-router/node.ts:180` componentCount computed with non-global match; always 0 or 1 |
| hono | `cd35af7655` | the subscription effect has no deps, so a changed subscribe or store is never picked up | **inform_only** | inform `src/jsx/hooks/index.ts:397` useSyncExternalStore now throws when used outside a DOM build, e.g. in string rendering without getServerSnapshot |
| hono | `8130a67da1` | CSP callbacks run for both Content-Security-Policy and its Report-Only header | **caught** | block `src/middleware/secure-headers/secure-headers.ts:272` CSP callback now runs on both CSP headers, corrupting or crashing when both are configured |
| hono | `1ce18c26dc` | truthiness checks drop retry: 0 and id: '' | **inform_only** | inform `src/helper/streaming/sse.ts:29` retry: 0 is silently dropped by truthiness check |
| hono | `12806614b9` | a FormData body is re-serialized with a new boundary while the old Content-Type header is kept | **caught** | warn `src/request.ts:463` Rebuilt request uses whichever body cache key exists first, which can lose or corrupt the body |
| hono | `9822548bf7` | new Request with a stream body needs duplex: 'half' | **caught** | warn `src/middleware/method-override/index.ts:115` Query override forwards a consumed or locked body and ignores the original request's duplex and stream |
| hono | `6fe828ca0f` | existing Cache-Control directive names are compared case-sensitively | **caught** | warn `src/middleware/cache/index.ts:17` Existing directive names are not lowercased before comparison |
| hono | `0edb243a74` | if (content) treats an empty file as not found | **inform_only** | inform `src/middleware/serve-static/index.ts:110` Precompressed lookup now skipped for non-compressible MIME types, with weaker Accept-Encoding parsing |
| hono | `71cdcf40d8` | expandIPv6('::') removes one empty section, so a second empty slot is expanded again | **missed** |  |
| hono | `48dc87dfd5` | replace(/\\/, '/') converts only the first backslash | **caught** | warn `src/utils/filepath.ts:26` Backslash normalization replaces only the first backslash |
| hono | `e9830c6ba1` | (Date.now() / 1000) \| 0 truncates to 32 bits, so timestamps past 2038 wrap | **missed** |  |
| hono | `1854e24145` | after splicing an array child in place the index still advances, skipping the next child | **caught** | warn `src/jsx/dom/render.ts:471` build mutates the caller's children array via splice |
| hono | `778ac10e84` | getPath stops at ? but not #, so a fragment ends up in the path | **inform_only** | inform `src/utils/url.ts:107` Decoded path reintroduces reserved characters, so a decoded %3F or %23 can alter routing |
| hono | `66fc63d134` | the Bearer prefix is matched case-sensitively | **inform_only** | inform `src/middleware/bearer-auth/index.ts:67` Empty-string prefix now changes behavior for existing callers |
| hono | `7768865e7a` | SSE data is split on \n only, so \r and \r\n line endings are not framed | **missed** |  |
| hono | `6d18f17e58` | app.fetch(request, evt) passes the FetchEvent as Env instead of as the execution context | **missed** |  |
| hono | `acb94b15fa` | safeEncodeURI decides by decodeURI(str) === str, which mis-handles a literal % and some encoded input | **caught** | warn `src/utils/url.ts:323` decode-then-encode alters redirect URLs containing reserved percent escapes |
| hono | `a16bcda120` | decodeURIComponent on an invalid query value throws instead of returning the raw value | **missed** |  |
| cobra | `b80aeb17fc` | append on a sub-slice of args writes into the caller's backing array (os.Args) | **missed** |  |
| cobra | `36aee64abe` | argsMinusFirstX removes the first arg equal to the subcommand name even when it is a flag's value | **missed** |  |
| cobra | `21f39ca07e` | the read loop reuses $c, clobbering the global $c that *_custom_func reads | **caught** | warn `bash_completions.go:75` COMPREPLY appended without being reset first |
| cobra | `6fd8e29b07` | declare -F ___custom_func has three underscores, so the fallback to __custom_func never runs | **caught** | block `bash_completions.go:137` Fallback checks misspelled function ___custom_func (three underscores) |
