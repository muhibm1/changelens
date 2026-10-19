# Accuracy on real bugs: focused review passes, a held-out set, and a stricter judge

2026-10-19. The goal set for this round was 95% of real bugs caught, with 80% as the floor. This page says
where the review stands against that, measured on bugs it was never tuned on, and what the numbers rest on.

**The short answer: 60% on held-out bugs, short of the 80% floor.** Focused review passes more than doubled
recall on the same model (18% to 42% on the tuning set). Every number here comes from Claude Haiku 5.5,
because the credit available for this round was $2.77. Sonnet and Opus were not run.

## What changed in the review

- **Four focused passes instead of one.** `orion review` now reads each change four times. Each pass has one
  focus: unusual but legal inputs, what the code's calls really do (language, library and protocol semantics),
  control flow and state, and contracts with callers and callees (`LENSES` in `src/orion/review.py`).
  One pass asked to find everything stops at the first plausible problems. Four passes, each told to be
  exhaustive about one kind of failure, find what a single read skips. `--lens general` restores the
  single pass.
- **Shared context is cached.** The system prompt and the change are the same for every pass; only the focus
  differs. The Anthropic provider marks that prefix for prompt caching, so the three later passes read it at a
  tenth of the price.
- **Repeats are merged.** Copies with near-identical wording on nearby lines are merged in code
  (`merge_candidates`). The second reading also names any finding that repeats an earlier one
  (`duplicate_of`); the repeat moves to `rejected` with a note, so it is never silently lost.
- **A stricter second reading.** It reads at `high` effort (`review.verify_effort`, `--verify-effort`). It
  sets `holds` to false when an earlier check on the same path rules the case out. A new kind,
  `pre_existing`, covers a case that failed the same way before the change; like every kind but `defect`, it
  stops at `inform`.
- **New knobs.** `--effort` and `review.effort` set how hard the model thinks (default `medium`).
  `--lens` and `review.lenses` choose the passes.

## How it was measured

- **Held-out set.** `pipelinebench/review/heldout.yaml` holds 45 real bugs from eight repositories that are not
  in the tuning set: starlette, werkzeug, gin, chi, echo, ky, h3 and express (15 Python, 15 Go, 15 TS/JS).
  They were mined with `mine-introducers`, taken in mined order, and labelled by hand from the upstream fix
  before any review ran. No design choice was made by looking at them. The 50 bugs in `recall.yaml` remain the
  tuning set.
- **A model judge for "about the bug".** The `must_mention` word check gave false credit. A warning about a
  different problem counted when it happened to use one of the words: "certifi" matched `cert`, and a CIDR
  warning matched a scheme bug. `--judge-model` now asks a model whether each nearby finding names the
  labelled bug's root cause and failing case. A different problem on the same lines does not count.
  Re-judging kept raw reviews costs nothing extra (`--rejudge`). Every number below uses this judge.
- `recall-eval --jobs N` reviews several cases at once, each in its own worktree of the clone.

## Results

Claude Haiku 5.5, review passes at `medium` effort, one run per row.

- **Caught:** a warning on the bug's lines that names the bug.
- **In file:** a warning in the bug's file that is about something else. It shows the review looked in the
  right place, and it is not a catch.
- **Recall in file:** caught plus in file.

| build | set | caught | in file | inform only | missed | recall | recall in file | cost |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| one general pass (`recall-haiku-5-5-one-pass`) | tuning 50 | 9 | 12 | 3 | 26 | 0.18 | 0.42 | $0.16 |
| four passes (`recall-haiku-5-5-passes`) | tuning 50 | 21 | 14 | 1 | 14 | 0.42 | 0.70 | $0.43 |
| four passes, stricter second reading (`recall-haiku-5-5-passes-final`) | tuning 50 | 20 | 14 | 3 | 13 | 0.40 | 0.68 | $0.46 |
| four passes (`heldout-haiku-5-5-passes`) | held-out 45 | 26 | 9 | 2 | 8 | 0.58 | 0.78 | $0.42 |
| four passes, stricter second reading (`heldout-haiku-5-5-passes-final`) | held-out 45 | 27 | 9 | 3 | 6 | **0.60** | **0.80** | $0.44 |

For comparison, Sonnet 5.5 with one pass caught 21 of 50 on the tuning set under the word judge
(`recall.md`). The word judge credits several cases this judge does not.

**Noise.** The extra passes cost precision.

- **Planted bugs** (`review-haiku-5-5-passes-x1`): recall 0.98 (41 of 42 bugs) and 6 false alarms, on 4 of 19
  correct changes. Haiku with one pass had 1, and Sonnet with one pass 0. Most of the six are edge-case
  worries on code the labels call correct: a negative `limit`, a currency code in an unexpected case, a
  concurrent index build that fails half way.
- **Replay of 26 merged changes** (`replay-haiku-5-5-passes`): 13 warnings on 7 changes. Of those, 4 are
  plausible, 5 noise and 4 not yet judged. An earlier Haiku build had 3 noise warnings on 38 changes. The
  confirmed hono ETag bug was not caught.

The stricter second reading did not bring false alarms back down on Haiku. Whether a stronger verifier
does was not measured.

## What the misses are

On the held-out set, 9 bugs got a warning in the right file about something else and 3 stopped at
`inform`. Six were missed outright:

- gin Hijack refusing after headers only;
- `Unwrap` on the pointer type;
- echo's WebSocket X-Forwarded-For;
- ky reading a cloned body twice;
- h3's case-sensitive extension lookup;
- express's `Buffer.byteLength` on a Buffer.

On the tuning set, the misses that remain are mostly the kind the inputs pass targets: a case-sensitive
Bearer prefix, `\r\n` in SSE, a `#` fragment, an empty file read as missing. Haiku either does not raise
them or raises them at `inform`. Two runs of one build differ by a few cases, so a two-case gap means
nothing.

## What it would take to go further

- **Measure a stronger model.** Run the same design with Sonnet 5.5 or Opus 5.5 on both sets, three runs each.
  The prompts and passes are model-independent, and a stronger second reading is the likeliest fix for the
  false alarms. One Sonnet run of this design on one set is about $3 to $5, and Opus is roughly double.
- **Raise the effort of the passes.** On one case, `high` effort cost about three times `medium`.
- Then decide the default, passes on or off, from recall and noise measured together on that model.

## Rerun

```
pipelinebench recall-eval --workdir <dir> --provider anthropic --model claude-haiku-5-5 --judge-model claude-haiku-5-5 --jobs 4 --raw-dir <dir>/raw
pipelinebench recall-eval --workdir <dir> --cases pipelinebench/review/heldout.yaml --provider anthropic --model claude-haiku-5-5 --judge-model claude-haiku-5-5 --jobs 4
pipelinebench recall-eval --workdir <dir> --rejudge <dir>/raw --provider anthropic --judge-model claude-haiku-5-5   # judge again, no reviews
pipelinebench recall-eval ... --review-arg=--lens --review-arg=general   # the single-pass baseline
```
