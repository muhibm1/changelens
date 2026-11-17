# fresh4: a held-out set of labelled warehouse bugs

Real bugs from public SQL and dbt warehouse repositories, each located at the commit that introduced it and labelled
from the later commit that fixed it. The labels come only from public upstream history (the fix commit, its message
and its diff). The labeller never read, opened, grepped or ran ChangeLens (or anything under the ChangeLens
repository), never read the earlier scoring results, and did not reuse any repository from the earlier sets
(`dev.yaml`, `blind.yaml`, `fresh3/`). This set is meant to be scored once.

29 bugs in total, all 29 in scope, across 12 repositories. No repository has more than 5.

## Files

- One YAML per repository, named after the repository. Each has top-level `repo`, `url` and `bugs`, with the same
  fields as `dev.yaml`: `commit`, `parent`, `title`, `fix`, `fix_title`, `bug`, `category`, `in_scope`,
  `must_mention`, `expect`. Every commit id is quoted, so YAML always reads it as a string.
- `repos.txt`: the local clone path and the upstream URL of each repository. Clones are in `/home/user/fresh-repos-4/`.
- `manifest.sha256`: `sha256sum` of every YAML file.

Commit ids are 10-character prefixes.

## Repositories

| Repository | URL | Commits at clone | SQL files | Bugs | In scope |
|---|---|---|---|---|---|
| dbt_artifacts | https://github.com/brooklyn-data/dbt_artifacts | 742 | 107 | 5 | 5 |
| queries-basedosdados | https://github.com/basedosdados/queries-basedosdados | 2,579 | 628 | 4 | 4 |
| dbt-project-evaluator | https://github.com/dbt-labs/dbt-project-evaluator | 1,426 | 123 | 3 | 3 |
| dbt-data-reliability | https://github.com/elementary-data/dbt-data-reliability | 3,630 | 338 | 3 | 3 |
| queries-rj-smtr | https://github.com/prefeitura-rio/queries-rj-smtr | 474 | 213 | 3 | 3 |
| dbt_tiktok_ads | https://github.com/fivetran/dbt_tiktok_ads | 197 | 53 | 2 | 2 |
| dbt_shopify_holistic_reporting | https://github.com/fivetran/dbt_shopify_holistic_reporting | 205 | 12 | 2 | 2 |
| dbt_mixpanel | https://github.com/fivetran/dbt_mixpanel | 317 | 20 | 2 | 2 |
| dbt-snowplow-unified | https://github.com/snowplow/dbt-snowplow-unified | 93 | 154 | 2 | 2 |
| dbt_iterable_source | https://github.com/fivetran/dbt_iterable_source | 315 | 41 | 1 | 1 |
| dbt_fivetran_log | https://github.com/fivetran/dbt_fivetran_log | 748 | 74 | 1 | 1 |
| teamster | https://github.com/TEAMSchools/teamster | 42,399 | 2,173 | 1 | 1 |

dbt_artifacts (Brooklyn Data), dbt-project-evaluator (dbt Labs) and dbt-data-reliability (Elementary) are open dbt
packages whose models read dbt's own artifacts. queries-basedosdados (Base dos Dados) and queries-rj-smtr (Rio de
Janeiro's transport secretariat) are public BigQuery dbt warehouses with Portuguese commit messages. teamster is the
KIPP TEAM and Family Schools dbt and Dagster warehouse. The rest are Fivetran packages not used before.

## Method

Same method as `../README.md` and `../fresh3/README.md`:

1. Clone each repository in full, anonymously, into `/home/user/fresh-repos-4/`.
2. List non-merge commits whose subject matches fix, revert, hotfix, wrong, duplicate, fanout, null, filter, missing,
   incorrect, dedup or join, and which touch at most six SQL or YAML files under a `models/` folder and change at most
   300 lines in them.
3. Run `git blame` on the fix's parent over every line the fix removes or replaces, to find the commits that wrote
   those lines. Keep candidates whose main introducing commit changed under 400 lines of SQL and YAML.
4. Read the fix diff, its message and the introducing commit by hand. Keep a bug only when the introducing commit
   itself added the faulty lines and a careful reviewer of that diff, with the repository at that commit in hand and
   no data, could in principle see the problem. A bug was dropped when the repository at that commit documented or
   tested the opposite of the fix, or when seeing it needed knowledge of the source data.
5. Record the lines in the introducing commit's own version of each file and check them by printing
   `git show <commit>:<path>`. The generator also checks that every span is non-empty in that version, that the
   fix descends from the introducing commit, and that no text field contains an em or en dash.

`must_mention` holds two or three words a correct finding must contain, preferring column and model names and the
mechanism word (null, comma, paren, unique).

## Counts by category

| Category | Bugs | In scope |
|---|---|---|
| row_count_change (removed or missing filter, inverted filter, right join, spine join that drops a day) | 6 | 6 |
| nullability_or_cast (anti-join column that is always null, null added in a recursive sum, lag that is always null) | 3 | 3 |
| schema_contract (duplicate output columns, uniqueness or test columns that contradict the model) | 4 | 4 |
| other (invalid SQL or Jinja: missing comma, paren, WHERE or alias; reference to a CTE, alias or column that does not exist; unaggregated passthrough column; wrong lag column) | 16 | 16 |
| **Total** | **29** | **29** |

No bug in the closed category `dropped_or_renamed_column_with_readers` or `documented_column_not_selected` met the
bar in these repositories, and no out-of-scope bug was kept.

## Notes on individual labels

- Two introducing commits each hold two separate defects with separate fixes: queries-basedosdados `42251f608e`
  (`select *` over an incremental self-join, fixed by `3132607500`; a join condition that names a CTE not in the
  FROM, fixed by `bb20583373`) and queries-rj-smtr `29b775503d` (a dropped parenthesis, fixed by `c1ebaa8e3e`; an
  inconsistent end date bound, fixed by `bfc599418a`). dbt_mixpanel `ab877baf08` also has two defects fixed in one
  commit (`19ebd2b79e`). dbt-snowplow-unified `fd7fee9491` put the same missing parenthesis in two models, fixed by
  two commits. Each is listed as a separate bug.
- dbt-data-reliability `a74fc552fd` is repaired by `1869b95741`, whose message describes the larger rewrite, not the
  missing comma; the repair is visible in the diff.
- dbt-data-reliability `240d7ba519`: the file lived under `models/models/` at that commit and moved later; `expect`
  uses the introducing commit's path.
- teamster `1349c00a5a` and `e8118404b0` are identical commits on two branches that were both merged; the bug is
  placed at `1349c00a5a`. Blame on the fix's parent named an older commit because the file was moved from
  `stg_csgf__hs_enrollment.sql` to `rpt_gsheets__csgf_hs_enrollment.sql` in between; `git log -S` found the commit
  that added the uncommaed literal.
- queries-rj-smtr `29b775503d` (end date): the change moved completed trips to the end date but left planned trips one
  day short in the same file. It is kept as a filter defect because the inconsistency is inside the diff.
- dbt_mixpanel `ab877baf08` is a work-in-progress commit ("working on monthly events") fixed three days later; it is
  kept because the faulty lines are all its own.

## Repositories tried and what was rejected

Unreachable: `ministryofjustice/create-a-derived-table` (anonymous clone refused again) and
`fivetran/dbt_workday_source` (clone failed).

Not used to respect the exclusion list: the Fivetran source packages of excluded packages (`dbt_hubspot_source`,
`dbt_salesforce_source`, `dbt_netsuite_source`, `dbt_jira_source`) were cloned and deleted unread.

Cloned and scanned with no bug kept: fivetran `dbt_amazon_ads`, `dbt_pinterest`, `dbt_snapchat_ads`,
`dbt_reddit_ads`, `dbt_twitter`, `dbt_shopify_source`, `dbt_xero_source`, `dbt_facebook_ads_source`,
`dbt_google_ads_source`, `dbt_linkedin_source`, `dbt_microsoft_ads_source`, `dbt_greenhouse_source`,
`dbt_lever_source`, `dbt_pendo_source`, `dbt_github_source`, `dbt_asana_source`, `dbt_intercom_source`,
`dbt_klaviyo_source`, `dbt_marketo_source`, `dbt_sage_intacct_source`, `dbt_twilio_source`,
`dbt_google_play_source`, `dbt_amplitude_source`, `dbt_mailchimp_source`, `dbt_zuora_source`, `dbt_recurly_source`,
`dbt_youtube_analytics_source`; snowplow `dbt-snowplow-web`, `dbt-snowplow-mobile`, `dbt-snowplow-utils`,
`dbt-snowplow-media-player`, `dbt-snowplow-ecommerce`, `dbt-snowplow-attribution`, `dbt-snowplow-fractribution`;
dbt-labs `snowplow`, `segment`, `facebook-ads`; `datacoves/balboa`; `rittmananalytics/ra_data_warehouse`;
`re-data/re-data`.

Rejected candidates, by reason:

- Blamed commit is a rename, move, reformat or cosmetic change, not the author of the defect: dbt-project-evaluator
  `be54bd1184` (the `'exposures'` literal predates the blamed column rename) and `8395600309` (the blamed commit only
  moved a CTE); dbt-data-reliability `03b3c5e8d3` and `667333e497`'s join lines (blamed commit is "cosmetics");
  teamster `fae3877bf7` (the region-blind join predates the blamed commit) and `0d78255f8e` (blamed commit is a
  model rename); queries-rj-smtr `25cb57e5e5` (the duplicate alias came from a later commit over 400 lines).
- Dialect or platform only: snowplow web and mobile varchar length fixes (`7f95a8038b`, `3a3d2b21de`),
  dbt-snowplow-ecommerce `6055c20792` (Redshift rejects a constant partition key), dbt_mixpanel `ccf4c497b3` and
  `21f3a62e3c` (timestamp and date casts), dbt_fivetran_log `5e25ebe7ae`, dbt-project-evaluator `b213073cf3` (null
  database on Databricks without Unity Catalog).
- Needs knowledge of the source data or of dbt's manifest that the diff does not carry: dbt-project-evaluator
  `8248b11e7b` and `3caaa9b7ef` (the manifest lists a dependency once per ref), dbt_fivetran_log `438c47ad01`
  (log_id is not unique in the source), queries-basedosdados `60a7331d97` (municipality code formats), teamster
  `ab3daa8621` (yearid is null for one district), dbt_tiktok_ads `e00bd62151`, dbt_artifacts `aa494d006e`.
- Documented as intended at the introducing commit: teamster `06f2c55b88` (the property file said the column is
  null on Y1 rows) and `12a88d3f4c` (the property file recorded the duplicate grain and the unique test).
- Latent with no behaviour change, or a business rule rather than a data-shape defect: teamster `bd181ee12c`,
  `a47bc5a25b`, `c84c32d47a`, `85ed19c7d7`, `713b83762c`, `65e6ab6004`, `ed9d9cff95`, `3a97b3ef08`; dbt_mixpanel
  `053edfa4ff` (definition of a repeat user); dbt_artifacts `3bde04677b`; queries-rj-smtr `50736cafc5`,
  `fc4d4512c7`, `7cfaade0e7`; queries-basedosdados `7b6c72c84f`.
- Work in progress with no defect at the blamed commit: dbt_tiktok_ads `82d9bb7cd2` and `1e8bb2e301` (columns
  commented out and the group-by count kept consistent until both were restored together).
- Fix is a design change rather than a repair: dbt_artifacts `fb177cd799` (join key moved to artifact_run_id),
  dbt_snapchat_ads `d744230194` (columns added that were never documented).
- Introducing commit over 400 lines: many teamster candidates (for example `8d1558d7bb`, `a20a759dc9`) and
  dbt-snowplow-web `0a3b968201`.
- teamster had over 3,000 matching commits; only a sample of the most specific subjects was read, and most fixes
  there either repair commits over 400 lines or fix latent risks with no change in output.
