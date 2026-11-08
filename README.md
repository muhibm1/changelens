# ChangeLens, data change review

**The context layer for AI agents working on data platforms.** Before a coding agent or an engineer changes a dbt model or an Airflow DAG, ChangeLens tells it what depends on that change (column-level lineage), which rules apply (constraints written as code), who owns the affected assets, and what has broken there before. Then it checks the change against all of that and returns one cited verdict.

Code-context tools index what the repository says. ChangeLens indexes what production says: which tables feed which dashboards, which DAG missed its SLA, which schema change caused the last outage, which column a finance job filters on. Every claim in a verdict carries an evidence id, the checks run in code that a model cannot override, and anything ChangeLens does not know is reported as "not recorded" rather than guessed.

> Status: 0.2.0, the first release of ChangeLens as its own project. Tested on the bundled fixture platform and on [cal-itp/data-infra](https://github.com/cal-itp/data-infra), a public warehouse with 625 dbt models and 21 Airflow DAGs, and on [ClearLedger, finance data platform (currently erp-data-platform)](https://github.com/muhibm1/erp-data-platform), a finance warehouse on Postgres it was not built around (`pipelinebench/results/partner-erp-data-platform.md`). On PipelineBench, ChangeLens's own benchmark, language-model agents given ChangeLens's verdict made no forbidden call and got every escalation right (`pipelinebench/results/llm-agents.md`).
>
> ChangeLens was originally part of Orion, a personal context layer, and was split out on 2026-10-25; the history before that date is Orion's.

## What is in the box

| Piece | What it does |
| --- | --- |
| `changelens review` | Reviews any diff in four stages: gather (parse hunks, run the deterministic pre-flight), generate (one provider call returns candidate findings), validate (code rejects findings outside the diff, strips evidence that does not resolve, marks unsupported findings uncertain, and keeps a finding that cites a firing constraint at least as severe as that constraint), format. Deterministic findings always come first and never depend on a model. Providers: `stub` (offline), `anthropic`, `openai` (any OpenAI-compatible endpoint, including local models), `command` (any local command that reads the prompt on stdin and prints the answer) and `claude-code` (`claude -p` with no tools, on the plan the CLI is signed in to; no API key). For `openai`, set `review.base_url` and `review.api_key_env` (the name of the variable holding the key) in `changelens.yaml`; schemas are sent in strict form, which strict endpoints require (live check: `pipelinebench/results/openai-compatible.md`). |
| `changelens sync` | Pulls a typed asset graph and an append-only ledger from dbt, Airflow, Git and postmortems-as-code into one DuckDB file. Every node, edge and event carries provenance; the store refuses facts without it. |
| `changelens explain <asset>` | Owner, tier, upstream and downstream, tier-1 consumers within the full blast radius, which DAG task builds it, column-level lineage, constraints in scope, and recent incidents, all with ids. |
| `changelens history <asset>` | Ledger events for an asset, newest first. |
| `changelens preflight` | The verdict. Give it `--git <base>` (diff of the working tree), `--path`, `--intent "<sentence>"` or `--ops <json>`; get back typed change ops, the column-level blast radius, the constraints that fire, the incidents on the same path, a transparent risk score, recommended checks and a summary, every claim with evidence ids. `--format markdown` is what the PR comment uses. |
| `changelens gate` | CI entry point: pre-flight the diff and exit non-zero at `--fail-on warn|block`. The verdict is advisory until a team wires this in. |
| `changelens serve` | The MCP server. Seven tools: `preflight`, `explain`, `history`, `record_decision`, `propose_constraint`, `ask` and `review` (the same review as the CLI, on a base ref or a diff). No write path into the platform. |
| `changelens ask` | Graph-scoped retrieval over postmortems, descriptions and constraint reasons. Every sentence cites a chunk; unknown returns "not recorded". |
| `changelens postflight` | Closes the loop. For each verdict, finds the commit that landed it and watches the touched and affected assets for the next three runs; writes one `outcome_observed` event (clean, failed, sla_missed, incident) linked to the verdict. `--summary` prints predicted severity against observed outcome. |
| `changelens propose` | Drafts constraints from ledger patterns (repeated SLA misses, failures after changes, postmortems with no matching constraint, verdicts that under-called) into `constraints/proposed/`. A weekly workflow opens them as a pull request; merging is the approval. |
| `pipelinebench` | The benchmark: sixty scenarios with hand-written ground truth, four context conditions (none, repo, rag, changelens), scripted / Claude / command agents, mechanical graders. `pipelinebench check-ground-truth` keeps ChangeLens and the ground truth honest with each other. |
| `fixture/` | PipelineBench's fixture platform: a dbt project over DuckDB, three Airflow DAGs, CODEOWNERS, three postmortems, four constraints, and run history (an Airflow task-instance export, dbt `run_results.json` files, Iceberg table metadata with snapshots, a schema change and a partition-spec change). Realistic enough that the questions a verdict asks have non-trivial answers. |

## Quickstart

On a dbt project, start with [ChangeLens for dbt teams](docs/data-teams.md): setup, two weeks in shadow mode, and what it measured on a real warehouse's history.

Review a change in any git repository, with no setup beyond an API key:

```bash
pip install "git+https://github.com/muhibm1/changelens"
export CHANGELENS_ANTHROPIC_API_KEY=...        # or ANTHROPIC_API_KEY
export CHANGELENS_API_CAP=1                    # dollars this machine may spend on the API, in total
changelens review --git main --provider anthropic
```

The Anthropic provider spends API credit, so it refuses to start without `CHANGELENS_API_CAP`. Every call is added to `~/.changelens/api-ledger.jsonl` (`CHANGELENS_API_LEDGER`) with its tokens, cache reads and dollars, and once the total reaches the cap every later call is refused, in this run and the next. `flywheel spend ~/.changelens/api-ledger.jsonl` totals it. On a Claude subscription, `--provider claude-code` needs no key and no cap.

With no `changelens.yaml`, this is a general code review. The credential scan, symbol references and the model's findings are all included, each with evidence, but there is no system context. Add an `changelens.yaml` that points at your dbt project, DAGs and constraints to get blast radius, constraints and incident history in the same review.

The full tour, on the bundled fixture platform:

```bash
uv venv .venv && uv pip install -e ".[fixture,dev,syntax]"

# build the fixture warehouse so dbt writes manifest.json and catalog.json
(cd fixture/dbt_project && export PIPELINEBENCH_DB=warehouse.duckdb && ../../.venv/bin/dbt seed --profiles-dir . && ../../.venv/bin/dbt build --profiles-dir . && ../../.venv/bin/dbt docs generate --profiles-dir .)

.venv/bin/changelens sync
.venv/bin/changelens explain fct_orders
.venv/bin/changelens explain raw_events
.venv/bin/changelens explain fct_revenue --column gross_revenue --json
.venv/bin/changelens preflight --intent "drop the discount_pct column from fct_orders"
.venv/bin/changelens postflight --summary
.venv/bin/changelens propose
```

`changelens explain fct_orders` answers, from the fixture:

- it is tier 1, owned by `team/data-platform`, built by `orders_pipeline.dbt_marts`;
- four tier-1 consumers sit in its blast radius, including `finance_close_job` two hops away;
- `net_amount` derives from `int_order_payments.amount` and `discount_pct`, and feeds `fct_revenue.gross_revenue`;
- `CON-031` locks its columns because of `INC-230`, which is listed with its id.

## A review

```
$ changelens review --git HEAD
BLOCK  1 findings  provider stub  rv_bd3f176d8a96c958
  block  fixture/dbt_project/models/marts/fct_orders.sql:8  CON-031 columns_locked fires  deterministic
         drop_column on fct_orders.discount_pct reaches a locked asset directly. fct_revenue columns are a
         public contract read by finance close; fct_orders feeds them. The same kind of change caused INC-230.
         Sign-off needed from team/finance-data.
         constraint:CON-031, incident:INC-230
```

The model is told what the change is meant to do: `--intent` (the workflow passes the pull request's title and body), or by default with `--git` the commit messages since the base. An announced behavior change is then not reported as a bug, while a change the intent does not cover still is; the intent is quoted to the model as data, never read by a deterministic check, and the review JSON says where it came from (`intent_source`). With `--provider anthropic` (or `openai`, or `review.provider` in `changelens.yaml`), model findings join the list for any file in the diff, platform or not, each with a `path:line` and evidence: `hunk:<path>:<line>` for a line in the diff, `ref:<path>:<line>` for a reference the facts listed, or an id from the store. A cited id that does not exist is removed and noted; a finding with no valid evidence moves to `uncertain`; one pointing outside the diff moves to `rejected`. If the provider fails, the review still returns its deterministic findings and says the model review was unavailable. `--format json|markdown`, `--diff-file <path|->` for a diff from elsewhere. `--format agent` is for a build pipeline or a coding agent: `{head, verdict, findings, uncertain, unresolved}`, where `verdict` is `pass` or `fail`, each finding is `blocking` (a warn or block) or `minor` (an inform) with `where`, `detail`, `proof` (the failing case), `plain` (one sentence for someone who does not read code) and its evidence, and a review whose model call failed is a `fail`, never a pass.

Four more things come from code, not the model, on any diff:

- **The code around the change.** A changed file up to 400 lines goes to the model whole. A longer one goes as the definition each hunk sits in (the function or class, by indentation and definition lines, merged and capped), so the model reads the function around a three-line hunk rather than guessing at it. For every name the added lines call that no changed file defines, the definition is looked up in the repository (`git grep` on definition lines, cut at the end of the block) and shown under "Definitions this change calls": a free call when the repository defines the name once, or in a module the changed file imports or sits beside; a method call only when its definition is in such a module, since `.name(` says nothing about the class it lands on. A name defined in several files is ambiguous and not shown. Each definition's line is citable as `ref:<path>:<line>`, and the review JSON lists them under `callees`. On fifty real bugs at the commit that introduced them, Sonnet 5.5 catches 21 at warn or block with a judge that requires the finding to be about the bug (26 counting any warning near it) and warns in the right file on 29; the context did not move that number beyond one run's variance, but it is what the findings now cite (`pipelinebench/results/recall.md`).
- **Focused passes.** The model reads each change four times, once per kind of failure: unusual inputs, what the called functions and protocols really do, control flow and state, and contracts with callers and callees. The passes share a prompt-cached prefix, repeats are merged, and `--lens general` gives one unfocused pass. On Claude Haiku 5.5, recall on real bugs rises from 0.18 to 0.42 on the tuning set and reaches 0.60 on 45 held-out bugs from eight other repositories, with more false alarms than one pass (`pipelinebench/results/accuracy.md`).
- **Who else uses what you changed.** For every definition a diff removes or re-signs (Python, TypeScript and JavaScript, Go, Rust, Ruby), the system facts list each reference to it in the repository, by file and line, and say which of those lines the diff touches. Candidate files come from `git grep`; with the `syntax` extra (`pip install -e ".[syntax]"`, tree-sitter grammars) each one is parsed, so a comment, a string, a `.name` call on some other object and a file with its own same-named definition are told apart from a real use, and each reference says whether its file is the definition's own, imports the name from that module (relative imports are resolved against the importing file, absolute ones matched by trailing path; `from other.http import fetch` is a different `fetch`), imports the module (`http.fetch`, `svc.Fetch`, `http::fetch`), or merely mentions it. A removed method is tied to its class, and a `.name` call elsewhere is linked when that file types the receiver as the class (`c: Client`, `c = Client()`, `new Client()`, `c := &Client{}`, `let c = Client::new()`, `Client::name`) and imports it from the definition's module; a receiver typed as something else is reported as not this definition, and an untyped one as a call that may or may not be. A removed definition that a linked, untouched line still references is a `warn` finding; the other mentions go to the model as facts that say what they are. Without the extra the search is textual and the facts say so. For dbt models the same holds for columns: a model that still selects a dropped or renamed column is listed, and flagged if the diff leaves it alone. A model that starts reading another model, seed, snapshot or source by its warehouse name (`project.dataset.table`) where `ref()` or `source()` would name it is a `warn` on that line, because dbt's graph no longer records the dependency; changed SQL is read in the warehouse's own dialect, as recorded by the dbt adapter.
- **Credentials.** An added line that looks like an AWS, GitHub, Slack, Google, Anthropic, OpenAI or Stripe key, a private key, a JWT, a password in a database URL, or a long literal assigned to a name like `password`, is a `block` finding. The value is redacted before the diff leaves the machine; a line ending in `pragma: allowlist secret` is skipped.
- **Nothing dropped quietly.** A diff too large for one model call is reviewed in several, one group of files per call. A changed file too long for even its windows to fit is named as not shown. A file whose hunks alone exceed the hard cap is named as not reviewed, to the model and in the output.

On request, `.github/workflows/changelens-review.yml` runs `changelens review --post-github OWNER/REPO#N` on a pull request (add the `changelens-review` label, or run the workflow by hand with the number; it no longer runs on every push, to save Actions minutes and tokens): one inline comment per new finding on the diff line it points at, one summary comment updated in place, and a reply on a finding a push has made go away. The summary remembers the previous findings, so a finding reworded on the same file and category within three lines is the same finding and gets no second comment. ChangeLens only ever posts a `COMMENT` review. Add the `CHANGELENS_ANTHROPIC_API_KEY` repository secret for model findings; without it the comments carry the deterministic findings only. Each run may spend at most 25 cents (`CHANGELENS_API_CAP` in the workflow), and its log ends with what it spent. The workflow is advisory and never fails the pull request; `changelens-gate` is the binding check.

Provider settings live in `changelens.yaml` under `review:` (`provider`, `model`, `base_url`, `api_key_env`, the name of the environment variable that holds the key). No vendor SDK is a dependency; requests are plain HTTPS with retries on rate limits and server errors. Every model `warn` or `block` must name the concrete case that fails (`failure`), or it is lowered to `inform`. A second reading then re-checks each remaining warning against the whole change and lowers the ones it cannot confirm; turn it off with `review.second_reading: false` or `--no-second-reading`, and set `review.verify_model` to have a stronger model check a cheaper one ([results](pipelinebench/results/second-reading.md)).

`review.provider: command` runs `review.command`, an argument list (no shell). Because a branch under review can edit `changelens.yaml`, it runs only when `CHANGELENS_ALLOW_COMMAND=1` is set where that file is trusted. In the list, `{schema}`, `{system}`, `{model}` and `{effort}` are filled per call; an empty `{model}` or `{effort}` drops the flag before it. The command runs in the current folder and may print the JSON answer, text holding it, or a harness `--output-format json` result. `--provider claude-code` is that provider with `claude -p --tools "" --setting-sources "" --strict-mcp-config --no-session-persistence`, run in an empty folder, so a coding agent's own subscription answers the review and no repository's instructions, hooks or tools reach it. Each pass is one CLI call, so a review takes minutes rather than seconds.

### Context for code that is not a data platform

Without a dbt project or DAGs, a review still knows three things about the files it changes, all read and none guessed:

- **Owners** from `CODEOWNERS` (at the root, `.github/` or `docs/`; the last matching rule wins, as on GitHub). A file no rule covers is "not recorded".
- **Sensitive areas** from a file the team writes: `--areas <file>` or `review.areas`. YAML or JSON; a list of areas, `{areas: [...]}`, or a mapping from project name to a list, read as `file#project`. Each area has `pattern` (a regular expression over the path) or `paths` (globs from the repository root, where `*` stays inside one folder and `**` crosses folders), `why` or `reason`, and optionally `id`, `severity` (default `inform`) and `approvers`. A change in an area gets a deterministic finding at that severity, and the model is told why the area is sensitive.
- **History**: the fixes, reverts and incident commits that touched the changed files before the base (`--no-history` turns it off).

The model may cite them as `area:<id>`, `commit:<sha>` and `ref:CODEOWNERS:<line>`; anything else is removed as evidence that does not resolve. The review JSON carries them under `context`.

## A verdict

```
$ changelens preflight --intent "drop the discount_pct column from fct_orders"
BLOCK  risk 88/100  pf_bca3c7c9eaea4621  graph as of 2026-10-16 19:10:10+00:00
Drop column on fct_orders.discount_pct reaches 2 downstream assets, 1 of them tier 1
(dash_revenue_weekly). Fires CON-031 (block). The same kind of change caused INC-230 on
2026-08-18. Before merging: run tests for fct_orders+; get sign-off from team/finance-data;
tell team/finance-data; confirm the resolved assets. Severity block.
change
  drop_column        model:fct_orders.discount_pct   (from intent)
affected (2)
  dashboard:dash_revenue_weekly tier 1, team/finance-data, 1 hop via asset  [edge:e_858b7b879501]
  task:orders_pipeline.dbt_test tier unset, team/data-platform, 1 hop via asset  [edge:e_d1823d6b4d8e]
constraints
  CON-031 columns_locked (block): drop_column on fct_orders.discount_pct reaches a locked asset directly
history
  2026-08-18 postmortem_filed  INC-230  discount_pct dropped from fct_orders broke the weekly revenue dashboard  (same asset, same kind of change)  [ev_31e3fd9866bc]
checks
  run_tests              {'target': 'fct_orders+'}
  get_approval           {'from': ['team/finance-data'], 'for': 'CON-031'}
  notify_owner           {'owner': 'team/finance-data', 'assets': ['dashboard:dash_revenue_weekly']}
risk: tier1_consumers=1 (+30); block_constraint=1 (+25); incident_recency=59 (+13.4); destructive_op=1 (+15); blast_radius=2 (+2.4)
```

Adding a column to the same model is `INFORM`, risk 15, and reaches nothing: column-level lineage is what separates a check an agent reads from one it learns to ignore.

The same change as a diff (`changelens preflight --git main`) resolves through the model's SQL: old and new output columns are compared with sqlglot, so a dropped, added, renamed, retyped or redefined column is a typed op with the file as evidence. DAG diffs are parsed the same way (schedule, SLA, pool, `>>` dependencies, `--partition-by` and dbt selectors in commands). Intent is parsed by a keyword grammar in this slice; an LLM extractor can replace it behind the same function, and ops resolved from intent are marked `from_intent` so the agent confirms them.

### The closed predicate set

| Predicate | Fires when |
| --- | --- |
| `columns_locked` | A column is dropped, renamed, retyped or redefined and the change reaches a locked asset, directly or through column lineage |
| `partition_key` | `change_partition` moves away from the named key |
| `no_backfill_window` | A backfill lands inside the window |
| `schedule_locked` | A DAG's schedule changes |
| `requires_approval` | No `decision_recorded` from the named approvers in the last 30 days |
| `freshness_sla` | The change adds work upstream of an SLA-bound asset |
| `deprecated` | The change builds on a deprecated asset |

### Risk

A weighted sum, not a model. Weights and thresholds live in the YAML file that `risk:` in `changelens.yaml` names, conventionally `changelens/risk.yaml` (optional; the defaults below apply to anything it leaves out) and the verdict lists every factor's contribution.

| Factor | Weight |
| --- | --- |
| Tier-1 consumers in the blast radius | 30 |
| A `block` constraint fires | 25 |
| Incident on the same path, decaying over 180 days | 20 |
| Destructive op (`drop_column`, `change_partition`, `change_type`, `change_expression`, `rename_column`) | 15 |
| Blast radius size, log scale | 10 |

Severity: `inform` under 30, `warn` from 30, `block` from 70, or whatever the strictest firing constraint says. ChangeLens never blocks on its own; `changelens gate` is how CI makes it binding.

## What production did

`changelens sync` also reads run history, so a verdict's `history` section carries operational facts next to incidents: the three 71-minute `dbt_marts` runs on the afternoon of INC-241, the two October mornings `finance_close.dbt_finance` blew its 30-minute SLA, the failed `not_null` test on `fct_refunds`, the day `raw_events` was repartitioned by `ingest_ts` and reverted six hours later. Sources are files in the shape their systems export them (`fixture/runs/`): an Airflow task-instance export (JSONL, as the REST API returns `taskInstances`), dbt `run_results.json`, and Iceberg table metadata. A task that has a declared `sla` and ran longer than it gets an `sla_missed` event without anyone configuring anything.

### Post-flight

```
$ changelens postflight
 verdict               predicted  risk  status      landed            outcome  runs
 pf_2c46bd2be8becd…    inform     15    observed    2026-10-16 20:02  clean    3
 pf_bca3c7c9eaea46…    block      88    not landed
calibration: 1 observed; 0 under-called (inform/warn then failed); 0 blocked then clean
```

A verdict that said `inform` and was followed by a failed run is an under-call. Those rows are the evidence for tuning `changelens/risk.yaml`, and the benchmark's ground truth.

### Proposals

```
$ changelens propose
CON-P31abc2 freshness_sla (warn) on model:fct_refunds, model:fct_revenue
  task:finance_close.dbt_finance missed its SLA 2 times since 2026-10-10; it builds model:fct_refunds, model:fct_revenue
  evidence: event:ev_78dbc605c406, event:ev_f292cc60c6c1
```

`changelens propose --write` puts each as `constraints/proposed/<id>.yaml` with its evidence, and `.github/workflows/changelens-propose.yml` opens the pull request weekly. The three fixture postmortems propose nothing because CON-014, CON-022 and CON-031 already cover them; remove one and the matching proposal appears.

### Retrieval

`changelens ask` ranks with BM25 by default. `retrieval.ranker: hybrid` in `changelens.yaml` fuses it with cosine over dense vectors (reciprocal rank fusion); `embedding` uses the vectors alone. Vectors come from a dependency-free hashed embedder unless `retrieval.model` names a sentence-transformers model (`pip install -e ".[embed]"`), and are cached per model in the `chunk_vectors` table. The sentence-transformers path is wired but not exercised by the test suite.

`changelens rag` is the retrieval core for code and the platform's prose (`src/changelens/rag/`); the review agent's code search runs on it. `changelens rag index` cuts each tracked file of a checkout by syntax (tree-sitter: one chunk per definition, long classes split into methods; markdown at its headings; commit patches per file), stores chunks, BM25 postings, local vectors and file-level import edges in one DuckDB file (`~/.cache/changelens/rag.duckdb`, `CHANGELENS_RAG`), and syncs the platform's graph prose (`--graph`). `changelens rag search` fuses BM25 and dense rankings by reciprocal rank and adds the graph as a third ranking: with `--changed`, the changed files, their importers and imports, the data assets in their blast radius rank first, nearest first. Every hit cites a chunk id and a line span; `changelens rag read <id>` returns the chunk or "not recorded", and `changelens.rag.cite.check` scores an answer's citations against the index. Embeddings are local: hashed vectors, `st:<model>` (the `embed` extra; the default when installed is `BAAI/bge-small-en-v1.5`) or `ollama:<model>`. A refresh of an unchanged checkout costs two quick git calls, vectors are keyed by chunk text so nothing is embedded twice, and repeated searches are served from an in-process cache until the index changes. `pipelinebench retrieval-eval` measures recall@k, MRR, citation faithfulness and latency on 42 hand-labeled queries (`pipelinebench/results/retrieval.md`).

## Using it from an agent

`changelens serve` runs the MCP server in a checkout with a `changelens.yaml`. Seven tools: `preflight`, `explain`, `history`, `record_decision`, `propose_constraint`, `ask` and `review`. None of them writes to the platform; the server writes only the ledger and draft constraint files.

To add it to a coding agent or any other MCP client:

```json
{ "mcpServers": { "changelens": { "command": "changelens", "args": ["serve", "--project-dir", "/path/to/repo"] } } }
```

The server's instructions tell the agent to call `preflight` before writing a platform change and again before opening a pull request, to cite ids rather than paraphrase, and that `block` means a human decides.

## How it is put together

```
changelens.yaml            where the sources live, relative to the repo root
src/changelens/
  model.py            Node, Edge, Event, Provenance; node ids are readable (model:fct_orders)
  store.py            DuckDB tables: nodes, edges, provenance, events; walk() with tier-1 carry-through
  adapters/
    dbt.py            manifest + catalog -> assets, exposures, owners, columns, column lineage (sqlglot)
    airflow.py        static AST parse of DAG files -> dags, tasks, >> chains, sensors, dbt selectors
    git.py            commits touching mapped paths -> change_landed events
    incidents.py      changelens/incidents/*.md front matter -> incident_opened, postmortem_filed events
    runs.py           Airflow task-instance export, dbt run_results.json, Iceberg metadata -> run, SLA, snapshot and schema events
  lineage.py          Jinja to SQL, output columns, leaf-column lineage
  resolver.py         refs (source:, exposure:, short names) and repo paths -> node ids
  constraints.py      changelens/constraints/*.yaml, a closed predicate set, validated with pydantic
  tiers.py            declared tiers win; gaps upstream of tier 1 are filled and marked inferred
  explain.py          the explain answer, every section carrying ids
  changes.py          diff / paths / intent / ops -> typed ChangeOps, with unresolved kept visible
  predicates.py       the closed predicate set evaluated against ops, graph and ledger
  risk.py             weighted-sum risk and severity thresholds
  verdict.py          blast radius, history, checks, summary; idempotent verdict ids; markdown render
  retrieval.py        graph-scoped retrieval (BM25, hybrid, embedding) over postmortems, descriptions and constraint reasons
  postflight.py       verdict -> landing commit -> next N runs -> outcome_observed; calibration table
  proposals.py        ledger patterns -> draft constraints with evidence, written under proposed/
  server.py           the MCP server (seven tools)
  diffparse.py        unified diffs to files, hunks and new-side line numbers; both sides of a file from a diff
  providers.py        Provider interface; stub, Anthropic, OpenAI-compatible, local command (claude-code preset); retries and timeouts
  codectx.py          context for any codebase: CODEOWNERS owners, sensitive areas, fix and revert history from git
  review.py           gather, generate, validate, format; deterministic findings first
  symbols.py, syntax.py  definitions a diff removes or re-signs and their references, classified by parsing (tree-sitter, optional)
  sync.py, cli.py     orchestration and the command
src/pipelinebench/    the benchmark (see pipelinebench/README.md)
```

Three rules hold everywhere:

1. **No fact without provenance.** The store raises on a node or edge with none.
2. **Adapters never reason.** They read one source and emit typed facts. Judgement lives in the walk, the predicates and (later) the verdict.
3. **Git is the store for decisions.** Constraints and postmortems are files; merging is approval; history is the audit trail.

## Decisions and incidents as code

A constraint:

```yaml
id: CON-031
scope: { assets: [model:fct_revenue, model:fct_orders], columns: "*" }
rule: { kind: columns_locked, allow: [add] }
severity: block
reason: fct_revenue columns are a public contract read by finance close.
source: INC-230
approvers: [team/finance-data]
```

A postmortem is Markdown with front matter naming `assets`, `started_at`, `resolved_at`, `cause`, `trigger_change` and `constraints_proposed`; the body is the narrative. See `fixture/changelens/`.

## Running the tests

```bash
.venv/bin/pytest -q
```

The end-to-end tests parse the fixture dbt project on the fly, so they need no warehouse; the catalog only adds column types.

## Roadmap

1. **Fixture and graph** (done): graph, adapters, lineage, `explain`.
2. **Pre-flight** (done): typed change ops, blast-radius walk, predicates, risk, the verdict, `gate`, the MCP server, graph-scoped `ask`.
3. **Ledger and post-flight** (done): run history and Iceberg snapshot ingestion, the post-flight watcher that writes `outcome_observed`, constraint proposals from ledger patterns and the nightly proposal PR, hybrid and embedding rankers behind `ask`.
4. **PipelineBench** (done): sixty scenarios (fifty in the first agent runs), four conditions, scripted / Claude / command agents, mechanical graders, and runs with language-model agents (`pipelinebench/results/`).
5. **Review loop** (done): `changelens review` on any diff, with or without platform config. Behind it: a provider interface (Anthropic, any OpenAI-compatible endpoint), the `review` MCP tool, and inline pull-request comments with memory across pushes. It parses symbol references, typing receivers by annotation, construction, field or declared return type. It also scans for credentials, reads the stated intent, and requires a concrete failing case for every warning, with a second reading. Evidence: the review eval, and design-partner runs on httpx, hono, cobra and cal-itp.
6. **Real platforms** (done for dbt and Airflow): dbt at the scale of 625 models with column lineage, gusty YAML DAGs, and post-flight on real `dbt build` results. A second real warehouse, erp-data-platform on Postgres, runs unchanged (`pipelinebench/results/partner-erp-data-platform.md`).

Next: more platform adapters (Dagster, Snowflake query history), Postgres as a shared team store, an LLM intent extractor behind `ops_from_intent`, and more seeds and a larger fixture for PipelineBench.

## License

Apache-2.0
