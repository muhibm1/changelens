# Replay eval: merged changes from public repositories

2026-10-18. `pipelinebench replay-eval` runs `orion review --git <commit>^` on 38 changes that maintainers
merged: 10 from httpx, 8 from hono, 8 from cobra and 12 from the cal-itp dbt warehouse. The commit message is
the stated intent. Each warning is matched to a hand label in `pipelinebench/review/replay.yaml`, judged
against the project's later history:

- **confirmed**: upstream later fixed exactly this. There are two: the hono ETag revert, and the hardcoded
  table names that cal-itp's next commit replaced with `ref()`.
- **plausible**: specific and true of the code, but never fixed upstream. It is a fair question to the author.
- **noise**: wrong, or an announced change reported as a bug.
- **setup**: an artifact of replaying against a graph synced after the change. It is counted apart.

Nearly every one of these changes is correct, so a warning here is mostly a cost to the team reading it.

## Second reading that names what a confirmed case is

The second reading used to keep any warning whose failing case could happen. It now also says what that case is:

- a defect;
- intended by the change;
- a trade-off the change chose;
- something that needs an unusual caller;
- style.

Only a defect keeps `warn`. The rest are lowered to `inform` with the reason, so they stay visible but stop
reading as a problem.

| Haiku 5.5, one run each | warnings | per change | confirmed (of 2) | plausible | noise |
| --- | ---: | ---: | ---: | ---: | ---: |
| before (`replay-haiku-5-5-baseline.md`) | 17 | 0.45 | 1 | 8 | 8 |
| after (`replay-haiku-5-5-verify-kinds.md`) | 12 | 0.32 | 2 | 7 | 3 |

**Noise the change removed.** These were announced changes reported as bugs:

- deduplication dropping duplicates;
- a date-range filter the fix introduced;
- test moves the commit names;
- a lookback window on an incremental model;
- a case that needs `Execute` twice in one process.

**Noise that is left.**

- Two claims about PowerShell semantics: `$null.Length` is 0, and a single result's `.Length` is 1. So both
  checks work as written.
- One announced test change.

**Unchanged on the review eval.** The 61 planted-bug cases still have recall 1.0, with 1 false alarm
(`review-haiku-5-5-verify-kinds.md`).

## Limits

- One run per condition. A difference of one or two warnings is within run-to-run variance.
- The labels were written by the same project that builds Orion, from the upstream history. They are not
  derived from Orion's output.
- cal-itp replays against a graph synced at `8af13f10`, after these changes. Models a change deletes are
  already gone, which is what the two `setup` warnings are.
- About 0.5M input tokens per run, roughly $0.8 with Haiku.

## Rerun

Clone the four repositories into one folder. cal-itp also needs a compiled manifest and `orion sync`
(`partner-calitp-dbt.md`). Then run:

```
pipelinebench replay-eval --workdir <folder> --provider anthropic --model claude-haiku-5-5
```
