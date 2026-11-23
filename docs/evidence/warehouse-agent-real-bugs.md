# The warehouse review agent on real bugs it had never seen

## In plain words

ChangeLens checks a change to a data warehouse before it merges and says what could break. This page measures its
review agent on real bugs from public warehouses, against the same models used on their own.

Three held-out tests were run, each on a set of real bugs from public warehouses that no part of the work had touched
before. An isolated labeller found each bug from the maintainers' own later fix, without ever seeing ChangeLens. The
rules were committed before mining, the labels before scoring, and the agent's code was frozen first. Each set was
scored once. When a version missed the 93% goal, its set became practice data, the agent was improved on the
practice sets only, and a new set was labelled from new repositories.

| Held-out set (2026-11-23) | Bugs | ChangeLens | Opus alone | Sonnet alone | Free checks |
|---|---|---|---|---|---|
| Fresh set 5: 17 warehouses, 22 bugs compile | 39 | **34 (87%)**, v9 | 31 (79%) | 29 (74%) | 2 (5%) |
| Fresh set 6: 15 warehouses, 27 bugs compile | 40 | **32 (80%)**, v10 | 32 (80%) | 25 (62%) | 0 (0%) |
| Fresh set 7: 16 warehouses, 23 bugs compile | 37 | **30 (81%)**, v11 | 30 (81%) | 31 (84%) | 1 (3%) |
| All three | 116 | **96 (83%)** | 93 (80%) | 85 (73%) | 3 (3%) |

"Opus alone" and "Sonnet alone" are one review call each, with the same commit, the same repository files and the
same read-only tools. "Free checks" are a SQL parser and fixed rules, no model.

**What this shows.** ChangeLens catches about four in five real warehouse bugs, most of them SQL that runs and
quietly returns wrong data, and it missed its own 93% goal on every held-out set. It beat Opus alone by three bugs on
the first set and tied it on the next two. Across 116 bugs, ChangeLens caught 10 that Opus alone missed and Opus
alone caught 7 that ChangeLens missed: not a significant difference. On these tests the gain over a strong model used
on its own is small. The render-and-parse step and the checker described below change which bugs are found and cut
wrong warnings, but they do not lift the total much above what Opus finds alone.

What it costs. On ordinary commits with no known bug, ChangeLens flagged 19 of 51, 24 of 42 and 17 of 42 (two
commits of the last set could not be read by any reviewer, a byte the tools could not decode); Opus alone flagged
19, 21 and 14. On fresh set 5 an isolated reader, not told which reviewer wrote which warning, judged 36 of
ChangeLens's 46 warnings real, against 31 of 34 for Opus alone.

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

- About 40 bugs per set is a small sample. Pooled over 116 bugs, ChangeLens 83% against Opus alone 80% is within
  noise (10 bugs only ChangeLens caught, 7 only Opus caught).
- Each version was tuned on the sets before it. Only the first score of each version on its own fresh set counts;
  the practice figures below are development data.
- Notes on fresh set 5's misses (v9):
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
  (`pipelinebench/review/warehouse/fresh6/` to `fresh9/`, each `PROTOCOL.md`; fresh sets 5, 6 and 7 are the folders `fresh7`, `fresh8` and `fresh9`).
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
| Fresh set 5 as practice | 39 | v10 / v11 | 37 / 37 | | |
| **Fresh set 6, held out, scored once** | **40** | **v10, frozen 2aa239e** | **32 (80%)** | **32 (80%)** | **25 (62%)** |
| Fresh set 6 as practice | 40 | v11 | 34 | | |
| **Fresh set 7, held out, scored once** | **37** | **v11, frozen fd9a1fb** | **30 (81%)** | **30 (81%)** | **31 (84%)** |

Fresh set 4 found the agent behind Opus alone. Five of its misses came from a defect in ChangeLens itself: it showed
the reviewers only `.sql` and YAML changes, so Hive `.hql` queries never reached them. That and the rest of v9 were
built on fresh set 4 as practice, then frozen before fresh set 5 was scored.

v10, built on fresh sets 3 to 5 as practice: the render-and-parse step now reads OHDSI SqlRender files (their
`{DEFAULT ...}` header lines had hidden most of them from the parser), a scope check finds a WHERE that filters on a
window-function alias of the same SELECT, the checker is shown the rendered SQL rather than the template, and a
second look on Opus may promote a finding held back only on intent when it cites the line that defines the
expectation and the changed line that breaks it. v11 added one whole-diff Opus reviewer beside the four specialists,
because on fresh set 6 ChangeLens and Opus alone each caught five bugs the other missed. Fresh set 7 drew at most
three repositories per GitHub owner; one repository was dropped before scoring because the previous labeller had
tried it (`fresh9/PROTOCOL.md`).

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
