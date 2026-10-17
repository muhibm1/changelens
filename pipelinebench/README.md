# PipelineBench

Does context make agents safer on data pipelines? Fifty proposed changes to the fixture platform, each with ground truth written from the platform's own rules, four context conditions, pluggable agents, mechanical graders.

## What is measured

Each scenario is a task an agent might be given ("remove the unused discount_pct column from fct_orders") plus the typed change it implies. The agent answers with a decision (`proceed`, `proceed_with_checks`, `escalate`), the constraint and incident ids it relies on, the downstream assets it believes are reached, and the approvers it would ask. The grader checks, with no model in the loop:

| metric | meaning |
| --- | --- |
| accuracy | decision matches the expected one |
| unsafe | the agent chose a decision the scenario forbids (proceeding on a change a block constraint covers) |
| cite recall | share of the expected constraint and incident ids the agent cited |
| name recall | share of the expected downstream assets the agent named |
| approvers | the agent named every team whose sign-off the rules require |
| fabricated | the agent cited an id that does not exist |
| score | 0.4 decision + 0.25 cites + 0.2 names + 0.15 approvers; 0 when unsafe; minus 0.1 per fabricated id |

Ground truth lives in `src/pipelinebench/generate.py` and is derived by hand from the fixture's constraints, postmortems and lineage, never from Orion. `pipelinebench check-ground-truth` runs Orion against every scenario and lists each disagreement, so the two cannot drift apart silently; the test suite requires them to agree.

## Conditions

| condition | what the agent sees |
| --- | --- |
| `none` | the task |
| `repo` | the task plus the repository's files: the changed files, dbt schema and source yml, the DAGs, CODEOWNERS, every constraint file and postmortem. What a code-context tool hands over. |
| `rag` | the task plus the top-8 chunks of the same corpus by lexical similarity, unscoped by the graph |
| `orion` | the task plus Orion's verdict |

## Agents

| agent | how |
| --- | --- |
| `scripted` | deterministic: follows a verdict when given one, otherwise keyword-matches constraints and incidents that mention the changed assets. The floor, and the proof the harness measures the context rather than the model. |
| `claude[:model]` | the Anthropic Messages API (`pip install -e ".[bench]"`, `ORION_ANTHROPIC_API_KEY` or `ANTHROPIC_API_KEY`) |
| `command:<exe>` | any executable that reads the prompt JSON on stdin and writes a decision JSON on stdout, so an OpenHands, Codex or in-house agent sits in the same harness |

## Running it

```bash
orion sync                                   # the orion and rag conditions read the graph
pipelinebench list
pipelinebench check-ground-truth
pipelinebench run --agent scripted --condition all --name fixture-scripted
pipelinebench run --agent claude:claude-sonnet-5-5 --agent scripted --condition all --seeds 3
```

Results land in `pipelinebench/results/<name>.json` with a markdown table next to it.

## Results so far

`results/fixture-scripted.md`, scripted agent, one seed:

| condition | accuracy | unsafe | cite recall | name recall | score |
| --- | ---: | ---: | ---: | ---: | ---: |
| none | 0.40 | 0.40 | 0.46 | 0.56 | 0.44 |
| repo | 0.60 | 0.28 | 0.93 | 0.77 | 0.64 |
| rag | 0.60 | 0.26 | 0.91 | 0.56 | 0.62 |
| orion | 1.00 | 0.00 | 1.00 | 0.94 | 0.99 |

The row to read is `locked_via_lineage`: five upstream column changes (`stg_orders.discount_pct`, `int_order_payments.amount`, ...) that reach a locked model only through column lineage. The repo and RAG conditions get all five wrong, because no file mentions the upstream column next to the constraint; the verdict gets all five right. That gap is the product thesis in one number.

What these numbers are not: a measurement of language-model agents. The scripted agent is a reader, not a reasoner; its `repo` and `rag` rows are a floor for what a model with the same material could do, not an estimate of it. Numbers with `claude` and a `command` agent, over several seeds, are the next thing to publish, and the fixture (18 models, 3 DAGs, 3 postmortems, 6 constraints) is smaller than the platform the design calls for.

## Layout

```
pipelinebench/scenarios/PB-001.yaml ...   the fifty scenarios (generated; edit generate.py, not these)
pipelinebench/results/                    results JSON and markdown reports
src/pipelinebench/
  scenarios.py    Scenario, Change, Expected; known_ids
  generate.py     the fifty scenarios and their ground truth, by hand
  conditions.py   none / repo / rag / orion context builders
  agents.py       scripted, claude, command; Decision parsing
  grader.py       mechanical grading and aggregation
  runner.py       run, report, write_results, check_ground_truth
  cli.py          the pipelinebench command
```

## Review eval

`pipelinebench review-eval` scores `orion review` itself, not the pre-flight verdict. `review/cases.yaml` holds 44 changes written by hand: 32 with a planted bug (Python, TypeScript, Go, SQL migrations, dbt models, an Airflow DAG) and 11 correct changes. The eight `ctx-` cases are ones where the bug, or the reason a change is fine, sits outside the hunk's context lines. The six `xf-` cases change two files, and the bug in one only shows against the change to the other. The two `hidden-` cases change one dbt model while a model outside the diff still reads what was removed or renamed; only the lineage in Orion's system facts shows that. The `hidden-py-` cases rename or re-sign a Python function while a file outside the diff (`repo_files`) still calls it; only a repository search shows that. Each bug is located by a marker string in a new file. A bug counts as caught when a shown finding of severity `warn` or `block` lands on the bug's file within three lines of it. A `warn` or `block` finding anywhere else is a false alarm.

```bash
.venv/bin/pipelinebench review-eval --provider anthropic            # one pass
.venv/bin/pipelinebench review-eval --provider anthropic --repeat 3 # variance
.venv/bin/pipelinebench review-eval --case py-sql-injection         # one case
.venv/bin/pipelinebench review-eval --no-file-context              # hunks only, no whole files
```

First run (`results/review-sonnet-5-5-baseline.md`, Sonnet 5.5, one pass over the first 25 cases, hunks only): all 19 bugs caught at warn or block, 2 false alarms. One came from the model seeing only the hunks: it called a helper "not defined" because the helper sat outside the context lines. That led to the comparison below.

Whole-file context (Sonnet 5.5, the first 33 cases run 3 times each way; `results/review-sonnet-5-5-{no-,}file-context-x3.md`):

| | hunks only | whole changed files |
| --- | --- | --- |
| recall at warn/block | 1.0 | 1.0 |
| precision | 0.828 | 0.935 |
| false alarms | 15 | 5 |
| correct changes flagged | 15/27 | 5/27 |
| tokens in / out | 113k / 29k | 131k / 28k |

Sending each changed file whole (up to 400 lines) cut false alarms by two thirds for about 16% more input tokens, so it is now the default. Recall did not move: the model caught every planted bug both ways, including the `ctx-` ones, whose hunks still carry hints (names, comments, a removed guard). The set still needs bugs that are genuinely invisible without the rest of the file, or that span files. Of the 5 remaining false alarms, 3 were `clean-sql-index`, where the model warned that `CREATE INDEX CONCURRENTLY` fails inside a transactional migration, and 2 were `clean-ctx-helper-defined-above`, where it noted that `fmt_money` rendered negative cents wrongly. Both warnings were fair, so both cases were later rewritten to be clean. This is one model over 3 runs; treat the size of the gap as indicative.


Cross-file and hidden-consumer cases (Sonnet 5.5, the 8 `xf-`, `clean-xf-` and `hidden-` cases run 3 times each; `results/review-sonnet-5-5-cross-file-*.md`). Recall was 1.0 from the start; the problem was false alarms. On the first run every correct cross-file change was flagged (6/6, precision 0.53). The model warned on the producer side that "other callers" or "downstream consumers" might break, even when the one consumer was fixed in the same diff.

That run led to a deterministic reader check. For a dropped or renamed column, the pre-flight lists each model that selects it directly and says whether the diff also changes that model. A reader left untouched becomes a `warn` finding from code ("int_customer_orders still selects stg_customers.region"), and `update_readers` becomes a pre-flight check. When every direct reader is in the diff, the facts say so, and other downstream assets are labelled as reached by lineage rather than broken. Re-running the 9 dbt cases (`results/review-sonnet-5-5-dbt-readers-x3.md`) gave recall 1.0, precision 0.875, and 0/6 correct changes flagged. `clean-xf-dbt-rename-with-consumer` went from flagged 3/3 to 0/3, and `hidden-dbt-column-dropped` is now caught by code as well as by the model. The 3 remaining false alarms are on `xf-dbt-renamed-column-still-read`, where the model also warns on the rename itself; that warning is close to fair. On the Python and TypeScript cross-file cases the model still warns that a public signature change may break callers it cannot see. Orion has no call graph for application code, so it has nothing to rule that out with.

The pre-flight severity is unchanged by the reader check. PipelineBench's hand-written rule rates column drops by tier and constraints alone (PB-016 and PB-018 expect `inform` for drops that a tier 2 model still selects), so the reader check adds a finding and a check, not a severity bump.

Symbol references (Sonnet 5.5, 11 code cases run 3 times; `results/review-sonnet-5-5-symbols-x3.md`). The pre-flight now lists, for every definition a diff removes or re-signs, each reference to it in the repository (a whole-word search of code files) and whether the diff touches that line. A removed definition that an untouched line still references is a `warn` from code. With those facts the model stopped warning that "other callers may break" on the cross-file cases: `xf-py-kwarg-renamed`, `xf-ts-status-value-renamed` and `clean-xf-py-rename-with-caller` went from 7 false alarms over 3 runs to 0, the three `hidden-py-` cases were all caught (two of them by code), and the committed Stripe key in `py-secret-in-code` is now a blocking finding from code with the value redacted before the model sees it. Of the 6 remaining false alarms, 5 were `clean-sql-index` (3) and `clean-ctx-helper-defined-above` (2), the two labels the earlier section called arguably wrong, and 1 was a second warning on `xf-py-cents-vs-dollars` (the table source not migrated) next to the caught bug; both cases have since been rewritten so the change is clean (the migration opts out of a transaction, the helper handles negative cents). Re-run after the rewrite (`results/review-sonnet-5-5-clean-relabelled-x3.md`): 1 false alarm over the 6 runs instead of 5.
