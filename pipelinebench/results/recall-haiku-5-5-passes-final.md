# Recall eval: `anthropic:claude-haiku-5-5`, 50 bugs at the commit that introduced them, judged by `claude-haiku-5-5` reading each label

| metric | value |
| --- | --- |
| cases | 50 |
| errors | 0 |
| caught | 20 |
| in file | 14 |
| inform only | 3 |
| missed | 13 |
| recall | 0.4 |
| recall in file | 0.68 |
| warns per case | 2.08 |
| tokens in | 601002 |
| tokens cache write | 712861 |
| tokens cache read | 2511513 |
| tokens out | 576586 |

| repo | commit | bug | outcome | finding |
| --- | --- | --- | --- | --- |
| httpx | `1805ee0d22` | verify=False returns before the client certificate is loaded, so cert= is ignored | **in_file** | httpx/_config.py:26 SSLKEYLOGFILE support and OpenSSL verify flags are dropped from the default context without a changelog entry; httpx/_config.py:33 Non-bool, non-SSLContext verify values are returned unchanged; httpx/_transports/default.py:151 verify=None no longer falls back to the default CA bundle |
| httpx | `fd3b69d2aa` | RFC 2069 digest (no qop) hashes HA1 twice: response = H(HA1:HA1:nonce:HA2) | **missed** |  |
| httpx | `15d09a3bbc` | a NO_PROXY entry with a scheme (http://host) is treated as a hostname and never matches | **in_file** | httpx/_utils.py:268 IPv6 CIDR entries in NO_PROXY produce a bracketed pattern that includes the prefix length; httpx/_utils.py:272 Bracketed IPv6 entries in NO_PROXY fall through to the domain branch |
| httpx | `27df5e49c7` | TextChunker.decode returns [''] for empty content, so iter_text yields an empty string | **in_file** | httpx/_models.py:1173 iter_bytes/aiter_bytes raise ValueError for empty cached content; httpx/_decoders.py:173 ByteChunker and TextChunker do not validate chunk_size of 0 or below |
| httpx | `b481166481` | with raise_app_exceptions=False an exception before the response completes is still raised | **caught** | warn `httpx/_transports/asgi.py:151` Exception handling behaviour changed when raise_app_exceptions is False |
| httpx | `59914c7690` | NetRCAuth(file: Optional[str]) has no default, so NetRCAuth() is a TypeError | **missed** |  |
| httpx | `c725387b2d` | Headers.update assigns each raw pair, so a repeated header in the update keeps only its last value | **caught** | warn `httpx/_models.py:663` Headers.update drops repeated values from its source |
| httpx | `6f5865f860` | hasattr(stream, 'read') also matches SyncByteStream subclasses, whose read() takes no size argument | **missed** |  |
| hono | `bfc190f725` | the style replacement regex has no s flag, so multiline CSS is not matched | **in_file** | src/helper/css/index.ts:92 Inline style script takes its nonce from Style only if Style's callback ran first; src/helper/css/index.ts:164 CSP nonce is written into HTML attributes without escaping |
| hono | `09a81e4f01` | undefined children become [undefined], so a void element is cloned with a child | **in_file** | src/jsx/base.ts:436 Caller-supplied props.children is silently overridden by the original children |
| hono | `9a6e52d734` | some() drops a Response returned by a short-circuiting middleware, so the context is never finalized | **in_file** | src/middleware/combine/index.ts:34 some() with no middleware never calls next() and silently ends the request; src/middleware/combine/index.ts:52 Falsy thrown values are dropped by the lastError truthiness check; src/middleware/combine/index.ts:87 every() drops the chain when a Condition returns true |
| hono | `f356702efc` | retainedHeaders are compared lower-cased against the option's values as given, so a mixed-case option never matches | **caught** | warn `src/middleware/etag/index.ts:52` retainedHeaders option is compared case-sensitively against lowercased header names |
| hono | `8b0f189a3f` | c.res.json() on an empty or invalid JSON body throws, turning the response into a 500 | **caught** | warn `src/middleware/pretty-json/index.ts:12` Pretty middleware parses every JSON response body and throws on bodies that are not valid JSON |
| hono | `1a1de75296` | an empty path segment in the middle of a path (/u//posts) matches a :param with an empty value | **in_file** | src/router/linear-router/router.ts:6 Route patterns containing nested braces are mis-parsed; src/router/linear-router/router.ts:42 Suffix-star route strips two characters regardless of the preceding slash; src/router/linear-router/router.ts:54 Wildcard route matches across a segment boundary; src/router/linear-router/router.ts:110 Duplicate param check is skipped when a captured value is an empty string |
| hono | `28a9c12891` | media types and language tags are compared case-sensitively | **in_file** | src/helper/accepts/accepts.ts:56 Wildcard entries with q=0 now select a supported type the client rejected |
| hono | `32ba5ba836` | rebuilding a cached body with new Response(body) loses the Content-Type, so formData() after text() throws | **in_file** | src/request.ts:218 json()/text() after parseBody() throws a TypeError |
| hono | `8b4392fa36` | streamToNodeStream ignores write() backpressure and resolves before the writable finishes | **caught** | warn `src/adapter/aws-lambda/handler.ts:73` Chunks are written without honouring write() backpressure |
| hono | `2d3bc55954` | abort subscribers are called bare, so a throwing or rejecting listener stops the rest or is unhandled | **missed** |  |
| hono | `d7964503c9` | content-type is dropped for a re-serialized FormData but the stale content-length is kept | **caught** | warn `src/request.ts:490` Stale Content-Length is kept when the multipart body is re-serialized |
| hono | `350d904ad0` | defaultMatch only accepts exact type matches, so */* and type/* never match | **missed** |  |
| hono | `366f760a30` | an undefined entry in a form array is appended as the string 'undefined' | **missed** |  |
| hono | `091ecb7e86` | If-None-Match is split on /,\s*/, so whitespace before a comma leaves a trailing space on the tag | **inform_only** | inform `src/middleware/etag/index.ts:34` If-None-Match list split ignores whitespace before commas |
| hono | `b143c4ed8b` | OPTIONS is not a safe method, so CORS preflights are rejected by the CSRF check | **in_file** | src/middleware/csrf/index.ts:37 Form-content-type check is case-sensitive, so a cross-site request with an uppercase content type skips the CSRF check; src/middleware/csrf/index.ts:37 Requests with no Content-Type bypass the origin check |
| hono | `d9b8b4b73b` | Vary is set rather than appended on OPTIONS, overwriting a Vary another middleware set | **in_file** | inform `src/middleware/cors/index.ts:119` Preflight Vary set overwrites existing Vary values |
| hono | `331b3d8a96` | String.replace with a string replacement expands $ tokens in the param value | **in_file** | src/client/utils.ts:13 Empty-string param values are now dropped along with undefined ones |
| hono | `ff34b1ba9a` | /\?/ matches a ? inside a regex quantifier, so :p{\d+?} is read as optional | **caught** | warn `src/utils/url.ts:144` Optional-marker removal also strips a `?` inside a regex pattern |
| hono | `cdb36b80af` | request cookies are joined with ',' and serialized with path=/; a Cookie header is '; '-joined name=value pairs | **caught** | warn `src/client/client.ts:100` Multiple cookies are joined with a comma and each carries cookie attributes |
| hono | `31f0605209` | pipe() releases the writer lock and re-acquires it only after pipeTo succeeds, so a throw leaves no writer | **caught** | warn `src/utils/stream.ts:34` pipe() leaves the writer released if pipeTo fails |
| hono | `7ce0ddd350` | m[0].match(/\//) without the g flag counts at most one slash | **caught** | block `src/router/trie-router/node.ts:180` Greedy match depth counts at most one segment boundary |
| hono | `cd35af7655` | the subscription effect has no deps, so a changed subscribe or store is never picked up | **missed** |  |
| hono | `8130a67da1` | CSP callbacks run for both Content-Security-Policy and its Report-Only header | **caught** | block `src/middleware/secure-headers/secure-headers.ts:271` Each CSP callback now rewrites both CSP headers, using the wrong directive layout |
| hono | `1ce18c26dc` | truthiness checks drop retry: 0 and id: '' | **caught** | warn `src/helper/streaming/sse.ts:29` retry: 0 is silently dropped |
| hono | `12806614b9` | a FormData body is re-serialized with a new boundary while the old Content-Type header is kept | **caught** | warn `src/request.ts:475` Original multipart Content-Type is kept while the body is rebuilt |
| hono | `9822548bf7` | new Request with a stream body needs duplex: 'half' | **caught** | warn `src/middleware/method-override/index.ts:115` Query override passes a live request body stream without duplex |
| hono | `6fe828ca0f` | existing Cache-Control directive names are compared case-sensitively | **caught** | warn `src/middleware/cache/index.ts:25` Existing Cache-Control directive names are compared case-sensitively |
| hono | `0edb243a74` | if (content) treats an empty file as not found | **missed** |  |
| hono | `71cdcf40d8` | expandIPv6('::') removes one empty section, so a second empty slot is expanded again | **missed** |  |
| hono | `48dc87dfd5` | replace(/\\/, '/') converts only the first backslash | **caught** | warn `src/utils/filepath.ts:26` Only the first backslash is converted to a slash |
| hono | `e9830c6ba1` | (Date.now() / 1000) \| 0 truncates to 32 bits, so timestamps past 2038 wrap | **caught** | warn `src/utils/jwt/jwt.ts:77` `\| 0` overflows 32-bit signed int for current epoch seconds after 2038-01-19 |
| hono | `1854e24145` | after splicing an array child in place the index still advances, skipping the next child | **in_file** | src/jsx/dom/render.ts:243 Component props object is no longer a fresh copy, so the style component's mutation persists on the element; src/jsx/dom/render.ts:299 findInsertBefore no longer falls back to the next sibling when a reused node has an empty child list; src/jsx/dom/render.ts:545 Sibling link nN is not updated when the current child is skipped, leaving a stale next-sibling pointer |
| hono | `778ac10e84` | getPath stops at ? but not #, so a fragment ends up in the path | **in_file** | src/utils/url.ts:85 Fallback decodes each run of %XX all-or-nothing, not as much as possible; src/utils/url.ts:107 Paths containing %25 plus an invalid sequence keep the %2525 protection escape in the output; src/utils/url.ts:107 Path is decoded here and again in param(), so single-encoded input is decoded twice |
| hono | `66fc63d134` | the Bearer prefix is matched case-sensitively | **missed** |  |
| hono | `7768865e7a` | SSE data is split on \n only, so \r and \r\n line endings are not framed | **inform_only** | inform `src/helper/streaming/sse.ts:25` SSE event and id values are not sanitized for CR/LF |
| hono | `6d18f17e58` | app.fetch(request, evt) passes the FetchEvent as Env instead of as the execution context | **missed** |  |
| hono | `acb94b15fa` | safeEncodeURI decides by decodeURI(str) === str, which mis-handles a literal % and some encoded input | **caught** | block `src/utils/url.ts:323` safeEncodeURI double-encodes percent escapes of reserved characters |
| hono | `a16bcda120` | decodeURIComponent on an invalid query value throws instead of returning the raw value | **missed** |  |
| cobra | `b80aeb17fc` | append on a sub-slice of args writes into the caller's backing array (os.Args) | **missed** |  |
| cobra | `36aee64abe` | argsMinusFirstX removes the first arg equal to the subcommand name even when it is a flag's value | **caught** | warn `command.go:335` argsMinusFirstX can remove a flag value instead of the subcommand token |
| cobra | `21f39ca07e` | the read loop reuses $c, clobbering the global $c that *_custom_func reads | **inform_only** | inform `bash_completions.go:75` Unscoped loop variable c in handle_reply reuses the shared parser index |
| cobra | `6fd8e29b07` | declare -F ___custom_func has three underscores, so the fallback to __custom_func never runs | **caught** | block `bash_completions.go:137` Unqualified fallback checks a misspelled function name and never runs plain __custom_func |
