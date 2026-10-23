# Agentic review (`orion review --agent`), first measurements

These runs were made on 2026-10-23 with Sonnet 5.5 through the `claude-code` provider, and a Sonnet 5.5 judge reading each hand label. Each set was run once. Held-out was run first, and that run's traces showed the agent was not calling tools. That observation led to the opening-turn change. The change was checked on the tuning set only, and no prompt or budget was tuned on held-out outcomes.

| run | set | recall | in file | warns/case | input tokens (cache write + read) | out | wall |
| --- | --- | --- | --- | --- | --- | --- | --- |
| four lenses (main, 2026-10-20, runs 2 and 3) | held-out 45 | 0.556–0.644 | 0.711 | | ~8.2–9.1M | ~350k | |
| `--agent`, answers whenever it likes | held-out 45 | 0.333 | 0.444 | 0.89 | 2.29M | 71k | 6 min |
| `--agent`, opening turn must call tools | tuning 50 | 0.24 | 0.46 | 0.92 | 4.71M | 92k | 8 min |

The detail is in [heldout-sonnet-5-5-agent.md](heldout-sonnet-5-5-agent.md) and [recall-sonnet-5-5-agent-open.md](recall-sonnet-5-5-agent-open.md).

## What it shows

- **One agent is about four times cheaper than four lenses, and catches fewer bugs.** It files fewer findings (under one warn a case), so recall falls. The four focused passes find different bugs, and one investigator does not make up for that.
- **Left free, the model does not investigate.** On held-out it answered on turn 1 in 44 of 45 cases, with no tool calls. Because of this, the opening turn's schema now has only `calls`.
- **When forced, it investigates briefly.** Each case had two turns and one to three calls, mostly grep and read. That doubled the tokens. In-file recall was about the same (0.46 against 0.444) and full recall was lower. The two sets differ, though, so this is not a like-for-like comparison.

## Status and next step

`--agent` stays opt-in (`review.agent: false` by default). The four-lens pipeline remains the default review. The tools, the evidence they make citable and the budgets are in place. The next experiment is investigation inside each lens (`--agent --lens inputs --lens semantics ...`), measured on the tuning set first. Tools could also be given to the second reading, to confirm or reject candidates. Both cost more than one agent, so either has to beat the lenses on recall to be worth it.
