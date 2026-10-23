# Recall eval: `claude-code:claude-sonnet-5-5` with `--agent`, 50 bugs at the commit that introduced them, judged by `claude-sonnet-5-5` reading each label

| metric | value |
| --- | --- |
| cases | 50 |
| errors | 0 |
| caught | 17 |
| in file | 9 |
| inform only | 3 |
| missed | 21 |
| recall | 0.34 |
| recall in file | 0.52 |
| warns per case | 1.12 |
| tokens in | 530 |
| tokens cache write | 2391573 |
| tokens cache read | 2735708 |
| tokens out | 111348 |

Outcomes, scored in this order: **caught**, a warn or block finding near the labelled lines that names the bug; **in_file**, a warn or block finding in the bug's file about something else; **inform_only**, the bug named near its lines only at `inform`; **missed**, none of these. The finding column shows the first finding that names the bug, at its own severity, or the warnings of an in_file row that has none. A case with no finding near the labelled lines is not sent to the judge, so its `judge_usage` is empty. Finding text is capped (`failure` at 500 characters, a note's reason at 300), so a long one ends mid-word.

| repo | commit | bug | outcome | finding |
| --- | --- | --- | --- | --- |
| httpx | `1805ee0d22` | verify=False returns before the client certificate is loaded, so cert= is ignored | **in_file** | httpx/_config.py:33 Non-bool, non-SSLContext `verify` is returned unchanged instead of rejected |
| httpx | `fd3b69d2aa` | RFC 2069 digest (no qop) hashes HA1 twice: response = H(HA1:HA1:nonce:HA2) | **missed** |  |
| httpx | `15d09a3bbc` | a NO_PROXY entry with a scheme (http://host) is treated as a hostname and never matches | **in_file** | httpx/_utils.py:265 CIDR entries in NO_PROXY are accepted but not honored as ranges |
| httpx | `27df5e49c7` | TextChunker.decode returns [''] for empty content, so iter_text yields an empty string | **in_file** | httpx/_models.py:1173 iter_bytes/aiter_bytes raise ValueError on already-read empty content |
| httpx | `b481166481` | with raise_app_exceptions=False an exception before the response completes is still raised | **missed** |  |
| httpx | `59914c7690` | NetRCAuth(file: Optional[str]) has no default, so NetRCAuth() is a TypeError | **caught** | warn `httpx/_auth.py:150` NetRCAuth requires `file` but docs call it with no arguments |
| httpx | `c725387b2d` | Headers.update assigns each raw pair, so a repeated header in the update keeps only its last value | **caught** | warn `httpx/_models.py:662` Headers.update drops repeated headers |
| httpx | `6f5865f860` | hasattr(stream, 'read') also matches SyncByteStream subclasses, whose read() takes no size argument | **missed** |  |
| hono | `bfc190f725` | the style replacement regex has no s flag, so multiline CSS is not matched | **missed** |  |
| hono | `09a81e4f01` | undefined children become [undefined], so a void element is cloned with a child | **caught** | warn `src/jsx/base.ts:431` Cloning a childless element yields children [undefined] |
| hono | `9a6e52d734` | some() drops a Response returned by a short-circuiting middleware, so the context is never finalized | **in_file** | src/middleware/combine/index.ts:87 every() with a condition returning true never calls next |
| hono | `f356702efc` | retainedHeaders are compared lower-cased against the option's values as given, so a mixed-case option never matches | **caught** | warn `src/middleware/etag/index.ts:51` Headers deleted while iterating the same Headers object |
| hono | `8b0f189a3f` | c.res.json() on an empty or invalid JSON body throws, turning the response into a 500 | **caught** | warn `src/middleware/pretty-json/index.ts:12` Re-parsing the response breaks on empty/invalid JSON bodies and stale headers |
| hono | `1a1de75296` | an empty path segment in the middle of a path (/u//posts) matches a :param with an empty value | **in_file** | src/router/linear-router/router.ts:42 Trailing wildcard route matches sibling paths sharing a prefix |
| hono | `28a9c12891` | media types and language tags are compared case-sensitively | **missed** |  |
| hono | `32ba5ba836` | rebuilding a cached body with new Response(body) loses the Content-Type, so formData() after text() throws | **in_file** | src/request.ts:216 cachedBody no longer skips the parsedBody cache key |
| hono | `8b4392fa36` | streamToNodeStream ignores write() backpressure and resolves before the writable finishes | **in_file** | inform `src/adapter/aws-lambda/handler.ts:73` No backpressure handling when writing to the response stream |
| hono | `2d3bc55954` | abort subscribers are called bare, so a throwing or rejecting listener stops the rest or is unhandled | **inform_only** | inform `src/utils/stream.ts:88` abort() runs subscribers unguarded; one failing listener breaks the rest |
| hono | `d7964503c9` | content-type is dropped for a re-serialized FormData but the stale content-length is kept | **missed** |  |
| hono | `350d904ad0` | defaultMatch only accepts exact type matches, so */* and type/* never match | **missed** |  |
| hono | `366f760a30` | an undefined entry in a form array is appended as the string 'undefined' | **missed** |  |
| hono | `091ecb7e86` | If-None-Match is split on /,\s*/, so whitespace before a comma leaves a trailing space on the tag | **missed** |  |
| hono | `b143c4ed8b` | OPTIONS is not a safe method, so CORS preflights are rejected by the CSRF check | **in_file** | src/middleware/csrf/index.ts:12 Content-type check is case-sensitive, so mixed-case types skip the CSRF check |
| hono | `d9b8b4b73b` | Vary is set rather than appended on OPTIONS, overwriting a Vary another middleware set | **missed** |  |
| hono | `331b3d8a96` | String.replace with a string replacement expands $ tokens in the param value | **missed** |  |
| hono | `ff34b1ba9a` | /\?/ matches a ? inside a regex quantifier, so :p{\d+?} is read as optional | **caught** | warn `src/utils/url.ts:138` Any '?' inside a param segment (e.g. in a regex pattern) is treated as an optional marker; '/' inside {} splits segments |
| hono | `cdb36b80af` | request cookies are joined with ',' and serialized with path=/; a Cookie header is '; '-joined name=value pairs | **caught** | warn `src/client/client.ts:100` Cookie request header built with Set-Cookie serialization and comma separator |
| hono | `31f0605209` | pipe() releases the writer lock and re-acquires it only after pipeTo succeeds, so a throw leaves no writer | **missed** |  |
| hono | `7ce0ddd350` | m[0].match(/\//) without the g flag counts at most one slash | **caught** | warn `src/router/trie-router/node.ts:180` Slash count uses match() length, wrong for 2+ slashes |
| hono | `cd35af7655` | the subscription effect has no deps, so a changed subscribe or store is never picked up | **missed** |  |
| hono | `8130a67da1` | CSP callbacks run for both Content-Security-Policy and its Report-Only header | **caught** | block `src/middleware/secure-headers/secure-headers.ts:271` Nonce callbacks run against both CSP headers, breaking requests when both are set |
| hono | `1ce18c26dc` | truthiness checks drop retry: 0 and id: '' | **caught** | warn `src/helper/streaming/sse.ts:29` retry: 0 is silently dropped by truthiness check |
| hono | `12806614b9` | a FormData body is re-serialized with a new boundary while the old Content-Type header is kept | **caught** | warn `src/request.ts:472` Rebuilt multipart request keeps the old Content-Type boundary |
| hono | `9822548bf7` | new Request with a stream body needs duplex: 'half' | **caught** | warn `src/middleware/method-override/index.ts:115` Override value is passed unvalidated to Request, and streaming body lacks duplex |
| hono | `6fe828ca0f` | existing Cache-Control directive names are compared case-sensitively | **inform_only** | inform `src/middleware/cache/index.ts:21` Existing Cache-Control directive names are compared case-sensitively and without trimming the name |
| hono | `0edb243a74` | if (content) treats an empty file as not found | **missed** |  |
| hono | `71cdcf40d8` | expandIPv6('::') removes one empty section, so a second empty slot is expanded again | **missed** |  |
| hono | `48dc87dfd5` | replace(/\\/, '/') converts only the first backslash | **caught** | warn `src/utils/filepath.ts:26` Backslash normalization replaces only the first backslash |
| hono | `e9830c6ba1` | (Date.now() / 1000) \| 0 truncates to 32 bits, so timestamps past 2038 wrap | **caught** | warn `src/utils/jwt/jwt.ts:77` `\| 0` truncates to int32, breaking JWT time checks after 2038 |
| hono | `1854e24145` | after splicing an array child in place the index still advances, skipping the next child | **in_file** | src/jsx/dom/render.ts:267 Deleting `vR` after apply breaks the `'vR' in node` check in buildNode |
| hono | `778ac10e84` | getPath stops at ? but not #, so a fragment ends up in the path | **missed** |  |
| hono | `66fc63d134` | the Bearer prefix is matched case-sensitively | **missed** |  |
| hono | `7768865e7a` | SSE data is split on \n only, so \r and \r\n line endings are not framed | **missed** |  |
| hono | `6d18f17e58` | app.fetch(request, evt) passes the FetchEvent as Env instead of as the execution context | **missed** |  |
| hono | `acb94b15fa` | safeEncodeURI decides by decodeURI(str) === str, which mis-handles a literal % and some encoded input | **caught** | warn `src/utils/url.ts:323` safeEncodeURI double-encodes reserved-character escapes and encodes IPv6 brackets |
| hono | `a16bcda120` | decodeURIComponent on an invalid query value throws instead of returning the raw value | **missed** |  |
| cobra | `b80aeb17fc` | append on a sub-slice of args writes into the caller's backing array (os.Args) | **missed** |  |
| cobra | `36aee64abe` | argsMinusFirstX removes the first arg equal to the subcommand name even when it is a flag's value | **inform_only** | inform `command.go:333` Removes first textual match, which may be a flag value rather than the matched subcommand token |
| cobra | `21f39ca07e` | the read loop reuses $c, clobbering the global $c that *_custom_func reads | **caught** | warn `bash_completions.go:75` Read loop overwrites the shared word-index variable `c` |
| cobra | `6fd8e29b07` | declare -F ___custom_func has three underscores, so the fallback to __custom_func never runs | **caught** | block `bash_completions.go:137` Fallback checks for ___custom_func (three underscores), so the unqualified fallback never runs |
