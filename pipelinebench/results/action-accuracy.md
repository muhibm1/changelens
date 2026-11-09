# Raising agent accuracy with ChangeLens

2026-11-09. PipelineBench, Sonnet 5.5 and Haiku 5.5, one seed, run through the signed-in command-line client (`--agent claude-code:<model>`), so no API credit was spent.

## The short version

An agent given ChangeLens's verdict picked the right action only about 6 times in 10 (`llm-agents.md`). It was never unsafe. It just would not say plain `proceed` on a safe change: it chose `proceed_with_checks` instead.

Three fixes, then one scoring pass on 42 scenarios no fix was tried against:

| agent, with ChangeLens | before | after |
| --- | ---: | ---: |
| Sonnet 5.5, held-out accuracy | 0.69 | **1.00** |
| Haiku 5.5, held-out accuracy | 0.60 | **0.93** |
| Sonnet 5.5, held-out unsafe moves | 0 | 0 |
| Haiku 5.5, held-out unsafe moves | 2 | 0 |

"Accuracy" means the agent chose the same action as the hand-written answer. "Unsafe" means it made a move the scenario forbids: going ahead with a change that must go to a person, or sending a safe change to a person.

## Why accuracy was low

On the 60 development scenarios (PB-001 to PB-060), Sonnet with the old verdict scored 0.67. All 20 misses were the same: a safe change answered with `proceed_with_checks` (`action-dev-sonnet-baseline.md`). Reading the rationales gave three causes.

1. **The verdict never said what to do.** It said `INFORM` and listed routine checks such as "run tests". The agent had to guess that `inform` means go ahead, and it read any listed check as "with checks".
2. **The verdict nagged on safe backfills.** A backfill already outside the blocked 06:00 to 20:00 window was still told "Before merging: schedule the backfill off-hours". That reads as a gate.
3. **The agent was never told what the three decisions mean.** The benchmark's prompt named them without defining them. An earlier attempt at definitions (`pb-haiku-5-5-defined.md`) defined `escalate` as "a person must sign off", which collides with warning rules that also ask for a sign-off, and it made things worse.

## The fixes

1. Every verdict now carries `action`: a decision (`proceed`, `proceed_with_checks` or `escalate`) and one sentence on what it means. It maps one to one from severity (`inform`, `warn`, `block`), so it adds no new judgement; it states the one ChangeLens already made. The markdown shows it under the summary as "Recommended action". (`src/changelens/verdict.py`)
2. The `schedule_off_hours` check appears only when a backfill lands inside a blocked window.
3. PipelineBench's agent prompt defines the three decisions. `proceed`: normal review and CI are enough, and running tests or updating readers in the same change is normal work. `proceed_with_checks`: allowed, but people outside the change must be involved first (a sign-off or heads-up a warning rule asks for, or the owners of a tier 1 consumer). `escalate`: a blocking rule forbids the change as written. The prompt is the same in every condition.

On the development set, fix 1 alone took Sonnet from 0.67 to 0.82 (`action-dev-sonnet-action.md`); all three took it to 1.00 (`action-dev-sonnet-v2.md`).

## How the held-out set was kept honest

- PB-061 to PB-102 (42 scenarios: 14 escalate, 14 proceed with checks, 14 proceed) were written by hand from the decision rule in `src/pipelinebench/generate.py`, and committed before any fix was run. They are tagged `heldout`; `pipelinebench run --split heldout` selects them.
- Their labels come from the fixture's rules, not from ChangeLens. `pipelinebench check-ground-truth` then confirmed ChangeLens agrees with all 42.
- The fixed version was frozen in a commit, and the held-out set was scored once, after. No change was made after seeing those scores.
- The "before" rows ran the commit before the fixes against the same 42 scenarios.

## Held-out results, all conditions (Sonnet 5.5, after the fixes)

`action-heldout-sonnet-after.md`

| condition | accuracy | unsafe | cite recall | name recall | approvers | score |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| none (the task only) | 0.33 | 0.10 | 0.50 | 0.52 | 0.50 | 0.43 |
| repo (all the repository's files) | 0.86 | 0.00 | 1.00 | 1.00 | 1.00 | 0.94 |
| rag (top 8 retrieved chunks) | 0.64 | 0.07 | 0.88 | 0.71 | 0.81 | 0.74 |
| **changelens (the verdict)** | **1.00** | **0.00** | 0.98 | 1.00 | 1.00 | **0.99** |

## What this does and does not show

- **The definitions help every condition, not just ChangeLens.** With the whole repository, Sonnet now reaches 0.86. ChangeLens's lead over that is 6 scenarios of 42, all of them changes that only lineage shows are blocked or allowed. The bigger gaps are against no context (0.33) and retrieval (0.64), and those two now make unsafe moves: with no context, Sonnet went ahead on 4 of the 14 changes that must be escalated.
- **ChangeLens agrees with every label on this fixture.** So with the verdict, this benchmark measures whether an agent follows it, not whether ChangeLens is right. Fix 1 makes following it easy. On a real platform where ChangeLens can be wrong, an agent that follows it will be wrong too.
- **Haiku's 3 misses** (PB-095, PB-096, PB-100) are over-caution on safe changes: two overnight backfills of `raw_events`, where it wanted a heads-up because of the earlier daytime incident, and a description-only edit near a model with a sign-off rule. None was unsafe.
- **Limits.** One seed. A small fixture (18 models, 3 DAGs, 6 constraints). The held-out set was written by the same project, from the same rule, on the same fixture, so it tests new changes, not a new platform.
