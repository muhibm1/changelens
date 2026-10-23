# Recall eval: `claude-code:claude-sonnet-5-5` with `--agent`, 45 bugs at the commit that introduced them, judged by `claude-sonnet-5-5` reading each label

| metric | value |
| --- | --- |
| cases | 45 |
| errors | 0 |
| caught | 15 |
| in file | 5 |
| inform only | 5 |
| missed | 20 |
| recall | 0.333 |
| recall in file | 0.444 |
| warns per case | 0.89 |
| tokens in | 276 |
| tokens cache write | 1276115 |
| tokens cache read | 1012110 |
| tokens out | 71252 |

| repo | commit | bug | outcome | finding |
| --- | --- | --- | --- | --- |
| starlette | `94a22b865e` | URL.replace() indexes hostname[-1] without checking it is non-empty, so a URL with no authority (empty netloc) raises IndexError | **inform_only** | inform `starlette/datastructures.py:125` IndexError on replace() for URLs with empty netloc |
| starlette | `0308681883` | old-style TemplateResponse(name, context, status_code, headers, ...) reads headers/media_type/background from args[2]/[3]/[4], off by one, so headers gets the status code and background is never found | **caught** | warn `starlette/templating.py:188` Old-style positional args map to wrong indices for headers/media_type/background |
| starlette | `097152be5a` | GZipMiddleware only adds Vary: Accept-Encoding when it actually compresses, so uncompressed responses (client without gzip, or small body) are cached and served to gzip clients wrongly | **missed** |  |
| starlette | `866a15f79c` | middleware is built as cls(app=app, *args, **kwargs), so any positional middleware args collide with app (TypeError: multiple values for argument 'app') or shift into the wrong parameter | **caught** | block `starlette/applications.py:105` cls(app=app, *args, **kwargs) fails whenever positional args are given |
| starlette | `d771bb7a5e` | _StreamingResponse no longer subclasses StreamingResponse and overrides __call__ without running self.background, so BackgroundTasks attached to responses passing through BaseHTTPMiddleware never run | **missed** |  |
| starlette | `94044e5991` | URL.replace rebuilds netloc from self.hostname, which strips the [ ] brackets of an IPv6 host (and drops userinfo), producing an invalid URL | **in_file** | starlette/datastructures.py:82 URL is no longer a str and is now unhashable |
| starlette | `14650bfd22` | FloatConvertor regex [0-9]+(.[0-9]+)? uses an unescaped dot, which matches any character, so paths like /1x5 match the float convertor | **caught** | warn `starlette/convertors.py:51` Float regex has unescaped '.', so non-numeric input matches and crashes |
| starlette | `0aef1724cf` | the feature probe calls hashlib.md5(usedforsecurity=True), which raises ValueError (not TypeError) on FIPS systems, so the FIPS workaround breaks import there | **missed** |  |
| werkzeug | `4aa0024efc` | the deprecated-name __getattr__ is defined under `if t.TYPE_CHECKING:` instead of `if not t.TYPE_CHECKING:`, so at runtime get_content_length is gone and importing it raises | **caught** | block `src/werkzeug/sansio/utils.py:232` Deprecation __getattr__ is defined only under TYPE_CHECKING, so it never exists at runtime |
| werkzeug | `411ba540d7` | a 304 response filters headers with `if k in {}`, which keeps no headers at all instead of removing only the representation headers | **caught** | block `src/werkzeug/wrappers/response.py:519` 304 responses lose every header because the filter is `k in {}` |
| werkzeug | `a6a7dd1445` | the condition for returning a plain path contradicts its comment: when neither host nor subdomain matching is enabled it falls through and always builds an external URL | **caught** | warn `src/werkzeug/routing/map.py:975` build() returns external URLs when subdomain_matching=False and host_matching=False |
| werkzeug | `1049dd6b2a` | _last_partial_boundary_index is called on the whole buffer instead of data[data_start:] (+ data_start), so it can return a newline before data_start and corrupt multipart parsing with chunked data | **caught** | warn `src/werkzeug/sansio/multipart.py:252` Partial-boundary search ignores data_start, so the leading line break can leak into part data |
| werkzeug | `082e0e5b9c` | _parse_data computes data_start but calls last_newline() over the whole buffer, whose leading CRLF makes the last newline index 0, and the find() shortcut is gone so each chunk runs a full regex: large multipart bodies parse wrongly and slowly | **caught** | block `src/werkzeug/sansio/multipart.py:265` Leading line break leaks into field data when body arrives without a boundary in the first chunk |
| werkzeug | `9c92a5f904` | Map.bind only catches UnicodeEncodeError around .encode('idna'), but the idna codec raises plain UnicodeError (e.g. empty or too-long label), so an invalid host crashes instead of raising BadHost | **missed** |  |
| werkzeug | `2cce3252ac` | the frame click handler uses getElementsByTagName('pre')[i] with the frame's index instead of [0], so frames after the first fail to expand (undefined.parentElement) | **missed** |  |
| gin | `66b47a8068` | RunFd creates an os.File for the fd but never closes it after net.FileListener dups it, leaking a file descriptor | **inform_only** | inform `gin.go:332` os.NewFile wrapper is never closed; its finalizer later closes the caller's fd |
| gin | `32065bbd42` | Hijack refuses whenever Written() is true, which includes the headers-only state (size 0), so websocket libraries that write headers before hijacking now fail | **missed** |  |
| gin | `f969bfaf50` | Unwrap is declared on *Error while Error() is on the value type, so a gin.Error value wrapped with %w does not unwrap and errors.Is/As miss the inner error | **missed** |  |
| gin | `2ae8a25fc9` | HandleContext resets the context (handlers and index) and re-enters routing without restoring them, so the original chain continues on the new route's handlers and middleware re-runs; it also returns c to the pool while still in use | **in_file** | inform `gin.go:280` reset() while the outer handler chain is still running |
| gin | `4bfae4c8c8` | ValidateStruct dereferences a pointer to a struct and validates a copy, so struct-level validators no longer receive the caller's pointer (and cannot see or modify the original) | **in_file** | binding/default_validator.go:45 Nil pointer now panics in ValidateStruct |
| chi | `98de1d836a` | defaultLogEntry.Panic now calls PrintPrettyStack, which hard-codes colour output and writes to stderr, ignoring the formatter's NoColor/useColor setting (and its Logger) | **caught** | warn `middleware/logger.go:154` defaultLogEntry.Panic ignores the configured Logger, NoColor, and the request context |
| chi | `2f332444aa` | ReadFrom with a tee copies through basicWriter.Write, which already counts bytes, then adds n again, so BytesWritten() reports twice the real size | **caught** | warn `middleware/wrap_writer.go:115` BytesWritten double-counted when Tee is set in ReadFrom |
| chi | `23b8ec2ca3` | RouteHeaders calls next.ServeHTTP when no routes are set but does not return, so it falls through and the next handler runs twice | **caught** | warn `middleware/route_headers.go:84` Missing return after empty-routes passthrough |
| chi | `1089a7c695` | Mux.Find returns only the subrouter's pattern for mounted routes, dropping the parent mount prefix, so nested routes report the wrong pattern | **missed** |  |
| chi | `3e5747d1f8` | RoutePattern trims a trailing slash unconditionally, so the root pattern "/" becomes the empty string | **caught** | warn `context.go:126` RoutePattern returns empty string for the root route "/" |
| echo | `39f61ef3ba` | the RFC 9457 handler writes default Status/Type/Title into the ProblemError it received, mutating shared (sentinel) errors across requests and racing | **caught** | warn `rfc9457.go:107` Handler mutates the caller's ProblemError (data race on shared sentinel errors) |
| echo | `c3887ebb13` | for WebSocket proxying X-Forwarded-For is only set when empty, so the client IP is not appended to an existing chain from upstream proxies | **missed** |  |
| echo | `7c8592a7e0` | default burst is int(config.Rate), which truncates to 0 for fractional rates below 1, so the limiter denies every request | **caught** | warn `middleware/rate_limiter.go:213` Fractional rate with default burst gives burst 0 and blocks every request |
| echo | `124825ee62` | ExtractIPFromRealIPHeader now checks whether the X-Real-IP value itself is trusted rather than whether the direct peer is a trusted proxy, so any client can spoof its IP with a private address | **missed** |  |
| echo | `626f13e338` | randomString maps random bytes with b % 52, which is not uniform over 256 values, biasing CSRF tokens and request IDs | **missed** |  |
| ky | `1341f5ca56` | error-body decoding honours the Content-Type charset even for JSON, which is always UTF-8, so a JSON error with a non-UTF-8 charset parameter is decoded wrongly | **missed** |  |
| ky | `2b0a10071d` | signals from every source are always combined, so a later signal: undefined (or a replacement) can no longer remove or replace an inherited signal when extending | **inform_only** | inform `source/utils/merge.ts:64` Explicit `signal: undefined` no longer overrides an inherited signal |
| ky | `29e78fe8e6` | deletion markers are applied after searchParams are appended (and the marker symbol is lost when init hooks get a cloned URLSearchParams), so params an init hook re-adds are deleted, or deletions are lost | **missed** |  |
| ky | `1e749ba065` | deepMerge is recursive and used for nested values, so setting context = {} at its end adds an empty context key to every merged nested object such as a json body | **missed** |  |
| ky | `bf9eca6aff` | .json() empty check clones the response and buffers the whole body, then reads the original again, double-reading the stream through a tee and causing races/hangs | **missed** |  |
| h3 | `080bf53d69` | origin regexes are checked with RegExp.test, which is stateful for /g and /y patterns (lastIndex), so the same origin alternates between allowed and rejected | **in_file** | src/utils/cors/handler.ts:14 handleCors resolves options but passes the raw options to the header builders; src/utils/cors/utils.ts:176 maxAge option is never emitted |
| h3 | `cda0bb29b2` | the new query parser skips decoding the whole key or value when decodeURIComponent throws on one malformed escape, so valid escapes next to it stay encoded (URLSearchParams decodes leniently) | **missed** |  |
| h3 | `50aeec071e` | getType looks the extension up case-sensitively, so files like INDEX.HTML or photo.JPG get no MIME type | **inform_only** | inform `src/utils/internal/mime.ts:37` Extension lookup is case-sensitive |
| h3 | `61b395e31b` | setChunkedCookie splits by raw string length, but cookies are percent-encoded when serialized, so chunks with special or non-ASCII characters exceed the cookie size limit | **missed** |  |
| h3 | `631249d0f4` | defineHandler does Object.assign(composedHandler, input), which copies the raw input.fetch over the composed one, so calling .fetch bypasses the middleware | **in_file** | inform `src/handler.ts:49` Raw user `fetch` overwrites the string/URL-accepting wrapper |
| express | `18e5985b8a` | with Transfer-Encoding set, the whole length block is skipped, so len stays undefined and the string is not converted, and ETag generation silently stops | **inform_only** | inform `lib/response.js:168` ETag generation is silently skipped when Transfer-Encoding is set |
| express | `0867302ddb` | res.location now calls loc.toLowerCase() and encodeUrl on the raw argument, so passing a non-string (e.g. a URL object or number) throws a TypeError | **missed** |  |
| express | `48940e6120` | a Buffer body when no ETag is generated and length < 1000 goes to Buffer.byteLength(chunk, encoding), the string-only path, which throws a TypeError on Node versions of the time | **missed** |  |
| express | `fd35351594` | mergeParams copies the parent into obj but then does mixin(parent, params), mutating the parent's req.params with the child's numeric indices | **caught** | warn `lib/router/index.js:482` Numeric merge mutates parent's req.params instead of the copy |
| express | `566720be15` | req.host calls trust(remoteAddress) without the hop index, so with a numeric trust proxy hop count (i < n with i undefined) X-Forwarded-Host is never trusted | **missed** |  |
