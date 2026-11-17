# The warehouse review agent on real bugs it had never seen (2026-11-17)

## In plain words

ChangeLens checks a change to a data warehouse before it merges and says what could break. Its earlier checker was a
set of fixed pattern rules. On real bugs from warehouses it had not been tuned on, those rules caught about 1 in 10.

This change adds a review agent. A model reads the commit, opens the files around it in the repository (the models a
join reads, the YAML that documents and tests them, the models downstream), and writes findings. A second, skeptical
model then re-checks each finding against the repository and keeps only what holds up. Nothing is run against the
warehouse and nothing is written anywhere: the agent only reads a copy of the repository as it stood at that commit,
with no git history, so it cannot peek at the later fix.

On a set of 29 real bugs from 12 public warehouses that were never used while building it, scored once on frozen
code, it caught **25 of 29 (86%)**. On ordinary commits from the same warehouses it flagged 10 of 36, but an
independent check found real defects in 8 of those 10; only **1 of 36 ordinary commits (3%)** got a warning with
nothing real behind it.

## What was measured

- **A bug** is a real defect from a public repository, found from a later fix commit and traced back with `git blame`
  to the commit that introduced it. The reviewer sees the introducing commit only. Labels were written by a separate
  labeller that never read or ran ChangeLens (`pipelinebench/review/warehouse/*/README.md`).
- **Caught** means a confirmed finding (severity `bug`, kept by the skeptical pass) describes the labelled defect. A
  model judge with no tools decides that, and must see the same model or column and the same mechanism; a finding
  about a different problem in the same commit does not count. A second, purely mechanical score (the finding sits on
  the labelled lines, or in the labelled file and names one of the label's key words) agrees on the fresh set: 25 of
  29 both ways.
- **Flagged ordinary commit** means a random commit with no known bug got at least one confirmed finding. Because
  those commits are unlabelled, every flag was then checked by hand-style review in the repository (read the files at
  that commit, grep for the readers, look at later history): REAL, FALSE or UNCLEAR.

## Results

| Set | Bugs | Version | Caught | Ordinary commits flagged | With nothing real behind it |
|---|---|---|---|---|---|
| Practice: cal-itp, tuva, ol-data-platform, spellbook, opensource-observer | 62 | v3 | 38 (61%) | 10 of 50 | 0 of 50 |
| Fresh set 1: 13 repos (mozilla bigquery-etl, openedx aspects, 11 Fivetran packages), scored once | 31 | v3, frozen f8ddc71 | 23 (74%) | 10 of 36 | 1 of 36 |
| Fresh set 1, then used as practice | 31 | v6 | 26 (84%) | 3 of 12 | not checked |
| **Fresh set 2: 12 new repos, scored once** | **29** | **v6, frozen f57f70c** | **25 (86%)** | **10 of 36** | **1 of 36 (3%)** |

Fresh set 1 landed under 85%, so it became practice data and a second fresh set was labelled from repositories
never used before (dbt_artifacts, dbt-project-evaluator, elementary's dbt-data-reliability, snowplow-unified, base
dos dados, Rio de Janeiro's SMTR queries, TEAM Schools, and five more Fivetran packages).

The rule-only checker on the earlier blind sets: 3 of 24 and 2 of 23 (about 10%).

Fresh set 2 by kind of bug: broken references, aliases and SQL that does not compile 16 of 16; contract and schema
4 of 4; nulls and casts 2 of 3; duplicated or lost rows 3 of 6. With 29 bugs, the 86% has a wide margin: a 95%
interval runs from about 68% to 96%.

## What changed between versions, and why

- **v1 to v2:** one reviewer with a written audit of every join, dedup and union in the change (what each joined
  table is unique on, and whether the join key covers it). Practice 54% to 60%.
- **v3:** three specialist reviewers in parallel (row counts and grain; values, casts and nulls; columns, contracts and
  references), merged, then one skeptical verifier on a stronger model that marks each candidate bug, risk or reject.
  This is where precision comes from: of 49 findings on ordinary practice and fresh-set-1 commits, 43 were real.
- **v4 (dropped):** a separate deep check of every changed join. It did not raise the catch rate; the misses it was
  meant to fix were joins the repository itself documents as safe (for example a `unique` test on the very key the
  later fix says repeats), which no reading of the code can catch without the data.
- **v6:** commits that touch more than four files are reviewed in chunks of about four files each, with the full file
  list in view. Misses on fresh set 1 were mostly large commits where the reviewer found two other real problems and
  stopped. The verifier also got a clearer rule for rows lost to an INNER join or a filter on the right side of a
  LEFT join. Fresh set 1: 74% to 84%.

## What it still misses

The four misses on fresh set 2:

- dbt_fivetran_log `fddbe5e0f8`: a date spine joined with `date_day >= set_up_at` drops each connector's first day,
  because midnight is earlier than the set-up time (an off-by-one boundary).
- dbt_artifacts `d270a3a2fa`: "latest full run" takes the latest invocation without filtering to `dbt run`, so after a
  `dbt test` the model comes back empty.
- dbt_artifacts `834074abc8`: a running total in a recursive query adds a value that is NULL for ephemeral models
  without a COALESCE, so the whole critical-path total turns NULL.
- dbt-project-evaluator `756d380b6a`: a new model skips the `filter_exceptions()` macro that every sibling model
  applies, so rows users listed as exceptions are still reported.

Three of the four are row-count or boundary logic, which stays the weakest kind (3 of 6 here, about half on
practice). In a customer deployment the agent would close part of this gap with read-only queries against the
warehouse (a duplicate-key count per changed join, a row count before and after); the public repositories ship no
data, so that is not measured here.

## Cost and running it

Every model call runs on the Claude Code subscription login (`claude -p`, read-only tools), not an API key: $0 of API
credit. One commit takes three to ten model calls and about one to three minutes. Specialists run on Sonnet, the
verifier on Opus.

```sh
changelens warehouse-review HEAD --repo path/to/warehouse          # text; exit 1 when a confirmed bug exists
changelens warehouse-review <sha> --repo . --format json --fail-on never
python -m pipelinebench.warehouse_agent_replay --repo <clone> --labels <yaml> --sample 3 --cache cache.jsonl --out out/
```

A review that fails (a model error, a timeout) exits 2 and is never reported as a clean result.

## Files

- `src/changelens/warehouse_agent.py`: the agent. `src/pipelinebench/warehouse_agent_replay.py`: the replay and scorer.
- `pipelinebench/review/warehouse/`: the labels (practice, fresh set 1 as `fresh3/`, fresh set 2 as `fresh4/`).
- `pipelinebench/results/warehouse-agent/`: every run's raw output and both false-alarm checks.
