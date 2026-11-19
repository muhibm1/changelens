# fresh5: a held-out set of labelled warehouse bugs

Real bugs from public SQL and dbt warehouse repositories, each located at the commit that introduced it and labelled
from the later commit that fixed it. The labels come only from public upstream history (the fix commit, its message
and its diff). The labeller never read, opened, grepped or ran ChangeLens (or anything under the ChangeLens
repository other than the three fresh4 format files), never read any scoring results, never ran a model, and did not
reuse any repository from the earlier sets (every name under `fresh-repos/` and `fresh-repos-4/`, plus spellbook,
opensource-observer, ol-data-platform, tuva, cal-itp data-infra, bigquery-etl and aspects-dbt, and the source
packages of excluded Fivetran packages). This set is meant to be scored once.

40 bugs in total, all 40 in scope, across 15 repositories. No repository has more than 5.

## Files

- One YAML per repository, named after the repository. Each has top-level `repo`, `url` and `bugs`, with the same
  fields as `dev.yaml` and fresh4: `commit`, `parent`, `title`, `fix`, `fix_title`, `bug`, `category`, `in_scope`,
  `must_mention`, `expect`. Every commit id is quoted, so YAML always reads it as a string.
- `repos.txt`: the local clone path and the upstream URL of each repository. Clones are in `/home/user/fresh-repos-5/`.
- `manifest.sha256`: `sha256sum` of every YAML file.

Commit ids are 10-character prefixes. `parent` is the first parent of the introducing commit.

## Repositories

| Repository | URL | Commits at clone | SQL files | Bugs | In scope |
|---|---|---|---|---|---|
| snowflake | https://github.com/Sage-Bionetworks/snowflake | 1,212 | 386 | 5 | 5 |
| mimic-code | https://github.com/MIT-LCP/mimic-code | 2,374 | 562 | 5 | 5 |
| dbt-ga4 | https://github.com/Velir/dbt-ga4 | 423 | 72 | 4 | 4 |
| dbt_ga4_export | https://github.com/fivetran/dbt_ga4_export | 103 | 17 | 4 | 4 |
| Achilles | https://github.com/OHDSI/Achilles | 767 | 414 | 4 | 4 |
| dbt-snowflake-monitoring | https://github.com/get-select/dbt-snowflake-monitoring | 380 | 33 | 3 | 3 |
| data-engineering | https://github.com/NYCPlanning/data-engineering | 3,098 | 1,356 | 3 | 3 |
| ethereum-etl-airflow | https://github.com/blockchain-etl/ethereum-etl-airflow | 1,925 | 92 | 3 | 3 |
| data-architecture | https://github.com/ccao-data/data-architecture | 2,100 | 199 | 2 | 2 |
| ETL-Synthea | https://github.com/OHDSI/ETL-Synthea | 300 | 66 | 2 | 2 |
| dbt_facebook_pages_source | https://github.com/fivetran/dbt_facebook_pages_source | 89 | 12 | 1 | 1 |
| dbt_salesforce_marketing_cloud | https://github.com/fivetran/dbt_salesforce_marketing_cloud | 62 | 42 | 1 | 1 |
| dbt_sap | https://github.com/fivetran/dbt_sap | 175 | 265 | 1 | 1 |
| polygon-etl | https://github.com/blockchain-etl/polygon-etl | 482 | 47 | 1 | 1 |
| eicu-code | https://github.com/MIT-LCP/eicu-code | 231 | 28 | 1 | 1 |

dbt-ga4 (Velir) and dbt-snowflake-monitoring (SELECT) are open dbt packages with real models. data-architecture is
the Cook County Assessor's Office dbt warehouse on Athena; data-engineering is NYC Planning's dbt and SQL build
repository. snowflake is Sage Bionetworks' Synapse data warehouse, kept as versioned Snowflake migrations (dynamic
tables, tasks and table DDL under `synapse_data_warehouse/`). mimic-code and eicu-code (MIT LCP) hold the derived
clinical concept SQL for the MIMIC and eICU databases. Achilles and ETL-Synthea (OHDSI) are SQL over the OMOP common
data model. ethereum-etl-airflow and polygon-etl (blockchain-etl) hold the BigQuery SQL stages of the public crypto
datasets. The rest are Fivetran packages not used before.

## Method

Same method as `../fresh4/README.md`:

1. Clone each repository in full, anonymously, into `/home/user/fresh-repos-5/`.
2. List non-merge commits whose subject matches fix, revert, hotfix, wrong, duplicate, fanout, null, filter, missing,
   incorrect, dedup or join (case-insensitive), and which touch at most six SQL or YAML files under a `models/`
   folder and change at most 300 lines in them. Where a repository keeps its SQL elsewhere, the equivalent folder was
   used instead: `concepts*/` (mimic-code, eicu-code), `sql/` (Achilles, ETL-Synthea, data-engineering's older
   products, polygon-etl), `stages/` (ethereum-etl-airflow) and `synapse_data_warehouse/` (snowflake).
3. Run `git blame` on the fix's parent over every line the fix removes or replaces, to find the commits that wrote
   those lines. Keep candidates whose main introducing commit changed under 400 lines of SQL and YAML.
4. Read the fix diff, its message and the introducing commit by hand. Keep a bug only when the introducing commit
   itself added the faulty lines and a careful reviewer of that diff, with the repository at that commit in hand and
   no data, could in principle see the problem. A bug was dropped when the repository at that commit documented or
   tested the opposite of the fix, when seeing it needed knowledge of the source data, or when the fix was a feature,
   style change, rename, or a fix for a later upstream change.
5. Record the lines in the introducing commit's own version of each file and check them by printing
   `git show <commit>:<path>`. The generator checks that every line is non-empty in that version, that the fix
   descends from the introducing commit (`git merge-base --is-ancestor`), and that no text field contains an em or
   en dash. Every YAML parses with PyYAML.

`must_mention` holds two words a correct finding would use, preferring column and model names and the mechanism
word (null, comma, paren, alias, duplicate).

## Counts by category

| Category | Bugs | In scope |
|---|---|---|
| row_count_change (temporary filter left in a staging model) | 1 | 1 |
| nullability_or_cast (full outer join keys coalesced from one side only, sum of nullable doses, case-sensitive variant paths lowercased) | 3 | 3 |
| schema_contract (YAML model names that match no model, duplicate output columns, test grain that contradicts the model, unaliased output column, mismatched count column, DDL on the wrong table) | 9 | 9 |
| other (invalid SQL or Jinja: double WHERE, missing comma or paren, stray paren, duplicate alias, trailing comma, unbalanced Jinja if, insert value count; reference to a column, alias, relation or task that does not exist; wrong ref or wrong source table; scalar subquery that groups) | 27 | 27 |
| **Total** | **40** | **40** |

## Notes on individual labels

- Several introducing commits hold two separate defects. dbt-ga4 `033c0ef6f7` (a reference to `ga_session_number`
  and a `user_id` missing from the conversion branch, both repaired by `32ca44bc07`, which also repairs
  `0aa1833a17`); snowflake `36e71c942d` (duplicated select columns fixed by `88e51aaead`, an empty insert column fixed
  by `c774da95e3`); data-engineering `1e99fc6987` (two buffer models that ref the wrong intermediate model, fixed by
  two commits); Achilles `7fd900cc37` (missing commas in one export and a misplaced paren in another, both fixed by
  `6bc5d745c9`). Each is listed as a separate bug.
- mimic-code: blame on the parent of `f3d003919f` named `8c07b5cc3d`, but the duplicate alias was made by
  `8292ad7b1b`, which added a second `AS norepinephrine_equivalent_dose` and left the old one; the bug is placed there.
  Likewise blame on `cfa61b067f` named `57533a99f9` for the old `rate as vaso_rate` line, and `git log` on the file
  found `621fcba8e9`, which added the second `vaso_rate` beside it. `8c07b5cc3d` is also kept for its own defect (a
  sum of nullable doses), repaired by `8292ad7b1b`.
- polygon-etl: blame on `dcd31e0f2a` named the 2021 commit that wrote the original `{% if %}` line; `git log -S`
  found `1532f22a58`, which added the new if/elif above it without removing it.
- eicu-code `e5cf1e124b` blamed `7e04cb475d` ("tidying") for the stray parens; it rewrote the query and wrote them.
- mimic-code `1b50aa73ec` and snowflake `6bfb6ab296`: the files were later renamed or copied
  (`concepts/pivot/pivoted-height.sql` became `mimic-iii/concepts/pivot/pivoted_height.sql`; `V2.23.1__update_node_tasks.sql`
  was carried into `V2.25.1`); `expect` uses the introducing commit's path.
- Several kept bugs are in work-in-progress commits fixed within minutes (dbt_ga4_export `6a11b5ad5d`, `d38f37da5c`,
  `796ed73881`; dbt-ga4 `0aa1833a17`); they are kept because the faulty lines are all their own, as in fresh4.
- dbt-snowflake-monitoring `f71f206c60` is a reformat, but it is the commit that changed the casing of the variant
  paths, so it authored the defect.
- snowflake is a migration repository; its kept bugs are in the dynamic table, task and table SQL that forms the
  warehouse.

## Repositories tried and what was rejected

Unreachable or not public: `mattermost/mattermost-data-warehouse`, the FlipsideCrypto `*-models` repositories,
`gitlab-data/analytics` (gitlab.com), and Fivetran `dbt_harvest`,
`dbt_pagerduty`, `dbt_freshdesk`, `dbt_google_search_console`, `dbt_braintree`. Empty repositories: Fivetran
`dbt_outreach`, `dbt_survey_monkey`, `dbt_typeform`, `dbt_survey_monkey_source`, `dbt_typeform_source`.

Cloned and deleted unscanned, too little SQL: `LBHackney-IT/Data-Platform`,
`GoogleCloudPlatform/marketing-analytics-jumpstart`, `near/near-analytics`, `dbt-labs/mrr-playbook`,
`alphagov/govuk-knowledge-graph-gcp`.

Cloned and scanned with no bug kept: `dagster-io/dagster-open-platform` (no matching commit in the models folder),
Fivetran `dbt_apple_search_ads`, `dbt_apple_search_ads_source`, `dbt_customer360`, `dbt_facebook_pages`,
`dbt_instagram_business`, `dbt_instagram_business_source`, `dbt_linkedin_pages`, `dbt_linkedin_pages_source`,
`dbt_pardot`, `dbt_pardot_source`, `dbt_social_media_reporting`, `dbt_twitter_organic`, `dbt_twitter_organic_source`,
`dbt_dynamics_365_crm`; `dbt-labs/snowflake_spend`; `OHDSI/ETL-CMS`.

Rejected candidates, by reason (examples):

- Needs knowledge of the source data or of an external system: dbt-ga4 `1a606a5158` (null session_key),
  `b694fa48a1` (session_engaged type), `0aa1833a17`'s duplicate-session rationale, `6c5b989e40` (table suffix length);
  dbt_social_media_reporting `a9866bc238` (Instagram fields repeat a value); dbt-snowflake-monitoring `970bc7e5cb`
  (service type renamed by Snowflake later); data-architecture `7f8d4d4531` (new ARI years), `1f2990266d`;
  data-engineering `8c3603f4e1` (zoning label text); ETL-Synthea `383711aaa0`; ethereum-etl-airflow `abf53b6166`
  (EIP-1559), `d00d9e0eca`.
- Consistent with the repository at the introducing commit, so not a defect a reviewer could see: data-engineering
  `7e0d43562e` (tracts use the same denominator), `01ea7b793a` (other flag models used the same column names),
  `8069b400fd` (the previous release used LoMod).
- Blamed commit is a move or reorganisation: data-engineering `6bdb409f03` (`test:` typo moved by `46c80f12fa`).
- Fix subject outside the keyword list: data-architecture `36837d7b` ("Correct quotes"), though it repairs a real
  defect in `3f926de88e`.
- Feature or design change rather than a repair: dbt-ga4 `7598e21c54`, `00b8464b81`, `12fc0883a2`;
  dbt-snowflake-monitoring `0d04288055`, `1c99bd0828`, `d83335cf4d`; data-architecture `3aec3a654f`, `ca4244dd1a`;
  dbt_ga4_export `60f210b51f` (source_relation across several commits, no single introducing commit);
  ethereum-etl-airflow `4c185d42dd`; snowflake `8edfab9a78`.
- Latent with no behaviour change: dbt-ga4 `ab102e92ed` (debug filters that never match); Achilles `fd825fea5a`
  (missing final semicolon in a file run on its own).
- Introducing commit over 400 lines: dbt-ga4 `4e983f163f`; dbt-snowflake-monitoring `9bafe63659`, `b3fb450ed2`;
  dbt_sap `032c685d84`; many data-engineering, mimic-code and Achilles candidates whose blame lands on bulk imports.
- Not enough reading time to confirm the dependency on code outside the diff: Achilles `5a33d22e0f` (export column
  names read by R code).
