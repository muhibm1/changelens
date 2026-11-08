# Shadow study: cal-itp/data-infra, four weeks of pull requests, deterministic checks only

**What this is.** [cal-itp/data-infra](https://github.com/cal-itp/data-infra) is California's public transit data warehouse: 625 dbt models on BigQuery and 21 Airflow DAGs, maintained by a team that reviews every pull request. For four weeks of their pull requests (opened or merged between 2026-10-08 and 2026-11-05) this study asks one question: had ChangeLens's pre-flight been posting on those pull requests, would it have agreed with the human reviewers, caught something they missed, or cried wolf?

**The short answer.** On 49 pull requests, ChangeLens 0.2.0's deterministic review raised no warning at all, and the reviewers found no defect that ChangeLens is built to find, with one exception. That exception was a real miss: a model's `ref()` calls were replaced by hardcoded production table names, the reviewer approved it, and it was fixed in a second pull request a few hours later. ChangeLens's change parser reported that it could not parse the model at exactly that line, but raised no finding. The study was then used to fix that: changed SQL is now parsed in the warehouse's own dialect, and a model that reads another asset by its warehouse name is a `warn`. Run again on the same 49 pull requests, ChangeLens warns on that one pull request, twice, on the two lines that need the `ref()` back, and on nothing else. `changelens preflight` on its own would have posted 19 warnings, every one of them "a changed path is not mapped to any asset"; `changelens review` lowers those to inform with a note, which is what the pull request comment uses, so the comment would have carried one warning in four weeks, the right one. Nothing in this study used a model or spent API credit.

**Why it matters.** The earlier cal-itp replay (`calitp-history-deterministic.md`, 116 merged commits over 26 weeks) measured the same checks against history. This study adds the reviewers' side: what people actually commented on, including pull requests still open, and whether ChangeLens's silence matched theirs.

## How it was run

| | |
| --- | --- |
| Pull requests | 49 run: 27 merged, 22 open; 3 closed without merge skipped (#5787, #5790, #5844) |
| Authors | 28 by 6 people, 21 by Dependabot |
| Graph | `changelens sync` over the dbt manifest and catalog the project publishes (generated 2026-10-26) and the repository's `airflow/dags`; 20,248 nodes, 28,033 edges, 1m 32s |
| Per pull request | checkout of the merge commit (merged) or the head (open); `changelens review --git <base> --provider stub --intent "<title and body>"` and `changelens preflight --git <base>`, both with no model |
| Two runs | once at 0.2.0 (`a4d5b0b`), once after the fix this study led to (same graph, re-synced with `--force` so the nodes carry the dialect). The tables below show the 0.2.0 run; "After the fix" shows what changed. |
| Time per pull request | median 1.2s, p90 2.5s, max 7.1s |
| Reviewer side | each pull request's page: review states, review summaries and comments. Inline comment bodies are collapsed on the page and were not readable, so a reviewer finding counts only when the summary or a comment names it. |
| Labels | one reader, from the pull request page, the diff and what happened next (a follow-up fix, a revert). Not derived from ChangeLens's output. |

## What the numbers say

| Call | Pull requests | Meaning |
| --- | ---: | --- |
| agreed | 22 | ChangeLens raised nothing, the reviewers found no defect, and nothing broke afterwards |
| caught something reviewers missed | 0 | |
| missed at 0.2.0, caught after the fix | 1 | #5767: hardcoded production table names in `dim_provider_gtfs_data`, approved, fixed in #5768 the same day; 0.2.0 reported `SQL did not parse` at the hardcoded name and did not warn; the fixed build warns on both names |
| false alarm | 19 at preflight severity, 0 at review severity | every one is "N changed paths are not mapped to any asset": lockfiles, test fixtures, operator code, deleted DAGs |
| out of scope | 7 | Terraform, Kubernetes, GitHub workflows and a new service: ChangeLens has no adapter and raised nothing, correctly, but says nothing useful either |

Per week over four weeks, at 0.2.0: 0 warnings and 0 false alarms from `changelens review`; 4.75 false alarms from `changelens preflight`. After the fix: 0.5 warnings and 0 false alarms from `changelens review`, the preflight figure unchanged.

What reviewers did find, and whether it is ChangeLens's job:

- **Cleanup leftovers** (#5777, #5796, #5822): stale README sections, environment variables and a Dockerfile that outlived the thing they described. ChangeLens's removed-definition check agreed with the reviewer that `PodOperator` had no remaining callers (#5796); it does not read documentation.
- **Rollout and process** (#5815, #5830, #5845): resources outside Terraform that a pull request would not clean up, deploy concurrency, an unrelated node-count change visible in a Terraform plan. Out of scope: no Terraform adapter.
- **Test design** (#5785, #5850): where tests should live, whether to add a task. #5850 is the only "changes requested" in the window; its diff mapped cleanly (schedule moved from 12:00 to 12:30 UTC, one task added, one removed) and ChangeLens listed the added task the reviewer objected to, with no opinion on it.
- **Design** (#5685, #5834): a new archiver service and a new compactor DAG, discussed on calls. Out of scope.

What ChangeLens said that reviewers did not: on the 14 pull requests whose diff mapped to assets, it listed the downstream blast radius every time (5 to 35 assets). On #5827 it named the `calitp_reports_site` exposure, the same one cal-itp's own "Impacted Exposures" CI bot named. No reviewer mentioned downstream effects on any pull request; whether that is because they already knew is not something this study can tell.

## The one miss, in detail

#5767 fixed trip double-counting in a GTFS summary model. While debugging, the author pointed `dim_provider_gtfs_data` at `` `cal-itp-data-infra.mart_transit_database.dim_gtfs_datasets` `` and a staging table directly, commented out the `{{ ref() }}` calls, and that version was approved and merged. #5768 ("update references missed in PR 5767") restored them a few hours later.

For a dbt project this is the textbook thing a deterministic check should catch: a model that reads a table by its production name no longer depends on the model that builds it, so dbt's graph, `dbt build --select +model`, and every lineage tool downstream silently lose the edge. ChangeLens 0.2.0's pre-flight listed the op as `modify model:dim_provider_gtfs_data` with the note `SQL did not parse: Expecting ). Line 3, Col: 14`, which is the hyphen inside the backticked project name. Two causes, both fixed in the change that carries this page:

1. `changes.py` parsed changed model SQL with sqlglot's DuckDB dialect regardless of the adapter. On BigQuery that failed on a backticked, hyphenated project name (#5767, #5768) and on three models in #5761; in all, 6 of the 14 models whose SQL changed in this window did not parse, and for those ChangeLens knew only that the model was modified. The dbt adapter now records the warehouse's dialect on every node, and the change parser reads it from the graph (an existing graph needs `changelens sync --force` once).
2. A literal table name in a model's `FROM` or `JOIN` that resolves to a model, seed, snapshot or source in the graph is now a `literal_relation` op: the pre-flight is at least `warn` and says which asset is read by which name, and the review carries one `warn` finding per name, on the line that reads it, with the node as evidence. Only a name the change introduces counts; one the old SQL already read, or the reverse diff that restores the `ref()`, raises nothing. When the SQL does not parse, a textual pass over quoted and dotted names stands in.

## After the fix

The same 49 pull requests, same graph, re-synced with `--force`:

| What changed | 0.2.0 | after |
| --- | ---: | ---: |
| pull requests with a `warn` from `changelens review` | 0 | 1 (#5767) |
| findings at warn | 0 | 2, both on `dim_provider_gtfs_data.sql` (lines 5 and 33), each naming the model it should `ref()` |
| changed models that did not parse | 6 of 14 | 2 of 14 (#5827's `COUNT(DISTINCT x WHERE …)`, a form sqlglot's BigQuery dialect does not read) |
| typed ops on #5761 | 22 | 28: six dropped `calitp_*_rank` columns and a filter change the duckdb reading could not see |
| typed ops on #5767 | 2 | 5: the two hardcoded names and two added columns |
| everything else | | unchanged: no new warning on any of the other 48 |

#5768, which restores the refs, stays at inform: the check reports only names a change introduces.

## Limits

- **Current graph, not the graph at each pull request.** The manifest is from 2026-10-26. Models and DAGs a pull request deleted are therefore "not in the graph", which is why #5777 and #5822 show unmapped deletions; in CI the graph is synced from the base branch and this does not happen.
- **Unmapped paths are counted against preflight.** `changelens preflight` raises an otherwise clean verdict to warn when a changed path has no asset; `changelens review` lowers it back to inform with a note. The pull request comment uses the review, so the 19 are shown as preflight false alarms and 0 review false alarms, not hidden.
- **Reviewer inline comments were not readable.** Review summaries and conversation comments were. A defect raised only inline would be missed by this study; #5850's eight resolved inline threads are the main gap.
- **Twenty-one of 49 are Dependabot bumps** with no human review. They are counted because a shadow deployment would see them too; they contribute 11 of the 19 preflight false alarms (`warehouse/uv.lock`).
- **One reader labelled everything.** The window is small enough to read in full, but a second reader would be better.
- **No model review.** The earlier replay measured model findings (`calitp-history-model-consumers.md`); this study is the deterministic layer only.
- **The fix was written after reading the miss.** The "after" row is a tuning result on the pull request that motivated it, not a held-out one. What it shows is that the check fires on the real case and on nothing else in the window.

## Per pull request

The 0.2.0 run. Columns: ChangeLens's review severity and its deterministic findings; preflight severity (`warn*` means raised only for unmapped paths); typed ops and downstream assets from preflight; what the reviewers did; the call. The JSON beside this page carries both runs per pull request (`after_fix`).

| PR | State | Change | Review | Preflight | Ops | Downstream | Reviewers | Call |
| --- | --- | --- | --- | --- | ---: | ---: | --- | --- |
| [#5685](https://github.com/cal-itp/data-infra/pull/5685) | merged | feat(gtfs-rt-archiver-high-frequency): add high-frequency cohort archiver as a separate service | inform: none | inform | 0 (none) | 0 | changes requested then approved (design of a new service, Terraform) | out of scope: services and iac are not platform assets; no finding |
| [#5728](https://github.com/cal-itp/data-infra/pull/5728) | merged | Add compacted GTFS-RT archive buckets in staging and prod | inform: none | inform | 0 (none) | 0 | approved | out of scope: Terraform only; no finding |
| [#5759](https://github.com/cal-itp/data-infra/pull/5759) | merged | fix: Update Enghouse Transactions Assumptions (#5697 #5738) | inform: none | inform | 1 (change_metadata) | 5 | approved | agreed: metadata change on stg_enghouse__taps reaches 5 models; no reviewer defect |
| [#5761](https://github.com/cal-itp/data-infra/pull/5761) | merged | fix: De-duplicate Littlepay Customers and Rides | inform: none | inform | 22 (add_asset, add_column, change_filter, change_metadata, modify) | 5 | approved (LGTM) | agreed: 22 ops, 5 downstream models; 3 models did not parse (see limits); no reviewer defect, no later fix |
| [#5763](https://github.com/cal-itp/data-infra/pull/5763) | merged | chore: Enghouse Transactions Cleanup (#5712) | inform: none | inform | 5 (change_filter, change_metadata, modify) | 2 | approved | agreed: 5 ops, 2 downstream models |
| [#5764](https://github.com/cal-itp/data-infra/pull/5764) | merged | fix(airflow): upgrade urllib3 and fix VCR replay; match test env to Composer | inform: 7 changed paths are not mapped to any asset | warn* | 0 (none) | 0 | approved; reviewer asked for the upgrade workflow to be documented | false alarm (preflight): 7 unmapped paths raised preflight to warn; the review reports them at inform |
| [#5767](https://github.com/cal-itp/data-infra/pull/5767) | merged | Fix daily schedule+RT route-direction summary trip counting | inform: none | inform | 2 (modify) | 34 | approved; a hardcoded production table name replacing ref() was merged and fixed hours later in #5768 | missed at 0.2.0; caught after the fix in this change: at 0.2.0 the change parser reported 'SQL did not parse' at the hardcoded name and raised nothing; with the adapter dialect and the literal-name check it warns twice, on lines 5 and 33 |
| [#5768](https://github.com/cal-itp/data-infra/pull/5768) | merged | (fix): update references missed in PR 5767 | inform: none | inform | 1 (modify) | 35 | approved | agreed: restores the ref() calls; 35 downstream assets listed |
| [#5770](https://github.com/cal-itp/data-infra/pull/5770) | open | Chore(deps): Bump @fastify/busboy and @sveltejs/kit in /apps/maps | inform: none | inform | 0 (none) | 0 | no human review (dependency bump) | agreed: no platform path |
| [#5771](https://github.com/cal-itp/data-infra/pull/5771) | open | Chore(deps): Bump werkzeug from 3.1.8 to 3.1.9 in /services/gtfs-rt-archiver | inform: none | inform | 0 (none) | 0 | no human review (dependency bump) | agreed: no platform path |
| [#5772](https://github.com/cal-itp/data-infra/pull/5772) | open | Chore(deps): Bump multidict from 6.7.1 to 6.9.1 in /warehouse | inform: 1 changed path is not mapped to any asset | warn* | 0 (none) | 0 | no human review (dependency bump) | false alarm (preflight): uv.lock is unmapped |
| [#5773](https://github.com/cal-itp/data-infra/pull/5773) | open | Chore(deps-dev): Bump source-map-js from 1.0.2 to 1.2.2 in /apps/maps | inform: none | inform | 0 (none) | 0 | no human review (dependency bump) | agreed: no platform path |
| [#5774](https://github.com/cal-itp/data-infra/pull/5774) | open | Chore(deps): Bump fsspec from 2024.5.0 to 2026.6.0 in /packages/calitp-data-analysis | inform: none | inform | 0 (none) | 0 | no human review (dependency bump) | agreed: no platform path |
| [#5777](https://github.com/cal-itp/data-infra/pull/5777) | merged | Remove deprecated GTFS Schedule DAGs and associated code (issue #4814) | inform: 31 changed paths are not mapped to any asset | warn* | 0 (none) | 0 | commented then approved; reviewer asked for follow-up cleanups (PodOperator, README, GKE, a bucket variable) | false alarm (preflight): 31 deleted paths are not in the graph (sync from the base branch would fix this); reviewer items out of scope |
| [#5781](https://github.com/cal-itp/data-infra/pull/5781) | merged | Fix GTFS validator test fixture expiration date | inform: 1 changed path is not mapped to any asset | warn* | 0 (none) | 0 | approved | false alarm (preflight): a test fixture zip is unmapped |
| [#5785](https://github.com/cal-itp/data-infra/pull/5785) | merged | fix: make TIDES datapackage.json pass Frictionless validation | inform: 2 changed paths are not mapped to any asset | warn* | 0 (none) | 0 | approved; reviewer asked where packages were installed and for tests | false alarm (preflight): 2 unmapped plugin paths; reviewer items out of scope |
| [#5786](https://github.com/cal-itp/data-infra/pull/5786) | merged | chore: update payments_entity_mapping for scmetro (#5778) | inform: none | inform | 1 (modify) | 16 | approved | agreed: seed change reaches 16 payments models |
| [#5788](https://github.com/cal-itp/data-infra/pull/5788) | merged | feat: enghouse parse job writes audit table (#5766) | inform: 4 changed paths are not mapped to any asset | warn* | 2 (change_metadata, modify) | 0 | approved | false alarm (preflight): task and source schema change mapped; operator and tests unmapped |
| [#5792](https://github.com/cal-itp/data-infra/pull/5792) | merged | fix(metabase): exclude cookies from Cloud Armor RCE inspection; exempt query-builder autocomplete | inform: none | inform | 0 (none) | 0 | commented then approved | out of scope: Terraform only; no finding |
| [#5796](https://github.com/cal-itp/data-infra/pull/5796) | merged | Remove deprecated PodOperator and GTFS Schedule configuration | inform: 5 changed paths are not mapped to any asset | warn* | 0 (none) | 0 | commented then approved; reviewer listed stale docs and environment variables | false alarm (preflight): ChangeLens's reference check found no remaining PodOperator callers, matching the reviewer; 5 unmapped paths; docs out of scope |
| [#5797](https://github.com/cal-itp/data-infra/pull/5797) | open | Chore(deps): Bump aiohttp from 3.12.15 to 3.14.4 in /images/jupyter-singleuser | inform: none | inform | 0 (none) | 0 | no human review (dependency bump) | agreed: no platform path |
| [#5798](https://github.com/cal-itp/data-infra/pull/5798) | open | Chore(deps): Bump aiohttp from 3.13.3 to 3.14.4 in /packages/calitp-data-analysis | inform: none | inform | 0 (none) | 0 | no human review (dependency bump) | agreed: no platform path |
| [#5799](https://github.com/cal-itp/data-infra/pull/5799) | open | Chore(deps): Bump aiohttp from 3.14.1 to 3.14.4 in /warehouse | inform: 1 changed path is not mapped to any asset | warn* | 0 (none) | 0 | no human review (dependency bump) | false alarm (preflight): uv.lock is unmapped |
| [#5800](https://github.com/cal-itp/data-infra/pull/5800) | merged | Chore(deps): Bump aiohttp from 3.9.5 to 3.14.4 in /apps/maps | inform: none | inform | 0 (none) | 0 | no human review (dependency bump) | agreed: no platform path |
| [#5801](https://github.com/cal-itp/data-infra/pull/5801) | open | fix: stop un-diagnosable errors for duplicate settlements (#5023) | inform: none | inform | 2 (change_metadata) | 0 | open, no review | agreed (no review yet): test metadata changes on 2 models, no downstream assets; the PR body's own dbt log shows 3 errors, which ChangeLens does not read |
| [#5802](https://github.com/cal-itp/data-infra/pull/5802) | merged | feat: Handle Littlepay passes and revenue-inspection-outcomes Tables (#5793) | inform: 1 changed path is not mapped to any asset | warn* | 3 (change_metadata, modify) | 10 | approved | false alarm (preflight): task and source changes reach 10 payments models including fct_payments_rides_v2; 1 unmapped operator path |
| [#5813](https://github.com/cal-itp/data-infra/pull/5813) | merged | Remove duplicate Terraform resource for calitp-tides | inform: none | inform | 0 (none) | 0 | approved; author questioned a Terraform 'no longer manage' notice | out of scope: Terraform only |
| [#5814](https://github.com/cal-itp/data-infra/pull/5814) | open | Chore(deps-dev): Bump vite, @sveltejs/adapter-auto, @sveltejs/adapter-static and @sveltejs/kit in /apps/maps | inform: none | inform | 0 (none) | 0 | no human review (dependency bump) | agreed: no platform path |
| [#5815](https://github.com/cal-itp/data-infra/pull/5815) | merged | Remove legacy airflow-jobs Kubernetes resources and bindings (i5789) | inform: 4 changed paths are not mapped to any asset | warn* | 0 (none) | 0 | commented then approved; reviewer found a rollout gap and a missing Python pin | false alarm (preflight): 4 unmapped paths; reviewer items out of scope |
| [#5816](https://github.com/cal-itp/data-infra/pull/5816) | open | Chore(deps): Bump virtualenv from 21.4.2 to 21.14.9 in /warehouse | inform: 1 changed path is not mapped to any asset | warn* | 0 (none) | 0 | no human review (dependency bump) | false alarm (preflight): uv.lock is unmapped |
| [#5817](https://github.com/cal-itp/data-infra/pull/5817) | open | Chore(deps): Bump certifi from 2026.5.20 to 2026.10.19 in /warehouse | inform: 1 changed path is not mapped to any asset | warn* | 0 (none) | 0 | no human review (dependency bump) | false alarm (preflight): uv.lock is unmapped |
| [#5822](https://github.com/cal-itp/data-infra/pull/5822) | merged | Remove leftover sync_elavon DAG metadata | inform: 1 changed path is not mapped to any asset | warn* | 0 (none) | 0 | approved; reviewer asked for stale docs to go too | false alarm (preflight): deleted DAG metadata not in the graph |
| [#5823](https://github.com/cal-itp/data-infra/pull/5823) | open | Chore(deps): Bump setuptools from 80.9.0 to 84.1.0 in /images/jupyter-singleuser | inform: none | inform | 0 (none) | 0 | no human review (dependency bump) | agreed: no platform path |
| [#5824](https://github.com/cal-itp/data-infra/pull/5824) | open | Chore(deps): Bump filelock from 3.29.1 to 4.2.1 in /warehouse | inform: 1 changed path is not mapped to any asset | warn* | 0 (none) | 0 | no human review (dependency bump) | false alarm (preflight): uv.lock is unmapped |
| [#5825](https://github.com/cal-itp/data-infra/pull/5825) | open | Chore(deps): Bump setuptools from 82.0.1 to 84.1.0 in /warehouse | inform: 1 changed path is not mapped to any asset | warn* | 0 (none) | 0 | no human review (dependency bump) | false alarm (preflight): uv.lock is unmapped |
| [#5826](https://github.com/cal-itp/data-infra/pull/5826) | open | Chore(deps): Bump pillow from 12.1.1 to 13.0.0 in /packages/calitp-data-analysis | inform: none | inform | 0 (none) | 0 | no human review (dependency bump) | agreed: no platform path |
| [#5827](https://github.com/cal-itp/data-infra/pull/5827) | merged | Add flex summary columns to daily and monthly GTFS route-direction and operator summary models | inform: none | inform | 12 (add_column, modify) | 9 | approved | agreed: 8 added columns and 4 modified models reach 9 assets including the calitp_reports_site exposure, the same exposure cal-itp's own Impacted Exposures bot named |
| [#5830](https://github.com/cal-itp/data-infra/pull/5830) | merged | Revert staging deployment policy to any `staging/**` branch (undo #5673) | inform: none | inform | 0 (none) | 0 | approved after a discussion of deploy concurrency | out of scope: workflow files only |
| [#5834](https://github.com/cal-itp/data-infra/pull/5834) | open | Add GTFS-RT raw compactor DAG and script | inform: 7 changed paths are not mapped to any asset | warn* | 1 (add_asset) | 0 | open; reviewer raised design questions | false alarm (preflight): new DAG mapped; 7 unmapped script and test paths; design questions out of scope |
| [#5835](https://github.com/cal-itp/data-infra/pull/5835) | open | Chore(deps): Bump certifi from 2026.2.25 to 2026.10.18 in /services/gtfs-rt-archiver | inform: none | inform | 0 (none) | 0 | no human review (dependency bump) | agreed: no platform path |
| [#5836](https://github.com/cal-itp/data-infra/pull/5836) | open | Chore(deps): Bump msgpack from 1.1.2 to 1.2.3 in /warehouse | inform: 1 changed path is not mapped to any asset | warn* | 0 (none) | 0 | no human review (dependency bump) | false alarm (preflight): uv.lock is unmapped |
| [#5839](https://github.com/cal-itp/data-infra/pull/5839) | merged | Fix flex group name in IAM bindings (#5837) | inform: none | inform | 0 (none) | 0 | approved | out of scope: Terraform IAM only |
| [#5842](https://github.com/cal-itp/data-infra/pull/5842) | open | Chore(deps): Bump certifi from 2026.2.25 to 2026.10.18 in /services/gtfs-rt-archiver-high-frequency | inform: none | inform | 0 (none) | 0 | no human review (dependency bump) | agreed: no platform path |
| [#5843](https://github.com/cal-itp/data-infra/pull/5843) | merged | chore: stop parsing ccjpa refunds (#5828) | inform: none | inform | 1 (modify) | 0 | approved; reverted 5 days later because the upstream data was fixed, not because the change was wrong | agreed: DAG modified, no schedule or task change |
| [#5845](https://github.com/cal-itp/data-infra/pull/5845) | merged | Decommission legacy airflow-jobs GKE clusters (#5789) | inform: none | inform | 0 (none) | 0 | approved; reviewer caught an unrelated node-count change in the plan | out of scope: Terraform only |
| [#5846](https://github.com/cal-itp/data-infra/pull/5846) | open | Chore(deps): Bump pyasn1 from 0.6.3 to 0.6.5 in /services/gtfs-rt-archiver | inform: none | inform | 0 (none) | 0 | no human review (dependency bump) | agreed: no platform path |
| [#5847](https://github.com/cal-itp/data-infra/pull/5847) | open | Chore(deps): Bump pyasn1 from 0.6.3 to 0.6.5 in /warehouse | inform: 1 changed path is not mapped to any asset | warn* | 0 (none) | 0 | no human review (dependency bump) | false alarm (preflight): uv.lock is unmapped |
| [#5850](https://github.com/cal-itp/data-infra/pull/5850) | merged | Filter existing Littlepay files before creating mapped tasks (issue #5838) | inform: 5 changed paths are not mapped to any asset | warn* | 3 (add_task, change_schedule, delete_asset) | 0 | changes requested (test structure, avoid an extra task) then approved | false alarm (preflight): schedule change 12:00 to 12:30 UTC, one task added and one removed; 5 unmapped paths |
| [#5855](https://github.com/cal-itp/data-infra/pull/5855) | merged | Revert "chore: stop parsing ccjpa refunds (#5828)" | inform: none | inform | 1 (modify) | 0 | approved | agreed: parse_kwargs removed with no remaining references |

Source rows: `calitp-shadow-2026-11.json`.
