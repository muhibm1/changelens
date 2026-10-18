# Changelog

## Unreleased

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
- An unmapped `dbt_project.yml` says why in words. Before, the reason was an empty list.

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
