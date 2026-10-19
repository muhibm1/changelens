# Recall eval: real bugs at the commit that introduced them

2026-10-19. The 61 planted-bug cases (`review-eval`) have recall 1.0 and the replay eval measures only
noise on correct changes, so neither said how often `orion review` catches a real bug on the day it is
written. `pipelinebench recall-eval` measures that. `pipelinebench mine-introducers` blames the lines each
fix commit in a repository changed and keeps the fixes where one earlier, small enough commit wrote most of
those lines. Fifty of those introducing commits (8 from httpx, 38 from hono, 4 from cobra) were then read
and labelled by hand in `pipelinebench/review/recall.yaml`: what the bug was, where it sits in the
introducing commit, and a few `must_mention` words a finding about that bug would use. Orion reviews each
introducing commit against its parent with the commit message as intent, as the team saw it.

A case is **caught** when a `warn` or `block` finding sits on the expected file within twelve lines of the
expected span and mentions the bug; **in file** when a warning is on that file but elsewhere;
**inform only** when a finding within the window stopped at `inform`; otherwise **missed**. The labels come
from the upstream history alone, never from Orion's output.

## Results

Sonnet 5.5, one run per build, the same 50 bugs. The first two runs used a judge that counted any nearby
warning; the `must_mention` requirement was added after reading the third run's raw reviews, where several
"caught" cases were a different warning that happened to land near the bug. The third run is shown both ways
so the builds compare on one judge.

| build | judge | caught | in file | inform only | missed | recall | recall in file | tokens in |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| main before this round (`recall-sonnet-5-5-before.md`) | any nearby warning | 25 | 3 | 8 | 14 | 0.50 | 0.56 | 1.11M |
| windows and callees, original prompts (`recall-sonnet-5-5-context.md`) | any nearby warning | 22 | 4 | 8 | 16 | 0.44 | 0.52 | 1.30M |
| windows and callees, retuned prompts (`recall-sonnet-5-5-context-callers.md`) | any nearby warning | 26 | 3 | 7 | 14 | 0.52 | 0.58 | 1.32M |
| same run | finding must mention the bug | 21 | 8 | 5 | 16 | 0.42 | 0.58 | 1.32M |

**What this says.** On real bugs the reviewer catches 21 of 50 when the finding has to be about the bug (26
when any nearby warning counts), and warns somewhere in the right file on 29. The context changes did not move the headline number: run-to-run variance on a single pass
is several cases (twenty cases changed outcome between the first two runs, which differ only by the context
shown, and the totals moved by three), so the three runs are within noise of each other. What the extra
context did change is what the finding says: with the called definitions shown, warnings cite the
definition's line rather than guessing at the function's behaviour, and the retuned prompts stopped the
second reading from lowering a warning because "no caller in the repository" passes the failing input.

**Second reading.** It still lowered six real-bug findings to `inform` in the final run, four as "did not
confirm the failing case" (httpx `1805ee0d`, hono `9a6e52d7`, `1a1de752`, `32ba5ba8`) and two as a trade-off
or intended (httpx `6f5865f8`, hono `cd35af76`). Each of those warnings was right. The second reading is
what keeps noise on correct changes near zero (`replay.md`), so this is the trade it makes; the raw reviews
in each run's JSON show the lowered finding and the reason, which is where the next prompt change should
start.

**Caught after the change, missed before** (one run each, so indicative): httpx `c725387b` (a repeated
header in `Headers.update` keeps only the last value), hono `31f06052` (`pipe()` releases the writer lock
before `pipeTo` settles), hono `8b4392fa` (backpressure ignored in `streamToNodeStream`). Missed after,
caught before: hono `d9b8b4b7` (`Vary` set rather than appended), hono `1854e241` (index advances after an
in-place splice), hono `b143c4ed` (OPTIONS treated as safe by the CSRF check; now `in_file`).

**Still missed on every run** are mostly bugs with no tell in the diff: an RFC 2069 digest hashing HA1 twice,
a stale `content-length` kept after a body is re-serialised, truthiness checks that drop `retry: 0`. Those need
either the specification or a test, not more of the repository.

## On the other evals

The same build on the replay of 26 merged httpx, hono and cobra changes
(`replay-sonnet-5-5-context-callers.md`): 4 warnings on 2 changes, all plausible, none noise. The confirmed
hono ETag revert (`7075369e`) came out `inform` on this run where the earlier run had it at `warn`, so confirmed
recall on the replay is 0/1 this run against 1/1 before. The replay JSON records warnings only, so whether the
ETag case was raised at `inform` or not at all is not recorded; count it as a regression on the one confirmed
bug until a rerun that keeps the raw reviews says otherwise. The 61 planted-bug cases
(`review-sonnet-5-5-context-callers-x1.md`): recall 1.0, 0 false alarms, 0 of 19 correct changes flagged,
201k input tokens.

## Limits

- One run per build, about 1.3M input tokens and roughly $4 each with Sonnet 5.5, so a two-case difference
  means nothing. Three runs per build would be needed to call a prompt change an improvement.
- The cases are the bugs a small, blame-able commit introduced in three repositories. Bugs spread over many
  commits, or fixed by rewriting the file, are not here.
- The `before` and `context` runs cannot be re-judged with `must_mention` because their raw reviews were not
  kept; every run from now on keeps them (`--raw-dir`).

## Rerun

```
pipelinebench recall-eval --workdir <folder> --provider anthropic --raw-dir <folder>/raw
pipelinebench recall-eval --workdir <folder> --orion /path/to/other/.venv/bin/orion   # another build
pipelinebench mine-introducers <clone> --repo hono --limit 600 --out candidates.yaml   # more cases to label
```
