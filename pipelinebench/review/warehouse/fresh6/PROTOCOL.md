# fresh6: protocol, written and committed before any repository was mined

This file fixes the rules for the fourth fresh set of real warehouse bugs before any of it exists. It was committed
before the labeller cloned a single repository, so nothing below can have been chosen to suit the results.

## Why a fourth set

Fresh set 3 (`../fresh5/`) scored 39 of 40, but three questions stay open:

1. 27 of its 40 bugs are SQL that does not compile or names something that does not exist. A parser or a compile
   step catches many of those for free. The hard kind, where the SQL runs and silently returns wrong rows, was 1 bug.
2. No plain model was run on the same bugs, so the result does not show how much the agent's design adds over the
   model on its own.
3. The false-alarm figure rested on our own reviewer deciding which flags were real.

## The set

- About 40 in-scope bugs from public SQL or dbt warehouse repositories never used before (list below), at most 5 per
  repository.
- **At least half of the in-scope bugs must compile cleanly**: the SQL parses, every `ref()`, `source()`, table and
  column it names exists, and dbt or the warehouse would build it without error. The bug is in what the data comes
  out as: rows duplicated or lost, wrong values, wrong NULL handling, wrong filters, boundaries or aggregations.
  Each bug carries `compiles: true` or `compiles: false`, decided by the labeller from the code.
- Bugs that need the data or an outside system to see are **kept, not dropped**: they carry `needs_data: true` and
  `in_scope: false`, and are scored and reported as their own bucket.
- Every other rule of `../fresh5/README.md` holds: the bug is found from a later fix commit, traced with `git blame`
  to the commit that introduced it, read by hand, and recorded with `commit`, `parent`, `title`, `fix`,
  `fix_title`, `bug`, `category`, `in_scope`, `must_mention`, `expect`, plus `compiles` and `needs_data`.
- The labeller never reads, opens, greps or runs ChangeLens or this repository (other than this protocol and the
  three format files it is handed), never sees any scoring result and never runs a model. The labels and a
  `manifest.sha256` are committed before anything is scored.

Repositories that may not be used, because an earlier set or the practice data used them: every repository in
`../fresh3/repos.txt`, `../fresh4/repos.txt`, `../fresh5/repos.txt` (and the ones that README lists as tried),
cal-itp data-infra, tuva, ol-data-platform, spellbook, opensource-observer, mozilla bigquery-etl, openedx
aspects-dbt.

## What is scored, once

Four reviewers, each on every bug and on 3 randomly sampled ordinary commits per repository (seed 20261117, the
same sampler as before):

| Reviewer | What it is |
|---|---|
| ChangeLens agent v8 | `changelens warehouse-review`, frozen at a commit recorded in the results before the run |
| Plain Sonnet | one Sonnet call with the same repository snapshot, the same read-only tools (Read, Grep, Glob), the same diff and commit message, and one general instruction: find the defects this commit introduces that would produce wrong data or break the build. No specialist passes, no verifier, no leads. |
| Plain Opus | the same single call on Opus |
| Free checks | no model: the deterministic warehouse rules, a SQL parse of every changed model in the warehouse's dialect, and a YAML parse of every changed YAML file |

All four see only the introducing commit, in a snapshot with no git history. Every model call runs on the
subscription login; no API credit is used.

## How it is scored

- **Caught**: a `bug`-severity finding that a judge model with no tools says describes the labelled defect (same
  model or column, same mechanism), as in fresh set 3. The mechanical score (finding on the labelled lines, or naming
  a `must_mention` word) is reported beside it. The judge prompt is the one in
  `src/pipelinebench/warehouse_agent_replay.py` at this commit, unchanged.
- Reported for each reviewer: all in-scope bugs, the `compiles: true` half on its own, the `compiles: false` half on
  its own, and the `needs_data` bucket.
- **False alarm**, decided by the maintainers, not by us: an ordinary commit counts as a false alarm when it has at
  least one `bug` finding and no later commit with a fix-like message (fix, revert, hotfix, wrong, duplicate, dedup,
  fanout, missing, incorrect, bug, broken, null) rewrote any flagged line. This counts a real defect the maintainers
  never fixed as a false alarm, so it is a ceiling, not a floor. An independent reviewer's reading of each flag is
  reported beside it, never instead of it.
- The bar is 93% on the in-scope bugs. If ChangeLens lands under it, this set becomes practice data and the next one
  is labelled from new repositories; this set's numbers are still published.
