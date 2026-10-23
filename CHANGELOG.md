# Changelog

## Unreleased

### Retrieval core

- `orion rag index | search | read`: hybrid (BM25 + local embeddings, reciprocal rank fusion), graph-scoped retrieval over code and the engineering record, in one DuckDB file. Code is chunked by syntax with line spans; import edges, record refs and the data graph's blast radius make a change's neighbourhood rank first. Optional local cross-encoder reranking (`ORION_RAG_RERANKER=ce:<model>`). Incremental by git blob and chunk text hash; unchanged checkouts and repeated searches are cached.
- `orion.rag.cite.check`: citation validity and support for any answer that cites chunk ids.
- `pipelinebench retrieval-eval`: 42 hand-labelled queries (dev and held-out), recall@k, MRR, citation faithfulness with a tamper control, latency, three runs per configuration.

### Reviews for app projects, on a subscription

- Context for code that is not a data platform: owners from `CODEOWNERS`, sensitive areas from a YAML or JSON file (`--areas file#project`, `review.areas`), and earlier fixes and reverts of the changed files from git. The model sees them as facts it can cite (`area:`, `commit:`, `ref:CODEOWNERS:N`); a change in an area gets a deterministic finding at the area's severity. The review JSON carries them under `context`.
- A `ref:` to a line of a changed file that the model was shown in full (or in a window around a hunk) is now valid evidence; it used to be removed as not resolving.
- `orion review --format agent`: a pass or fail with `blocking` and `minor` findings, each with the failing case as `proof` and a `plain` sentence the model writes for a reader who does not read code. A review whose model call failed fails. Uncertain findings and unresolved paths stay apart from the findings.
- `command` provider: any local command that reads the prompt on stdin and prints the JSON answer. The `claude-code` preset runs `claude -p` with no tools, settings or MCP servers, so a review runs on the plan the CLI is signed in to, with no API key. The evals take `--provider claude-code` too.
- `recall-eval` scores a case whose review call failed (a usage limit, say) as `error`, not `missed`, and a failed `claude -p` reports its own reason instead of its usage block.

### Focused review passes

- `orion review` reads each change in four focused passes: unusual inputs, the real semantics of the calls the code makes, control flow and state, and contracts with callers and callees. The system prompt and the change form a shared, prompt-cached prefix. Findings from the passes are merged, and the second reading rejects a finding that repeats an earlier one (kept under `rejected` with a note). `--lens` and `review.lenses` choose the passes; `--lens general` is the old single pass.
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
- `pipelinebench recall-eval` reviews each introducing commit against its parent with its commit message as intent, as a team would have seen it, and counts the bugs caught at warn or block within twelve lines (`caught`), warned about elsewhere in the file (`in_file`), raised only as inform, or missed. `--orion` points it at another build so two builds compare. Sonnet 5.5 catches 21 of the 50 at warn or block and warns in the right file on 29 (`pipelinebench/results/recall.md`); the context changes did not move that number beyond one run's variance, and the second reading still lowers about six right warnings per run.
- Replay and recall clones keep `orion.yaml` and the graph out of the diff under review (`.git/info/exclude`); before, both appeared as added files in every replayed change.

### Verdict quality

- A verdict's `history` lists incidents and events from the same kind of change. Failed runs, missed SLAs and schema changes from other kinds of change move to a new `background_events` field, and the markdown counts them in one line. They read as reasons for caution when they were not.
- Incident recency adds risk only for an incident from the same kind of change. Other incidents on the path are still listed and named in the summary as not from this kind of change.
- An incident's opening and its postmortem are one line in the markdown.
- Repartitioning to the key an asset already has reaches nothing and is not matched to repartition incidents.

### Real-repository noise

- `pipelinebench replay-eval` replays merged commits from public repositories (httpx, hono, cobra, cal-itp) through `orion review` and counts warnings per change against hand labels: confirmed upstream, plausible, or noise.
- The second reading of a model warning says what a confirmed case is: a defect, intended by the change, a trade-off it chose, an unusual caller, or style. Only a defect keeps `warn`. On the replay, warnings fell from 17 to 12 and noise from 8 to 3, with both confirmed bugs caught (`pipelinebench/results/replay.md`).
- A model file the change adds is new, not unmapped: nothing in a graph synced from the base branch reads it yet. Its schema entries are accounted for too.
- A review raised to `warn` only because no changed path maps to an asset now carries a finding that says so and lists each path's reason. Before, the review was `warn` with nothing to explain it.
- An `orion.yaml` with no dbt or Airflow adapter (an application repository) no longer reports every changed file as unmapped. Before, every review in such a repository was `warn` with no finding to explain it.
- A diff given as a file is filtered by the platform roots, as a git diff already was.
- A renamed file is diffed against its old path; before, a renamed model read as a new file.
- A schema entry removed together with its model's SQL file is accounted for, not unmapped.
- An unmapped `dbt_project.yml` says why in words. Before, the reason was an empty list. A changed `packages.yml` or `package-lock.yml` says a package upgrade can change any macro the models call.
- With the default model, the replay of 26 changes from httpx, hono and cobra has 5 warnings, none of them noise, and catches the confirmed bug (`pipelinebench/results/replay.md`).

### PipelineBench

- Ten more scenarios (PB-051 to PB-060), with ground truth written by hand: lineage through renamed, redefined and retyped columns, a constraint scoped to a downstream model (CON-014 on `stg_events`), the start of the CON-022 window, a backfill reaching tier-1 exposures, and a tier-1 export that reads a whole model. PB-001 to PB-050 keep their ids, so earlier agent runs still compare. Orion agrees with all sixty.

### Real dbt and Airflow projects

- Model SQL read without a compiled manifest (`dbt parse` only) handles versioned refs (`ref('m', v=2)`), package refs (`ref('pkg', 'm')`), `{{ this }}` in incremental filters, and macro calls in expressions and as a whole CTE body. On datacoves/balboa, models whose columns could not be read fell from 7 to 1 (a Snowflake semantic view, which is not SQL).
- TaskFlow DAGs are read: a `@dag` function's `@task` functions and operators become tasks, `@task_group` prefixes their ids, and dependencies come from `>>` chains and from passing one task's result to another. A bash or dbt task's command is the string it returns. On balboa, Airflow went from 0 DAGs to 32 DAGs and 53 tasks.
- A dbt selector in quotes (`-s 'tag:a+ tag:b+'`) is read as two selectors, and a tag on a source selects the models downstream of it.

## 0.1.0 (2026-10-18)

The first release. Orion reviews any diff, and when it knows your data platform it also says what the change reaches in production, which decisions it breaks, and what happened the last time.

### Review

- `orion review` on any diff or git range, in any repository. Without an `orion.yaml` it runs a general code review; with one it adds system context. `--git base` diffs from where the branch left the base.
- Four stages. Deterministic findings come first; the model's candidate findings follow; then code checks each candidate against the diff and the store; then the output is formatted. A finding with no valid evidence is marked uncertain, and one outside the diff is rejected.
- Warnings must name a concrete failing case; a warning without one becomes inform. A second reading re-checks each warn and block, optionally with a stronger model (`review.verify_model`).
- The model reads the change's stated intent (`--intent`, or the commit messages), so an announced behaviour change is not reported as a bug.
- Credential scan (AWS, GitHub, Slack, Google, Anthropic, OpenAI, Stripe, private keys, JWTs, database URLs). Secrets are redacted before the diff leaves the machine, and a model finding that repeats the block is dropped.
- Symbol references by parsing (tree-sitter, optional): imports resolved to the file they name; receivers typed by annotation, construction, a field or a declared return type, scoped to the enclosing class. A removed name or method still referenced elsewhere is a finding.
- Providers: `stub`, `anthropic`, and `openai` (any OpenAI-compatible endpoint, schemas sent in strict form). No vendor SDK.
- Inline pull-request comments with memory across pushes (`--post-github`, `.github/workflows/orion-review.yml`). When the model is unavailable, earlier model findings stay open as not re-checked.

### System context

- `orion sync`: dbt (manifest and catalog; column lineage by sqlglot from compiled SQL, parallel and cached), Airflow (Python DAGs and gusty YAML DAG folders; a DAG file that does not parse is skipped and noted), Git, postmortems as code, run history (Airflow task instances, dbt `run_results.json`, Iceberg snapshots), into one DuckDB file with provenance on every fact. A 625-model dbt project syncs in about two minutes, and about 13 seconds when cached.
- `orion explain`, `orion history`, `orion ask` (graph-scoped retrieval, every sentence cited).
- `orion preflight`: typed change ops from diffs, schema files, singular tests, task files, paths or intent; column-level blast radius; the closed predicate set; a transparent risk score; evidence ids on every claim. Unmapped paths are listed and warned on, never dropped.
- `orion gate` for CI, `orion serve` (MCP: preflight, explain, history, record_decision, propose_constraint, ask, review, search_record, read_record).
- `orion postflight` watches landed changes over the next runs and records the outcome; `orion propose` drafts constraints from ledger patterns as a pull request.
- `orion record`: search and read the projects' written record.

### Evidence

- Review eval, 61 hand-labelled diffs: Sonnet recall 1.0 with 0 false alarms with the second reading; Haiku through the OpenAI-compatible endpoint has recall 1.0 and precision 0.955.
- Design partners: httpx, hono, cobra and cal-itp/data-infra (`pipelinebench/results/partner-*.md`). One cal-itp warning was confirmed by the project's next commit.
- Real dbt builds: a breaking rename is predicted block and observed failed, and a safe column add is predicted inform and observed clean (`pipelinebench/results/postflight-real-dbt.md`).
- PipelineBench, 50 scenarios × 4 context conditions, with Sonnet 5.5 and Haiku 5.5 as agents (`pipelinebench/results/llm-agents.md`). With Orion's verdict, neither model made a forbidden decision, both got all 20 escalations right, and the scores were 0.82 and 0.83. With the whole repository the scores were 0.76 and 0.72, and the models escalated 10 to 12 of the 30 changes that should go ahead. With retrieved chunks the scores were 0.58 and 0.60.
