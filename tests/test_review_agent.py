"""Agentic review: the read-only tools, the turn loop, and how what the agent read becomes evidence."""

import json
import subprocess
from pathlib import Path

import pytest

from orion.providers import ProviderError, StubProvider
from orion.review import SECRET_PATH_RE, review
from orion.review_agent import Toolbox, investigate
from orion.risk import RiskModel
from orion.store import Store

ROOT = Path(__file__).resolve().parents[1]

LIB = """def parse_amount(text):
    if not text:
        return 0
    return int(text)


def total(items):
    return sum(parse_amount(i) for i in items)
"""

CALLER = """from lib import total


def report(rows):
    return total(r["amount"] for r in rows)
"""


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "lib.py").write_text(LIB)
    (root / "app.py").write_text(CALLER)
    (root / ".env").write_text("TOKEN=abc\n")
    _git(root, "add", "lib.py", "app.py")
    _git(root, "commit", "-q", "-m", "fix: empty amounts parse as zero")
    (root / "lib.py").write_text(LIB.replace("    if not text:\n        return 0\n", ""))
    _git(root, "commit", "-q", "-am", "simplify parse_amount")
    return root


def _diff(root: Path) -> str:
    return _git(root, "diff", "HEAD~1", "HEAD")


def test_tools_read_search_and_record_what_they_showed(repo: Path):
    t = Toolbox(repo, base_ref="HEAD~1", secret_path=SECRET_PATH_RE)
    out = t.run("read", {"path": "app.py", "start": 4, "end": 5})
    assert "    4   def report(rows):" in out and "ref" in t.observed and "app.py:5" in t.observed["ref"]
    old = t.run("read", {"path": "lib.py", "side": "old"})
    assert "if not text" in old and "lib.py:2" not in t.observed["ref"]  # the old side is shown, never cited
    assert "not readable" in t.run("read", {"path": "../outside.py"})
    assert "not readable" in t.run("read", {"path": ".env"})
    grep = t.run("grep", {"pattern": "total\\(", "glob": "*.py"})
    assert "app.py:5:" in grep and "lib.py:5:" in grep
    d = t.run("definition", {"name": "parse_amount"})
    assert "lib.py: `parse_amount` at line 1" in d and "lib.py:2" in t.observed["ref"]
    refs = t.run("references", {"name": "total"})
    assert "app.py:1" in refs
    hist = t.run("history", {"path": "lib.py"})
    assert "fix: empty amounts parse as zero" in hist and len(t.observed["commit"]) == 2  # history before the change: the base and older
    full = next(c for c in t.observed["commit"] if len(c) == 40)
    assert "if not text" in t.run("history", {"path": "lib.py", "commit": full})
    assert t.run("history", {"commit": "--all"}) == "commit must be a hash"
    imp = t.run("blast_radius", {"target": "lib.py"})
    assert "app.py:1" in imp and "no system graph" in imp
    assert t.run("nope", {}).startswith("unknown tool")
    assert "app.py:5:" in t.run("grep", {"pattern": "total\\(", "path": "ignored", "start": 3})  # arguments a tool does not take are ignored
    (repo / "café.py").write_text("total = 2\n")
    _git(repo, "add", "café.py")
    assert "café.py:1:" in t.run("grep", {"pattern": "total = 2"}) and "café.py:1" in t.observed["ref"]
    assert t.run("read", {"path": "app.py", "start": "x"}).startswith("read failed")


def test_credential_files_stay_hidden_behind_symlinks_and_in_commits(repo: Path):
    (repo / "config.txt").symlink_to(".env")
    _git(repo, "add", "-f", ".env", "config.txt")
    _git(repo, "commit", "-q", "-m", "add config")
    t = Toolbox(repo, secret_path=SECRET_PATH_RE)
    assert "not readable" in t.run("read", {"path": "config.txt"})
    head = _git(repo, "rev-parse", "HEAD").strip()
    shown = t.run("history", {"commit": head})
    assert "TOKEN=abc" not in shown and "not shown: a file that may hold credentials" in shown


def test_only_what_fits_the_output_is_shown_and_citable(repo: Path):
    (repo / "big.py").write_text("".join(f"x_{i} = {'y' * 80}\n" for i in range(1, 201)))
    t = Toolbox(repo)
    out = t.run("read", {"path": "big.py"})
    assert "output limit; read from line" in out and len(out) <= 6000
    last = max(int(r.split(":")[1]) for r in t.observed["ref"] if r.startswith("big.py:"))
    assert f"{last:>5}   x_{last} =" in out and f"x_{last + 1} =" not in out
    assert t.run("read", {"path": "big.py", "start": 50, "end": 10}).splitlines()[1].startswith("   50")
    assert Toolbox(repo, base_ref="--output=x").run("read", {"path": "lib.py", "side": "old"}).startswith("lib.py: no such file")


def test_tool_output_is_redacted(repo: Path):
    (repo / "conf.py").write_text('KEY = "AKIAABCDEFGHIJKLMNOP"\n')  # pragma: allowlist secret
    out = Toolbox(repo).run("read", {"path": "conf.py"})
    assert "AKIAABCDEFGHIJKLMNOP" not in out  # pragma: allowlist secret


class Scripted:
    """A provider that plays one answer per turn and keeps what it was shown."""

    name = "scripted"

    def __init__(self, turns):
        self.turns, self.seen, self.schemas, self.last_usage = list(turns), [], [], {"input_tokens": 10, "output_tokens": 2}

    def complete_json(self, system, user, schema, *, schema_name="result"):
        self.seen.append((system, user, schema_name))
        self.schemas.append(schema)
        nxt = self.turns.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def _finding(evidence, line=1, severity="warn"):
    return {"path": "lib.py", "line": line, "severity": severity, "category": "correctness", "title": "empty amount now raises", "body": "int('') raises ValueError", "failure": "report([{'amount': ''}]) raises ValueError at app.py:5", "evidence": evidence}


def _review(repo: Path, provider, **kw):
    kw.setdefault("escalation", "model")  # these test the agent; the evidence gate has its own tests
    return review(Store(":memory:"), repo, provider, constraints=[], risk_model=RiskModel.load(None), base_ref="HEAD~1", diff_text=_diff(repo), ignore=["*"], second_reading=False, agent=True, **kw)


def test_agent_investigates_then_cites_what_it_read(repo: Path):
    p = Scripted([
        {"notes": "who calls total?", "done": False, "findings": [], "calls": [{"tool": "references", "args": {"name": "total"}}, {"tool": "read", "args": {"path": "app.py", "start": 1, "end": 5}}]},
        {"notes": "", "done": True, "calls": [], "findings": [_finding(["ref:app.py:5", "ref:app.py:99", "hunk:lib.py:2"])]},
    ])
    r = _review(repo, p)
    assert r["passes"] == ["agent"] and r["agent"]["tool_calls"] == 2 and r["agent"]["runs"][0]["stopped"] == "done"
    f = r["findings"][0]
    assert f["evidence"] == ["ref:app.py:5", "hunk:lib.py:2"] and f["severity"] == "warn"
    assert "ref:app.py:99" in f["notes"][0]  # a line no tool showed is not evidence
    # the second turn shows the tool output after the shared prefix, and the prefix is the pipeline's own message
    _, user, name = p.seen[1]
    assert name == "review_step" and len(user) == 3 and "Diff:" in user[0] and ">>> references" in user[1] and "app.py:5" in user[1]
    assert r["usage"]["input_tokens"] == 20


def test_without_reading_a_caller_the_ref_is_not_evidence(repo: Path):
    p = Scripted([{"notes": "", "calls": [{"tool": "grep", "args": {"pattern": "nothing_matches_this"}}]}, {"notes": "", "done": True, "calls": [], "findings": [_finding(["ref:app.py:5"])]}])
    r = _review(repo, p)
    assert not [f for f in r["findings"] if f["source"] == "model"]
    assert r["uncertain"][0]["title"] == "empty amount now raises"


def test_the_last_turn_must_answer(repo: Path):
    call = {"notes": "", "done": False, "findings": [], "calls": [{"tool": "grep", "args": {"pattern": "int"}}]}
    last = {"notes": "", "done": False, "findings": [_finding(["hunk:lib.py:2"])], "calls": [{"tool": "grep", "args": {"pattern": "x"}}]}
    p = Scripted([call, call, last])
    r = _review(repo, p, agent_steps=3)
    run = r["agent"]["runs"][0]
    assert run["stopped"] == "budget" and len(run["turns"]) == 3 and r["findings"][0]["title"] == "empty amount now raises"
    assert "This is the last turn" in p.seen[-1][1][-1]
    assert "calls" not in p.schemas[-1]["properties"] and "calls" in p.schemas[1]["properties"]
    assert set(p.schemas[0]["properties"]) == {"notes", "calls"}  # the opening turn investigates, it cannot report


def test_a_provider_error_keeps_the_deterministic_review(repo: Path):
    p = Scripted([{"notes": "", "done": False, "findings": [], "calls": [{"tool": "read", "args": {"path": "app.py"}}]}, ProviderError("rate limited")])
    r = _review(repo, p)
    assert r["provider_error"] == "rate limited" and r["agent"]["runs"][0]["stopped"] == "error"


def test_the_second_reading_sees_the_lines_an_agent_cited(repo: Path):
    p = Scripted([
        {"notes": "", "done": False, "findings": [], "calls": [{"tool": "read", "args": {"path": "app.py"}}]},
        {"notes": "", "done": True, "calls": [], "findings": [_finding(["ref:app.py:5", "hunk:lib.py:2"])]},
        {"verdicts": [{"index": 0, "holds": True, "kind": "defect", "reason": "it raises", "duplicate_of": -1}]},
    ])
    r = review(Store(":memory:"), repo, p, constraints=[], risk_model=RiskModel.load(None), base_ref="HEAD~1", diff_text=_diff(repo), ignore=["*"], agent=True, escalation="model")
    assert r["findings"][0]["severity"] == "warn"
    verify_user = p.seen[2][1]
    assert "Cited lines (read from the repository by the reviewer)" in verify_user and 'app.py:5:     return total(r["amount"] for r in rows)' in verify_user


def test_agent_tools_reach_the_system_graph_and_index(synced_store):
    t = Toolbox(ROOT, synced_store)
    out = t.run("blast_radius", {"target": "model:fct_orders"})
    assert out.startswith("node:model:fct_orders") and "downstream" in out and "node" in t.observed
    s = t.run("search", {"query": "events partitioned by ingest_ts"})
    assert s.startswith("chunk:ev_") and any(c.startswith("ev_") for c in t.observed["chunk"])


def test_investigate_with_a_stub_that_never_calls_a_tool():
    run = investigate(StubProvider(), "sys", "shared", "", Toolbox(ROOT), {"type": "object"})
    # A model that will not investigate on the opening turn is asked once more, as the last turn, and answers.
    assert run.stopped == "done" and run.candidates == [] and len(run.trace) == 2


def test_cli_agent_flag(synced_store, tmp_path):
    from test_review import DROP_DIFF
    from typer.testing import CliRunner

    from orion.cli import app

    cfg = ROOT / f".orion-test-{tmp_path.name}.yaml"
    text = (ROOT / "orion.yaml").read_text()
    assert "db: .orion/graph.duckdb" in text  # the test must point the CLI at the synced store, not the repository's graph
    cfg.write_text(text.replace("db: .orion/graph.duckdb", f"db: {synced_store.path}"))
    d = tmp_path / "change.diff"
    d.write_text(DROP_DIFF)
    synced_store.con.close()
    try:
        res = CliRunner().invoke(app, ["review", "--diff-file", str(d), "--format", "json", "--config", str(cfg), "--agent", "--agent-steps", "3"])
    finally:
        cfg.unlink()
        synced_store.con = __import__("duckdb").connect(str(synced_store.path))
    assert res.exit_code == 0, res.output
    out = json.loads(res.output)
    assert out["passes"] == ["agent"] and out["severity"] == "block" and out["agent"]["runs"][0]["stopped"] == "done"


def test_a_token_budget_makes_the_next_turn_the_last(repo: Path):
    class Hungry(Scripted):
        def complete_json(self, system, user, schema, *, schema_name="result"):
            self.last_usage = {"input_tokens": 1000}
            return super().complete_json(system, user, schema, schema_name=schema_name)

    call = {"notes": "", "done": False, "findings": [], "calls": [{"tool": "grep", "args": {"pattern": "int"}}]}
    p = Hungry([call, {"notes": "", "done": True, "calls": [], "findings": []}])
    run = investigate(p, "sys", "shared", "", Toolbox(repo), {"type": "object"}, max_steps=6, max_input_tokens=500)
    assert len(run.trace) == 2 and "This is the last turn" in p.seen[1][1][-1] and run.seconds >= 0


def test_a_repeated_tool_call_runs_once(repo: Path):
    t = Toolbox(repo)
    first = t.run("grep", {"pattern": "total"})
    (repo / "more.py").write_text("total = 1\n")
    _git(repo, "add", "more.py")
    assert t.run("grep", {"pattern": "total"}) == first


def test_agent_steps_in_config_are_bounded():
    from pydantic import ValidationError

    from orion.config import ReviewConfig

    assert ReviewConfig(agent_steps=6).agent_steps == 6
    with pytest.raises(ValidationError):
        ReviewConfig(agent_steps=500)


def test_calls_past_the_turn_limit_are_reported_not_run(repo: Path):
    many = {"notes": "", "calls": [{"tool": "grep", "args": {"pattern": f"p{i}"}} for i in range(8)]}
    p = Scripted([many, {"notes": "", "done": True, "calls": [], "findings": []}])
    r = _review(repo, p)
    assert len(r["agent"]["runs"][0]["turns"][0]["calls"]) == 6
    assert "2 more call(s) past the limit of 6" in p.seen[1][1][1]


def test_grep_keeps_hits_in_a_file_whose_name_has_a_colon(repo: Path):
    (repo / "a:b.py").write_text("x = parse_amount('1')\n")
    _git(repo, "add", "a:b.py")
    t = Toolbox(repo, secret_path=SECRET_PATH_RE)
    out = t.run("grep", {"pattern": "parse_amount\\('1'\\)"})
    assert "a:b.py:1: x = parse_amount('1')" in out and "a:b.py:1" in t.observed["ref"]


def test_a_merge_commits_credential_sections_are_hidden(repo: Path):
    t = Toolbox(repo, secret_path=SECRET_PATH_RE)
    patch = "commit abc\n\ndiff --cc .env\nindex 1,2..3\n+TOKEN=abc\ndiff --cc lib.py\n+x = 1\n"
    out = t._without_secret_files(patch)
    assert "TOKEN=abc" not in out and "diff --cc .env\n(not shown" in out and "+x = 1" in out


def test_credential_sections_with_spaces_or_quotes_in_the_name_are_hidden(repo: Path):
    t = Toolbox(repo)
    patch = (
        "diff --git a/deploy keys/.env b/deploy keys/.env\nnew file mode 100644\n--- /dev/null\n+++ b/deploy keys/.env\n+API_SECRET=plainvalue\n"
        'diff --git "a/q\\tx/.env" "b/q\\tx/.env"\n+++ "b/q\\tx/.env"\n+X=quoted\n'
        "diff --git a/lib.py b/lib.py\n+y = 2\n"
    )
    out = t._without_secret_files(patch)
    assert "plainvalue" not in out and "X=quoted" not in out and "+y = 2" in out


def test_a_reply_that_is_not_an_object_is_an_error_not_a_finish(repo: Path):
    p = Scripted([{"notes": "", "calls": [{"tool": "grep", "args": {"pattern": "int"}}]}, ["not", "an", "object"]])
    r = _review(repo, p)
    assert r["agent"]["runs"][0]["stopped"] == "error" and "not a JSON object" in r["provider_error"]
