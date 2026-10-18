# Orion

**Provider-agnostic code review and change intelligence for developers and coding agents.** General review of any diff is the baseline; what the change could affect in production, and why, is the difference.

Orion started as **the pre-flight check for agents touching data pipelines**, and that remains its deepest system context: Lineage, SLAs, incident history and locked decisions, surfaced at the moment of change, over MCP, for any agent.

Code-context tools index what the repository says. Orion indexes what production says: which tables feed which dashboards, which DAG missed its SLA, which schema change caused the last outage, which column a finance job filters on. It fuses that into one cited verdict an agent reads *before* it writes.

> Status: all four slices are built against the bundled fixture platform: the asset graph, adapters, column lineage, ledger, `explain`, the pre-flight verdict, the CI gate, the MCP server, run-history ingestion, the post-flight watcher, constraint proposals, and PipelineBench (fifty scenarios, four conditions, mechanical graders). Benchmark numbers so far come from a scripted baseline, not language-model agents; see `pipelinebench/README.md`.

## What is in the box

| Piece | What it does |
| --- | --- |
| `orion review` | Reviews any diff in four stages: gather (parse hunks, run the deterministic pre-flight), generate (one provider call returns candidate findings), validate (code rejects findings outside the diff, strips evidence that does not resolve, marks unsupported findings uncertain, and keeps a finding that cites a firing constraint at least as severe as that constraint), format. Deterministic findings always come first and never depend on a model. Providers: `stub` (offline), `anthropic`, `openai` (any OpenAI-compatible endpoint, including local models). |
| `orion sync` | Pulls a typed asset graph and an append-only ledger from dbt, Airflow, Git and postmortems-as-code into one DuckDB file. Every node, edge and event carries provenance; the store refuses facts without it. |
| `orion explain <asset>` | Owner, tier, upstream and downstream, tier-1 consumers within the full blast radius, which DAG task builds it, column-level lineage, constraints in scope, and recent incidents, all with ids. |
| `orion history <asset>` | Ledger events for an asset, newest first. |
| `orion preflight` | The verdict. Give it `--git <base>` (diff of the working tree), `--path`, `--intent "<sentence>"` or `--ops <json>`; get back typed change ops, the column-level blast radius, the constraints that fire, the incidents on the same path, a transparent risk score, recommended checks and a summary, every claim with evidence ids. `--format markdown` is what the PR comment uses. |
| `orion gate` | CI entry point: pre-flight the diff and exit non-zero at `--fail-on warn|block`. The verdict is advisory until a team wires this in. |
| `orion serve` | The MCP server. Nine tools: `preflight`, `explain`, `history`, `record_decision`, `propose_constraint`, `ask`, `review` (the same review as the CLI, on a base ref or a diff), and the record's `search_record` and `read_record`. No write path into the platform. Without an `orion.yaml` it serves the two record tools only. |
| `orion record` | Search and read the projects' written record: design notes, decision records, plans, retros, and my build pipeline's lessons, ledger and run folders, across every repository `record.toml` names. Standard library only, no model call; see [The record](#the-record). |
| `orion ask` | Graph-scoped retrieval over postmortems, descriptions and constraint reasons. Every sentence cites a chunk; unknown returns "not recorded". |
| `orion record-decision` | Writes a human's approve / waive / reject on a verdict to the ledger. |
| `orion postflight` | Closes the loop. For each verdict, finds the commit that landed it and watches the touched and affected assets for the next three runs; writes one `outcome_observed` event (clean, failed, sla_missed, incident) linked to the verdict. `--summary` prints predicted severity against observed outcome. |
| `orion propose` | Drafts constraints from ledger patterns (repeated SLA misses, failures after changes, postmortems with no matching constraint, verdicts that under-called) into `constraints/proposed/`. A nightly workflow opens them as a pull request; merging is the approval. |
| `pipelinebench` | The benchmark: fifty scenarios with hand-written ground truth, four context conditions (none, repo, rag, orion), scripted / Claude / command agents, mechanical graders. `pipelinebench check-ground-truth` keeps Orion and the ground truth honest with each other. |
| `fixture/` | PipelineBench's fixture platform: a dbt project over DuckDB, three Airflow DAGs, CODEOWNERS, three postmortems, four constraints, and run history (an Airflow task-instance export, dbt `run_results.json` files, Iceberg table metadata with snapshots, a schema change and a partition-spec change). Realistic enough that the questions a verdict asks have non-trivial answers. |

## The record

Orion also holds what the projects wrote down about themselves, so one place answers both "what does this change touch?" and "why was it built this way?". `record.toml` lists the repositories (siblings of this checkout; all but my build pipeline's and Orion's are optional) and which of their files count. Markdown is cut at its `##` and `###` headings; my build pipeline's notes become one section per labelled lesson, its ledger one per run, and its run folders one per page part, decision and review. Ranking is BM25 with headings weighted three times.

```bash
PYTHONPATH=src python3 -m orion.record search "web app ship route cloud merge" --json   # nothing to install
PYTHONPATH=src python3 -m orion.record read "pipeline:notes/playbooks/webapp.md#W2"
orion record index                                                                    # once installed
```

Every section has a stable id: `repo:path#N` for Markdown, `#LABEL` for a lesson (`pipeline:notes/rulebook.md#R5`), `#TG-NN` for a ledger row, `#parts.N`, `#detail.N` and `decisions.json#N` for a run. `read` of an id the record does not hold prints an error and exits 1, so a citation that does not read back is not recorded. Every search checks whether a covered file was added, removed or changed and rebuilds the index if so, so a running server sees a new lesson on its next search. The index is cached in the user cache folder (`~/.cache/orion/record/`, or `ORION_RECORD_CACHE`), never inside a repository, and a cache that cannot be written is skipped. `ORION_RECORD` points at a different corpus file (needed when Orion is installed outside a checkout). A required repository that is missing makes search and read return `record unavailable` rather than a partial answer.

## Quickstart

```bash
uv venv .venv && uv pip install -e ".[fixture,dev,syntax]"

# build the fixture warehouse so dbt writes manifest.json and catalog.json
(cd fixture/dbt_project && export PIPELINEBENCH_DB=warehouse.duckdb && ../../.venv/bin/dbt seed --profiles-dir . && ../../.venv/bin/dbt build --profiles-dir . && ../../.venv/bin/dbt docs generate --profiles-dir .)

.venv/bin/orion sync
.venv/bin/orion explain fct_orders
.venv/bin/orion explain raw_events
.venv/bin/orion explain fct_revenue --column gross_revenue --json
.venv/bin/orion preflight --intent "drop the discount_pct column from fct_orders"
.venv/bin/orion postflight --summary
.venv/bin/orion propose
```

`orion explain fct_orders` answers, from the fixture:

- it is tier 1, owned by `team/data-platform`, built by `orders_pipeline.dbt_marts`;
- four tier-1 consumers sit in its blast radius, including `finance_close_job` two hops away;
- `net_amount` derives from `int_order_payments.amount` and `discount_pct`, and feeds `fct_revenue.gross_revenue`;
- `CON-031` locks its columns because of `INC-230`, which is listed with its id.

## A review

```
$ orion review --git HEAD
BLOCK  1 findings  provider stub  rv_bd3f176d8a96c958
  block  fixture/dbt_project/models/marts/fct_orders.sql:8  CON-031 columns_locked fires  deterministic
         drop_column on fct_orders.discount_pct reaches a locked asset directly. fct_revenue columns are a
         public contract read by finance close; fct_orders feeds them. The same kind of change caused INC-230.
         Sign-off needed from team/finance-data.
         constraint:CON-031, incident:INC-230
```

The model is told what the change is meant to do: `--intent` (the workflow passes the pull request's title and body), or by default with `--git` the commit messages since the base. An announced behaviour change is then not reported as a bug, while a change the intent does not cover still is; the intent is quoted to the model as data, never read by a deterministic check, and the review JSON says where it came from (`intent_source`). With `--provider anthropic` (or `openai`, or `review.provider` in `orion.yaml`), model findings join the list for any file in the diff, platform or not, each with a `path:line` and evidence: `hunk:<path>:<line>` for a line in the diff, `ref:<path>:<line>` for a reference the facts listed, or an id from the store. A cited id that does not exist is removed and noted; a finding with no valid evidence moves to `uncertain`; one pointing outside the diff moves to `rejected`. If the provider fails, the review still returns its deterministic findings and says the model review was unavailable. `--format json|markdown`, `--diff-file <path|->` for a diff from elsewhere.

Three more things come from code, not the model, on any diff:

- **Who else uses what you changed.** For every definition a diff removes or re-signs (Python, TypeScript and JavaScript, Go, Rust, Ruby), the system facts list each reference to it in the repository, by file and line, and say which of those lines the diff touches. Candidate files come from `git grep`; with the `syntax` extra (`pip install -e ".[syntax]"`, tree-sitter grammars) each one is parsed, so a comment, a string, a `.name` call on some other object and a file with its own same-named definition are told apart from a real use, and each reference says whether its file is the definition's own, imports the name from that module (relative imports are resolved against the importing file, absolute ones matched by trailing path; `from other.http import fetch` is a different `fetch`), imports the module (`http.fetch`, `svc.Fetch`, `http::fetch`), or merely mentions it. A removed method is tied to its class, and a `.name` call elsewhere is linked when that file types the receiver as the class (`c: Client`, `c = Client()`, `new Client()`, `c := &Client{}`, `let c = Client::new()`, `Client::name`) and imports it from the definition's module; a receiver typed as something else is reported as not this definition, and an untyped one as a call that may or may not be. A removed definition that a linked, untouched line still references is a `warn` finding; the other mentions go to the model as facts that say what they are. Without the extra the search is textual and the facts say so. For dbt models the same holds for columns: a model that still selects a dropped or renamed column is listed, and flagged if the diff leaves it alone.
- **Credentials.** An added line that looks like an AWS, GitHub, Slack, Google, Anthropic, OpenAI or Stripe key, a private key, a JWT, a password in a database URL, or a long literal assigned to a name like `password`, is a `block` finding. The value is redacted before the diff leaves the machine; a line ending in `pragma: allowlist secret` is skipped.
- **Nothing dropped quietly.** A diff too large for one model call is reviewed in several, one group of files per call. A changed file too long to send whole is named as not shown. A file whose hunks alone exceed the hard cap is named as not reviewed, to the model and in the output.

On pull requests, `.github/workflows/orion-review.yml` runs `orion review --post-github OWNER/REPO#N`: one inline comment per new finding on the diff line it points at, one summary comment updated in place, and a reply on a finding a push has made go away. The summary remembers the previous findings, so a finding reworded on the same file and category within three lines is the same finding and gets no second comment. Orion only ever posts a `COMMENT` review. Add the `ORION_ANTHROPIC_API_KEY` repository secret for model findings; without it the comments carry the deterministic findings only. The workflow is advisory and never fails the pull request; `orion-gate` is the binding check.

Provider settings live in `orion.yaml` under `review:` (`provider`, `model`, `base_url`, `api_key_env`, the name of the environment variable that holds the key). No vendor SDK is a dependency; requests are plain HTTPS with retries on rate limits and server errors.

## A verdict

```
$ orion preflight --intent "drop the discount_pct column from fct_orders"
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

The same change as a diff (`orion preflight --git main`) resolves through the model's SQL: old and new output columns are compared with sqlglot, so a dropped, added, renamed, retyped or redefined column is a typed op with the file as evidence. DAG diffs are parsed the same way (schedule, SLA, pool, `>>` dependencies, `--partition-by` and dbt selectors in commands). Intent is parsed by a keyword grammar in this slice; an LLM extractor can replace it behind the same function, and ops resolved from intent are marked `from_intent` so the agent confirms them.

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

A weighted sum, not a model. Weights live in `orion/risk.yaml` (optional; defaults below) and the verdict lists every factor's contribution.

| Factor | Weight |
| --- | --- |
| Tier-1 consumers in the blast radius | 30 |
| A `block` constraint fires | 25 |
| Incident on the same path, decaying over 180 days | 20 |
| Destructive op (`drop_column`, `change_partition`, `change_type`, `change_expression`, `rename_column`) | 15 |
| Blast radius size, log scale | 10 |

Severity: `inform` under 30, `warn` from 30, `block` from 70, or whatever the strictest firing constraint says. Orion never blocks on its own; `orion gate` is how CI makes it binding.

## What production did

`orion sync` also reads run history, so a verdict's `history` section carries operational facts next to incidents: the three 71-minute `dbt_marts` runs on the afternoon of INC-241, the two October mornings `finance_close.dbt_finance` blew its 30-minute SLA, the failed `not_null` test on `fct_refunds`, the day `raw_events` was repartitioned by `ingest_ts` and reverted six hours later. Sources are files in the shape their systems export them (`fixture/runs/`): an Airflow task-instance export (JSONL, as the REST API returns `taskInstances`), dbt `run_results.json`, and Iceberg table metadata. A task that has a declared `sla` and ran longer than it gets an `sla_missed` event without anyone configuring anything.

### Post-flight

```
$ orion postflight
 verdict               predicted  risk  status      landed            outcome  runs
 pf_2c46bd2be8becd…    inform     15    observed    2026-10-16 20:02  clean    3
 pf_bca3c7c9eaea46…    block      88    not landed
calibration: 1 observed; 0 under-called (inform/warn then failed); 0 blocked then clean
```

A verdict that said `inform` and was followed by a failed run is an under-call. Those rows are the evidence for tuning `orion/risk.yaml`, and the benchmark's ground truth.

### Proposals

```
$ orion propose
CON-P31abc2 freshness_sla (warn) on model:fct_refunds, model:fct_revenue
  task:finance_close.dbt_finance missed its SLA 2 times since 2026-10-10; it builds model:fct_refunds, model:fct_revenue
  evidence: event:ev_78dbc605c406, event:ev_f292cc60c6c1
```

`orion propose --write` puts each as `constraints/proposed/<id>.yaml` with its evidence, and `.github/workflows/orion-propose.yml` opens the pull request nightly. The three fixture postmortems propose nothing because CON-014, CON-022 and CON-031 already cover them; remove one and the matching proposal appears.

### Retrieval

`orion ask` ranks with BM25 by default. `retrieval.ranker: hybrid` in `orion.yaml` fuses it with cosine over dense vectors (reciprocal rank fusion); `embedding` uses the vectors alone. Vectors come from a dependency-free hashed embedder unless `retrieval.model` names a sentence-transformers model (`pip install -e ".[embed]"`), and are cached per model in the `chunk_vectors` table. The sentence-transformers path is wired but not exercised by the test suite.

## Using it from an agent

```json
{ "mcpServers": { "orion": { "command": "orion", "args": ["serve"], "cwd": "/path/to/repo" } } }
```

The server's instructions tell the agent to call `preflight` before writing and again before opening a PR, to cite evidence ids rather than paraphrase, and that `block` means a human decides.

## How it is put together

```
orion.yaml            where the sources live, relative to the repo root
src/orion/
  model.py            Node, Edge, Event, Provenance; node ids are readable (model:fct_orders)
  store.py            DuckDB tables: nodes, edges, provenance, events; walk() with tier-1 carry-through
  adapters/
    dbt.py            manifest + catalog -> assets, exposures, owners, columns, column lineage (sqlglot)
    airflow.py        static AST parse of DAG files -> dags, tasks, >> chains, sensors, dbt selectors
    git.py            commits touching mapped paths -> change_landed events
    incidents.py      orion/incidents/*.md front matter -> incident_opened, postmortem_filed events
    runs.py           Airflow task-instance export, dbt run_results.json, Iceberg metadata -> run, SLA, snapshot and schema events
  lineage.py          Jinja to SQL, output columns, leaf-column lineage
  resolver.py         refs (source:, exposure:, short names) and repo paths -> node ids
  constraints.py      orion/constraints/*.yaml, a closed predicate set, validated with pydantic
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
  providers.py        Provider interface; stub, Anthropic, OpenAI-compatible; retries and timeouts
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

A postmortem is Markdown with front matter naming `assets`, `started_at`, `resolved_at`, `cause`, `trigger_change` and `constraints_proposed`; the body is the narrative. See `fixture/orion/`.

## Running the tests

```bash
.venv/bin/pytest -q
```

The end-to-end tests parse the fixture dbt project on the fly, so they need no warehouse; the catalog only adds column types.

## Roadmap

1. **Fixture and graph** (done): graph, adapters, lineage, `explain`.
2. **Pre-flight** (done): typed change ops, blast-radius walk, predicates, risk, the verdict, `gate`, the MCP server, graph-scoped `ask`.
3. **Ledger and post-flight** (done): run history and Iceberg snapshot ingestion, the post-flight watcher that writes `outcome_observed`, constraint proposals from ledger patterns and the nightly proposal PR, hybrid and embedding rankers behind `ask`.
4. **PipelineBench** (harness done): fifty scenarios, four conditions, scripted / Claude / command agents, mechanical graders, a results file from the scripted baseline. Still to do: runs with language-model agents over several seeds, and a larger fixture.

5. **Review loop** (built after the scope document of 2026-10-16): `orion review` on any diff with a provider interface, the `review` MCP tool, inline pull-request comments with memory across pushes, symbol references, credential scanning, and `pipelinebench review-eval` with live Sonnet numbers (`pipelinebench/README.md`). Both models are in the eval and references are classified by parsing rather than text; imports of a changed name are resolved to the file they name, and a removed method called on a receiver another file types as its class is a finding. The model reads the change's stated intent, and a design partner run on ten merged httpx changes is in `pipelinebench/results/partner-httpx-sonnet-5-5.md`. Still to do: receivers typed only by inference (a call result, a field) stay facts for the model.

Next: a design partner's platform behind the adapters, the LLM intent extractor behind `ops_from_intent`, Postgres as the team store, and the benchmark numbers that matter.

## License

Apache-2.0
