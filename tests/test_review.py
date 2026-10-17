"""The review loop: diff parsing, provider interface, validation, deterministic findings."""

import io
import json
import urllib.error
from pathlib import Path

import pytest

from orion import providers
from orion.constraints import load_constraints
from orion.diffparse import parse, render
from orion.providers import AnthropicProvider, OpenAICompatibleProvider, ProviderError, StubProvider, make_provider
from orion.review import FINDINGS_SCHEMA, review, to_markdown
from orion.risk import RiskModel

ROOT = Path(__file__).resolve().parents[1]
FCT = "fixture/dbt_project/models/marts/fct_orders.sql"

DROP_DIFF = f"""diff --git a/{FCT} b/{FCT}
index 5d4a5bf..51f9e65 100644
--- a/{FCT}
+++ b/{FCT}
@@ -5,7 +5,6 @@ select
     order_date,
     status,
     amount,
-    discount_pct,
     amount * (1 - discount_pct / 100.0) as net_amount,
     currency,
     paid_amount,
"""

APP_DIFF = """diff --git a/app/service.py b/app/service.py
new file mode 100644
--- /dev/null
+++ b/app/service.py
@@ -0,0 +1,6 @@
+import subprocess
+
+
+def run(cmd):
+    return subprocess.run(cmd, shell=True)
+
"""


@pytest.fixture(scope="module")
def constraints():
    cs, _ = load_constraints(ROOT / "fixture" / "orion" / "constraints")
    return cs


def _review(store, constraints, diff, provider):
    return review(store, ROOT, provider, constraints=constraints, risk_model=RiskModel.load(None), diff_text=diff, scope=["fixture"], ignore=["fixture/orion"])


def test_parse_diff_line_numbers():
    files = parse(DROP_DIFF + APP_DIFF)
    assert [f.path for f in files] == [FCT, "app/service.py"]
    fct, app = files
    assert fct.status == "modified" and fct.contains(8) and not fct.contains(40)
    assert fct.first_changed_line() == 8, "a pure deletion points at the line where the removed text stood"
    assert app.status == "added" and app.hunks[0].added() == [1, 2, 3, 4, 5, 6]
    assert "    5 + " in render([app]) or "5 +     return" in render([app])


def test_deterministic_findings_need_no_model(synced_store, constraints):
    r = _review(synced_store, constraints, DROP_DIFF, StubProvider())
    assert r["severity"] == "block"
    det = [f for f in r["findings"] if f["source"] == "deterministic"]
    assert len(det) == 1
    f = det[0]
    assert f["path"] == FCT and f["line"] == 8
    assert "constraint:CON-031" in f["evidence"] and "incident:INC-230" in f["evidence"]
    assert all(not e.startswith("event:INC") for e in f["evidence"])
    assert "team/finance-data" in f["body"]


def test_validation_rejects_out_of_diff_and_fabricated_evidence(synced_store, constraints):
    stub = StubProvider({"findings": [
        {"path": "app/service.py", "line": 5, "severity": "block", "category": "security", "title": "shell=True with caller input", "body": "Command injection.", "evidence": ["hunk:app/service.py:5"]},
        {"path": "app/service.py", "line": 99, "severity": "warn", "category": "correctness", "title": "Line not in diff", "body": "x", "evidence": ["hunk:app/service.py:99"]},
        {"path": "app/other.py", "line": 1, "severity": "warn", "category": "correctness", "title": "File not in diff", "body": "x", "evidence": []},
        {"path": "app/service.py", "line": 1, "severity": "inform", "category": "maintainability", "title": "Owned by team/payments", "body": "invented", "evidence": ["node:team:team/payments", "constraint:CON-999"]},
    ]})
    r = _review(synced_store, constraints, APP_DIFF, stub)
    valid = [f for f in r["findings"] if f["source"] == "model"]
    assert [f["title"] for f in valid] == ["shell=True with caller input"]
    assert valid[0]["severity"] == "block"
    assert {f["title"] for f in r["rejected"]} == {"Line not in diff", "File not in diff"}
    assert [f["title"] for f in r["uncertain"]] == ["Owned by team/payments"]
    assert "CON-999" in r["uncertain"][0]["notes"][0]
    assert r["counts"] == {"deterministic": 0, "model_valid": 1, "uncertain": 1, "rejected": 2}
    system, user = stub.calls[0]
    assert "Never invent" in system and "app/service.py" in user


def test_citing_a_firing_constraint_raises_severity_and_deduplicates(synced_store, constraints):
    stub = StubProvider({"findings": [
        {"path": FCT, "line": 8, "severity": "inform", "category": "data_contract", "title": "discount_pct is part of a contract", "body": "CON-031.", "evidence": ["constraint:CON-031"]},
        {"path": FCT, "line": 8, "severity": "inform", "category": "correctness", "title": "net_amount still references discount_pct", "body": "The expression on line 8 reads the column this diff removes.", "evidence": ["hunk:" + FCT + ":8"]},
        {"path": FCT, "line": 8, "severity": "warn", "category": "data_contract", "title": "net_amount now wrong for finance", "body": "A bug that also cites the contract it breaks.", "evidence": ["hunk:" + FCT + ":8", "constraint:CON-031"]},
    ]})
    r = _review(synced_store, constraints, DROP_DIFF, stub)
    titles = [f["title"] for f in r["findings"]]
    assert titles[0].startswith("CON-031"), "deterministic first"
    assert "discount_pct is part of a contract" not in titles, "covered by the deterministic finding"
    assert "net_amount still references discount_pct" in titles
    assert "net_amount now wrong for finance" in titles, "a correctness bug citing the constraint is not a duplicate of it"


def test_provider_failure_still_returns_deterministic_review(synced_store, constraints):
    class Broken:
        name = "broken"

        def complete_json(self, *a, **k):
            raise ProviderError("HTTP 500")

    r = _review(synced_store, constraints, DROP_DIFF, Broken())
    assert r["provider_error"] == "HTTP 500"
    assert r["severity"] == "block" and r["findings"]
    assert "Model review unavailable" in to_markdown(r)


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_anthropic_request_shape_and_tool_output(monkeypatch):
    seen = {}

    def fake(req, timeout):
        seen["url"], seen["headers"], seen["body"] = req.full_url, dict(req.headers), json.loads(req.data)
        return _Resp(json.dumps({"stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 5}, "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": '{"findings": [{"title": "t"}]}'}]}).encode())

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake)
    p = AnthropicProvider(model="claude-sonnet-5-5", api_key="k")
    out = p.complete_json("sys", "user", FINDINGS_SCHEMA, schema_name="report_findings")
    assert out == {"findings": [{"title": "t"}]}
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["X-api-key"] == "k"
    assert "tool_choice" not in seen["body"], "forced tool use is a 400 on current Claude models"
    assert seen["body"]["output_config"] == {"format": {"type": "json_schema", "schema": FINDINGS_SCHEMA}, "effort": "medium"}
    assert p.last_usage == {"input_tokens": 10, "output_tokens": 5}


def test_openai_compatible_endpoint_and_retries(monkeypatch):
    calls = []

    def fake(req, timeout):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "slow down", {}, io.BytesIO(b"rate limited"))
        return _Resp(json.dumps({"choices": [{"message": {"content": '```json\n{"findings": []}\n```'}}]}).encode())

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake)
    monkeypatch.setattr(providers.time, "sleep", lambda s: None)
    p = OpenAICompatibleProvider(model="local-model", base_url="http://localhost:11434/v1", api_key="")
    assert p.complete_json("s", "u", FINDINGS_SCHEMA) == {"findings": []}
    assert calls == ["http://localhost:11434/v1/chat/completions"] * 2


def test_non_retryable_error_and_unknown_provider(monkeypatch):
    def fake(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 401, "no", {}, io.BytesIO(b"bad key"))

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake)
    with pytest.raises(ProviderError, match="401"):
        OpenAICompatibleProvider(api_key="x", retries=3).complete_json("s", "u", FINDINGS_SCHEMA)
    with pytest.raises(ProviderError):
        make_provider("nope")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ORION_ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="ORION_ANTHROPIC_API_KEY or ANTHROPIC_API_KEY"):
        make_provider("anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "general")
    assert make_provider("anthropic").api_key == "general"
    monkeypatch.setenv("ORION_ANTHROPIC_API_KEY", "orion")
    assert make_provider("anthropic").api_key == "orion"
    assert make_provider("stub").name == "stub"


def test_both_sides_works_whichever_side_the_file_is_on():
    from orion.diffparse import both_sides

    fd = parse(DROP_DIFF)[0]
    current = (ROOT / FCT).read_text()
    old, new = both_sides(fd, current)
    assert old == current and "    discount_pct,\n" not in new
    old2, new2 = both_sides(fd, new)  # the diff already applied
    assert old2 == current and new2 == new
    assert both_sides(fd, "unrelated\n") is None


def test_cli_review_from_diff_file(synced_store, tmp_path):
    from typer.testing import CliRunner

    from orion.cli import app

    cfg = ROOT / f".orion-test-{tmp_path.name}.yaml"  # beside orion.yaml so relative paths resolve the same way
    cfg.write_text((ROOT / "orion.yaml").read_text().replace("db: .orion/graph.duckdb", f"db: {synced_store.path}"))
    d = tmp_path / "change.diff"
    d.write_text(DROP_DIFF)
    synced_store.con.close()
    try:
        res = CliRunner().invoke(app, ["review", "--diff-file", str(d), "--format", "json", "--config", str(cfg)])
    finally:
        cfg.unlink()
        synced_store.con = __import__("duckdb").connect(str(synced_store.path))
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["severity"] == "block" and out["provider"] == "stub"
    assert out["findings"][0]["evidence"][0] == "constraint:CON-031"


def test_dotfile_paths_are_not_mangled(synced_store, constraints):
    wf = ".github/workflows/deploy.yml"
    diff = f"""diff --git a/{wf} b/{wf}
new file mode 100644
--- /dev/null
+++ b/{wf}
@@ -0,0 +1,3 @@
+on: pull_request_target
+jobs:
+  run: {{runs-on: ubuntu-latest}}
"""
    stub = StubProvider({"findings": [
        {"path": wf, "line": 1, "severity": "warn", "category": "security", "title": "pull_request_target runs fork code with secrets", "body": "x", "evidence": [f"hunk:{wf}:1"]},
        {"path": f"./{wf}", "line": 1, "severity": "inform", "category": "security", "title": "same file via ./", "body": "x", "evidence": [f"hunk:./{wf}:1"]},
    ]})
    r = _review(synced_store, constraints, diff, stub)
    assert {f["title"] for f in r["findings"]} == {"pull_request_target runs fork code with secrets", "same file via ./"}
    assert all(f["path"] == wf for f in r["findings"])
    assert r["rejected"] == []


def test_whole_changed_file_goes_to_the_model_unless_turned_off(synced_store, constraints):
    stub = StubProvider()
    _review(synced_store, constraints, APP_DIFF, stub)
    _, user = stub.calls[0]
    assert "app/service.py (whole file after the change)" in user
    assert user.index("whole file after the change") < user.index("Diff:")
    off = StubProvider()
    review(synced_store, ROOT, off, constraints=constraints, risk_model=RiskModel.load(None), diff_text=APP_DIFF, file_context=False)
    assert "whole file after the change" not in off.calls[0][1]
    given = StubProvider()
    review(synced_store, ROOT, given, constraints=constraints, risk_model=RiskModel.load(None), diff_text=APP_DIFF, new_files={"app/service.py": "import subprocess\n\n\ndef run(cmd):\n    return subprocess.run(cmd, shell=True)\n\n# SENTINEL\n"})
    assert "# SENTINEL" in given.calls[0][1]


def test_whole_file_context_skips_secret_files_and_mismatched_text(synced_store, constraints):
    env_diff = APP_DIFF.replace("app/service.py", "deploy/.env.production")
    stub = StubProvider()
    _review(synced_store, constraints, env_diff, stub)
    assert "whole file after the change" not in stub.calls[0][1]
    wrong = StubProvider()
    review(synced_store, ROOT, wrong, constraints=constraints, risk_model=RiskModel.load(None), diff_text=APP_DIFF, new_files={"app/service.py": "unrelated\ncontent\n"})
    assert "whole file after the change" not in wrong.calls[0][1], "text that does not match the diff's new side is not sent"


def test_whole_file_context_never_reads_outside_the_repo(tmp_path):
    from orion.review import _read_in_repo

    (tmp_path / "repo").mkdir()
    (tmp_path / "outside.txt").write_text("secret\n")
    (tmp_path / "repo" / "in.txt").write_text("ok\n")
    assert _read_in_repo(tmp_path / "repo", "in.txt") == "ok\n"
    assert _read_in_repo(tmp_path / "repo", "../outside.txt") is None
    assert _read_in_repo(tmp_path / "repo", "missing.txt") is None
