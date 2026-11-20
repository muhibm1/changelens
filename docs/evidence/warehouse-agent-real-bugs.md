# The warehouse review agent on real bugs it had never seen

## In plain words

ChangeLens checks a change to a data warehouse before it merges and says what could break. Its earlier checker was a
set of fixed pattern rules. On real bugs from warehouses it had not been tuned on, those rules caught about 1 in 10.

The review agent replaces that. Four models each read the commit with one job (row counts; values and casts; columns
and references; NULLs, date boundaries and conventions the neighbouring models share), open the files around it in
the repository, and write findings. A fifth, skeptical model re-checks each finding against the repository and keeps
only what holds up. Nothing is run against the warehouse and nothing is written anywhere: the agent only reads a copy
of the repository as it stood at that commit, with no git history, so it cannot peek at the later fix.

On 40 real bugs from 15 public warehouses that no part of the work had touched before, scored once on frozen code
(2026-11-20), it caught **39 of 40 (98%)**. A stricter mechanical score that needs the finding on the labelled lines
agrees on 37 of 40 (92%). On ordinary commits from the same warehouses it flagged 19 of 45; an independent check
found real defects behind 12 of those, and **6 of 45 ordinary commits (13%)** got only warnings with nothing real
behind them.

## What was measured

- **A bug** is a real defect from a public repository, found from a later fix commit and traced back with `git blame`
  to the commit that introduced it. The reviewer sees the introducing commit only. Labels were written by a separate
  labeller that never read or ran ChangeLens (`pipelinebench/review/warehouse/*/README.md`), and each set was committed
  before it was scored.
- **Caught** means a confirmed finding (severity `bug`, kept by the skeptical pass) describes the labelled defect. A
  model judge with no tools decides that, and must see the same model or column and the same mechanism; a finding
  about a different problem in the same commit does not count. The mechanical score is the second check.
- **Flagged ordinary commit** means a randomly sampled commit with no known bug got at least one confirmed finding.
  Those commits are unlabelled, so every flag was checked by an independent reviewer in the repository (read the
  files at that commit, grep for the readers, look at later history): REAL, FALSE or UNCLEAR.

## Results

| Set | Bugs | Version | Caught | Ordinary commits flagged | With nothing real behind it |
|---|---|---|---|---|---|
| Practice: cal-itp, tuva, ol-data-platform, spellbook, opensource-observer | 62 | v3 | 38 (61%) | 10 of 50 | 0 of 50 |
| Fresh set 1: 13 repos (mozilla bigquery-etl, openedx aspects, 11 Fivetran packages), scored once | 31 | v3, frozen f8ddc71 | 23 (74%) | 10 of 36 | 1 of 36 |
| Fresh set 2: 12 new repos, scored once | 29 | v6, frozen f57f70c | 25 (86%) | 10 of 36 | 1 of 36 |
| Fresh sets 1 and 2, then used as practice | 60 | v7 | 54 (90%) | 23 of 75 | not checked |
| **Fresh set 3: 15 new repos, scored once** | **40** | **v7, frozen 6edb302** | **39 (98%)** | **19 of 45** | **6 of 45 (13%)** |

Each fresh set that landed under the goal became practice data, and the next one was labelled from repositories
never used before. Fresh set 3 comes from Velir's dbt-ga4, SELECT's dbt-snowflake-monitoring, the Cook County
Assessor's data architecture, NYC Planning's data engineering, MIT's MIMIC and eICU clinical concepts, OHDSI Achilles
and ETL-Synthea, Sage Bionetworks' Snowflake warehouse, the blockchain-etl Ethereum and Polygon pipelines, and five
Fivetran packages. Six of them are plain SQL rather than dbt.

The rule-only checker on the earlier blind sets: 3 of 24 and 2 of 23 (about 10%).

Fresh set 3 by kind of bug: broken references, aliases and SQL that does not compile 27 of 27; contract and schema 9
of 9; nulls and casts 3 of 3; duplicated or lost rows 0 of 1. The labeller took bugs in the order it found them, and
this set came out heavy on compile and reference errors, which the agent catches almost every time. The kind that is
hardest (duplicated or lost rows) is barely represented here; on fresh sets 1 and 2, now practice, v7 catches 19 of 23
of those (83%), and that is where most of its remaining misses are. With 40 bugs, a 95% interval for 98% runs from
about 87% to 99.6%.

## What changed between versions, and why

- **v1 to v2:** one reviewer with a written audit of every join, dedup and union in the change (what each joined
  table is unique on, and whether the join key covers it). Practice 54% to 60%.
- **v3:** three specialist reviewers in parallel (row counts and grain; values, casts and nulls; columns, contracts and
  references), merged, then one skeptical verifier on a stronger model that marks each candidate bug, risk or reject.
- **v4 (dropped):** a separate deep check of every changed join. It did not raise the catch rate; the misses it was
  meant to fix were joins the repository itself documents as safe, which no reading of the code can catch without the
  data.
- **v6:** commits that touch more than four files are reviewed in chunks of about four files each, with the full file
  list in view, and the verifier got a clearer rule for rows lost to an INNER join or a filter on the right side of a
  LEFT join. Fresh set 1: 74% to 84%.
- **v7:** a fourth reviewer for what the misses had in common: a join on a column that can be NULL (NULL never
  matches), a WHERE on the outer side of a LEFT join, a running total where one term can be NULL, a date compared to
  a timestamp so the first day drops, a "latest run" that does not filter to the kind of run meant, and a new model
  that skips something every sibling model applies. The verifier may confirm these when the repository shows the
  column can be NULL, or that every sibling does it. Practice (fresh sets 1 and 2): 51 to 54 of 60.

## The cost of v7

The fourth reviewer finds more, and the false warnings went up with it: 1 of 36 ordinary commits before, 6 of 45 now.
Of the 28 confirmed findings on ordinary commits in fresh set 3, 18 were real defects (most were fixed later by the
maintainers), 9 were wrong and 1 was unclear. Four of the six wrong commits share one cause: the change was deliberate
(a switch to milliseconds the next commit documents, a removed switch the same commit's README explains) or the
problem was already there before the commit. Teaching the verifier to read the commit's stated intent, and to check
that the faulty line is one the commit changed, is the next fix; it has to be measured on another fresh set.

## What it still misses

The one miss on fresh set 3: dbt_ga4_export `796ed73881` adds two filters marked "temp" to the staging events model
that keep only LinkedIn referral traffic, so every report built on it loses all other traffic. The agent found four
other real problems in the same commit (a removed dedup and a renamed column still read downstream) but not the
filters.

On the practice sets, the misses that remain are rows lost or duplicated through joins whose risk only the data would
show. In a customer deployment the agent would close part of this gap with read-only queries against the warehouse
(a duplicate-key count per changed join, a row count before and after); the public repositories ship no data, so that
is not measured here.

## Cost and running it

Every model call runs on the Claude Code subscription login (`claude -p`, read-only tools), not an API key: $0 of API
credit. One commit takes four to fourteen model calls and about one to three minutes. Reviewers run on Sonnet, the
verifier on Opus.

```sh
changelens warehouse-review HEAD --repo path/to/warehouse          # text; exit 1 when a confirmed bug exists
changelens warehouse-review <sha> --repo . --format json --fail-on never
python -m pipelinebench.warehouse_agent_replay --repo <clone> --labels <yaml> --sample 3 --cache cache.jsonl --out out/
```

A review that fails (a model error, a timeout, a usage limit) exits 2 and is never reported as a clean result.

## Files

- `src/changelens/warehouse_agent.py`: the agent. `src/pipelinebench/warehouse_agent_replay.py`: the replay and scorer.
- `pipelinebench/review/warehouse/`: the labels (practice, fresh set 1 as `fresh3/`, fresh set 2 as `fresh4/`, fresh
  set 3 as `fresh5/`).
- `pipelinebench/results/warehouse-agent/`: every run's raw output and every false-alarm check.
