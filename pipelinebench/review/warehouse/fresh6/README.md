# fresh6: a held-out set of labelled warehouse bugs

Real bugs from public SQL and dbt warehouse repositories. Each bug is placed at the commit that introduced it. Its label comes from the later commit that fixed it.

The labels come only from public upstream history: the fix commit, its message and its diff. The labeller worked only from the brief folder and from its own anonymous clones. It never read, opened, grepped or ran ChangeLens, or any other project folder under `/home/user`. It never read scoring results and never ran a model.

No repository from the earlier sets was reused: nothing in `excluded-repos.txt`, nothing that `fresh5-README.md` lists as tried, and no source package of an excluded Fivetran package. This set is meant to be scored once.

The set has 42 bugs: 40 in scope and 2 recorded as `needs_data`, which are out of scope. They come from 18 repositories, and no repository has more than 5. Of the 40 in-scope bugs, 29 are `compiles: true` and 11 are `compiles: false`.

## Files

- **One YAML per repository**, named after the repository.
  - Each file has top-level `repo`, `url` and `bugs`.
  - The bug fields are `commit`, `parent`, `title`, `fix`, `fix_title`, `bug`, `category`, `in_scope`, `compiles`, `needs_data`, `must_mention` and `expect`.
  - Every commit id is quoted, so YAML always reads it as a string.
- **`repos.txt`**: the local clone path and the upstream URL of each repository. The clones are in `/home/user/fresh-repos-6/`.
- **`progress.md`**: the running count kept while labelling.
- There is no `manifest.sha256`, as the brief asked.

Commit ids are 10-character prefixes. `parent` is the first parent of the introducing commit. `compiles` says whether the introducing commit's SQL still parses and runs: `true` means a silent wrong result, and `false` means invalid SQL or Jinja. `needs_data` means the defect cannot be seen without knowing the source data; those bugs are recorded with `in_scope: false`.

## Repositories

| Repository | URL | Commits at clone | SQL files | Bugs | In scope |
|---|---|---|---|---|---|
| analytics-refinery | https://github.com/wikimedia/analytics-refinery | 2,772 | 342 | 5 | 5 |
| mimic-omop | https://github.com/MIT-LCP/mimic-omop | 608 | 82 | 5 | 4 |
| DataQualityDashboard | https://github.com/OHDSI/DataQualityDashboard | 983 | 32 | 4 | 4 |
| FeatureExtraction | https://github.com/OHDSI/FeatureExtraction | 601 | 33 | 4 | 4 |
| dbt-expectations | https://github.com/calogica/dbt-expectations | 310 | 91 | 4 | 4 |
| dbt_xero | https://github.com/fivetran/dbt_xero | 253 | 71 | 3 | 3 |
| snowplow | https://github.com/dbt-labs/snowplow | 266 | 35 | 3 | 3 |
| dbt-utils | https://github.com/dbt-labs/dbt-utils | 816 | 116 | 2 | 2 |
| dbt-audit-helper | https://github.com/dbt-labs/dbt-audit-helper | 355 | 59 | 2 | 2 |
| dbt-date | https://github.com/calogica/dbt-date | 227 | 51 | 2 | 1 |
| Vocabulary-v5.0 | https://github.com/OHDSI/Vocabulary-v5.0 | 4,780 | 690 | 1 | 1 |
| dbt_intercom | https://github.com/fivetran/dbt_intercom | 299 | 66 | 1 | 1 |
| dbt_google_play | https://github.com/fivetran/dbt_google_play | 106 | 72 | 1 | 1 |
| dbt_github | https://github.com/fivetran/dbt_github | 234 | 71 | 1 | 1 |
| adwords | https://github.com/dbt-labs/adwords | 101 | 24 | 1 | 1 |
| facebook-ads | https://github.com/dbt-labs/facebook-ads | 160 | 31 | 1 | 1 |
| redshift | https://github.com/dbt-labs/redshift | 97 | 32 | 1 | 1 |
| segment | https://github.com/dbt-labs/segment | 168 | 8 | 1 | 1 |

"SQL files" counts the `.sql` and `.hql` files that are tracked at HEAD.

The repositories fall into five groups:

- **analytics-refinery** is the Wikimedia analytics cluster's Hive and Oozie job repository.
- **mimic-omop** (MIT LCP) is the MIMIC-III to OMOP ETL.
- **DataQualityDashboard, FeatureExtraction and Vocabulary-v5.0** (OHDSI) are SQL over the OMOP common data model and its vocabularies.
- **dbt-utils, dbt-audit-helper, dbt-expectations and dbt-date** are the dbt-labs and calogica macro packages, which compile into warehouse SQL.
- **adwords, facebook-ads, redshift, segment and snowplow** are the older dbt-labs warehouse packages. The dbt-labs snowplow package is not Snowplow's own dbt-snowplow-* packages.

The rest are Fivetran packages that were not used before.

## Method

The method is the same as in `fresh5-README.md`:

1. Clone each repository in full, anonymously over https, into `/home/user/fresh-repos-6/`.
2. List the commits whose subject matches any of these words, case-insensitive: fix, revert, hotfix, wrong, duplicate, fanout, null, filter, missing, incorrect, dedup or join. Keep a commit only if it touches at most six SQL, HQL or YAML files and changes at most 300 lines in them.
3. Run `git blame` on the fix's parent over every line the fix removes or replaces, to find the commits that wrote those lines. Keep a candidate only if its main introducing commit changed under 400 lines of SQL and YAML.
4. Read the fix diff, its message and the introducing commit by hand. Keep a bug only when the introducing commit itself added the faulty lines, and when a careful reviewer of that diff could in principle see the problem with the repository at that commit in hand.
   - When seeing the bug needed knowledge of the source data, the bug was kept as `needs_data`, out of scope.
   - A bug was dropped for any of these reasons:
     - the repository at that commit documented or tested the opposite of the fix;
     - the fix was a feature, a style change, a rename, or a fix for a later upstream change;
     - blame landed on a move or reorganisation;
     - the introducing commit was over 400 lines.
5. Record the lines in the introducing commit's own version of each file, checking each one with `git show <commit>:<path>`. A check script confirmed six things:
   - every line is non-empty in that version;
   - `parent` is the first parent;
   - `title` and `fix_title` are the exact subjects;
   - the fix descends from the introducing commit (`git merge-base --is-ancestor`);
   - each bug has two `must_mention` words;
   - no file contains an em or en dash.

   Every YAML parses with PyYAML.

`must_mention` holds two words that a correct finding would use. Column and model names were preferred, along with the mechanism word, such as null, comma, group or inner.

## Counts by category

| Category | Bugs | In scope |
|---|---|---|
| row_count_change (inner join after a left join, join on too few keys, filter that drops NULLs, non-aggregated column added to a GROUP BY, cross join without a source_relation match, OR precedence, COUNT(*) where persons were meant) | 10 | 9 |
| nullability_or_cast (offset or diff from a full or left join with no coalesce, ISNUMERIC and FK checks that count NULLs, NULL concept ids, empty string instead of NULL, invalid leap-day date, NULL-unsafe match flags, boolean summed, recursive CTE type mismatch, CASE with no ELSE) | 12 | 11 |
| business_logic (wrong column copied, fraction compared with a percent threshold, between test that ORs its bounds, wrong side of a join tested for NULL, 0 treated as an absent bound, double-counted window sum, previous instead of current financial year end, source url_tags dropped, wrong ethnicity window) | 11 | 11 |
| other (invalid SQL or Jinja: trailing comma, missing comma, missing space or dot, typo in a column name, macro called with too many arguments, dangling union all, GROUP BY on a select alias, column that does not exist in the table) | 9 | 9 |
| **Total** | **42** | **40** |

| compiles | In scope |
|---|---|
| true | 29 |
| false | 11 |
| needs_data (out of scope) | 2 |

## Notes on individual labels

- **Two bugs in one introducing commit, each listed separately:**
  - dbt_xero `5ae72b0568`: the wrong financial year, fixed by `36ab6496ce`, and the leap-day date, fixed by `b2a7e60858`.
  - analytics-refinery `679aeeaa50`: a NULL estimate from a full outer join, fixed by `9377d9c25d`, and a trailing comma in the monthly GROUP BY, fixed by `2daeaa5b1c`. `679aeeaa50` is also the fix of an earlier bug, `1bc92086e8`.
- **Introducers found with `git log -S` because blame landed on a later edit:** dbt_xero `492e2ad6c5` and `5ae72b0568`, and dbt-expectations `392dda97c8`. For analytics-refinery `679aeeaa50`, the NULL-producing line itself was unchanged, but that commit changed the join that makes it NULL.
- **dbt_google_play `c03eb59a5d`:** the fix's subject is a release title, "v1.1.1 release  (#30)", with two spaces. The double count is named as a bug fix in its body and in the CHANGELOG. It is kept although the subject has no keyword.
- **snowplow `668dfed5fb`:** this is a merge commit, "Merge changes. Add last_column". The faulty lines are its own, and blame names it. `parent` is its first parent.
- **Renamed files:** `expect` uses the introducing commit's own path. For dbt_github, `models/intermediate/pull_request_times.sql` is now `int_github__pull_request_times.sql`. For redshift, `models/views/table_stats.sql` is now `redshift_admin_table_stats.sql`.
- **Vocabulary-v5.0 `ec8cd5ffca` / `d93782f10d`:** both commits are recent, and the fix commit carries an AI co-author trailer. It is kept because the defect is a plain type mismatch.
- **DataQualityDashboard `c05f15ff53`:** this is an "Add files via upload" commit. It is kept because it is the commit that wrote the faulty template.
- **The two `needs_data` bugs:**
  - mimic-omop `bb1d2386a6`: `error = 0` drops the rows where error is NULL. Seeing this needs to know that MIMIC chartevents leaves `error` NULL.
  - dbt-date `e5b9411ef3`: a Snowflake CASE with no ELSE. Seeing this needs to know the session's week start setting.

## Repositories tried and what was rejected

**Not cloned** (both needed a login): `meltano/squared` and Fivetran `dbt_sage_intacct`.

**Cloned and scanned, with no bug kept:**

- Snowplow `dbt-snowplow-web`, `dbt-snowplow-mobile`, `dbt-snowplow-ecommerce`, `dbt-snowplow-utils`, `dbt-snowplow-media-player` and `dbt-snowplow-attribution`.
- Fivetran `dbt_ad_reporting`, `dbt_amazon_ads`, `dbt_amplitude`, `dbt_app_reporting`, `dbt_asana` (+source), `dbt_facebook_ads` (+source), `dbt_github_source`, `dbt_google_ads` (+source), `dbt_greenhouse` (+source), `dbt_intercom_source`, `dbt_klaviyo` (+source), `dbt_lever` (+source), `dbt_linkedin`, `dbt_mailchimp`, `dbt_marketo` (+source), `dbt_microsoft_ads`, `dbt_pendo`, `dbt_pinterest`, `dbt_recurly` (+source), `dbt_shopify`, `dbt_snapchat_ads`, `dbt_twitter`, `dbt_workday`, `dbt_xero_source`, `dbt_youtube_analytics` and `dbt_zuora` (+source).
- dbt-labs `dbt-codegen` and `dbt-external-tables`. Their fixes are to code generation, CI and tests, not to warehouse SQL.

**Examples of rejected candidates, by reason:**

- **Blame lands on a refactor, a rename or a move:**
  - Snowplow web: the bot filter's case sensitivity, and user stitching.
  - dbt_mailchimp: the fan-out.
  - dbt_github: the inner join, and the reviewers dedupe `4541e5f43f`.
  - dbt_google_ads `97e8fa2561` and `552da51275`: the keyword report came from a rename in `b0cbb4a`, and its real source `ad74aa2` is 429 lines.
  - dbt-utils: the week datediff `8a31461608`.
  - dbt-labs snowplow `5000d564ad`: the inner joins to the optional contexts were carried over by a reorganisation.
  - dbt_workday `2d5b0be633`: the active-metric filters were blamed on `84826e9` ("final changes to compile properly"), which only renamed columns. The logic came from `533e466`, which is 887 lines.
- **The bug lives in another package:**
  - Snowplow: the unexpired sessions macro.
  - dbt_klaviyo `cf75982eee`: the integer-only `try_cast` regex had moved into fivetran_utils in `864732b7e4`.
  - dbt_ad_reporting `0e622ef78d`: the variable name changed when the vars were reworked later.
- **The introducing commit is over 400 lines:**
  - dbt_recurly: `taxes_this_month`, introduced by `aaa45dc` (448 lines).
  - dbt_shopify: quantity squared and duplicates, introduced by `9bd631f` and `6f25322`.
  - dbt-labs segment: `64b904d03b`, `75461379a5` and `976d23e9ba`, all introduced by `d57ded6bd6` (1,074 lines).
  - Most Snowplow package candidates.
- **Work in progress, or a fix for a later upstream change:**
  - dbt_lever: the author notes that the fan-out was known while the work was unreleased.
  - dbt_amplitude `f116cc3213`: `unique_session_id` did not exist yet.
  - dbt-labs segment `f78a569d06`: the fix tracks a dbt-utils release.
- **Fix subject outside the keyword list:** dbt_intercom: the missing `part_type = 'close'` filter in `ff51276`, fixed by `a469289` ("close clause added"). dbt-labs snowplow: the bot-filter weakening in `fab51b2b9a`, fixed by `84ce2dc92c` ("update BR Type filter").
- **Needs source-data or domain knowledge (not recorded):**
  - dbt_intercom: first_admin_response.
  - adwords `f8039bcdf0`: duplicate gclids.
  - Most Vocabulary-v5.0 fixes.
  - DataQualityDashboard `2af8068543`: the plausible-gender csv holds lists.
  - analytics-refinery `a203f30c33` (padded month) and `a887aae794` (an outside API).
- **Consistent with the repository, or a design choice:**
  - Vocabulary-v5.0 `0db9f8880f` (the condition was carried over unchanged) and `053933116a`.
  - dbt-utils `e9db777ea4`.
  - dbt_github `4155417184`: the inner join to requested reviewers was there from the start.
  - dbt_zuora `68ef299cb2`: a change of date spine.
- **Uncertain or ambiguous origin:**
  - dbt-utils `423277393a`: the effect of Jinja scoping.
  - dbt-expectations `9b567fccff`: the `ignore_row_if` logic came from several earlier commits across several files.
  - dbt-audit-helper: `conflicting_values` from `361fc5f` and `4560b5c`.
  - dbt-date `010752e461`: depends on dbt core `dateadd`.
  - DataQualityDashboard `2fc64f14d0`: a platform cast.
