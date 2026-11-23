# fresh9 labels

Independent labels for 39 real bugs in 17 public SQL and dbt warehouse repositories. Every label comes from the repositories' own git history. No reviewer output, earlier labels (other than the example file) or model was consulted.

## How I searched

1. Searched GitHub for public repositories with SQL models: dbt projects, SQLMesh, Dataform, plain SQL, Spark/Hive and warehouse utility repos. Dropped the ones listed in `brief/excluded-repos.txt`. The fresh8 list was not visible to me, so I relied on that file.
2. Full clones into `/home/user/fresh-repos-9/<repo>`.
3. Mined each history for fix-like commits (fix, bug, typo, wrong, incorrect, missing, revert) that touch SQL or YAML, and kept small fixes (at most about 40 changed SQL lines).
4. For each fix, ran `git blame` on the fix's parent to find the commit that introduced the faulty lines. Where blame landed on a move or rename, I followed it with `git log -S` or dropped the case.
5. Read the introducing commit's version of the file to confirm it added the faulty line. Checked that the fix descends from the introducing commit (`git merge-base --is-ancestor`), that the introducing commit is under about 400 lines of SQL and YAML, and that each `expect` line is non-empty in that version.
6. A script (`gen.py`) resolved full ids, first parents, exact subjects, ancestry and `expect` line numbers, and rejected any dash character or non-2-word `must_mention`.

## Dropped

- Introducing commit over 400 lines of SQL and YAML (for example a duckdb time model, the first dbt_duckdb_demo_public candidate).
- Blame landed on a move, rename or refactor (parts of re-data, dbt-cost-optimization and sagerx).
- Fix was config, CI, docs, style or a feature.
- Outcome depends on the data in the warehouse. I considered these as needs_data but none was clean enough to include.
- Repositories scanned with no usable cases: dataform and sqlmesh (tooling code, not warehouse SQL), cortex-data-foundation, eia, sao-paolo, tff, octocatalog, dbt-ngpvan, dbt-tpcdi, wald-stack-demo, dbt-activity-schema, ga4dataform-community, CommonDataModel, OMOP-Queries, analytics-reportupdater-queries, fhir-dbt-analytics, dbt_shopify, dbt_jira_source, dbt_salesforce_source, dbt_netsuite_source, dbt_quickbooks_source, dbt_duckdb_demo_public, dre.

## Counts

- Repositories: 17. Bugs: 39. In scope: 39. Needs data: 0.
- compiles true: 23 (59 percent). compiles false: 16.
- By category: business_logic 15, other 17, row_count_change 6, nullability_or_cast 1, schema_contract 0.
- Per repository: dbt-data-diff 5; dbt-analytics, sagerx, devstats, dbt_hubspot_source 4; stellar-dbt-public 3; dbt-re-data, dbt-dataquality, dbt_stripe_source, balboa 2; one each for providence, dbt_zendesk_source, dbt-redshift, dbt-cost-optimization, chess_com_bi, adventure-works, ga4-pipeline-framework.
- Per owner: fivetran 3 repositories and 7 bugs, dbt-labs 2 repositories and 2 bugs, every other owner 1 repository and at most 5 bugs.
- Templating and warehouse variety: dbt on Snowflake, BigQuery, Redshift, Postgres and DuckDB, SQLMesh (adventure-works), Dataform (ga4-pipeline-framework), plain SQL and Postgres (devstats, sagerx).

## compiles

`compiles: false` means the faulty version cannot compile or run: invalid SQL or Jinja, or a reference to a model, column or macro that does not exist. `compiles: true` means a silent wrong result.

## Files

One YAML per repository, `repos.txt`, and `manifest.sha256`.
