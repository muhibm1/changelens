# Design partner: cal-itp/data-infra (dbt on BigQuery, real graph)

[cal-itp/data-infra](https://github.com/cal-itp/data-infra) is California's public transit data warehouse: 625 dbt models, 219 sources, 17 seeds, 344 tests, 5 exposures, Airflow DAGs. The manifest and catalog are the ones the project publishes at dbt-docs.calitp.org (current main, 2026-10-16); Orion did not run dbt.

## Sync at scale

| | before | after |
| --- | --- | --- |
| first sync | over 20 minutes, killed | 2m 9s |
| forced re-sync (lineage cached) | | 13s |
| models with column lineage | 4 of 625 | 621 of 625 |
| graph | | 25,327 dbt nodes (columns included), 32,971 edges |

What it took: bulk writes through staged temp tables instead of a row at a time; lineage from dbt's `compiled_code` in the adapter's dialect (BigQuery here) instead of de-Jinja'd source; one sqlglot pass per model over only the tables it reads, typed `UNKNOWN` (empty type strings broke sqlglot's type annotation); ephemeral-model CTEs detached so lineage stops at the ephemeral model; a process pool above 40 models and a cache keyed by SQL, dialect and schema.

## Review of 12 merged commits, deterministic only

Commits: b0b73b32, fbe45897, 008d78a8, 6f1cc4b7, ad497afc, 34716dcc, 2f53a705, 6a6df64e, 3127f574, e636f6e2, 2eff9b46, 5b9885a0 (fixes, refactors, column additions, model removals).

| | first pass | after fixes |
| --- | --- | --- |
| commits with every path mapped to an asset | 5 | 10 |
| warnings | 7 | 2 |
| warnings that were only "paths not mapped" | 7 | 2 (both honest, see below) |

Every first-pass warning came from files Orion could not map, dressed as a blast-radius finding ("Change reaches 7 downstream assets, 0 tier 1"). The fixes:

- **Shared schema .yml files** map to the models they document (dbt's `patch_path`), one op per entry the diff changed. Editing one model's tests in `_payments.yml` touches that model, not all thirty in the file. A `config:` change is a modify, because it can change the build; tests and descriptions are metadata.
- **Hand-written tests** (`tests/*.sql`) map to the models they read.
- **Markdown** (doc blocks) is listed under `ignored`, not `unresolved`.
- A warning raised only because paths are unmapped now says so: "2 changed paths are not mapped to any asset", with the paths.
- cal-itp records no tiers, so summaries say "no tiers are recorded" instead of "none are tier 1".

The two warnings left are model deletions (fbe45897, 008d78a8). The published manifest is newer than those commits, so the deleted models are not in it and Orion says "deleted, and the graph has no asset here; sync the graph from the base branch". That is the right answer: in CI the graph comes from the base branch, which still has the model and its readers.

## Review of the same 12 commits with a model

`orion review --provider anthropic`, Sonnet, whole-file context, second reading on. About 245k tokens in and 9k out across the 12 commits.

| | count |
| --- | --- |
| model findings at warn | 9, in 4 commits |
| model findings at inform | 9 |
| commits with no model finding | 2 (e636f6e2, fbe45897) |
| findings rejected or uncertain | 0 |

One warning is confirmed by the project itself. On 34716dcc (#5767), Orion warned that `ref()` calls in `dim_provider_gtfs_data.sql` were replaced with hardcoded prod table names, "likely a debugging leftover". The next commit in the repository, b0b73b32 (#5768, "fix refs"), puts the `ref()` calls back.

The other warnings are plausible and specific, but not confirmed here. They include renamed output columns in two GTFS summary tables and an NTD time series (3127f574), an inner join that drops NULL `direction_id` rows, and two dedup-ranking edge cases in the Littlepay funding-source models (ad497afc). Each names the reader that breaks and how. None is about style.

## Airflow (gusty)

cal-itp's DAGs are gusty YAML folders. Before, Orion read 30 Airflow nodes from top-level `dags/*.py`. Now it reads 382 (21 DAGs, 348 tasks); 206 of 208 external-table tasks link to the dbt source they write, so editing one external-table task file reaches 18 downstream assets.

## Open

- `dbt_project.yml` changes stay unresolved; project-level config is not modelled.
