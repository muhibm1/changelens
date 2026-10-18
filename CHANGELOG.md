# Changelog

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
