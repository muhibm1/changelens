# Model precision: a named failing case, then a second reading

Two changes aimed at one problem: on real commits the model raised warnings for code that was correct, or for risks nobody could point at.

1. **A warning names its failing case.** Each model finding carries `failure`, the concrete input or sequence that breaks. A `warn` or `block` with no failure is lowered to `inform` with a note saying why. Nothing is removed.
2. **A second reading.** After validation, one more model call re-reads every remaining model `warn`/`block` against the whole change and must confirm the failing case. One it cannot confirm is lowered to `inform`, with its reason in the notes. On by default (`review.second_reading`); `review.verify_model` lets a stronger model check a cheaper one.

## Labelled cases (61 cases, 19 of them correct changes, one run each)

| reviewer | recall | false alarms | correct changes flagged | tokens in / out |
| --- | --- | --- | --- | --- |
| Sonnet 5.5, before (13 hard cases) | 1.0 | 1 | 1/5 | |
| Sonnet 5.5, failing case + second reading | **1.0** | **0** | **0/19** | 180k / 18k |
| Haiku 5.5, before (13 hard cases) | 1.0 | 2 | 2/5 | |
| Haiku 5.5, failing case only | 1.0 | 2 | 1/19 | 122k / 26k |
| Haiku 5.5, failing case + its own second reading | 0.976 | 3 | 2/19 | 184k / 36k |
| Haiku 5.5, failing case + Sonnet second reading | 1.0 | 2 | 2/19 | 184k / 32k |

Files: [`review-sonnet-5-5-verify-x1.md`](review-sonnet-5-5-verify-x1.md), [`review-haiku-5-5-failure-x1.md`](review-haiku-5-5-failure-x1.md), [`review-haiku-5-5-verify-x1.md`](review-haiku-5-5-verify-x1.md), [`review-haiku-5-5-verify-sonnet-x1.md`](review-haiku-5-5-verify-sonnet-x1.md), baselines [`review-sonnet-5-5-hard-x1.md`](review-sonnet-5-5-hard-x1.md) and [`review-haiku-5-5-hard-x1.md`](review-haiku-5-5-hard-x1.md).

Haiku checking itself dropped a real bug to `inform` (`xf-ts-status-value-renamed`) and added a false alarm, so a cheap model should not be its own second reader. With Sonnet as the second reader Haiku keeps full recall. Sonnet with its own second reading is the configuration to use.

## Real commits (httpx, hono, cobra; 26 commits, Sonnet 5.5)

| | model warn/block findings |
| --- | --- |
| before | 15 |
| failing case required | 10 |
| failing case + second reading | 4 |

The httpx ETag bug that upstream later fixed (#5205) stayed a warning throughout. The lowered ones were style and "could in theory" findings (string formatting, operator precedence that was already correct) that a reader would have had to dismiss by hand. The second reading costs about 15% more tokens on these commits.
