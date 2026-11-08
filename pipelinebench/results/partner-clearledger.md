# Second warehouse: ClearLedger (dbt on Postgres, finance marts)

ChangeLens was built against its own fixture and then tried on cal-itp's public warehouse. This note is the second real warehouse: [muhibm1/clearledger](https://github.com/muhibm1/clearledger), a deal-to-cash pipeline that copies Odoo's CRM and accounting records into Postgres and builds finance marts with dbt. It is small (11 models in staging, intermediate and mart layers over 9 Odoo source tables, 18 tests, 2 exposures), but unlike the fixture it was written for another purpose, by its own rules: one dbt source (`odoo`) in schema `raw`, a `latest()` macro behind every staging model, and four postmortems, three of them from failure drills.

The question: does ChangeLens give the right answer on a warehouse it was not built around, with no changes to that warehouse? Everything below is deterministic (no model call), recorded on 2026-11-04 against ClearLedger `4a73c77` with `dbt parse` only (no catalog, so no column types).

## Sync

`changelens sync` reads the manifest, git and `changelens/incidents/`: 136 graph nodes, 214 edges, 12 ledger events (four incidents, each opened and filed, plus git and sync events).

Two problems showed up, and one is fixed in ChangeLens:

- **Fixed: every postmortem pointed at a source ChangeLens could not find.** The postmortems name sources the way an engineer thinks of them, by warehouse schema (`source:raw.load_runs`), while dbt names them by source (`odoo.load_runs`). ChangeLens now also matches `<schema>.<table>` when exactly one source fits; two candidates stay unresolved and are listed. Before the fix, three of the four incidents lost their source and `sync` printed three `unresolved asset` warnings; after it, none.
- **Reported, not fixed: column lineage stops at `select r.*`.** The `latest()` macro selects `r.*` from the source. Without a catalog ChangeLens does not know a source's columns, so staging columns show no upstream (`← —`), and `stg_deals` (which joins two such CTEs) logs `Unknown column: odoo_id`. With `dbt docs generate` against a live warehouse the catalog supplies the columns. ChangeLens says what it could not trace rather than guessing.

## Verdicts

| intent | verdict | risk | what fired | tier-1 reached |
| --- | --- | --- | --- | --- |
| drop the late_over_90 column from receivables_aging | block | 72 | CON-101 (columns locked) | collections_queue, month_end_close |
| rename the column customer_name in revenue_by_customer | block | 72 | CON-101 | month_end_close |
| drop the column expected_revenue from stg_deals | warn | 47 | | deal_to_cash, collections_queue |
| backfill raw.account_move | warn | 35 | | 7 assets, from stg_invoices to month_end_close |
| drop the column stage from stg_deals | inform | 15 | | none: no downstream column reads `stage` |
| add a column region to revenue_by_customer | inform | 0 | | none |

Each row is what the warehouse's owners would want. The two finance marts are locked by CON-101, so removing or renaming a column needs finance sign-off. Dropping `expected_revenue` from staging warns and names the one model that still selects it (`deal_to_cash`). Dropping `stage` from the same model is harmless, because column-level lineage shows nothing reads it, and a table-level tool would have warned anyway.

The rename row needed the second fix. "Rename the column X in T", with no new name yet, used to fall through to a generic `modify` and warn at 32 without firing CON-101. ChangeLens's intent parser now reads a rename in any of three word orders, with or without the new name, and the verdict blocks.

## How to repeat it

```bash
git clone https://github.com/muhibm1/clearledger && cd clearledger
uv venv .venv && uv pip install "git+https://github.com/muhibm1/changelens" dbt-postgres
(cd dbt && ../.venv/bin/dbt parse --profiles-dir .)   # needs no database
.venv/bin/changelens sync
.venv/bin/changelens preflight --intent "rename the column customer_name in revenue_by_customer" --format markdown
```

Risk includes how recent the last incident on an asset was, so a later run of the same intent can score lower than the table.
