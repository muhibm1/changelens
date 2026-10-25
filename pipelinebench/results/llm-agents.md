# PipelineBench with language-model agents

2026-10-18. 50 scenarios on the fixture platform, one seed. The agents are Claude Sonnet 5.5 and Haiku 5.5, called through Fathom's own Anthropic provider. Ground truth is hand-written in `src/pipelinebench/generate.py`. Grading is mechanical; no model grades another.

## Headline

| agent | condition | accuracy | unsafe | cite recall | name recall | approvers | score |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Sonnet 5.5 | none | 0.48 | 0.16 | 0.46 | 0.56 | 0.46 | 0.39 |
| Sonnet 5.5 | repo | 0.52 | 0.08 | 1.00 | 1.00 | 1.00 | 0.76 |
| Sonnet 5.5 | rag | 0.42 | 0.18 | 0.93 | 0.82 | 0.82 | 0.58 |
| Sonnet 5.5 | **fathom** | **0.56** | **0.00** | 1.00 | 1.00 | 1.00 | **0.82** |
| Haiku 5.5 | none | 0.48 | 0.16 | 0.46 | 0.56 | 0.46 | 0.39 |
| Haiku 5.5 | repo | 0.46 | 0.10 | 1.00 | 1.00 | 1.00 | 0.72 |
| Haiku 5.5 | rag | 0.44 | 0.16 | 0.93 | 0.81 | 0.86 | 0.60 |
| Haiku 5.5 | **fathom** | **0.58** | **0.00** | 1.00 | 1.00 | 1.00 | **0.83** |

Sources:
- Sonnet `none` and `repo`: `pb-sonnet-5-5-first-run.md`.
- Sonnet `rag` and `fathom`: `pb-sonnet-5-5-rag-fathom.md`.
- Haiku: `pb-haiku-5-5.md`.

## What it shows

The 50 scenarios are 20 whose answer is `escalate`, 20 whose answer is `proceed`, and 10 whose answer is `proceed_with_checks`. A scenario forbids `proceed` when the answer is `escalate`, and forbids `escalate` when the answer is `proceed`. The `unsafe` column is the share of scenarios where the agent made a forbidden move.

**Neither model ever proceeded on a change that must be escalated, in any condition.**
- With no context, they missed 1 or 2 of the 20 escalations, and chose `proceed_with_checks` for those. That move is allowed, so it is not counted as unsafe.
- So on this fixture, every unsafe decision is the other forbidden move: escalating a change whose answer is `proceed`. The benchmark forbids that as well, because an agent that sends safe work to a human gets switched off.

**Without Fathom, the models escalate a third to a half of the changes that should go ahead. With Fathom, they escalate one or two, and never a plain `proceed` change.**

| | none | repo | rag | fathom |
| --- | ---: | ---: | ---: | ---: |
| Sonnet: `proceed` changes escalated (of 20; these are the unsafe ones) | 8 | 4 | 9 | 0 |
| Sonnet: `proceed_with_checks` changes escalated (of 10) | 5 | 6 | 9 | 2 |
| Sonnet: escalations missed (of 20) | 2 | 0 | 1 | 0 |
| Haiku: `proceed` changes escalated (of 20; these are the unsafe ones) | 8 | 5 | 8 | 0 |
| Haiku: `proceed_with_checks` changes escalated (of 10) | 6 | 7 | 8 | 1 |
| Haiku: escalations missed (of 20) | 1 | 0 | 0 | 0 |

The first row of each model, divided by 50, is the `unsafe` column. For example, Sonnet `none` is 8 of 50, or 0.16.

- With the verdict, both models got every escalation right and made no forbidden move.
- They also cited every expected constraint and incident, named every reached asset and asked every required approver.
- With the whole repository, the models find the constraints, but they cannot tell whether a change actually reaches what a constraint protects. So they escalate anything near a locked model.
- With retrieved chunks, they escalate even more (16 to 18 of 30), because the chunks name a constraint without the lineage that would rule it out.

**Plain accuracy is low in every condition, for one reason.**
- Neither model will say `proceed`. Of the 20 scenarios whose answer is `proceed`, the agent chose `proceed_with_checks` on all 20 with Fathom. In the other conditions it chose that or `escalate`, and plain `proceed` at most twice.
- The models treat a safe change as one that still deserves a check. A metadata-only description edit or a new column on a leaf model gets "proceed with checks".
- That costs every condition alike. The gap between conditions comes from safety, citations, names and approvers.

## Two follow-up experiments (Haiku)

**Defining the three decisions in the agent prompt**
- The added text: "proceed: normal review and CI is enough; proceed_with_checks: something specific beyond that; escalate: a person must sign off" (`pb-haiku-5-5-defined.md`).
- It made things worse with Fathom: accuracy fell from 0.58 to 0.46.
- The definition of escalate collides with the benchmark's rule that a warn constraint asking for a team's sign-off is `proceed_with_checks`. The agent escalated those instead.
- The definitions were reverted. This run is kept as a record.

**Wording routine tests as routine**
- Before this change, an `inform` verdict with no downstream reach said "Before merging: run tests for X+", which reads as a gate.
- It now says "Nothing beyond normal review and CI: run tests for X+", and the markdown says "Routine checks".
- With the default prompt, Fathom's accuracy moved from 0.58 to 0.60 and the score from 0.83 to 0.84 (`pb-haiku-5-5-routine-fathom-default-prompt.md`).
- The model still chose `proceed_with_checks` on all 20 `proceed` scenarios. It now explains why: same-asset history in the verdict (SLA misses and failed runs that are not the same kind of change), or a reader that still selects a dropped column.
- That reader case is the open question the design doc already lists: should dropping a column that a tier-2 model reads be `inform`? The ground truth says yes. Fathom recommends updating the reader, and the agents take that as a check.

## Corrections made while running

**Void first run**
- The first Sonnet run's `rag` and `fathom` rows are void. The run read a graph that had never been synced, so both conditions got no context.
- `pipelinebench run` now refuses an empty graph for the conditions that read it (#25).
- Those two conditions were re-run. The void rows are kept, marked, in `pb-sonnet-5-5-first-run.md`.

**Grader fix**
- The grader counted Fathom event and verdict ids (`ev_…`, `pf_…`) that agents quoted from the verdict as fabricated, because it knew only constraint and incident ids.
- It now treats an id that appears in the context the agent was shown as real.
- The result files here were re-graded with that rule; each JSON file says so in `regraded`.
- No agent cited an id that was not in its material.

## Cost

About 2.8M input and 0.4M output tokens across the six runs, roughly $7 at list prices. Both models were called through Fathom's `AnthropicProvider`, and no call failed.

## Limits

- The fixture is small: 18 models, 3 DAGs, 3 postmortems and 6 constraints. The scenarios were written by the same project that built Fathom, even though the ground truth comes from the platform's rules and not from Fathom's output.
- One seed per cell. A difference of 0.02 to 0.04 in accuracy is one or two scenarios.
- **What the grader does not measure.**
  - Cite recall counts only the expected ids that were cited. It does not penalise citing an extra real id.
  - "Fabricated" means an id that exists neither in the fixture nor in the agent's context. A real but irrelevant id is not fabricated.
  - The approvers check passes when the required teams are named. It does not penalise naming extra or made-up approvers. In PB-036, Haiku named "ops" where no sign-off was needed.
  - All three are precision gaps, and they apply to every condition alike.
- The models never choose `proceed`, which caps accuracy near 0.6 in this setup. A run with more seeds, a larger platform and an agent loop that can act (not only decide) is the next measurement.
