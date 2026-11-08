"""``changelens sync`` on a dbt project that has no manifest says what is missing and how to build it."""

from typer.testing import CliRunner

from changelens.cli import app


def test_sync_without_manifest_names_path_and_command(tmp_path):
    project = tmp_path.resolve() / "shop"
    project.mkdir()
    (project / "dbt_project.yml").write_text("name: shop\nversion: '1.0'\nprofile: shop\n")
    cfg = tmp_path / "changelens.yaml"
    cfg.write_text("adapters:\n  dbt:\n    project: shop\n")

    result = CliRunner().invoke(app, ["sync", "--config", str(cfg)])

    out = " ".join(result.output.split())
    assert result.exit_code != 0
    assert "Traceback" not in out
    assert str(project / "target" / "manifest.json") in out
    assert f"dbt parse --project-dir {project}" in out
