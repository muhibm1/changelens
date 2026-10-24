# Agentic review (`orion review --agent`), first measurements

These runs were made on 2026-10-23 with Sonnet 5.5 through the `claude-code` provider, and a Sonnet 5.5 judge reading each hand label. Each set was run once. Held-out was run first, and that run's traces showed the agent was not calling tools. That observation led to the opening-turn change. The change was checked on the tuning set only, and no prompt or budget was tuned on held-out outcomes.

| run | set | recall | in file | warns/case | input tokens (cache write + read) | out | wall |
| --- | --- | --- | --- | --- | --- | --- | --- |
| four lenses (main, 2026-10-20, runs 2 and 3) | held-out 45 | 0.556–0.644 | 0.711 | | ~8.2–9.1M | ~350k | |
| `--agent`, answers whenever it likes | held-out 45 | 0.333 | 0.444 | 0.89 | 2.29M | 71k | 6 min |
| `--agent`, opening turn must call tools | tuning 50 | 0.24 | 0.46 | 0.92 | 4.71M | 92k | 8 min |

## Tuning set, like for like (2026-10-23 afternoon)

Same set (tuning 50), same model and judge, under the model's own severity (these runs predate the evidence gate, #41).

| run | recall | in file | warns/case | input tokens (cache write + read) | out | wall |
| --- | --- | --- | --- | --- | --- | --- |
| four lenses (default review) | 0.36 | 0.58 | 1.48 | 6.42M | 280k | 17 min |
| `--agent`, free text focus, opening turn | 0.24 | 0.46 | 0.92 | 4.71M | 92k | 8 min |
| `--agent`, lenses as a checklist, opening turn | 0.34 | 0.52 | 1.12 | 5.13M | 111k | 9.5 min |

Telling the one agent to work through the four lenses in order, as a checklist, brings it to within one case of the four separate passes (17 against 18 caught). The four lenses caught two bugs the checklist missed, and the checklist caught one they missed. It uses 80% of the input tokens, 40% of the output and a little over half the wall time. Most of the input is the provider's per-call overhead, so the next saving is fewer calls. Details: [recall-sonnet-5-5-lenses.md](recall-sonnet-5-5-lenses.md), [recall-sonnet-5-5-agent-checklist.md](recall-sonnet-5-5-agent-checklist.md).

The held-out detail is in [heldout-sonnet-5-5-agent.md](heldout-sonnet-5-5-agent.md) and [recall-sonnet-5-5-agent-open.md](recall-sonnet-5-5-agent-open.md).

## What it shows

- **One agent is about four times cheaper than four lenses, and catches fewer bugs.** Left to choose its own focus it files fewer findings (under one warn a case), so recall falls. The four focused passes find different bugs, and one investigator does not make up for that.
- **Left free, the model does not investigate.** On held-out it answered on turn 1 in 44 of 45 cases, with no tool calls. Because of this, the opening turn's schema now has only `calls`.
- **When forced, it investigates briefly.** Each case had two turns and one to three calls, mostly grep and read. That doubled the tokens. In-file recall was about the same (0.46 against 0.444) and full recall was lower. The two sets differ, though, so this is not a like-for-like comparison.

## The sweep: suspects from every lens, settled with tools (2026-10-23 night)

`--agent` with no named lens now runs a sweep. On the opening turn the agent goes through the four lenses as one checklist and lists every suspect each one raises: a place, and the case that may fail there. In the same turn it asks for the tool calls that would settle the suspects it cannot settle from what it is shown. Each later turn shows the suspects again. The agent must report every suspect that is not ruled out, at `warn` when its failing case holds. Two other changes went in with it:

- **claude-code caches the shared prefix.** `claude -p` caches its system prompt but not its stdin prompt. The shared review message (facts, files, diff) was therefore written to cache in full on every call, and every lens pass and agent turn paid for it again. The preset now passes the system prompt plus the message's first part through `--system-prompt-file`. On a 43k-token prefix the second call read 41.9k from cache, where before it read none. This helps the four-lens pipeline as much as the agent.
- **A "pre-existing" verdict is checked against the diff.** On the tuning set, the second reading lowered four real bugs as pre-existing, on lines the introducing commit had written. If a line is added by the change and its text is removed nowhere in the diff, it did not exist before. A pre-existing verdict on such a line no longer lowers the finding. Code decides this, not the model.

Tuning set, Sonnet 5.5 via claude-code, with the model's severity (`--escalation model`), one run each:

| run | escalation recall | detection recall | in file | warns/case | input tokens (write + read) | out |
| --- | --- | --- | --- | --- | --- | --- |
| four lenses (above) | 0.36 | 0.42 | 0.58 | 1.48 | 6.42M | 280k |
| sweep | 0.36 | 0.52 | 0.60 | 1.36 | 5.01M | 180k |
| sweep, suspects that hold are warn | 0.36 | 0.54 | 0.68 | 1.68 | 5.67M | 217k |
| sweep, warn rule, pre-existing check | 0.38 | 0.54 | 0.66 | 1.66 | 5.71M | 202k |

Then the held-out set, three runs of the last build, never tuned on ([heldout-sonnet-5-5-agent-sweep-x3.md](heldout-sonnet-5-5-agent-sweep-x3.md)). It is set against the four-lens baseline under the same severity ([heldout-sonnet-5-5-baseline-x3.md](heldout-sonnet-5-5-baseline-x3.md)):

| held-out 45, 3 runs | escalation recall | detection recall | in file | inform only | other warns/case |
| --- | --- | --- | --- | --- | --- |
| four lenses | 0.615 (0.578–0.644) | 0.741 (0.733–0.756) | 0.719 | 5.0 | 0.99 |
| sweep | **0.637** (0.622–0.644) | **0.763** (0.711–0.800) | 0.771 | 4.3 | 1.07 |

On held-out, the sweep matches the four lenses: its mean is one case higher, and the two ranges overlap. It is also cheaper. A held-out run took about 9.5 minutes at `--jobs 4`. The first, cold-cache run used 4.99M input tokens (2.70M cache write, 2.29M read) and 189k output. The second and third runs reused the cached prefixes, at 0.92M written and 4.2–4.5M read. The tuning-set run used 11% fewer input tokens and 28% fewer output tokens than the four lenses. An agent run averages 2.2 turns, 4.7 suspects and about 2.5 tool calls, mostly grep and read. `code_search` was called twice in 135 cases.

What did not work: telling the agent that a suspect that holds is a `warn`. It moved detection more than escalation. Several right findings still end at `inform`, by the model's own choice or through the second reading's "did not confirm". These two are the next lever, together with false alarms: "other warns per case" is 1.07 against 0.99, and this build has not been run on the replay (noise) eval.

## Status and next step

`--agent` stays opt-in (`review.agent: false`), because the default review should not change on recall alone. Before it becomes the default, it needs a replay-eval run for noise, and the strategy thread's history replay for false alarms per week. `--agent --lens <name>` still runs one investigating agent per lens. `--agent --agent-steps 1` is the one-call checklist from #49, with no tools.
