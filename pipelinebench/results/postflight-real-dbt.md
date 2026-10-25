# Post-flight on real dbt runs

Every earlier post-flight test used hand-written run history. This run closes the loop with real builds: dbt 1.12 with dbt-duckdb on the fixture platform, each `dbt build`'s own `target/run_results.json` filed under `fixture/runs/dbt/`, `fathom sync` reading it, and `fathom postflight` judging each verdict against what the builds did.

## Steps

1. Baseline `dbt build`: PASS=25. `fathom sync` reads 29 run events from dbt 1.12's `run_results.json` with no adapter change.
2. Breaking change: in `stg_payments`, `amount as paid_amount` becomes `amount as amount_paid`. `fathom preflight --git HEAD` says **block**, risk 90: the rename reaches 7 downstream assets, 5 of them tier 1, fires CON-031, and repeats the kind of change behind INC-230.
3. The change lands as a commit and `dbt build` fails: `fct_orders` errors with `Values list "p" does not have a column named "paid_amount"`, and 6 models downstream are skipped.
4. `fathom sync`, then `fathom postflight`: the verdict is **observed, outcome failed**, after 2 runs.
5. Revert, then a safe change: `dim_products` gains `unit_price_cents`. Pre-flight says **inform**, risk 0, nothing downstream.
6. It lands, and three `dbt build`s pass. Post-flight records **clean** after 3 runs.

| verdict | predicted | risk | outcome | runs watched |
| --- | --- | --- | --- | --- |
| rename `paid_amount` | block | 90 | failed | 2 |
| add `unit_price_cents` | inform | 0 | clean | 3 |

Calibration: 0 under-called, 0 blocked then clean.

## What the run found in Fathom

Two defects showed up only against a real working tree. Both are fixed in this change.

- **Run history read as a code change.** The new `run_results.json` file was untracked, so it sat in the `--git HEAD` diff. Fathom listed it as unresolved, which raised the safe change to `warn`. Run history Fathom reads (the `runs` paths in `fathom.yaml`) and dbt's `target/` build output are now ignored, the same as constraints and incidents.
- **Intent counted twice.** When `--intent` named the same change the diff shows, Fathom listed two identical ops and the summary said "and 1 more change". Such an intent op now merges into the diff's op as extra evidence (`intent`).

The first, pre-fix `warn` verdict on the safe change is still in this ledger, also observed as clean.
