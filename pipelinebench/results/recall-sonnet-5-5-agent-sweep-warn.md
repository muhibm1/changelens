# Recall eval: `claude-code:claude-sonnet-5-5` with `--agent --escalation model`, 50 bugs at the commit that introduced them, judged by `claude-sonnet-5-5` reading each label

| metric | value |
| --- | --- |
| cases | 50 |
| errors | 0 |
| caught | 18 |
| in file | 16 |
| inform only | 8 |
| missed | 8 |
| detection recall | 0.54 |
| escalation recall | 0.36 |
| recall in file | 0.68 |
| warns per case | 1.68 |
| other warns per case | 1.28 |
| tokens in | 581 |
| tokens cache write | 3084452 |
| tokens cache read | 2584403 |
| tokens out | 216522 |

Outcomes, scored in this order: **caught**, a warn or block finding near the labelled lines that names the bug; **in_file**, a warn or block finding in the bug's file about something else; **inform_only**, the bug named near its lines only at `inform`; **missed**, none of these. The finding column shows the first finding that names the bug, at its own severity, or the warnings of an in_file row that has none. A case with no finding near the labelled lines is not sent to the judge, so its `judge_usage` is empty. Finding text is capped (`failure` at 500 characters, a note's reason at 300), so a long one ends mid-word.

| repo | commit | bug | outcome | finding |
| --- | --- | --- | --- | --- |
| httpx | `1805ee0d22` | verify=False returns before the client certificate is loaded, so cert= is ignored | **in_file** | httpx/_config.py:33 create_ssl_context returns any non-bool verify value unchanged |
| httpx | `fd3b69d2aa` | RFC 2069 digest (no qop) hashes HA1 twice: response = H(HA1:HA1:nonce:HA2) | **missed** |  |
| httpx | `15d09a3bbc` | a NO_PROXY entry with a scheme (http://host) is treated as a hostname and never matches | **in_file** | httpx/_utils.py:266 CIDR NO_PROXY entries are mounted but never match a range; httpx/_utils.py:268 IPv6 CIDR entry produces a malformed bracketed URL pattern |
| httpx | `27df5e49c7` | TextChunker.decode returns [''] for empty content, so iter_text yields an empty string | **in_file** | httpx/_models.py:1173 iter_bytes/iter_text/iter_lines raise ValueError on an already-read empty body; httpx/_models.py:1174 chunk_size=0 or negative is not validated and fails obscurely |
| httpx | `b481166481` | with raise_app_exceptions=False an exception before the response completes is still raised | **inform_only** | inform `httpx/_transports/asgi.py:151` Refactor changes exception handling when raise_app_exceptions=False |
| httpx | `59914c7690` | NetRCAuth(file: Optional[str]) has no default, so NetRCAuth() is a TypeError | **caught** | warn `httpx/_auth.py:150` NetRCAuth requires `file` but docs call NetRCAuth() with no arguments |
| httpx | `c725387b2d` | Headers.update assigns each raw pair, so a repeated header in the update keeps only its last value | **caught** | warn `httpx/_models.py:662` Headers.update drops repeated header values |
| httpx | `6f5865f860` | hasattr(stream, 'read') also matches SyncByteStream subclasses, whose read() takes no size argument | **inform_only** | inform `httpx/_content.py:53` Any iterable with a `read` attribute is now read via read(CHUNK_SIZE) |
| hono | `bfc190f725` | the style replacement regex has no s flag, so multiline CSS is not matched | **in_file** | src/helper/css/index.ts:164 CSP nonce is interpolated into raw HTML without escaping |
| hono | `09a81e4f01` | undefined children become [undefined], so a void element is cloned with a child | **caught** | warn `src/jsx/base.ts:431` Childless element is cloned with an explicit undefined child |
| hono | `9a6e52d734` | some() drops a Response returned by a short-circuiting middleware, so the context is never finalized | **in_file** | src/middleware/combine/index.ts:87 every()/some() drop a Response returned by a wrapped middleware; src/middleware/combine/index.ts:87 every(): a condition returning true never calls next, so the chain stops |
| hono | `f356702efc` | retainedHeaders are compared lower-cased against the option's values as given, so a mixed-case option never matches | **in_file** | src/middleware/etag/index.ts:51 Headers deleted while iterating over the same Headers object |
| hono | `8b0f189a3f` | c.res.json() on an empty or invalid JSON body throws, turning the response into a 500 | **caught** | warn `src/middleware/pretty-json/index.ts:12` Middleware parses any application/json response and throws on an empty or invalid body |
| hono | `1a1de75296` | an empty path segment in the middle of a path (/u//posts) matches a :param with an empty value | **in_file** | src/router/linear-router/router.ts:42 Trailing '/*' route matches sibling paths sharing a prefix; src/router/linear-router/router.ts:71 Label route without a leading slash crashes match() |
| hono | `28a9c12891` | media types and language tags are compared case-sensitively | **inform_only** | inform `src/helper/accepts/accepts.ts:22` Media type comparison is case-sensitive |
| hono | `32ba5ba836` | rebuilding a cached body with new Response(body) loses the Content-Type, so formData() after text() throws | **in_file** | src/request.ts:216 cachedBody no longer skips parsedBody and breaks json()/text() after parseBody() |
| hono | `8b4392fa36` | streamToNodeStream ignores write() backpressure and resolves before the writable finishes | **caught** | warn `src/adapter/aws-lambda/handler.ts:73` No backpressure when writing the response stream |
| hono | `2d3bc55954` | abort subscribers are called bare, so a throwing or rejecting listener stops the rest or is unhandled | **inform_only** | inform `src/utils/stream.ts:88` One throwing abort subscriber stops the rest and aborted cannot be retried |
| hono | `d7964503c9` | content-type is dropped for a re-serialized FormData but the stale content-length is kept | **inform_only** | inform `src/request.ts:494` Stale content-length header kept when FormData is re-serialized |
| hono | `350d904ad0` | defaultMatch only accepts exact type matches, so */* and type/* never match | **missed** |  |
| hono | `366f760a30` | an undefined entry in a form array is appended as the string 'undefined' | **missed** |  |
| hono | `091ecb7e86` | If-None-Match is split on /,\s*/, so whitespace before a comma leaves a trailing space on the tag | **missed** |  |
| hono | `b143c4ed8b` | OPTIONS is not a safe method, so CORS preflights are rejected by the CSRF check | **in_file** | src/middleware/csrf/index.ts:11 Content-Type regex is case-sensitive, so the CSRF check can be bypassed |
| hono | `d9b8b4b73b` | Vary is set rather than appended on OPTIONS, overwriting a Vary another middleware set | **in_file** | inform `src/middleware/cors/index.ts:119` Preflight overwrites an existing Vary header |
| hono | `331b3d8a96` | String.replace with a string replacement expands $ tokens in the param value | **in_file** | src/client/utils.ts:13 Empty-string param value now drops the whole path segment |
| hono | `ff34b1ba9a` | /\?/ matches a ? inside a regex quantifier, so :p{\d+?} is read as optional | **caught** | warn `src/utils/url.ts:130` Splitting on '/' and testing for '?' breaks segments with regex patterns |
| hono | `cdb36b80af` | request cookies are joined with ',' and serialized with path=/; a Cookie header is '; '-joined name=value pairs | **caught** | warn `src/client/client.ts:98` serialize with Path attribute used for a request Cookie header |
| hono | `31f0605209` | pipe() releases the writer lock and re-acquires it only after pipeTo succeeds, so a throw leaves no writer | **caught** | warn `src/utils/stream.ts:34` pipe() does not reacquire the writer if pipeTo fails |
| hono | `7ce0ddd350` | m[0].match(/\//) without the g flag counts at most one slash | **caught** | warn `src/router/trie-router/node.ts:180` Slash count caps at 1 so deeper greedy matches resume at the wrong segment |
| hono | `cd35af7655` | the subscription effect has no deps, so a changed subscribe or store is never picked up | **in_file** | src/jsx/hooks/index.ts:397 Streaming/Suspense string rendering bypasses the server-snapshot path |
| hono | `8130a67da1` | CSP callbacks run for both Content-Security-Policy and its Report-Only header | **caught** | block `src/middleware/secure-headers/secure-headers.ts:271` CSP nonce callback also runs on the other CSP header and throws on string values |
| hono | `1ce18c26dc` | truthiness checks drop retry: 0 and id: '' | **caught** | warn `src/helper/streaming/sse.ts:29` retry: 0 is dropped by truthiness check |
| hono | `12806614b9` | a FormData body is re-serialized with a new boundary while the old Content-Type header is kept | **caught** | warn `src/request.ts:475` Rebuilt multipart request keeps the old Content-Type boundary |
| hono | `9822548bf7` | new Request with a stream body needs duplex: 'half' | **caught** | warn `src/middleware/method-override/index.ts:116` Query override forwards body stream without duplex option |
| hono | `6fe828ca0f` | existing Cache-Control directive names are compared case-sensitively | **caught** | warn `src/middleware/cache/index.ts:21` Existing directive names are compared case-sensitively |
| hono | `0edb243a74` | if (content) treats an empty file as not found | **missed** |  |
| hono | `71cdcf40d8` | expandIPv6('::') removes one empty section, so a second empty slot is expanded again | **in_file** | src/middleware/ip-restriction/index.ts:60 CIDR prefix is not validated; src/middleware/ip-restriction/index.ts:155 IPv4-mapped IPv6 and non-IP addresses are mishandled and can cause a 500; src/middleware/ip-restriction/index.ts:157 Remote IPv6 address is not normalised, so deny rules can be bypassed; src/utils/ipaddr.ts:27 IPv4 validation accepts malformed addresses |
| hono | `48dc87dfd5` | replace(/\\/, '/') converts only the first backslash | **caught** | warn `src/utils/filepath.ts:26` Backslash normalisation replaces only the first backslash |
| hono | `e9830c6ba1` | (Date.now() / 1000) \| 0 truncates to 32 bits, so timestamps past 2038 wrap | **inform_only** | inform `src/utils/jwt/jwt.ts:77` `\| 0` truncates to 32 bits; `now` wraps negative after January 2038 |
| hono | `1854e24145` | after splicing an array child in place the index still advances, skipping the next child | **in_file** | src/jsx/dom/render.ts:267 Deleting node.vR breaks the 'vR' in node check in buildNode; src/jsx/dom/render.ts:471 build mutates the children array in place; src/jsx/dom/render.ts:545 Skip flag s can go stale, and nN links are not updated next to skipped nodes |
| hono | `778ac10e84` | getPath stops at ? but not #, so a fragment ends up in the path | **in_file** | src/utils/url.ts:85 Fallback decoding leaves a whole run encoded when it mixes valid and invalid bytes; src/utils/url.ts:107 getPath now returns decoded control characters and dots, and the logger prints them |
| hono | `66fc63d134` | the Bearer prefix is matched case-sensitively | **in_file** | src/middleware/bearer-auth/index.ts:74 Empty prefix yields a WWW-Authenticate challenge with no auth scheme |
| hono | `7768865e7a` | SSE data is split on \n only, so \r and \r\n line endings are not framed | **missed** |  |
| hono | `6d18f17e58` | app.fetch(request, evt) passes the FetchEvent as Env instead of as the execution context | **inform_only** | inform `src/adapter/service-worker/handler.ts:27` FetchEvent is exposed as c.env, not c.executionCtx |
| hono | `acb94b15fa` | safeEncodeURI decides by decodeURI(str) === str, which mis-handles a literal % and some encoded input | **caught** | block `src/utils/url.ts:323` Reserved-character escapes (%2F, %3F, %23, %26...) get double-encoded |
| hono | `a16bcda120` | decodeURIComponent on an invalid query value throws instead of returning the raw value | **missed** |  |
| cobra | `b80aeb17fc` | append on a sub-slice of args writes into the caller's backing array (os.Args) | **missed** |  |
| cobra | `36aee64abe` | argsMinusFirstX removes the first arg equal to the subcommand name even when it is a flag's value | **inform_only** | inform `command.go:333` First-match removal can delete a flag's value instead of the command token |
| cobra | `21f39ca07e` | the read loop reuses $c, clobbering the global $c that *_custom_func reads | **caught** | warn `bash_completions.go:75` Read loop overwrites the global word index `c` in generated completion script |
| cobra | `6fd8e29b07` | declare -F ___custom_func has three underscores, so the fallback to __custom_func never runs | **caught** | warn `bash_completions.go:137` Fallback guard tests ___custom_func (three underscores), so the legacy fallback never runs |
