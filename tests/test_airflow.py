from pathlib import Path

from fathom.adapters.airflow import parse_bash_command, parse_dag_file

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


def test_gusty_folders_are_dags_and_their_files_are_tasks(tmp_path):
    from fathom.adapters.airflow import dag_files, load

    dags = tmp_path / "dags"
    ext = dags / "create_external_tables"
    (ext / "payments").mkdir(parents=True)
    (ext / "METADATA.yml").write_text("schedule_interval: '0 11 * * *'\ntags: [external]\ndefault_args:\n  owner: team/data\n  retry_delay: !timedelta 'minutes: 2'\n")
    (ext / "payments" / "METADATA.yml").write_text("tooltip: a task group, not a DAG\n")
    (ext / "payments" / "settlements.yml").write_text("operator: operators.ExternalTable\ndestination_project_dataset_table: external_payments.settlements\n")
    (ext / "check.sql").write_text("---\noperator: operators.BigQueryCheck\ndependencies: [settlements]\nexternal_dependencies:\n  - sync_payments: all\n---\nselect 1\n")
    (ext / "README.md").write_text("not a task")
    (dags / "loader.py").write_text("from gusty import create_dag\n")
    (dags / "other.py").write_text("from airflow import DAG\nwith DAG(dag_id='other_dag', schedule='@daily') as dag:\n    pass\n")
    other = dags / "other_dag"
    for g in ("a", "b"):  # one stem in two task groups: both kept, told apart by group
        (other / g).mkdir(parents=True)
        (other / g / "load.yml").write_text("operator: operators.Noop\ndependencies: {odd: shape}\nexternal_dependencies: [{x: [1]}]\n")
    (other / "METADATA.yml").write_text("schedule_interval: '@daily'\n")
    assets = {"source_table:payments.settlements": {"id": "source_table:payments.settlements", "kind": "source_table", "name": "payments.settlements", "properties": {"schema": "external_payments", "identifier": "settlements"}}}

    out = load(dags, tmp_path, assets, [])
    by_id = {n.id: n for n in out.nodes}
    assert by_id["dag:create_external_tables"].properties["schedule"] == "0 11 * * *"
    assert by_id["dag:create_external_tables"].owner == "team/data"
    assert out.notes == ["DAG other_dag is defined in both dags/other.py and dags/other_dag/METADATA.yml; their tasks are merged under one DAG"]
    assert {i for i in by_id if i.startswith("task:")} == {"task:create_external_tables.settlements", "task:create_external_tables.check", "task:other_dag.a.load", "task:other_dag.b.load"}
    assert by_id["task:create_external_tables.check"].properties["path"] == "dags/create_external_tables/check.sql"
    edges = {(e.src, e.dst, str(e.kind)) for e in out.edges}
    assert ("task:create_external_tables.settlements", "source_table:payments.settlements", "produces") in edges
    assert ("task:create_external_tables.check", "task:create_external_tables.settlements", "depends_on") in edges
    assert ("dag:create_external_tables", "dag:sync_payments", "depends_on") in edges
    assert dags / "create_external_tables" / "README.md" not in dag_files(dags)
    assert ext / "payments" / "settlements.yml" in dag_files(dags)


def test_a_dag_file_that_does_not_parse_is_noted_and_skipped(tmp_path):
    from fathom.adapters.airflow import load

    dags = tmp_path / "dags"
    dags.mkdir()
    (dags / "broken.py").write_text("from airflow import DAG\nwith DAG(dag_id='x' as dag:\n")
    (dags / "latin1.py").write_bytes(b"# caf\xe9\n")
    (dags / "ok.py").write_text("from airflow import DAG\nwith DAG(dag_id='ok_dag') as dag:\n    pass\n")
    out = load(dags, tmp_path, {}, [])
    assert "dag:ok_dag" in {n.id for n in out.nodes}
    assert sorted(out.notes) == ["skipped dags/broken.py: not parseable as Python (SyntaxError)", "skipped dags/latin1.py: not parseable as Python (UnicodeDecodeError)"]


TASKFLOW = '''
from airflow.decorators import dag, task, task_group
from airflow.providers.airbyte.operators.airbyte import AirbyteTriggerSyncOperator
from utils import helpers


@dag(schedule="0 6 * * *", tags=["daily"], default_args=helpers.set_default_args(owner="team/loans", retries=2))
def daily_loans():
    @task_group(group_id="extract")
    def extract():
        AirbyteTriggerSyncOperator(task_id="ignored_unassigned", connection_id="x")
        sync = AirbyteTriggerSyncOperator(task_id="sync_loans", connection_id="x")

        @task.bash
        def load_rates():
            return "python load.py --target raw_rates"

        load_rates()

    @task.datacoves_dbt(connection_id="main")
    def transform(files: list = []):
        def label():
            return "not a command"

        if files:
            return "dbt build -s result:error+ --state logs"
        return "dbt build -s 'tag:daily+ stg_rates' && echo done"

    @task
    def notify(result):
        return None

    loaded = [extract()]
    built = transform()
    loaded >> built
    notify(built)


daily_loans()
'''


def test_taskflow_dags_read_tasks_groups_commands_and_dependencies(tmp_path):
    path = tmp_path / "loans.py"
    path.write_text(TASKFLOW)
    parsed = parse_dag_file(path)
    assert parsed["dag"]["dag_id"] == "daily_loans" and parsed["dag"]["schedule"] == "0 6 * * *"
    assert parsed["default_args"]["owner"] == "team/loans"
    tasks = {k: t["task_id"] for k, t in parsed["tasks"].items()}
    assert tasks == {"sync": "extract.sync_loans", "load_rates": "extract.load_rates", "transform": "transform", "notify": "notify"}
    assert parsed["tasks"]["load_rates"]["bash_command"] == "python load.py --target raw_rates"
    assert "not a command" not in parsed["tasks"]["transform"]["bash_command"], "a nested helper's return is not the task's command"
    cmd = parse_bash_command(parsed["tasks"]["transform"]["bash_command"])
    assert sorted(cmd["dbt_select"]) == ["result:error+", "stg_rates", "tag:daily+"], "both commands, quoted selectors split, shell operators stop the list"
    assert set(parsed["deps"]) == {("sync", "transform"), ("load_rates", "transform"), ("transform", "notify")}


def test_taskflow_dbt_task_builds_what_a_source_tag_selects(tmp_path):
    from fathom.adapters.airflow import load

    dags = tmp_path / "dags"
    dags.mkdir()
    (dags / "loans.py").write_text(TASKFLOW)
    assets = {
        "source_table:raw.loans": {"kind": "source_table", "name": "raw.loans", "properties": {"tags": ["daily"]}},
        "model:stg_loans": {"kind": "model", "name": "stg_loans", "properties": {}},
        "model:fct_loans": {"kind": "model", "name": "fct_loans", "properties": {}},
        "model:stg_rates": {"kind": "model", "name": "stg_rates", "properties": {}},
        "model:unrelated": {"kind": "model", "name": "unrelated", "properties": {}},
    }
    consumes = [("model:stg_loans", "source_table:raw.loans"), ("model:fct_loans", "model:stg_loans")]
    out = load(dags, tmp_path, assets, consumes)
    built = {e.dst for e in out.edges if e.src == "task:daily_loans.transform" and e.kind.value == "produces"}
    assert built == {"model:stg_loans", "model:fct_loans", "model:stg_rates"}, "the tagged source is the start, not something dbt builds"
    assert ("task:daily_loans.transform", "task:daily_loans.extract.sync_loans") in {(e.src, e.dst) for e in out.edges if e.kind.value == "depends_on"}
