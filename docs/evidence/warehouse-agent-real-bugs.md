# The warehouse review agent on real bugs it had never seen

## In plain words

ChangeLens checks a change to a data warehouse before it merges and says what could break. This page measures its
review agent on real bugs from public warehouses, against the same model used on its own.

The latest held-out test (fresh set 5, 2026-11-23) used 39 real bugs from 17 public warehouses that no part of the
work had touched. More than half of them are SQL that runs without error and quietly returns wrong data, the kind a
compiler cannot catch. An isolated labeller found each bug from the maintainers' own later fix, without ever seeing
ChangeLens, and the labels were committed before anything was scored. The agent's code was frozen first and the test
was scored once.

| Reviewer, same commits and same repository files | Real bugs caught |
|---|---|
| **ChangeLens review agent v9** | **34 of 39 (87%)** |
| Claude Opus on its own, one review call | 31 of 39 (79%) |
| Claude Sonnet on its own, one review call | 29 of 39 (74%) |
| Free checks: the parser and fixed rules, no model | 2 of 39 (5%) |

ChangeLens caught every bug that Opus alone caught, plus three more; Opus caught none that ChangeLens missed. Its
own target was 93%, and it missed that.

What it costs. On 51 ordinary commits with no known bug, ChangeLens and Opus alone each flagged 19. An isolated
reader, not told which reviewer wrote which warning, checked every warning in the repository: 36 of ChangeLens's 46
warnings were real defects, against 31 of 34 for Opus alone. So ChangeLens surfaces more real problems and also more
wrong ones. Commits where every warning was wrong: 3 of 51 for ChangeLens, 2 for Opus alone.

## What makes the difference

The agent is not one prompt. Four reviewers on Sonnet each read the change with one job (row counts; values and
casts; columns and references; NULLs, date edges and conventions shared by sibling models), opening any file in the
repository. Code then adds what a model reading a diff tends to miss, and a skeptical checker on Opus decides.

- **Render and parse, in code.** Each changed file is rendered the way the warehouse would render it, under every
  switch its template has (dbt Jinja branches, SqlRender conditions evaluated from parameter values, Hive
  variables), and parsed in the dialect it parsed in before the commit. Only new failures count. This needs no model.
- **A checker that must show its work.** For every bug it confirms, the checker names the changed line that causes
  it (code checks the line is in the commit) and writes one concrete input that breaks: a row, a NULL, a second
  source, an edge date. No witness, no bug.
- **Documentation is never a bug on its own**, and a change the commit or its documentation says is deliberate is
  not reported as a defect.

Measured on fresh set 5 by switching parts off, with no extra model calls: without the checker the agent catches 37
of 39 but flags 33 of 51 ordinary commits, so the checker cuts that noise by 40% for 3 catches. The render-and-parse
step carried one catch on its own.

## Limits, stated plainly

- 39 bugs is a small sample. The edge over Opus alone, 3 bugs to 0, is not statistically significant (exact paired
  test, p about 0.25). A 95% interval for 87% runs from about 73% to 96%.
- Only fresh set 5 is held out. Every earlier figure below is development data: each set that landed under the goal
  was used to improve the agent and a new set was labelled from new repositories.
- Of the five misses, Opus alone also missed all five. Two were found and held back as uncertain because the
  repository's own documentation leaves the intended behaviour open. Three no reviewer found: a business rule in a
  Stripe model and two template errors in OHDSI R packages.
- Several wrong warnings came from the render-and-parse step reading Flyway placeholders and SqlRender syntax as
  parse errors; the checker let some through.
- Public repositories ship no data. In a customer deployment the agent would add read-only queries (a duplicate-key
  count per changed join, a row count before and after); that is not measured here.

## How each test was run

- **A bug** is a real defect from a public repository, found from a later fix commit and traced back with `git blame`
  to the commit that introduced it. The reviewer sees the introducing commit only, in a copy of the repository with
  no git history, so it cannot peek at the fix. Rules for each set are committed before mining
  (`pipelinebench/review/warehouse/fresh6/PROTOCOL.md`, `fresh7/PROTOCOL.md`).
- **Caught** means a confirmed finding that a judge model with no tools says describes the labelled defect (same model
  or column, same mechanism). A stricter mechanical score that needs the finding on the labelled lines gives
  ChangeLens 36 of 39 on fresh set 5.
- **False alarms** are read two ways: the maintainers' rule (a flagged commit counts as a false alarm unless a later
  commit with a fix-like message rewrote a flagged line; 15 of 51 for both ChangeLens and Opus alone) and the blind
  reader above.

## Every set

| Set | Bugs | Version | ChangeLens | Opus alone | Sonnet alone |
|---|---|---|---|---|---|
| Practice: cal-itp, tuva, ol-data-platform, spellbook, opensource-observer | 62 | v3 | 38 (61%) | not run | not run |
| Fresh set 1, then practice | 31 | v3 | 23 (74%) | not run | not run |
| Fresh set 2, then practice | 29 | v6 | 25 (86%) | not run | not run |
| Fresh set 3 (mostly SQL that does not compile), then practice | 40 | v7 | 39 (98%) | not run | not run |
| Fresh set 4 (29 of 40 compile cleanly), then practice | 40 | v8 | 28 (70%) | 30 (75%) | 26 (65%) |
| **Fresh set 5, held out, scored once** | **39** | **v9, frozen 6bcde0e** | **34 (87%)** | **31 (79%)** | **29 (74%)** |

Fresh set 4 found the agent behind Opus alone. Five of its misses came from a defect in ChangeLens itself: it showed
the reviewers only `.sql` and YAML changes, so Hive `.hql` queries never reached them. That and the rest of v9 were
built on fresh set 4 as practice, then frozen before fresh set 5 was scored.

## Earlier history

### What changed between versions, and why

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

### The cost of v7

The fourth reviewer finds more, and the false warnings went up with it: 1 of 36 ordinary commits before, 6 of 45 now.
Of the 28 confirmed findings on ordinary commits in fresh set 3, 18 were real defects (most were fixed later by the
maintainers), 9 were wrong and 1 was unclear. Four of the six wrong commits share one cause: the change was deliberate
(a switch to milliseconds the next commit documents, a removed switch the same commit's README explains) or the
problem was already there before the commit. Teaching the verifier to read the commit's stated intent, and to check
that the faulty line is one the commit changed, is the next fix; it has to be measured on another fresh set.

### What it still misses

The one miss on fresh set 3: dbt_ga4_export `796ed73881` adds two filters marked "temp" to the staging events model
that keep only LinkedIn referral traffic, so every report built on it loses all other traffic. The agent found four
other real problems in the same commit (a removed dedup and a renamed column still read downstream) but not the
filters.

On the practice sets, the misses that remain are rows lost or duplicated through joins whose risk only the data would
show. In a customer deployment the agent would close part of this gap with read-only queries against the warehouse
(a duplicate-key count per changed join, a row count before and after); the public repositories ship no data, so that
is not measured here.

### Cost and running it

Every model call runs on the Claude Code subscription login (`claude -p`, read-only tools), not an API key: $0 of API
credit. One commit takes four to fourteen model calls and about one to three minutes. Reviewers run on Sonnet, the
verifier on Opus.

```sh
changelens warehouse-review HEAD --repo path/to/warehouse          # text; exit 1 when a confirmed bug exists
changelens warehouse-review <sha> --repo . --format json --fail-on never
python -m pipelinebench.warehouse_agent_replay --repo <clone> --labels <yaml> --sample 3 --cache cache.jsonl --out out/
```

A review that fails (a model error, a timeout, a usage limit) exits 2 and is never reported as a clean result.

### Files

- `src/changelens/warehouse_agent.py`: the agent. `src/pipelinebench/warehouse_agent_replay.py`: the replay and scorer.
- `pipelinebench/review/warehouse/`: the labels (practice, fresh set 1 as `fresh3/`, fresh set 2 as `fresh4/`, fresh
  set 3 as `fresh5/`).
- `pipelinebench/results/warehouse-agent/`: every run's raw output and every false-alarm check.
