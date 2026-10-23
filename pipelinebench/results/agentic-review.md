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

## Status and next step

`--agent` stays opt-in (`review.agent: false` by default). The four-lens pipeline remains the default review. The tools, the evidence they make citable and the budgets are in place. The agent also has `code_search`, which searches the change-scoped hybrid code index (`orion.rag`) by words, so it can find related code it cannot name. It is not yet measured. The earlier candidates (investigation inside each lens, and tools for the second reading to confirm or reject candidates) still stand. Like any recall experiment, they continue in the agentic review recall work, are measured on the tuning set first, and have to beat the lenses on recall to be worth their cost.
