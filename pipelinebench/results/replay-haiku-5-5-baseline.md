# Replay eval: `anthropic:claude-haiku-5-5`, 38 merged changes

| metric | value |
| --- | --- |
| changes | 38 |
| errors | 0 |
| changes warned | 11 |
| warns | 17 |
| warns per change | 0.45 |
| confirmed | 1 |
| plausible | 8 |
| noise | 8 |
| unjudged | 0 |
| setup | 2 |
| confirmed recall | 1/2 |
| tokens in | 507818 |
| tokens out | 62736 |

| repo | commit | change | severity | warnings |
| --- | --- | --- | --- | --- |
| httpx | `1d73150` | Cleanup response.json() method (#2911) | block | **plausible** `httpx/_models.py:761` json() ignores the declared charset and decodes raw bytes as UTF-8/16/32 |
| httpx | `359f77d` | Clean up URL signature (#3245) | inform | none |
| httpx | `41597ad` | Move remaining utility functions to _models.py (#3387) | inform | none |
| httpx | `6212e8f` | Move utility functions to _multipart.py (#3388) | inform | none |
| httpx | `75a13a1` | Remove typeshed iscoroutine workaround (#2466) | inform | none |
| httpx | `83a8518` | Move normalize header functions to _models.py (#3382) | inform | none |
| httpx | `8752e26` | Drop .read/.aread from SyncByteStream/AsyncByteStream (#2407) | inform | none |
| httpx | `9baf3a6` | Drop RawURL (#2241) | inform | none |
| httpx | `b97c059` | Streaming multipart support (#2382) | warn | **plausible** `httpx/_multipart.py:181` Non-seekable file content is lost when the multipart stream is iterated a second time |
| httpx | `ba2e512` | Review urlescape percent-safe set (#3373) | inform | none |
| hono | `3a67f7f3` | Merge commit from fork | inform | none |
| hono | `4b44abe1` | perf(router): share null object creation (#5267) | inform | none |
| hono | `6cadf753` | fix(lambda-edge): descriptive error on a malformed event (#5358) | warn | **plausible** `src/adapter/lambda-edge/handler.ts:186` Missing config and host header now builds a request to host 'undefined' instead of failing |
| hono | `7075369e` | Revert fix(etag) incremental hashing (#5199) | inform | none |
| hono | `8a5852d4` | fix(etag): incremental hashing for chunked responses (#5199) | inform | none |
| hono | `a1946287` | fix(pattern-router/linear-router): prefix overmatch (#5252) | inform | none |
| hono | `a1e4ac7d` | perf(cors): pre-join static array header options (#5210) | inform | none |
| hono | `c409d855` | fix(request): serialize cached JSON body in cloneRawRequest (#5288) | inform | none |
| cobra | `24ada7f` | Remove the default completion cmd if it is alone (#1559) | warn | **noise** `completions.go:746` Leftover default completion command counts as a real sub-command on a second run |
| cobra | `5bef9d8` | Fix --version help and output for plugins (#2180) | inform | none |
| cobra | `6c3c116` | feat: add CompletionFunc type (#2220) | inform | none |
| cobra | `8607918` | feat: make InitDefaultCompletionCmd public (#1467) | inform | none |
| cobra | `a0a6ae0` | Improve API to get flag completion function (#2063) | inform | none |
| cobra | `a4ab3fa` | powershell completion with custom comp (#1208) | warn | **noise** `powershell_completions.go:146` Empty-result check for ShellCompDirectiveNoFileComp never matches; **noise** `powershell_completions.go:201` Single-match check never true, so Complete mode inserts the description into the command line |
| cobra | `b84ef40` | Rename BashCompDirectives to ShellCompDirectives (#1082) | inform | none |
| cobra | `fce8d8a` | Expose ValidateRequiredFlags and ValidateFlagGroups (#1760) | inform | none |
| calitp | `008d78a8` | Remove non-RBAP Payments Tables (#5651) | warn | **setup** `warehouse/models/mart/payments/elavon_littlepay__daily_history_transactions_deposits_billing.sql:0` 8 changed paths are not mapped to any asset |
| calitp | `2eff9b46` | De-duplicate fct_payments_settlements rows (#5586) | warn | **noise** `warehouse/models/intermediate/payments/int_payments__settlements_deduped.sql:15` Dedup key drops rows that previously survived because they differed by amount |
| calitp | `2f53a705` | SUM instead of COUNT for total arrivals (#5679) | inform | none |
| calitp | `3127f574` | Add _feed_valid_from to daily GTFS summary tables (#5656) | warn | **plausible** `warehouse/models/mart/gtfs/fct_daily_schedule_rt_route_direction_summary.sql:191` Inner join on common_shape drops schedule rows with a NULL direction_id |
| calitp | `34716dcc` | Fix route-direction summary trip counting (#5767) | block | **confirmed** `warehouse/models/mart/transit_database/dim_provider_gtfs_data.sql:33` Hardcoded production table paths replace dbt refs; **plausible** `warehouse/models/mart/gtfs/fct_daily_schedule_rt_route_direction_summary.sql:20` GROUP BY collapses trip update and vehicle position keys independently; **noise** `warehouse/models/mart/gtfs/fct_daily_schedule_rt_route_direction_summary.sql:201` Date-range join silently drops schedule rows outside every valid range; **plausible** `warehouse/models/mart/gtfs/fct_daily_schedule_rt_route_direction_summary.sql:214` Dedupe ordering has no tiebreaker |
| calitp | `5b9885a0` | Incrementally build vehicle position trip summaries (#5642) | warn | **noise** `warehouse/models/mart/gtfs/fct_vehicle_positions_trip_summaries.sql:18` Late-arriving rows older than the lookback window are never reprocessed |
| calitp | `6a6df64e` | Add Public/Conditional Operating column to dim tables (#5674) | inform | none |
| calitp | `6f1cc4b7` | Enghouse transactions cleanup (#5763) | warn | **noise** `warehouse/models/intermediate/payments/_int_payments.yml:121` unique_combination test no longer excludes null payment_reference rows; **noise** `warehouse/models/intermediate/payments/_int_payments.yml:157` not_null test on payment_reference contradicts the rows the model keeps |
| calitp | `ad497afc` | De-duplicate Littlepay customers and rides (#5761) | warn | **plausible** `warehouse/models/intermediate/payments/int_payments__customer_funding_source_vaults.sql:10` Keeping only the latest row per vault collapses the validity windows; **plausible** `warehouse/models/intermediate/payments/int_payments__deduped_customer_funding_sources.sql:60` Vault rank is computed before the funding-source filter, so the latest vault row can be dropped |
| calitp | `b0b73b32` | fix refs (#5768) | inform | none |
| calitp | `e636f6e2` | Type datetimes in fct_payments_aggregations (#5583) | inform | none |
| calitp | `fbe45897` | Remove legacy stg_rt models (#5633) | warn | **setup** `warehouse/dbt_project.yml:98` 6 changed paths are not mapped to any asset |
