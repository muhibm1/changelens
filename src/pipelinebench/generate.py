"""Write the sixty scenarios. Ground truth comes from the fixture's own rules, written here by hand:

* CON-031 locks every column of fct_orders and fct_revenue (adds allowed); a column change on an
  upstream model whose lineage lands in either is the same change (stg_orders.discount_pct feeds
  fct_orders.net_amount feeds fct_revenue.gross_revenue). INC-230 is the incident on that path.
* CON-014 pins raw_events to event_date (INC-214). CON-022 forbids raw_events backfills 06:00-20:00 UTC (INC-241).
* CON-040 locks orders_pipeline's schedule because finance_close waits on it.
* CON-050 deprecates fct_sessions. CON-061 requires growth-analytics sign-off on mart_churn_scores.
* Decision rule: escalate when a block constraint applies; proceed_with_checks when a warn constraint applies or a
  destructive change or a backfill reaches a tier-1 consumer; otherwise proceed. "Reaches" follows column lineage for column
  changes and asset lineage otherwise; an exposure that reads a whole model is reached by any column change on it.

Nothing here runs Orion. ``pipelinebench check-ground-truth`` compares the two afterwards.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

FIN = ["team/finance-data"]
PLAT = ["team/data-platform"]
MODELS = "fixture/dbt_project/models"
DAGS = "fixture/dags"

_scenarios: list[dict[str, Any]] = []


def S(
    title: str,
    family: str,
    task: str,
    ops: list[dict[str, Any]] | None,
    decision: str,
    severity: str,
    *,
    cite: list[str] | None = None,
    name: list[str] | None = None,
    approvers: list[str] | None = None,
    intent: str | None = None,
    files: list[str] | None = None,
    why: str = "",
    tags: list[str] | None = None,
) -> None:
    must_not = ["proceed"] if decision == "escalate" else (["escalate"] if decision == "proceed" else [])
    _scenarios.append(
        {
            "title": title,
            "family": family,
            "task": task,
            "change": {"ops": ops or [], "intent": intent, "files": files or []},
            "expected": {
                "decision": decision,
                "severity": severity,
                "must_cite": cite or [],
                "must_name": name or [],
                "approvers": approvers or [],
                "must_not": must_not,
                "why": why,
            },
            "tags": tags or [],
        }
    )


def col(kind: str, asset: str, column: str, **details: Any) -> dict[str, Any]:
    d: dict[str, Any] = {"kind": kind, "asset": asset, "column": column}
    if details:
        d["details"] = details
    return d


def build() -> list[dict[str, Any]]:
    _scenarios.clear()
    sql = lambda m, sub="marts": [f"{MODELS}/{sub}/{m}.sql"]

    # A. column changes on the locked models themselves
    S("Drop discount_pct from fct_orders", "locked_direct", "Remove the unused discount_pct column from fct_orders; nothing selects it.", [col("drop_column", "fct_orders", "discount_pct")], "escalate", "block", cite=["CON-031", "INC-230"], name=["dash_revenue_weekly"], approvers=FIN, files=sql("fct_orders"), why="CON-031 locks fct_orders columns; INC-230 was this exact change.", tags=["column", "destructive"])
    S("Drop status from fct_orders", "locked_direct", "Drop the status column from fct_orders, it duplicates stg_orders.status.", [col("drop_column", "fct_orders", "status")], "escalate", "block", cite=["CON-031", "INC-230"], name=["dash_revenue_weekly"], approvers=FIN, files=sql("fct_orders"), tags=["column", "destructive"])
    S("Rename payment_count in fct_orders", "locked_direct", "Rename fct_orders.payment_count to payments for consistency with the payments mart.", [col("rename_column", "fct_orders", "payment_count", to="payments")], "escalate", "block", cite=["CON-031", "INC-230"], name=["dash_revenue_weekly"], approvers=FIN, files=sql("fct_orders"), tags=["column", "destructive"])
    S("Retype gross_revenue in fct_revenue", "locked_direct", "Change fct_revenue.gross_revenue from decimal(18,2) to double for the BI tool.", [col("change_type", "fct_revenue", "gross_revenue", to="double")], "escalate", "block", cite=["CON-031", "INC-230"], name=["dash_revenue_weekly", "finance_close_job"], approvers=FIN, files=sql("fct_revenue"), tags=["column", "destructive", "contract"])
    S("Drop order_count from fct_revenue", "locked_direct", "Remove order_count from fct_revenue; it can be computed in the dashboard.", [col("drop_column", "fct_revenue", "order_count")], "escalate", "block", cite=["CON-031", "INC-230"], name=["dash_revenue_weekly", "finance_close_job"], approvers=FIN, files=sql("fct_revenue"), tags=["column", "destructive", "contract"])
    S("Redefine collected_revenue in fct_revenue", "locked_direct", "Change collected_revenue in fct_revenue to exclude refunded orders.", [col("change_expression", "fct_revenue", "collected_revenue")], "escalate", "block", cite=["CON-031", "INC-230"], name=["dash_revenue_weekly", "finance_close_job"], approvers=FIN, files=sql("fct_revenue"), tags=["column", "destructive", "contract"])
    S("Drop paid_amount from fct_orders", "locked_direct", "Drop paid_amount from fct_orders; payments live in their own mart now.", [col("drop_column", "fct_orders", "paid_amount")], "escalate", "block", cite=["CON-031", "INC-230"], name=["dash_revenue_weekly", "finance_close_job"], approvers=FIN, files=sql("fct_orders"), why="paid_amount feeds fct_revenue.collected_revenue.", tags=["column", "destructive"])
    S("Add currency_rate to fct_orders", "locked_add", "Add a currency_rate column to fct_orders for multi-currency reporting.", [col("add_column", "fct_orders", "currency_rate")], "proceed", "inform", files=sql("fct_orders"), why="CON-031 allows adds.", tags=["column", "additive"])
    S("Add refund_total to fct_revenue", "locked_add", "Add refund_total to fct_revenue so the dashboard can show net of refunds.", [col("add_column", "fct_revenue", "refund_total")], "proceed", "inform", files=sql("fct_revenue"), tags=["column", "additive", "contract"])
    S("Rename currency in fct_revenue", "locked_direct", "Rename fct_revenue.currency to ccy to match the finance warehouse.", [col("rename_column", "fct_revenue", "currency", to="ccy")], "escalate", "block", cite=["CON-031", "INC-230"], name=["dash_revenue_weekly", "finance_close_job"], approvers=FIN, files=sql("fct_revenue"), tags=["column", "destructive", "contract"])

    # B. upstream column changes that reach the locked models only through lineage
    S("Drop discount_pct from stg_orders", "locked_via_lineage", "Drop discount_pct from stg_orders; the raw column is being retired.", [col("drop_column", "stg_orders", "discount_pct")], "escalate", "block", cite=["CON-031", "INC-230"], name=["fct_revenue", "dash_revenue_weekly"], approvers=FIN, files=sql("stg_orders", "staging"), why="stg_orders.discount_pct -> fct_orders.net_amount -> fct_revenue.gross_revenue.", tags=["column", "destructive", "lineage"])
    S("Drop currency from stg_orders", "locked_via_lineage", "Remove currency from stg_orders; every order is USD now.", [col("drop_column", "stg_orders", "currency")], "escalate", "block", cite=["CON-031", "INC-230"], name=["fct_revenue"], approvers=FIN, files=sql("stg_orders", "staging"), tags=["column", "destructive", "lineage"])
    S("Retype amount in int_order_payments", "locked_via_lineage", "Cast int_order_payments.amount to float to speed up the aggregation.", [col("change_type", "int_order_payments", "amount", to="float")], "escalate", "block", cite=["CON-031", "INC-230"], name=["fct_orders", "fct_revenue"], approvers=FIN, files=sql("int_order_payments", "intermediate"), tags=["column", "destructive", "lineage"])
    S("Drop paid_amount from stg_payments", "locked_via_lineage", "Drop paid_amount from stg_payments and use the raw amount downstream.", [col("drop_column", "stg_payments", "paid_amount")], "escalate", "block", cite=["CON-031", "INC-230"], name=["fct_revenue"], approvers=FIN, files=sql("stg_payments", "staging"), tags=["column", "destructive", "lineage"])
    S("Drop payment_method from stg_payments", "unlocked_column", "Drop payment_method from stg_payments; nothing downstream uses it.", [col("drop_column", "stg_payments", "payment_method")], "proceed", "inform", files=sql("stg_payments", "staging"), why="No column lineage leaves payment_method.", tags=["column", "destructive", "harmless"])
    S("Drop region from stg_customers", "unlocked_column", "Drop region from stg_customers; it is being moved to a separate geo model.", [col("drop_column", "stg_customers", "region")], "proceed", "inform", files=sql("stg_customers", "staging"), why="Reaches dim_customers and mart_churn_scores, tier 2, no constraint on the change.", tags=["column", "destructive", "harmless"])
    S("Rename session_id in stg_events", "unlocked_column", "Rename stg_events.session_id to sid.", [col("rename_column", "stg_events", "session_id", to="sid")], "proceed", "inform", files=sql("stg_events", "staging"), tags=["column", "destructive", "harmless"])
    S("Drop order_count from int_customer_orders", "unlocked_column", "Drop order_count from int_customer_orders.", [col("drop_column", "int_customer_orders", "order_count")], "proceed", "inform", files=sql("int_customer_orders", "intermediate"), tags=["column", "destructive", "harmless"])
    S("Add channel to stg_orders", "additive_upstream", "Add an acquisition channel column to stg_orders.", [col("add_column", "stg_orders", "channel")], "proceed", "inform", files=sql("stg_orders", "staging"), tags=["column", "additive"])
    S("Drop status from stg_orders", "locked_via_lineage", "Drop status from stg_orders; downstream should use the orders status table.", [col("drop_column", "stg_orders", "status")], "escalate", "block", cite=["CON-031", "INC-230"], name=["fct_orders"], approvers=FIN, files=sql("stg_orders", "staging"), tags=["column", "destructive", "lineage"])

    # C. partitioning
    S("Repartition raw_events by ingest_ts", "partition", "Repartition raw_events by ingest_ts so late-arriving events land in one place.", [{"kind": "change_partition", "asset": "raw_events", "details": {"to": "ingest_ts", "from": "event_date"}}], "escalate", "block", cite=["CON-014", "INC-214"], name=["fct_daily_active_users"], approvers=PLAT, files=[f"{DAGS}/events_pipeline.py"], tags=["partition", "destructive"])
    S("Repartition raw_events by event_date (no-op)", "partition", "Make the partitioning of raw_events explicit: event_date.", [{"kind": "change_partition", "asset": "raw_events", "details": {"to": "event_date", "from": "event_date"}}], "proceed", "inform", files=[f"{DAGS}/events_pipeline.py"], why="Same key as CON-014 requires.", tags=["partition", "harmless"])
    S("Repartition raw_events by ingest_ts (intent)", "partition", "Repartition raw_events by ingest_ts.", None, "escalate", "block", cite=["CON-014", "INC-214"], name=["fct_daily_active_users"], approvers=PLAT, intent="repartition raw_events by ingest_ts", tags=["partition", "destructive", "intent"])
    S("Repartition raw_orders by order_date", "partition", "Partition raw_orders by order_date to speed up incremental loads.", [{"kind": "change_partition", "asset": "raw_orders", "details": {"to": "order_date", "from": None}}], "proceed_with_checks", "warn", name=["fct_orders"], why="No constraint, but a destructive change with tier-1 consumers downstream.", tags=["partition", "destructive"])

    # D. backfills
    for when, dec, sev in [("09:00", "escalate", "block"), ("13:30", "escalate", "block"), ("19:45", "escalate", "block"), ("22:00", "proceed", "inform"), ("03:00", "proceed", "inform"), ("05:59", "proceed", "inform")]:
        inside = dec == "escalate"
        S(f"Backfill raw_events at {when} UTC", "backfill", f"Backfill raw_events for the last 7 days, starting at {when} UTC.", [{"kind": "backfill", "asset": "raw_events", "details": {"at": f"2026-10-20T{when}:00+00:00", "days": 7}}], dec, sev, cite=["CON-022", "INC-241"] if inside else [], approvers=PLAT if inside else [], why="CON-022 forbids 06:00-20:00 UTC." if inside else "Outside the CON-022 window.", tags=["backfill"] + (["destructive"] if inside else ["harmless"]))
    S("Backfill fct_orders at noon", "backfill", "Rebuild fct_orders for September at 12:00 UTC.", [{"kind": "backfill", "asset": "fct_orders", "details": {"at": "2026-10-20T12:00:00+00:00"}}], "proceed_with_checks", "warn", name=["dash_revenue_weekly"], why="CON-022 covers raw_events only, but the rebuild reaches the finance dashboard while it runs.", tags=["backfill"])
    S("Backfill stg_events at 10:00", "backfill", "Backfill stg_events for last week at 10:00 UTC.", [{"kind": "backfill", "asset": "stg_events", "details": {"at": "2026-10-20T10:00:00+00:00"}}], "proceed", "inform", tags=["backfill", "harmless"])

    # E. schedules
    S("Move orders_pipeline to every six hours", "schedule", "Change orders_pipeline from hourly to every six hours to cut warehouse cost.", [{"kind": "change_schedule", "asset": "orders_pipeline", "details": {"to": "0 */6 * * *", "from": "0 * * * *"}}], "proceed_with_checks", "warn", cite=["CON-040"], name=["finance_close"], approvers=["team/finance-data", "team/data-platform"], files=[f"{DAGS}/orders_pipeline.py"], tags=["schedule"])
    S("Move orders_pipeline to daily (intent)", "schedule", "Change the schedule of orders_pipeline to daily.", None, "proceed_with_checks", "warn", cite=["CON-040"], name=["finance_close"], approvers=["team/finance-data", "team/data-platform"], intent="change the schedule of orders_pipeline to daily", tags=["schedule", "intent"])
    S("Move events_pipeline to 01:00", "schedule", "Run events_pipeline at 01:00 instead of 02:30.", [{"kind": "change_schedule", "asset": "events_pipeline", "details": {"to": "0 1 * * *", "from": "30 2 * * *"}}], "proceed", "inform", files=[f"{DAGS}/events_pipeline.py"], tags=["schedule", "harmless"])
    S("Move finance_close to 07:00", "schedule", "Run finance_close at 07:00 to give orders_pipeline more slack.", [{"kind": "change_schedule", "asset": "finance_close", "details": {"to": "0 7 * * *", "from": "0 6 * * *"}}], "proceed", "inform", files=[f"{DAGS}/finance_close.py"], tags=["schedule", "harmless"])
    S("Raise retries on orders_pipeline", "schedule", "Set retries=3 on orders_pipeline's default_args.", [{"kind": "modify", "asset": "orders_pipeline"}], "proceed", "inform", files=[f"{DAGS}/orders_pipeline.py"], tags=["dag", "harmless"])

    # F. deprecation and approvals
    S("Build fct_product_engagement on fct_sessions", "deprecated", "Join fct_sessions into fct_product_engagement to add session counts per product.", [{"kind": "add_dependency", "asset": "fct_product_engagement", "details": {"on": "model:fct_sessions"}}], "proceed_with_checks", "warn", cite=["CON-050"], approvers=["team/product-analytics"], files=sql("fct_product_engagement"), tags=["dependency"])
    S("Tweak fct_sessions", "deprecated", "Add a 30-minute inactivity cutoff to fct_sessions.", [{"kind": "modify", "asset": "fct_sessions"}], "proceed_with_checks", "warn", cite=["CON-050"], approvers=["team/product-analytics"], files=sql("fct_sessions"), tags=["modify"])
    S("Add tenure_days to mart_churn_scores", "approval", "Add a tenure_days feature to mart_churn_scores.", [col("add_column", "mart_churn_scores", "tenure_days")], "proceed_with_checks", "warn", cite=["CON-061"], approvers=["team/growth-analytics"], files=sql("mart_churn_scores"), tags=["column", "additive", "approval"])
    S("Change the churn score formula", "approval", "Change the churn_score formula in mart_churn_scores to weight sessions_30d higher.", [{"kind": "modify", "asset": "mart_churn_scores"}], "proceed_with_checks", "warn", cite=["CON-061"], approvers=["team/growth-analytics"], files=sql("mart_churn_scores"), tags=["modify", "approval"])
    S("Drop sessions_30d from mart_churn_scores", "approval", "Drop sessions_30d from mart_churn_scores; the model no longer uses it.", [col("drop_column", "mart_churn_scores", "sessions_30d")], "proceed_with_checks", "warn", cite=["CON-061"], approvers=["team/growth-analytics"], files=sql("mart_churn_scores"), tags=["column", "destructive", "approval"])
    S("Add tenure to dim_customers", "additive", "Add a tenure column to dim_customers.", [col("add_column", "dim_customers", "tenure")], "proceed", "inform", files=sql("dim_customers"), tags=["column", "additive"])

    # G. several changes at once, and distractors
    S("Drop discount_pct and slow orders_pipeline", "multi", "Clean up fct_orders (drop discount_pct) and move orders_pipeline to every six hours in one PR.", [col("drop_column", "fct_orders", "discount_pct"), {"kind": "change_schedule", "asset": "orders_pipeline", "details": {"to": "0 */6 * * *", "from": "0 * * * *"}}], "escalate", "block", cite=["CON-031", "CON-040", "INC-230"], name=["dash_revenue_weekly", "finance_close"], approvers=FIN, files=sql("fct_orders") + [f"{DAGS}/orders_pipeline.py"], tags=["multi", "destructive"])
    S("Two additive columns", "multi", "Add currency_rate to fct_orders and refund_total to fct_revenue.", [col("add_column", "fct_orders", "currency_rate"), col("add_column", "fct_revenue", "refund_total")], "proceed", "inform", files=sql("fct_orders") + sql("fct_revenue"), tags=["multi", "additive"])
    S("Swap payment_method for processor", "multi", "Replace stg_payments.payment_method with a processor column.", [col("drop_column", "stg_payments", "payment_method"), col("add_column", "stg_payments", "processor")], "proceed", "inform", files=sql("stg_payments", "staging"), tags=["multi", "harmless"])
    S("Repartition and backfill raw_events", "multi", "Repartition raw_events by ingest_ts and backfill the last 30 days starting at 10:00 UTC.", [{"kind": "change_partition", "asset": "raw_events", "details": {"to": "ingest_ts", "from": "event_date"}}, {"kind": "backfill", "asset": "raw_events", "details": {"at": "2026-10-20T10:00:00+00:00", "days": 30}}], "escalate", "block", cite=["CON-014", "CON-022", "INC-214", "INC-241"], name=["fct_daily_active_users"], approvers=PLAT, files=[f"{DAGS}/events_pipeline.py"], tags=["multi", "destructive"])
    S("Describe stg_products", "distractor", "Add a description to stg_products in schema.yml.", [{"kind": "modify", "asset": "stg_products"}], "proceed", "inform", files=[f"{MODELS}/staging/schema.yml"], tags=["harmless"])
    S("Drop unit_price from dim_products", "unlocked_column", "Drop unit_price from dim_products; prices move to a pricing service.", [col("drop_column", "dim_products", "unit_price")], "proceed", "inform", files=sql("dim_products"), why="dim_products has no downstream consumers.", tags=["column", "destructive", "harmless"])
    S("Rename reason in fct_refunds", "tier1_unlocked", "Rename fct_refunds.reason to refund_reason.", [col("rename_column", "fct_refunds", "reason", to="refund_reason")], "proceed_with_checks", "warn", name=["finance_close_job"], why="No constraint, but the finance close export reads the whole of fct_refunds.", tags=["column", "destructive"])

    # H. added after the first agent runs (PB-051 on), so PB-001..050 keep their ids: lineage through renamed and
    # redefined columns, a constraint scoped to a downstream model, a window boundary, and a tier-1 reader of a whole model
    S("Rename amount in stg_orders", "locked_via_lineage", "Rename stg_orders.amount to gross_amount to say what it holds.", [col("rename_column", "stg_orders", "amount", to="gross_amount")], "escalate", "block", cite=["CON-031", "INC-230"], name=["fct_orders"], approvers=FIN, files=sql("stg_orders", "staging"), why="stg_orders.amount -> int_order_payments.amount -> fct_orders.amount and net_amount.", tags=["column", "destructive", "lineage"])
    S("Redefine paid_amount in stg_payments", "locked_via_lineage", "Count refunded payments as negative in stg_payments.paid_amount.", [col("change_expression", "stg_payments", "paid_amount")], "escalate", "block", cite=["CON-031", "INC-230"], name=["fct_revenue"], approvers=FIN, files=sql("stg_payments", "staging"), why="paid_amount -> int_order_payments.paid_amount -> fct_orders.paid_amount -> fct_revenue.collected_revenue.", tags=["column", "destructive", "lineage"])
    S("Retype order_date in stg_orders", "locked_via_lineage", "Keep stg_orders.order_date as a timestamp instead of casting it to a date.", [col("change_type", "stg_orders", "order_date", to="timestamp")], "escalate", "block", cite=["CON-031", "INC-230"], name=["fct_revenue"], approvers=FIN, files=sql("stg_orders", "staging"), why="order_date -> fct_orders.order_date -> fct_revenue.revenue_date.", tags=["column", "destructive", "lineage"])
    S("Drop customer_id from int_order_payments", "locked_via_lineage", "Drop customer_id from int_order_payments; customers are joined in the marts.", [col("drop_column", "int_order_payments", "customer_id")], "escalate", "block", cite=["CON-031", "INC-230"], name=["fct_orders"], approvers=FIN, files=sql("int_order_payments", "intermediate"), why="int_order_payments.customer_id -> fct_orders.customer_id, a locked column.", tags=["column", "destructive", "lineage"])
    S("Repartition stg_events by ingest_ts", "partition", "Repartition stg_events by ingest_ts to match the loader.", [{"kind": "change_partition", "asset": "stg_events", "details": {"to": "ingest_ts", "from": "event_date"}}], "escalate", "block", cite=["CON-014", "INC-214"], name=["fct_daily_active_users"], approvers=PLAT, files=sql("stg_events", "staging"), why="CON-014 covers stg_events as well as raw_events.", tags=["partition", "destructive"])
    S("Backfill raw_events at 06:00 UTC", "backfill", "Backfill raw_events for the last 7 days, starting at 06:00 UTC.", [{"kind": "backfill", "asset": "raw_events", "details": {"at": "2026-10-20T06:00:00+00:00", "days": 7}}], "escalate", "block", cite=["CON-022", "INC-241"], approvers=PLAT, why="The CON-022 window starts at 06:00.", tags=["backfill", "destructive"])
    S("Backfill fct_revenue overnight", "backfill", "Rebuild fct_revenue for the quarter at 02:00 UTC.", [{"kind": "backfill", "asset": "fct_revenue", "details": {"at": "2026-10-20T02:00:00+00:00"}}], "proceed_with_checks", "warn", name=["dash_revenue_weekly", "finance_close_job"], why="No window applies to fct_revenue, but the rebuild reaches two tier-1 exposures.", tags=["backfill"])
    S("Drop refund_amount from stg_refunds", "tier1_unlocked", "Drop refund_amount from stg_refunds; refunds are read from the payments processor now.", [col("drop_column", "stg_refunds", "refund_amount")], "proceed_with_checks", "warn", name=["finance_close_job"], why="refund_amount -> fct_refunds.refund_amount, and the finance close export reads the whole of fct_refunds.", tags=["column", "destructive", "lineage"])
    S("Drop event_type from stg_events", "unlocked_column", "Drop event_type from stg_events; no model groups by it.", [col("drop_column", "stg_events", "event_type")], "proceed", "inform", files=sql("stg_events", "staging"), why="No column lineage leaves event_type; CON-014 is about the partition key, not columns.", tags=["column", "destructive", "harmless"])
    S("Add fee to stg_payments", "additive_upstream", "Add a processing fee column to stg_payments.", [col("add_column", "stg_payments", "fee")], "proceed", "inform", files=sql("stg_payments", "staging"), why="A new column reaches nothing yet.", tags=["column", "additive"])

    assert len(_scenarios) == 60, len(_scenarios)
    for i, s in enumerate(_scenarios, 1):
        s["id"] = f"PB-{i:03d}"
    return list(_scenarios)


def write(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.glob("PB-*.yaml"):
        old.unlink()
    paths = []
    for s in build():
        p = directory / f"{s['id']}.yaml"
        ordered = {k: s[k] for k in ("id", "title", "family", "task", "change", "expected", "tags")}
        p.write_text(yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True))
        paths.append(p)
    return paths
