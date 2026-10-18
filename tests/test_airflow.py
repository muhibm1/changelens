from pathlib import Path

from orion.adapters.airflow import parse_bash_command, parse_dag_file

DAGS = Path(__file__).resolve().parents[1] / "fixture" / "dags"


def test_parse_dag_recovers_tasks_and_chain():
    parsed = parse_dag_file(DAGS / "orders_pipeline.py")
    assert parsed["dag"]["dag_id"] == "orders_pipeline"
    assert parsed["dag"]["schedule"] == "0 * * * *"
    assert parsed["default_args"]["owner"] == "team/data-platform"
    assert set(parsed["tasks"]) == {"load_orders", "dbt_staging", "dbt_marts", "dbt_test"}
    assert ("load_orders", "dbt_staging") in parsed["deps"]
    assert ("dbt_marts", "dbt_test") in parsed["deps"]
    assert parsed["tasks"]["dbt_marts"]["sla"] == "timedelta(minutes=45)"


def test_parse_bash_command_flags():
    cmd = parse_bash_command("spark-submit jobs/ingest_events.py --partition-by event_date --target raw_events")
    assert cmd["partition_by"] == ["event_date"]
    assert cmd["produces"] == ["raw_events"]
    dbt = parse_bash_command("dbt run --select stg_events+ tag:orders --threads 4")
    assert dbt["dbt_select"] == ["stg_events+", "tag:orders"]
    assert dbt["dbt_command"] == ["run"]
    odd = parse_bash_command("dbt run --select stg_a --vars '{{ params.vars }} --target raw_b")
    assert odd["dbt_select"] == ["stg_a"] and odd["produces"] == ["raw_b"], "unbalanced quotes fall back to whitespace words"
