"""Context for code that is not a data platform (owners, sensitive areas, fix history), and the command provider."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from orion.codectx import (
    AreasError,
    _codeowners_regex,
    code_context,
    file_history,
    load_areas,
    load_codeowners,
    owners_of,
)
from orion.diffparse import parse
from orion.providers import CLAUDE_CODE_COMMAND, CommandProvider, ProviderError, StubProvider, make_provider
from orion.review import FINDINGS_SCHEMA, review
from orion.store import Store
from orion.verdict import load_risk


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), "-c", "user.email=a@b", "-c", "user.name=a", *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def shop(tmp_path: Path) -> Path:
    """A small app repository: CODEOWNERS, a payments module with an earlier fix, and a docs file."""
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github/CODEOWNERS").write_text("# owners\n*                 @team/web\n/src/pay/         @team/payments @ana\ndocs/             \n")
    (tmp_path / "src/pay").mkdir(parents=True)
    (tmp_path / "src/pay/charge.py").write_text("def charge(cents):\n    return cents\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/notes.md").write_text("notes\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init")
    (tmp_path / "src/pay/charge.py").write_text("def charge(cents):\n    return max(cents, 0)\n")
    git(tmp_path, "commit", "-qam", "fix: never charge a negative amount")
    (tmp_path / "docs/notes.md").write_text("more notes\n")
    git(tmp_path, "commit", "-qam", "docs: fix a typo")
    return tmp_path


def test_codeowners_patterns_follow_github_rules():
    assert _codeowners_regex("/src/pay/").match("src/pay/charge.py")
    assert not _codeowners_regex("/src/pay/").match("lib/src/pay/charge.py")
    assert _codeowners_regex("docs/").match("docs/a/b.md")
    assert _codeowners_regex("*.js").match("web/app.js") and not _codeowners_regex("*.js").match("web/app.jsx")
    assert _codeowners_regex("/apps/*.py").match("apps/a.py") and not _codeowners_regex("/apps/*.py").match("apps/x/a.py")
    assert _codeowners_regex("**/logs").match("a/b/logs/x.txt")
    assert _codeowners_regex("Makefile").match("tools/Makefile")
    assert _codeowners_regex("/src/*").match("src/a.py") and not _codeowners_regex("/src/*").match("src/legacy/x.py"), "as on GitHub"
    assert not _codeowners_regex("docs/").match("docs"), "a trailing slash names a folder"


def test_last_matching_codeowners_rule_wins_and_an_empty_rule_clears_owners(shop):
    place, rules = load_codeowners(shop)
    assert place == ".github/CODEOWNERS"
    assert owners_of(rules, "src/pay/charge.py").owners == ["@team/payments", "@ana"]
    assert owners_of(rules, "src/pay/charge.py").line == 3
    assert owners_of(rules, "README.md").owners == ["@team/web"]
    assert owners_of(rules, "docs/notes.md") is None
    (shop / "CODEOWNERS").write_text("*\t# nobody yet\n")
    place, rules = load_codeowners(shop)
    assert place == ".github/CODEOWNERS", "GitHub reads .github/ first"
    (shop / ".github/CODEOWNERS").unlink()
    place, rules = load_codeowners(shop)
    assert (place, owners_of(rules, "a.py")) == ("CODEOWNERS", None), "a comment after a tab is not an owner"


def test_areas_load_from_yaml_or_a_project_keyed_json_file(tmp_path):
    (tmp_path / "areas.yaml").write_text("areas:\n  - id: PAY\n    paths: [src/pay/]\n    reason: moves money\n    severity: warn\n    approvers: ['@ana']\n")
    (tmp_path / "risky.json").write_text(json.dumps({"_about": "notes", "shop": [{"pattern": "^src/pay/", "why": "moves money"}], "other": []}))
    (area,) = load_areas("areas.yaml", tmp_path)
    assert (area.id, area.severity, area.approvers, area.matches("src/pay/charge.py"), area.matches("src/web.py")) == ("PAY", "warn", ["@ana"], True, False)
    (keyed,) = load_areas(str(tmp_path / "risky.json") + "#shop")
    assert (keyed.id, keyed.severity, keyed.reason, keyed.source) == ("SHOP-1", "inform", "moves money", "risky.json#shop")
    with pytest.raises(AreasError, match="name one with file#project"):
        load_areas("risky.json", tmp_path)
    with pytest.raises(AreasError, match="no areas for nope"):
        load_areas("risky.json#nope", tmp_path)
    (tmp_path / "bad.yaml").write_text("- pattern: '['\n  why: x\n")
    with pytest.raises(AreasError, match="does not compile"):
        load_areas("bad.yaml", tmp_path)
    (tmp_path / "silent.yaml").write_text("- pattern: x\n")
    with pytest.raises(AreasError, match="why it is sensitive"):
        load_areas("silent.yaml", tmp_path)
    (tmp_path / "typed.yaml").write_text("- pattern: [a, b]\n  why: x\n")
    with pytest.raises(AreasError, match="pattern is not a string"):
        load_areas("typed.yaml", tmp_path)
    (tmp_path / "one.yaml").write_text("- paths: src/\n  why: x\n  approvers: '@ana'\n")
    assert load_areas("one.yaml", tmp_path)[0].approvers == ["@ana"], "one approver written as a string is not split into letters"
    (tmp_path / "globs.yaml").write_text("- _note: keep this current\n- paths: ['api/*.py', 'web/**/auth.ts']\n  why: x\n")
    (globbed,) = load_areas("globs.yaml", tmp_path)
    assert globbed.matches("api/auth.py") and not globbed.matches("api/internal/cache.py"), "* stays inside one folder"
    assert globbed.matches("web/auth.ts") and globbed.matches("web/a/b/auth.ts") and not globbed.matches("lib/api/auth.py")


def test_history_lists_only_fixes_and_reverts_of_the_changed_files(shop):
    found = file_history(shop, ["src/pay/charge.py"], "HEAD")
    assert [h["subject"] for h in found] == ["fix: never charge a negative amount"]
    assert found[0]["paths"] == ["src/pay/charge.py"]
    assert file_history(shop, ["src/pay/charge.py"], "HEAD~2") == [], "history stops at the given revision"
    assert file_history(shop / "no-repo", ["x"], None) is None, "git could not be read: not the same as no history"
    out = shop / "leaked.txt"
    assert file_history(shop, ["src/pay/charge.py"], f"--output={out}") is None
    assert not out.exists(), "a revision is never read as an option"


def test_history_matches_paths_with_pattern_characters_and_non_ascii_names(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    names = ["pages/[id].tsx", "app/café.py"]
    for name in names:
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text("a\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init")
    for name in names:
        (tmp_path / name).write_text("b\n")
    git(tmp_path, "commit", "-qam", "fix: both pages")
    assert file_history(tmp_path, names, None)[0]["paths"] == sorted(names)


def _change(shop: Path) -> str:
    (shop / "src/pay/charge.py").write_text("def charge(cents):\n    return cents\n")
    return git(shop, "diff", "HEAD")


def test_review_of_an_app_change_cites_its_area_owners_and_history(shop, tmp_path):
    (tmp_path / "risky.json").write_text(json.dumps({"shop": [{"pattern": "^src/pay/", "why": "moves money", "severity": "warn"}]}))
    areas = load_areas(str(tmp_path / "risky.json") + "#shop")
    fix = git(shop, "rev-parse", "HEAD~1")[:12]
    _change(shop)
    seen = {}

    def respond(system, user):
        seen["user"] = user
        return {"findings": [{"path": "src/pay/charge.py", "line": 2, "severity": "warn", "category": "correctness", "title": "Negative charges are back", "body": "b", "failure": "charge(-5) returns -5", "evidence": [f"commit:{fix}", "area:SHOP-1", "area:SHOP-9"]}]}

    r = review(Store(":memory:"), shop, StubProvider(respond), constraints=[], risk_model=load_risk(None), base_ref="HEAD", ignore=["*"], areas=areas, second_reading=False, lenses=[])
    assert "area:SHOP-1 (warn) covers src/pay/charge.py: moves money" in seen["user"]
    assert "owner of src/pay/charge.py: @team/payments @ana (ref:.github/CODEOWNERS:3)" in seen["user"]
    assert f"commit:{fix}" in seen["user"] and "never charge a negative amount" in seen["user"]
    det, model = r["findings"][0], r["findings"][1]
    assert (det["source"], det["severity"], det["title"]) == ("deterministic", "warn", "Touches sensitive area SHOP-1: src/pay/charge.py")
    assert det["evidence"] == ["area:SHOP-1", "hunk:src/pay/charge.py:2", "ref:.github/CODEOWNERS:3"]
    assert "Owners: @team/payments @ana." in det["body"]
    assert model["evidence"] == [f"commit:{fix}", "area:SHOP-1"], "an area or commit not in the facts does not resolve"
    assert r["context"]["areas"][0]["id"] == "SHOP-1" and r["context"]["history"][0]["commit"].startswith(fix)


def test_without_codeowners_or_areas_the_owner_is_not_recorded(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "a.py").write_text("A = 1\n")
    git(tmp_path, "add", "a.py")
    git(tmp_path, "commit", "-qm", "init")
    files = parse("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-A = 1\n+A = 2\n")
    ctx = code_context(tmp_path, files, [], "HEAD")
    from orion.codectx import facts

    assert facts(ctx) == ["- owners: not recorded (the repository has no CODEOWNERS file)", "- history: no fix, revert or incident commit among the last 300 commits that touched these files"]
    unread = code_context(tmp_path / "not-a-repo", files, [], None)
    assert facts(unread) == ["- owners: not recorded (the repository has no CODEOWNERS file)"], "git could not be read: claim nothing about history"


def test_an_area_points_at_a_file_with_a_changed_line(tmp_path):
    from orion.codectx import Area

    files = parse("diff --git a/api/x b/api/x\nold mode 100644\nnew mode 100755\ndiff --git a/api/y.py b/api/y.py\n--- a/api/y.py\n+++ b/api/y.py\n@@ -1 +1 @@\n-A = 1\n+A = 2\n")
    (hit,) = code_context(tmp_path, files, [Area(id="API", reason="r", paths=["api/"])], history=False).areas
    assert (hit["paths"], hit["line"]) == (["api/y.py", "api/x"], 1)


def test_cli_review_reads_areas_from_a_flag(shop, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from orion.cli import app

    (tmp_path / "areas.yaml").write_text("- id: PAY\n  paths: [src/pay/]\n  why: moves money\n")
    _change(shop)
    monkeypatch.chdir(shop)
    res = CliRunner().invoke(app, ["review", "--git", "HEAD", "--format", "json", "--areas", str(tmp_path / "areas.yaml")])
    assert res.exit_code == 0, res.output
    r = json.loads(res.stdout[res.stdout.index("{"):])
    assert [f["title"] for f in r["findings"]] == ["Touches sensitive area PAY: src/pay/charge.py"]
    bad = CliRunner().invoke(app, ["review", "--git", "HEAD", "--areas", str(tmp_path / "missing.yaml")])
    assert bad.exit_code == 2 and "cannot read areas file" in bad.output


# A stand-in for an agent's CLI: echoes what it was given inside a harness style result envelope.
FAKE_AGENT = r"""
import json, sys
args = sys.argv[1:]
prompt = sys.stdin.read()
if "--fail" in args:
    print("signed out", file=sys.stderr); sys.exit(3)
if "--limit" in args:
    print(json.dumps({"type": "result", "usage": {"output_tokens": 0, "server_tool_use": {"web_search_requests": 0}} | {f"pad{i}": 0 for i in range(40)}, "result": "You've hit your limit"})); sys.exit(1)
answer = {"findings": [], "args": args, "prompt": prompt}
if "--plain" in args:
    print("Here you go:\n" + json.dumps(answer)); sys.exit(0)
print(json.dumps({"type": "result", "is_error": "--error" in args, "result": "credit too low" if "--error" in args else "", "structured_output": answer, "usage": {"output_tokens": 7}}))
"""


@pytest.fixture
def agent(tmp_path: Path) -> list[str]:
    script = tmp_path / "agent.py"
    script.write_text(FAKE_AGENT)
    return [sys.executable, str(script)]


def test_command_provider_fills_arguments_and_reads_the_envelope(agent):
    p = CommandProvider([*agent, "--schema", "{schema}", "--system", "{system}", "--model", "{model}", "--effort", "{effort}"], model="m1", label="claude-code")
    out = p.complete_json("SYS", ["shared", "focus"], FINDINGS_SCHEMA)
    assert out["args"] == ["--schema", json.dumps(FINDINGS_SCHEMA), "--system", "SYS", "--model", "m1"], "an empty {effort} drops its flag"
    assert CommandProvider([*agent, "{system}"], model="m1").complete_json("say {model} here", "u", {})["args"] == ["say {model} here"]
    assert out["prompt"].startswith("shared\n\nfocus") and "matches this JSON Schema" in out["prompt"] and "SYS" not in out["prompt"]
    assert p.name == "claude-code:m1" and p.last_usage == {"output_tokens": 7}


def test_command_provider_without_a_system_argument_puts_it_on_stdin_and_reads_plain_text(agent):
    out = CommandProvider([*agent, "--plain"]).complete_json("SYS", "user", FINDINGS_SCHEMA)
    assert out["prompt"].startswith("SYS\n\nuser")


def test_a_command_runs_where_orion_runs_unless_isolated(agent, tmp_path, monkeypatch):
    monkeypatch.setenv("ORION_ALLOW_COMMAND", "1")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools/agent.py").write_text(Path(agent[1]).read_text())
    monkeypatch.chdir(tmp_path)
    assert CommandProvider([sys.executable, "tools/agent.py"]).complete_json("s", "u", {})["findings"] == [], "a relative path in review.command works"
    with pytest.raises(ProviderError, match="exited 2"):
        CommandProvider([sys.executable, "tools/agent.py"], isolate=True, retries=0).complete_json("s", "u", {})
    assert make_provider("claude-code").isolate and not make_provider("command", command=["x"]).isolate


def test_command_provider_errors_are_provider_errors(agent, monkeypatch):
    with pytest.raises(ProviderError, match="exited 3: signed out"):
        CommandProvider([*agent, "--fail"], retries=0).complete_json("s", "u", {})
    with pytest.raises(ProviderError, match="exited 1: You've hit your limit"):
        CommandProvider([*agent, "--limit"], retries=0).complete_json("s", "u", {})
    with pytest.raises(ProviderError, match="reported an error: credit too low"):
        CommandProvider([*agent, "--error"]).complete_json("s", "u", {})
    with pytest.raises(ProviderError, match="not found"):
        CommandProvider(["/no/such/agent"]).complete_json("s", "u", {})
    monkeypatch.delenv("ORION_ALLOW_COMMAND", raising=False)
    with pytest.raises(ProviderError, match="ORION_ALLOW_COMMAND=1"):
        make_provider("command", command=["./from-orion-yaml"])
    monkeypatch.setenv("ORION_ALLOW_COMMAND", "1")
    with pytest.raises(ProviderError, match="needs a command"):
        make_provider("command")


def test_claude_code_preset_runs_claude_print_mode_with_no_tools(monkeypatch):
    p = make_provider("claude-code", model="claude-sonnet-5-5", effort="high")
    args = p.args("SYS", {"type": "object"})
    assert args[:2] == ["claude", "-p"] and args[args.index("--tools") + 1] == "" and "--no-session-persistence" in args
    assert args[args.index("--model") + 1] == "claude-sonnet-5-5" and args[args.index("--effort") + 1] == "high"
    assert "--model" not in make_provider("claude-code").args("SYS", {}), "no model: the CLI's own default"
    assert make_provider("claude-code", command=["./my-agent"]).command == CLAUDE_CODE_COMMAND, "review.command never replaces the preset"
    assert CLAUDE_CODE_COMMAND[0] == "claude"
