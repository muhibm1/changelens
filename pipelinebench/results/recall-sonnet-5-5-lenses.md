# Recall eval: `claude-code:claude-sonnet-5-5`, 50 bugs at the commit that introduced them, judged by `claude-sonnet-5-5` reading each label

| metric | value |
| --- | --- |
| cases | 50 |
| errors | 0 |
| caught | 18 |
| in file | 11 |
| inform only | 3 |
| missed | 18 |
| recall | 0.36 |
| recall in file | 0.58 |
| warns per case | 1.48 |
| tokens in | 788 |
| tokens cache write | 3940214 |
| tokens cache read | 2479501 |
| tokens out | 279639 |

Outcomes, scored in this order: **caught**, a warn or block finding near the labelled lines that names the bug; **in_file**, a warn or block finding in the bug's file about something else; **inform_only**, the bug named near its lines only at `inform`; **missed**, none of these. The finding column shows the first finding that names the bug, at its own severity, or the warnings of an in_file row that has none. A case with no finding near the labelled lines is not sent to the judge, so its `judge_usage` is empty. Finding text is capped (`failure` at 500 characters, a note's reason at 300), so a long one ends mid-word.

| repo | commit | bug | outcome | finding |
| --- | --- | --- | --- | --- |
| httpx | `1805ee0d22` | verify=False returns before the client certificate is loaded, so cert= is ignored | **in_file** | httpx/_config.py:33 create_ssl_context passes any non-bool verify through unchecked |
| httpx | `fd3b69d2aa` | RFC 2069 digest (no qop) hashes HA1 twice: response = H(HA1:HA1:nonce:HA2) | **missed** |  |
| httpx | `15d09a3bbc` | a NO_PROXY entry with a scheme (http://host) is treated as a hostname and never matches | **in_file** | httpx/_utils.py:265 CIDR ranges in NO_PROXY are accepted but only match the base address; httpx/_utils.py:268 IPv6 CIDR in NO_PROXY builds a malformed bracketed URL pattern |
| httpx | `27df5e49c7` | TextChunker.decode returns [''] for empty content, so iter_text yields an empty string | **in_file** | httpx/_models.py:1173 Empty cached content makes iter_bytes/aiter_bytes raise ValueError |
| httpx | `b481166481` | with raise_app_exceptions=False an exception before the response completes is still raised | **inform_only** | inform `httpx/_transports/asgi.py:151` Refactor also changes the error path when raise_app_exceptions=False |
| httpx | `59914c7690` | NetRCAuth(file: Optional[str]) has no default, so NetRCAuth() is a TypeError | **caught** | warn `httpx/_auth.py:150` NetRCAuth() requires `file` but docs show calling it with no arguments |
| httpx | `c725387b2d` | Headers.update assigns each raw pair, so a repeated header in the update keeps only its last value | **caught** | warn `httpx/_models.py:662` Headers.update drops repeated keys in the input |
| httpx | `6f5865f860` | hasattr(stream, 'read') also matches SyncByteStream subclasses, whose read() takes no size argument | **missed** |  |
| hono | `bfc190f725` | the style replacement regex has no s flag, so multiline CSS is not matched | **missed** |  |
| hono | `09a81e4f01` | undefined children become [undefined], so a void element is cloned with a child | **in_file** | src/jsx/base.ts:430 children passed via props are overwritten by the original element's children |
| hono | `9a6e52d734` | some() drops a Response returned by a short-circuiting middleware, so the context is never finalized | **in_file** | src/middleware/combine/index.ts:87 every() halts the chain when a condition function returns true; src/middleware/combine/index.ts:87 every() discards the Response returned by a wrapped middleware |
| hono | `f356702efc` | retainedHeaders are compared lower-cased against the option's values as given, so a mixed-case option never matches | **caught** | warn `src/middleware/etag/index.ts:52` retainedHeaders is matched case-sensitively and can drop ETag |
| hono | `8b0f189a3f` | c.res.json() on an empty or invalid JSON body throws, turning the response into a 500 | **caught** | warn `src/middleware/pretty-json/index.ts:12` prettyJSON re-parses every JSON-typed response without guarding empty, invalid or null-body responses |
| hono | `1a1de75296` | an empty path segment in the middle of a path (/u//posts) matches a :param with an empty value | **in_file** | src/router/linear-router/router.ts:6 Custom regex patterns containing '}' are split wrongly and throw at match time; src/router/linear-router/router.ts:42 Trailing /* matches any path sharing the prefix, without a segment boundary; src/router/linear-router/router.ts:96 Regex-constrained param followed by another label skips a character; src/router/linear-router/router.ts:109 Duplicate-param error thrown before the route is known to match |
| hono | `28a9c12891` | media types and language tags are compared case-sensitively | **missed** |  |
| hono | `32ba5ba836` | rebuilding a cached body with new Response(body) loses the Content-Type, so formData() after text() throws | **in_file** | src/request.ts:216 cachedBody no longer skips the parsedBody cache entry |
| hono | `8b4392fa36` | streamToNodeStream ignores write() backpressure and resolves before the writable finishes | **caught** | warn `src/adapter/aws-lambda/handler.ts:73` Stream writes ignore backpressure |
| hono | `2d3bc55954` | abort subscribers are called bare, so a throwing or rejecting listener stops the rest or is unhandled | **inform_only** | inform `src/utils/stream.ts:88` One throwing abort subscriber stops the rest |
| hono | `d7964503c9` | content-type is dropped for a re-serialized FormData but the stale content-length is kept | **missed** |  |
| hono | `350d904ad0` | defaultMatch only accepts exact type matches, so */* and type/* never match | **missed** |  |
| hono | `366f760a30` | an undefined entry in a form array is appended as the string 'undefined' | **missed** |  |
| hono | `091ecb7e86` | If-None-Match is split on /,\s*/, so whitespace before a comma leaves a trailing space on the tag | **missed** |  |
| hono | `b143c4ed8b` | OPTIONS is not a safe method, so CORS preflights are rejected by the CSRF check | **in_file** | src/middleware/csrf/index.ts:12 Content-type check is case-sensitive, so mixed-case form types skip the CSRF check |
| hono | `d9b8b4b73b` | Vary is set rather than appended on OPTIONS, overwriting a Vary another middleware set | **missed** |  |
| hono | `331b3d8a96` | String.replace with a string replacement expands $ tokens in the param value | **inform_only** | inform `src/client/utils.ts:13` Falsy check drops the segment for empty-string params, and replacement string still expands `$` patterns |
| hono | `ff34b1ba9a` | /\?/ matches a ? inside a regex quantifier, so :p{\d+?} is read as optional | **caught** | warn `src/utils/url.ts:138` Any '?' in a param segment is treated as the optional marker, including regex quantifiers |
| hono | `cdb36b80af` | request cookies are joined with ',' and serialized with path=/; a Cookie header is '; '-joined name=value pairs | **caught** | warn `src/client/client.ts:98` Cookie header built with Set-Cookie serialization and comma separator |
| hono | `31f0605209` | pipe() releases the writer lock and re-acquires it only after pipeTo succeeds, so a throw leaves no writer | **caught** | warn `src/utils/stream.ts:33` pipe() loses the writer lock if pipeTo fails |
| hono | `7ce0ddd350` | m[0].match(/\//) without the g flag counts at most one slash | **caught** | warn `src/router/trie-router/node.ts:180` Slash count uses non-global match, so it is always 0 or 1 |
| hono | `cd35af7655` | the subscription effect has no deps, so a changed subscribe or store is never picked up | **in_file** | src/jsx/hooks/index.ts:399 String rendering now throws when getServerSnapshot is omitted |
| hono | `8130a67da1` | CSP callbacks run for both Content-Security-Policy and its Report-Only header | **caught** | block `src/middleware/secure-headers/secure-headers.ts:271` Nonce callbacks run against both CSP headers and break when both are configured |
| hono | `1ce18c26dc` | truthiness checks drop retry: 0 and id: '' | **caught** | warn `src/helper/streaming/sse.ts:29` retry: 0 is silently dropped by truthiness check |
| hono | `12806614b9` | a FormData body is re-serialized with a new boundary while the old Content-Type header is kept | **caught** | warn `src/request.ts:472` Rebuilt body depends on whichever cache key was stored first |
| hono | `9822548bf7` | new Request with a stream body needs duplex: 'half' | **caught** | warn `src/middleware/method-override/index.ts:115` Query override builds Request with body of consumed/duplex stream and no duplex option |
| hono | `6fe828ca0f` | existing Cache-Control directive names are compared case-sensitively | **in_file** | inform `src/middleware/cache/index.ts:21` Existing directive names are compared case-sensitively |
| hono | `0edb243a74` | if (content) treats an empty file as not found | **missed** |  |
| hono | `71cdcf40d8` | expandIPv6('::') removes one empty section, so a second empty slot is expanded again | **missed** |  |
| hono | `48dc87dfd5` | replace(/\\/, '/') converts only the first backslash | **caught** | warn `src/utils/filepath.ts:26` Backslash normalisation only replaces the first backslash |
| hono | `e9830c6ba1` | (Date.now() / 1000) \| 0 truncates to 32 bits, so timestamps past 2038 wrap | **caught** | warn `src/utils/jwt/jwt.ts:77` `\| 0` truncates to int32, so the JWT clock overflows in January 2038 |
| hono | `1854e24145` | after splicing an array child in place the index still advances, skipping the next child | **in_file** | src/jsx/dom/intrinsic-element/components.ts:238 Missing props now throws where it was handled before; src/jsx/dom/render.ts:267 Deleting `vR` after apply breaks the `'vR' in node` already-built check in buildNode; src/jsx/dom/render.ts:299 findInsertBefore loop no longer falls back to the outer node's next sibling; src/jsx/dom/render.ts:384 Skip flag `s` is only cleared when the child already has an element; src/jsx/dom/render.ts:471 build now mutates the caller's children array in place; src/jsx/dom/render.ts:545 nN links not updated when previous sibling is skipped |
| hono | `778ac10e84` | getPath stops at ? but not #, so a fragment ends up in the path | **missed** |  |
| hono | `66fc63d134` | the Bearer prefix is matched case-sensitively | **missed** |  |
| hono | `7768865e7a` | SSE data is split on \n only, so \r and \r\n line endings are not framed | **missed** |  |
| hono | `6d18f17e58` | app.fetch(request, evt) passes the FetchEvent as Env instead of as the execution context | **missed** |  |
| hono | `acb94b15fa` | safeEncodeURI decides by decodeURI(str) === str, which mis-handles a literal % and some encoded input | **caught** | block `src/utils/url.ts:323` safeEncodeURI double-encodes percent-escaped reserved characters |
| hono | `a16bcda120` | decodeURIComponent on an invalid query value throws instead of returning the raw value | **missed** |  |
| cobra | `b80aeb17fc` | append on a sub-slice of args writes into the caller's backing array (os.Args) | **missed** |  |
| cobra | `36aee64abe` | argsMinusFirstX removes the first arg equal to the subcommand name even when it is a flag's value | **missed** |  |
| cobra | `21f39ca07e` | the read loop reuses $c, clobbering the global $c that *_custom_func reads | **caught** | warn `bash_completions.go:75` read loop clobbers the shell-global/dynamic-scope variable `c` |
| cobra | `6fd8e29b07` | declare -F ___custom_func has three underscores, so the fallback to __custom_func never runs | **caught** | warn `bash_completions.go:137` Fallback checks for ___custom_func (three underscores), so it never fires |
