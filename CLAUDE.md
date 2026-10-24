# Working on Orion

Orion is a provider-agnostic code-review and change-intelligence layer for developers and coding agents. General review is the baseline; system context (blast radius, constraints, ownership, incidents, outcomes) is the differentiator. These rules hold for every change.

## Product boundaries

- Keep deterministic checks separate from model judgement. Change resolution, graph walks, predicates and risk are code; a model may interpret, summarise and prioritise inside those results, never override them.
- Every finding needs evidence (an id from the store, a file and line, or a diff hunk) and a location where one applies. A finding without valid evidence is rejected or marked uncertain, never shown as fact.
- Never invent owners, dependencies, lineage, constraints, incidents or approvals. If the store does not know, say "not recorded".
- Surface uncertainty. Unresolved paths and low-confidence mappings go in the output's `unresolved` list; they are never dropped silently.
- Orion advises and records. It never writes to a production platform and never approves its own constraint proposals; enforcement belongs to an explicitly configured gate (`orion gate` in CI).
- No mandatory model vendor. Model calls go through the provider interface, never a vendor SDK called from review logic.

## How to change the code

- Small increments: each has a goal, the components it touches, acceptance criteria and tests.
- Run `.venv/bin/ruff check src tests` and `.venv/bin/pytest -q` after any meaningful change.
- Avoid new dependencies and broad rewrites. Prefer extending an existing module over adding an abstraction.
- Preserve CLI flags, MCP tool names and argument shapes, and the verdict JSON fields once released.
- Do not build deferred features (more providers, more platform adapters, hosted services) without an explicit reason recorded in the commit message.
- Never commit credentials, API keys or `.env` files.
- Actions minutes are limited: batch commits and push once per ready state, keep work-in-progress PRs as drafts, and ask for Orion's self-review with the `orion-review` label only when ready. See `CONTRIBUTING.md`.

## Layout

- `src/orion/`: change resolution (`changes.py`, `resolver.py`), system context (`store.py`, `adapters/`, `lineage.py`, `constraints.py`), deterministic analysis (`predicates.py`, `risk.py`, `verdict.py`), retrieval, ledger (`postflight.py`, `proposals.py`), interfaces (`cli.py`, `server.py`).
- `src/pipelinebench/`: the benchmark. Ground truth in `generate.py` is written by hand and must never be derived from Orion.
- `fixture/`: the sample data platform every test runs against.
