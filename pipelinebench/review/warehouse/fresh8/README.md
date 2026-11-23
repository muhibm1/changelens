# fresh8: real warehouse bugs from public SQL, dbt and Dataform repositories

Labelled by an independent labeller that never read, opened or ran ChangeLens, pipelinebench or any earlier set's
labels (other than the format example it was handed), and never ran a model. Every label comes from the repository's
own history.

## How the bugs were found

1. Candidate repositories came from public web search (dbt packages, open data warehouses, Dataform projects,
   Redshift admin scripts), the awesome-dbt and awesome-public-dbt-projects lists, and GitHub owners known to publish
   warehouse SQL. Every repository named in `excluded-repos.txt` or present in any earlier set's clone folder
   (fresh-repos, -4, -5, -6, -7) was skipped.
2. Each repository was cloned in full over HTTPS into `/home/user/fresh-repos-8/<name>`.
3. A script listed non-merge commits whose subject matches fix, revert, hotfix, wrong, duplicat, fanout, null, filter,
   missing, incorrect, dedup, join, bug, broken or typo, that touch at most six SQL or YAML files (JS too for the
   Dataform project) and change at most 300 lines.
4. For each candidate the lines the fix removed were blamed on the fix's parent to find the introducing commit.
   Each fix and introducing commit was then read by hand. A bug was kept only when the introducing commit added the
   faulty lines itself (not a move or refactor of older code), the fix descends from it, and the introducing commit
   changes under 400 lines of SQL, YAML or SQL-bearing JS.
5. `compiles` was decided from the code: false when the template or the warehouse would refuse the SQL (Jinja
   errors, undefined variables or columns, syntax errors, type mismatches the warehouse rejects). `needs_data` was
   set when seeing the bug requires knowing the data or an outside system (vendor log semantics, value formats in a
   source column, a domain rule); those bugs are kept with `in_scope: false`.
6. A build script filled `parent` (first parent), `title` and `fix_title` (exact subjects) from git, resolved each
   `expect` line in the introducing commit's own version of the file and checked it is non-empty, checked
   `git merge-base --is-ancestor`, checked that no file contains an em or en dash, and parsed every YAML with PyYAML.

## Repositories used (15)

| Repository | Warehouse / templating | Bugs |
|---|---|---|
| awslabs/amazon-redshift-utils | Redshift, plain SQL | 4 (1 needs_data) |
| Datavault-UK/automate-dv | Snowflake, BigQuery, SQL Server, Databricks, Postgres; dbt Jinja macros | 4 |
| cagov/data-infrastructure | Snowflake, dbt | 1 |
| ScalefreeCOM/datavault4dbt | BigQuery, Snowflake, Postgres, Redshift; dbt Jinja macros | 5 |
| Snowflake-Labs/dbt_constraints | Snowflake, dbt Jinja macros | 1 |
| fivetran/dbt_fivetran_utils | multi-adapter dbt macros | 2 |
| dbt-labs/dbt_metrics | multi-adapter dbt macros | 4 |
| fivetran/dbt_salesforce_formula_utils | dbt Jinja macro generating SQL | 1 |
| DFE-Digital/dfe-analytics-dataform | BigQuery, Dataform (SQL inside JS) | 5 |
| davidgasquez/filecoin-data-portal | DuckDB, dbt | 1 |
| make-open-data/make-open-data | Postgres, dbt | 2 (1 needs_data) |
| matsonj/nba-monte-carlo | DuckDB, dbt | 5 |
| fecgov/openFEC | Postgres, plain SQL (views, functions, DDL) | 5 (1 needs_data) |
| rittmananalytics/ra_data_warehouse | BigQuery, dbt | 2 |
| dcaribou/transfermarkt-datasets | DuckDB, dbt | 2 (1 needs_data) |

Per owner: fivetran has 2 repositories and 3 bugs; every other owner has 1 repository and at most 5 bugs.

## Counts

- Bugs: 44. In scope: 40. Needs data: 4 (`needs_data: true, in_scope: false`).
- In scope by `compiles`: true 27, false 13. The 4 needs_data bugs are all `compiles: true` (31 true and 13 false
  over all 44).
- In scope by category: business_logic 12, other 13, row_count_change 8, nullability_or_cast 7, schema_contract 0.
- Needs data by category: business_logic 3, nullability_or_cast 1.

## Tried and dropped

Clones refused (repository private, renamed or gone): carto/analytics-toolbox-core,
ministryofjustice/create-a-derived-table, FlipsideCrypto/sql_models, Artemis-xyz/dbt, dbt-labs/netsuite,
dbt-labs/heap, dbt-labs/hubspot, PEDSnet/pedsnet_dcc_etl, uktrade/data-flow, MITLibraries/dss-dbt,
lidofinance/dune-queries, Uniswap/uniswap-dbt, gitcoinco repositories, mattermost/mattermost-data-warehouse,
mbta/dbt and a few others found only by name.

Cloned and dropped:

| Repository | Reason |
|---|---|
| datadex | no SQL fixes in history |
| sqlmesh-examples | 29 commits |
| dune-v1-abstractions | history squashed, 28 commits |
| danish-democracy-data | too few commits |
| SOMA-B2B-SaaS | too few commits |
| dbt-activity-schema | too few commits |
| re-data | no usable fix candidates |
| balboa | fixes are tooling only |
| nr-dap-ods | candidates were formatting or unclear |
| analytics-wmde-scripts | weak candidates |
| openaq-db | weak candidates |
| N3C Data-Ingestion-and-Harmonization | header and typo fixes only |
| openalex-guts | fixes in Python UDFs, not SQL |
| dbt_reddit_ads | no usable fix candidates |

Individual candidates rejected in used repositories: introducing commits over 400 lines (dbt_metrics rolling
constructor and composite join, amazon-redshift-utils distribution check), duplicate mechanisms of a bug already
kept, fixes whose blamed lines were moved from older code rather than written in the blamed commit (several in
dfe-analytics-dataform), unclear WIP chains (automate-dv ghost records, datavault4dbt end_of_all_times), and
domain-only rules (sports seeding, FEC parity). One needs_data candidate in nba-monte-carlo was left out by the
five-per-repository cap.

## Gaps

- No public SQLMesh project with enough ordinary history was found, and no Spark or Hive SQL repository with usable
  fixes, so those engines are not represented.
- No schema_contract bug (a YAML test or contract contradicting its model) qualified.
