# fresh3: a held-out set of labelled warehouse bugs

Real bugs from public SQL and dbt warehouse repositories, each located at the commit that introduced it and labelled
from the later commit that fixed it. The labels come only from public upstream history. The labeller never read,
opened, grepped or ran ChangeLens (or anything under the ChangeLens repository) and never read the earlier scoring
results, so nothing here is derived from ChangeLens's output. This set is meant to be scored once.

32 bugs in total, 31 of them in scope, across 13 repositories. No repository has more than 12.

## Files

- One YAML per repository, named after the repository (`bigquery-etl.yaml`, `dbt_stripe.yaml`, and so on). Each has
  top-level `repo`, `url` and `bugs`, with the same fields as `dev.yaml`: `commit`, `parent`, `title`, `fix`,
  `fix_title`, `bug`, `category`, `in_scope`, `must_mention`, `expect`.
- `repos.txt`: the local clone path and the upstream URL of each repository, one per line. The clones are kept in
  `/home/user/fresh-repos/`.
- `manifest.sha256`: `sha256sum` of every YAML file.

Commit ids are 10-character prefixes. Ids made only of digits (`6189002586`, `6978139496`, `2500350295`) are quoted
so YAML reads them as strings. `must_mention` words that YAML would otherwise read as a boolean, null or number are
quoted for the same reason.

## Repositories

| Repository | URL | Commits at clone | SQL files | Bugs | In scope |
|---|---|---|---|---|---|
| bigquery-etl | https://github.com/mozilla/bigquery-etl | 9,402 | 2,502 | 12 | 12 |
| aspects-dbt | https://github.com/openedx/aspects-dbt | 373 | 53 | 3 | 3 |
| dbt_zendesk | https://github.com/fivetran/dbt_zendesk | 760 | 142 | 3 | 3 |
| dbt_quickbooks | https://github.com/fivetran/dbt_quickbooks | 641 | 221 | 2 | 2 |
| dbt_stripe | https://github.com/fivetran/dbt_stripe | 585 | 120 | 2 | 1 |
| dbt_jira | https://github.com/fivetran/dbt_jira | 563 | 96 | 2 | 2 |
| dbt_salesforce | https://github.com/fivetran/dbt_salesforce | 371 | 69 | 2 | 2 |
| dbt_hubspot | https://github.com/fivetran/dbt_hubspot | 650 | 335 | 1 | 1 |
| dbt_recharge | https://github.com/fivetran/dbt_recharge | 128 | 71 | 1 | 1 |
| dbt_apple_store | https://github.com/fivetran/dbt_apple_store | 192 | 82 | 1 | 1 |
| dbt_servicenow | https://github.com/fivetran/dbt_servicenow | 186 | 67 | 1 | 1 |
| dbt_qualtrics | https://github.com/fivetran/dbt_qualtrics | 67 | 80 | 1 | 1 |
| dbt_netsuite | https://github.com/fivetran/dbt_netsuite | 432 | 157 | 1 | 1 |

bigquery-etl is plain BigQuery SQL, not dbt; only `sql/` query and view files were considered. aspects-dbt is the
Open edX analytics dbt project on ClickHouse. The rest are Fivetran's public dbt packages, which together make up
the largest share of repositories but no more than 3 bugs each.

## Method

The same method as the parent `README.md`:

1. Clone each repository in full, anonymously, into a scratch folder (`/home/user/fresh-repos/`).
2. List non-merge commits whose subject matches fix, revert, hotfix, wrong, duplicate, fanout, null, filter,
   missing, incorrect, dedup or join, and which touch at most six SQL or YAML files under `models/` (under `sql/`
   for bigquery-etl) and change at most 300 lines in them.
3. Run `git blame` on the fix's parent over every line the fix removes or replaces, to find the commits that wrote
   those lines. Keep candidates whose introducing commit changed under 400 lines.
4. Read the fix diff, its message (and the CHANGELOG entry when there is one) and the introducing commit by hand.
   Keep a bug only when the introducing commit itself added the faulty lines, and the problem is visible to a careful
   reviewer of that diff with the repository at hand.
5. Record the lines in the introducing commit's own version of each file and check them by printing
   `git show <commit>:<path>`. The generator also checks that every span is non-empty in that version and that the
   fix descends from the introducing commit.

`must_mention` holds two or three words a correct finding must contain, preferring column and model names and the
mechanism word (fan, duplicate, null, dropped).

## Counts by category

| Category | Bugs | In scope |
|---|---|---|
| row_count_change (join fanout, null join keys, dropped rows from a filter or inner join, removed filter) | 17 | 17 |
| other (invalid SQL or Jinja, wrong join key, keys built with concat, dropped pass-through columns) | 7 | 7 |
| nullability_or_cast (joins on a nullable or wrong key that leave columns null) | 3 | 3 |
| dropped_or_renamed_column_with_readers | 2 | 2 |
| schema_contract (duplicate output column names) | 2 | 2 |
| business_logic (out of scope) | 1 | 0 |
| **Total** | **32** | **31** |

The out-of-scope bug is dbt_stripe `6189002586`, which divides count columns by 100 as if they were cents. It is a
units mistake, not something lineage, schema or row-count reasoning sees.

## Notes on individual labels

- dbt_netsuite `21d48ef48b` is fixed by a revert that gives no reason. It is kept because the defect follows directly
  from the diff: the block selecting `balance_sheet_transaction_detail_columns` is replaced by a loop over
  `accounts_pass_through_columns`.
- dbt_salesforce `a2bd63a9b7` is itself the fix for `1b026344ce` and introduced its own bug (two aliases on one
  column), fixed one minute later. Both are listed as separate defects.
- dbt_zendesk `149477ada4` is titled as a fix; it added a second `sla_breach_at` column, fixed by the next commit.
- dbt_jira `2d27665f2c`: blame on the fix's parent names a later commit (`96b80afb1`) for the `last_value` line,
  but that commit only edited the expression. The CTE without a per-issue collapse and the join that fans out were
  added by `2d27665f2c`, so the bug is placed there.
- bigquery-etl `e7cf0dc09e` is a one-line commit; the raw `device_id` line it duplicates was added by the commit
  before it (`1a5bf3c9e7`), but the duplicate column name exists only from `e7cf0dc09e`.
- Several Fivetran fixes are on feature branches before release (for example dbt_servicenow, dbt_qualtrics,
  dbt_recharge). They still fix a real defect in the introducing commit's diff.

## Repositories tried and what was rejected

Unreachable: `ministryofjustice/create-a-derived-table` (anonymous clone refused, the page returns 403 through the
proxy), `meltano/squared`, `FlipsideCrypto/solana-models` and `FlipsideCrypto/ethereum-models` (anonymous clone
refused), `fivetran/dbt_braintree` and `fivetran/dbt_chargebee` (clone failed).

Cloned and scanned with no bug kept: fivetran `dbt_shopify`, `dbt_xero`, `dbt_marketo`, `dbt_marketo_source`,
`dbt_hubspot_source`, `dbt_jira_source`, `dbt_netsuite_source`, `dbt_zendesk_source`, `dbt_klaviyo`,
`dbt_ad_reporting`, `dbt_app_reporting`, `dbt_amplitude`, `dbt_mixpanel`, `dbt_github`, `dbt_asana`, `dbt_intercom`,
`dbt_iterable`, `dbt_linkedin`, `dbt_google_ads`, `dbt_microsoft_ads`, `dbt_mailchimp`, `dbt_pendo`, `dbt_recurly`,
`dbt_sage_intacct`, `dbt_workday`, `dbt_zuora`, `dbt_twilio`, `dbt_greenhouse`, `dbt_lever`, `dbt_facebook_ads`,
`dbt_google_play`, `dbt_youtube_analytics`, `dbt_amazon_selling_partner`.

Rejected candidates, by reason:

- Cause predates the faulty concept. Many Fivetran fixes add a missing `source_relation` to a join (dbt_quickbooks
  `c96c8d8e83`, dbt_klaviyo `e1cb9c503c` and `ba7c621521`, dbt_xero `c3b3de5b57`): blame names a commit written
  before `source_relation` existed, so the introducing commit did not add the faulty line. Same for dbt_shopify
  `903e5629b4` (customer tags), where the null-for-no-orders behaviour predates the blamed refactor.
- Semantics unchanged at the blamed commit: dbt_quickbooks `b10353f76d` (the blamed commit turned `cross join ...
  where` into an equivalent inner join; the row-dropping behaviour was older).
- Blamed commit is a rename or a move: dbt_github `4155417184` (the inner join line was last touched by a column
  rename in a commit that also added a 5,299-line log file), dbt_hubspot_source `1371ceda7d` (the column was removed
  by an untraceable later edit).
- Introducing commit over 400 lines: dbt_linkedin `f0d76e2703` (tautological `report.source_relation =
  report.source_relation`, introduced in a 903-line commit), dbt_microsoft_ads `96f893253d`, dbt_intercom
  `cbdbe0d8b2` and `c316dee424`, dbt_zuora `2fc2a0a211`, dbt_ad_reporting `ecd1f94806`, dbt_hubspot `30af8e5724`
  and `e96667edcd`, dbt_quickbooks `74ba053a2c`, dbt_jira `aace7ac967`, bigquery-etl `e9c394aaf5` and
  `b238fefead`, bigquery-etl `5a17168e63` (809 lines with test fixtures).
- Needs knowledge of the source data that no reviewer of the diff has: bigquery-etl `7b67cd7831` (duplicates in an
  upstream subscriptions table), `c618c32847` (duplicate source pings), `eb32385805` and `4a95d53023` (subscription
  attribution rules); dbt_zendesk `f3e117fc1b` (erroneous audit log rows with identical timestamps);
  dbt_servicenow `9ba23b5ddc` (`sys_created_by` holds a user name); dbt_app_reporting `bb7ad2369c` (grain of the
  traffic source report).
- Business rules rather than data-shape defects: bigquery-etl `571d12011c`, `837d4555fa`, `1762aad761`,
  `f91ab569f4`; dbt_netsuite `208ee23fae` and `d595bf30e6`; dbt_workday `2d5b0be633`; aspects-dbt `606029fc31`,
  `4ac287faab`.
- Work in progress spread over several commits so no single introducer: dbt_jira `23895b4e34` (sprint join),
  dbt_amplitude `f116cc3213`, dbt_mailchimp `6a37d30998`, dbt_quickbooks `4731e55fde`, aspects-dbt `9d790efd55`.
- Introducing line came from a different commit than the defect: bigquery-etl `92de3382a9` (the inner joins were
  correct until a later commit added Fenix data).
- bigquery-etl reached the cap of 12, so further bigquery-etl candidates were not labelled.
