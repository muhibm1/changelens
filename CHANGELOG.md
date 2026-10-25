# Changelog

## Unreleased

### Split from Orion (2026-10-25)

- Fathom is the data-platform half of Orion, moved into its own repository with its history. It keeps sync (dbt, Airflow, git, incidents, run history), column-level lineage, constraints, ownership and incident history, `preflight`, `gate`, `review`, `postflight`, `propose` and PipelineBench.
- Renamed throughout: the package and command are `fathom`, the config file is `fathom.yaml`, environment variables start with `FATHOM_`, and the local store is `.fathom/`.
- Left in Orion: the engineering record, the agent context tools (`context`, `impact`, `search`, `read`, `file_history`), the record tools, the code graph, the always-on service and prompt hook, and lessons from Auspex cases. The MCP server now has seven tools.

### A dollar cap on API calls

- The `anthropic` provider refuses to start unless `FATHOM_API_CAP` names a dollar cap, and refuses an unpriced model. Every call goes through Flywheel's budget guard (`flywheel.spend`, pinned by commit): it is added to `~/.fathom/api-ledger.jsonl` (`FATHOM_API_LEDGER`) with its uncached input, cache writes, cache reads, output and list price, and once the ledger's total reaches the cap every later call is refused, in that run and the next. A refusal is a provider error, so a review still returns its deterministic findings. `flywheel spend` totals the ledger.
- The reviewer workflow runs each review under a 25-cent cap and prints what it spent.
- Tests can no longer reach a real key: the suite removes the key variables and runs its faked calls under a one-dollar cap in a temporary ledger.

### Precision at warn

- **Escalation policies** (`--escalation`, `review.escalation`).
  - `platform` is the default. A model warning on a file that maps to a dbt or Airflow asset stays at warn or block only when recorded system context backs it: a firing constraint, a downstream reader of a column the hunk names, a change to the rows or values of an asset other assets read (anything but docs, tests or a new asset), an incident, or a warehouse-confirmed failing case. Otherwise it is lowered to inform with a note.
  - On files Fathom records no system context for, the model's severity stands.
  - `evidence` gates every file, adding sensitive areas, revert history and outside callers as backing for code.
  - `model` keeps the model's severity everywhere.
  - Under `evidence` and `platform`, an unmapped path is reported at inform. `fathom gate` still raises it.
- **Measured on 45 held-out bugs** (Sonnet 5.5, 3 runs).
  - Detection recall (surfaced at any severity) is 0.741.
  - Escalation recall (at warn or block) is 0.615 under `model` and `platform`, and 0.148 under `evidence`, with other warnings per case falling from 0.99 to 0.17.
  - The held-out set is all application code, so `platform` leaves it at model severity. There the gate lowers right and wrong warnings alike, and the app-code replay had no noise at model severity, so gating code buys little. `platform` keeps the gate where Fathom has system context to check against.
- **Warehouse confirmation** (`review.warehouse`). A model warning on a data model gets its failing case run as one read-only SELECT, checked in code. Rows found make it eligible for warn; no rows lower it under every policy.
- **Findings cache.** Model answers are cached in DuckDB, keyed exactly by the prompt, provider, model, effort and endpoint. There is no semantic matching. Turn it off with `--no-cache` or `FATHOM_REVIEW_CACHE=0`; evals always run without it. Lens passes run in parallel after the first warms the prompt cache.
- **Model routing.** `--complex-model` is used for large diffs.
- **dbt precision.**
  - A changed macro maps to the models that call it.
  - Moved definitions are not reported as removed.
  - A reader that gets a column through `select *` is not reported as broken.
  - A column documented in YAML that the SQL does not select is a new check.
- **Benchmarks.**
  - `pipelinebench history-replay` reviews every merged change in a window and reports warnings per week, false alarms per week and bugs flagged against hand labels. A `--escalation model` run scores all three policies at once.
  - `recall-eval` splits detection from escalation recall.
  - `recall-spread` reports the spread over repeated runs.
- **cal-itp, deterministic checks, 116 commits over 26 weeks.** Warnings fell from 33 to 4, all of them artifacts of replaying against a newer graph, so there were 0 false alarms a week.
- **cal-itp, model review, same 116 commits** (Haiku 5.5 passes, Sonnet 5.5 second reading, `--escalation model`, scored for all three policies; `pipelinebench/results/calitp-history-model.md`). Warnings were labelled by hand from the diff and the upstream history, with limited checking against the code, so treat noise counts as one reader's judgement.

  | policy | warnings a week | false alarms a week | bugs flagged at warn |
  | --- | ---: | ---: | ---: |
  | `model` | 5.27 | 1.62 | 3 of 6 |
  | `platform` | 1.50 | 0.58 | 0 of 6 |
  | `evidence` | 0.23 | 0.08 | 0 of 6 |

  Five of the six bugs surface at some severity. As first scored, `platform` lowered all three bugs caught at warn, because a change to a whole model got no backing unless it was the only asset the change touched.
- **The gate now backs a finding with the consumers of its own file's asset.** Pre-flight's `affected` entries say which changed assets reach them (`from`), and any change to the rows or values of an asset other assets read is backing. Re-scored on the same reviews (`pipelinebench/results/calitp-history-model-consumers.md`), `platform` has 2.23 warnings and 0.81 false alarms a week and flags 3 of 6 bugs, the same bugs as `model` at half its false alarms. The rule was written after seeing the misses, so this is a tuning result. The held-out set is application code, which `platform` does not gate, so it is unchanged there.
- `pipelinebench history-relabel` re-scores a finished replay against edited labels without re-running the reviews.
- **cal-itp with the agentic review (`--agent`), same 116 commits, Haiku 5.5** (`pipelinebench/results/calitp-history-agent.md`). Under `platform` it raised 1.31 warnings and 0.35 false alarms a week and flagged 2 of 6 bugs at warn, against 2.23, 0.81 and 3 of 6 for the four lenses. It surfaced 3 of 6 bugs at any severity, against 5 of 6. It used 29.6M input and 1.9M output tokens, against 18.7M and 2.7M. Fewer false alarms, but less recall, so `review.agent` stays off. 25 of its warnings were new and were labelled by hand the same way.

### Retrieval core

- `fathom rag index | search | read`: hybrid (BM25 + local embeddings, reciprocal rank fusion), graph-scoped retrieval over code and the platform's prose, in one DuckDB file. Code is chunked by syntax with line spans; import edges, record refs and the data graph's blast radius make a change's neighbourhood rank first. Optional local cross-encoder reranking (`FATHOM_RAG_RERANKER=ce:<model>`). Incremental by git blob and chunk text hash; unchanged checkouts and repeated searches are cached.
- `fathom.rag.cite.check`: citation validity and support for any answer that cites chunk ids.
- `pipelinebench retrieval-eval`: 42 hand-labelled queries (dev and held-out), recall@k, MRR, citation faithfulness with a tamper control, latency, three runs per configuration.

### Reviews for app projects, on a subscription

- Context for code that is not a data platform: owners from `CODEOWNERS`, sensitive areas from a YAML or JSON file (`--areas file#project`, `review.areas`), and earlier fixes and reverts of the changed files from git. The model sees them as facts it can cite (`area:`, `commit:`, `ref:CODEOWNERS:N`); a change in an area gets a deterministic finding at the area's severity. The review JSON carries them under `context`.
- A `ref:` to a line of a changed file that the model was shown in full (or in a window around a hunk) is now valid evidence; it used to be removed as not resolving.
- `fathom review --format agent`: a pass or fail with `blocking` and `minor` findings, each with the failing case as `proof` and a `plain` sentence the model writes for a reader who does not read code. A review whose model call failed fails. Uncertain findings and unresolved paths stay apart from the findings.
- `command` provider: any local command that reads the prompt on stdin and prints the JSON answer. The `claude-code` preset runs `claude -p` with no tools, settings or MCP servers, so a review runs on the plan the CLI is signed in to, with no API key. The evals take `--provider claude-code` too.
- `recall-eval` scores a case whose review call failed (a usage limit, say) as `error`, not `missed`, and a failed `claude -p` reports its own reason instead of its usage block.

### Focused review passes

- `fathom review` reads each change in four focused passes: unusual inputs, the real semantics of the calls the code makes, control flow and state, and contracts with callers and callees. The system prompt and the change form a shared, prompt-cached prefix. Findings from the passes are merged, and the second reading rejects a finding that repeats an earlier one (kept under `rejected` with a note). `--lens` and `review.lenses` choose the passes; `--lens general` is the old single pass.
- `--effort` and `review.effort` set how hard an Anthropic model thinks (default `medium`). The second reading runs at `high` (`--verify-effort`, `review.verify_effort`). It now lowers a case that failed the same way before the change (`pre_existing`) and one that an earlier check on the same path rules out.
- `pipelinebench/review/heldout.yaml`: 45 real bugs from eight repositories outside the tuning set, labelled by hand from upstream history and never used for tuning. `recall-eval --judge-model` judges whether a finding is about the labelled bug with a model, instead of the `must_mention` words, which gave false credit. `--rejudge` judges kept raw reviews again without reviewing, and `--jobs` reviews cases in parallel worktrees.
- On Claude Haiku 5.5, the passes raise recall on the 50 tuning bugs from 0.18 to 0.42 and reach 0.60 on the held-out bugs (0.80 warned in the right file). That is short of the 0.95 target and the 0.80 floor, and it costs precision: 6 false alarms on the planted-bug cases where a single pass had 1 (`pipelinebench/results/accuracy.md`).
- The pull-request review workflow uses Claude Haiku 5.5 to keep each push's review to a few cents.

### Review context

- A changed file over 400 lines no longer goes to the model as bare hunks: each hunk's enclosing definition goes instead (`context.windows`), merged and capped at 400 lines per file, numbered like the diff.
- The definitions a change calls into are shown to the model (`context.callees`): for each name the added lines call that no changed file defines, `git grep` finds its definition line in the repository and the block is cut at its end. A free call is shown when the repository defines the name once, or in a module the changed file imports or sits beside; a method call only in such a module. Ambiguous names are left out. The definitions' lines are valid evidence (`ref:<path>:<line>`) and the review JSON lists them under `callees`.
- The model is told to read the shown code and the called definitions, and that a finding about a called function must follow from its shown definition. The second reading no longer lowers a warning because no caller in the repository passes the failing input: most code under review is a library or service whose callers are elsewhere, so a value any caller could pass is an ordinary case.

### Recall on real bugs

- `pipelinebench mine-introducers` blames the lines a fix commit changed to find the commit that introduced the bug, and lists the small enough ones as candidates. `pipelinebench/review/recall.yaml` holds fifty of them from httpx, hono and cobra, read and labelled by hand (what the bug was, and where it sits in the introducing commit).
- `pipelinebench recall-eval` reviews each introducing commit against its parent with its commit message as intent, as a team would have seen it, and counts the bugs caught at warn or block within twelve lines (`caught`), warned about elsewhere in the file (`in_file`), raised only as inform, or missed. `--fathom` points it at another build so two builds compare. Sonnet 5.5 catches 21 of the 50 at warn or block and warns in the right file on 29 (`pipelinebench/results/recall.md`); the context changes did not move that number beyond one run's variance, and the second reading still lowers about six right warnings per run.
- Replay and recall clones keep `fathom.yaml` and the graph out of the diff under review (`.git/info/exclude`); before, both appeared as added files in every replayed change.

### Verdict quality

- A verdict's `history` lists incidents and events from the same kind of change. Failed runs, missed SLAs and schema changes from other kinds of change move to a new `background_events` field, and the markdown counts them in one line. They read as reasons for caution when they were not.
- Incident recency adds risk only for an incident from the same kind of change. Other incidents on the path are still listed and named in the summary as not from this kind of change.
- An incident's opening and its postmortem are one line in the markdown.
- Repartitioning to the key an asset already has reaches nothing and is not matched to repartition incidents.

### Real-repository noise

- `pipelinebench replay-eval` replays merged commits from public repositories (httpx, hono, cobra, cal-itp) through `fathom review` and counts warnings per change against hand labels: confirmed upstream, plausible, or noise.
- The second reading of a model warning says what a confirmed case is: a defect, intended by the change, a trade-off it chose, an unusual caller, or style. Only a defect keeps `warn`. On the replay, warnings fell from 17 to 12 and noise from 8 to 3, with both confirmed bugs caught (`pipelinebench/results/replay.md`).
- A model file the change adds is new, not unmapped: nothing in a graph synced from the base branch reads it yet. Its schema entries are accounted for too.
- A review raised to `warn` only because no changed path maps to an asset now carries a finding that says so and lists each path's reason. Before, the review was `warn` with nothing to explain it.
- An `fathom.yaml` with no dbt or Airflow adapter (an application repository) no longer reports every changed file as unmapped. Before, every review in such a repository was `warn` with no finding to explain it.
- A diff given as a file is filtered by the platform roots, as a git diff already was.
- A renamed file is diffed against its old path; before, a renamed model read as a new file.
- A schema entry removed together with its model's SQL file is accounted for, not unmapped.
- An unmapped `dbt_project.yml` says why in words. Before, the reason was an empty list. A changed `packages.yml` or `package-lock.yml` says a package upgrade can change any macro the models call.
- With the default model, the replay of 26 changes from httpx, hono and cobra has 5 warnings, none of them noise, and catches the confirmed bug (`pipelinebench/results/replay.md`).

### PipelineBench

- Ten more scenarios (PB-051 to PB-060), with ground truth written by hand: lineage through renamed, redefined and retyped columns, a constraint scoped to a downstream model (CON-014 on `stg_events`), the start of the CON-022 window, a backfill reaching tier-1 exposures, and a tier-1 export that reads a whole model. PB-001 to PB-050 keep their ids, so earlier agent runs still compare. Fathom agrees with all sixty.

### Real dbt and Airflow projects

- Model SQL read without a compiled manifest (`dbt parse` only) handles versioned refs (`ref('m', v=2)`), package refs (`ref('pkg', 'm')`), `{{ this }}` in incremental filters, and macro calls in expressions and as a whole CTE body. On datacoves/balboa, models whose columns could not be read fell from 7 to 1 (a Snowflake semantic view, which is not SQL).
- TaskFlow DAGs are read: a `@dag` function's `@task` functions and operators become tasks, `@task_group` prefixes their ids, and dependencies come from `>>` chains and from passing one task's result to another. A bash or dbt task's command is the string it returns. On balboa, Airflow went from 0 DAGs to 32 DAGs and 53 tasks.
- A dbt selector in quotes (`-s 'tag:a+ tag:b+'`) is read as two selectors, and a tag on a source selects the models downstream of it.

## 0.1.0 (2026-10-18, released as part of Orion)

The first release. Fathom reviews any diff, and when it knows your data platform it also says what the change reaches in production, which decisions it breaks, and what happened the last time.

### Review

- `fathom review` on any diff or git range, in any repository. Without an `fathom.yaml` it runs a general code review; with one it adds system context. `--git base` diffs from where the branch left the base.
- Four stages. Deterministic findings come first; the model's candidate findings follow; then code checks each candidate against the diff and the store; then the output is formatted. A finding with no valid evidence is marked uncertain, and one outside the diff is rejected.
- Warnings must name a concrete failing case; a warning without one becomes inform. A second reading re-checks each warn and block, optionally with a stronger model (`review.verify_model`).
- The model reads the change's stated intent (`--intent`, or the commit messages), so an announced behaviour change is not reported as a bug.
- Credential scan (AWS, GitHub, Slack, Google, Anthropic, OpenAI, Stripe, private keys, JWTs, database URLs). Secrets are redacted before the diff leaves the machine, and a model finding that repeats the block is dropped.
- Symbol references by parsing (tree-sitter, optional): imports resolved to the file they name; receivers typed by annotation, construction, a field or a declared return type, scoped to the enclosing class. A removed name or method still referenced elsewhere is a finding.
- Providers: `stub`, `anthropic`, and `openai` (any OpenAI-compatible endpoint, schemas sent in strict form). No vendor SDK.
- Inline pull-request comments with memory across pushes (`--post-github`, `.github/workflows/fathom-review.yml`). When the model is unavailable, earlier model findings stay open as not re-checked.

### System context

- `fathom sync`: dbt (manifest and catalog; column lineage by sqlglot from compiled SQL, parallel and cached), Airflow (Python DAGs and gusty YAML DAG folders; a DAG file that does not parse is skipped and noted), Git, postmortems as code, run history (Airflow task instances, dbt `run_results.json`, Iceberg snapshots), into one DuckDB file with provenance on every fact. A 625-model dbt project syncs in about two minutes, and about 13 seconds when cached.
- `fathom explain`, `fathom history`, `fathom ask` (graph-scoped retrieval, every sentence cited).
- `fathom preflight`: typed change ops from diffs, schema files, singular tests, task files, paths or intent; column-level blast radius; the closed predicate set; a transparent risk score; evidence ids on every claim. Unmapped paths are listed and warned on, never dropped.
- `fathom gate` for CI, `fathom serve` (MCP: preflight, explain, history, record_decision, propose_constraint, ask, review, search_record, read_record).
- `fathom postflight` watches landed changes over the next runs and records the outcome; `fathom propose` drafts constraints from ledger patterns as a pull request.
- `fathom record`: search and read the projects' written record.

### Evidence

- Review eval, 61 hand-labelled diffs: Sonnet recall 1.0 with 0 false alarms with the second reading; Haiku through the OpenAI-compatible endpoint has recall 1.0 and precision 0.955.
- Design partners: httpx, hono, cobra and cal-itp/data-infra (`pipelinebench/results/partner-*.md`). One cal-itp warning was confirmed by the project's next commit.
- Real dbt builds: a breaking rename is predicted block and observed failed, and a safe column add is predicted inform and observed clean (`pipelinebench/results/postflight-real-dbt.md`).
- PipelineBench, 50 scenarios × 4 context conditions, with Sonnet 5.5 and Haiku 5.5 as agents (`pipelinebench/results/llm-agents.md`). With Fathom's verdict, neither model made a forbidden decision, both got all 20 escalations right, and the scores were 0.82 and 0.83. With the whole repository the scores were 0.76 and 0.72, and the models escalated 10 to 12 of the 30 changes that should go ahead. With retrieved chunks the scores were 0.58 and 0.60.
