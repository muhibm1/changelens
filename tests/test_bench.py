"""Slice 4: PipelineBench scenarios, graders, conditions and the scripted baseline."""

from pathlib import Path

from pipelinebench.agents import Decision, ScriptedAgent, parse_decision
from pipelinebench.conditions import build_context, load_platform
from pipelinebench.grader import aggregate, grade
from pipelinebench.runner import check_ground_truth, report, run
from pipelinebench.scenarios import known_ids, load_scenarios

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "pipelinebench" / "scenarios"


def test_sixty_scenarios_with_consistent_ground_truth():
    scenarios = load_scenarios(SCENARIOS)
    assert len(scenarios) == 60
    ids = known_ids(ROOT / "fixture")
    for s in scenarios:
        assert s.change.ops or s.change.intent, s.id
        assert set(s.expected.must_cite) <= ids, (s.id, s.expected.must_cite)
        if s.expected.decision == "escalate":
            assert "proceed" in s.expected.must_not
            assert any(c.startswith("CON-") for c in s.expected.must_cite), s.id
    families = {s.family for s in scenarios}
    assert {"locked_direct", "locked_via_lineage", "partition", "backfill", "schedule", "deprecated", "approval", "multi"} <= families


def test_grader_is_mechanical():
    s = load_scenarios(SCENARIOS)[0]  # drop discount_pct: escalate, cite CON-031 + INC-230, name dash_revenue_weekly
    ids = known_ids(ROOT / "fixture")
    perfect = grade(s, Decision(decision="escalate", cited=["CON-031", "INC-230"], affected=["dashboard:dash_revenue_weekly"], approvers=["team/finance-data"]), ids)
    assert perfect["score"] == 1.0 and perfect["decision_correct"] and not perfect["unsafe"]
    reckless = grade(s, Decision(decision="proceed"), ids)
    assert reckless["unsafe"] and reckless["score"] == 0.0
    fabricating = grade(s, Decision(decision="escalate", cited=["CON-031", "INC-230", "CON-999"], affected=["dash_revenue_weekly"], approvers=["team/finance-data"]), ids)
    assert fabricating["fabricated"] == ["CON-999"] and fabricating["score"] < perfect["score"]
    quoting = grade(s, Decision(decision="escalate", cited=["CON-031", "INC-230", "ev_78dbc605c406", "EV_78DBC605"], affected=["dash_revenue_weekly"], approvers=["team/finance-data"]), ids, "history: sla_missed — ev_78dbc605c406")
    assert quoting["fabricated"] == ["EV_78DBC605"], "an id quoted from the context is real; a fragment of one is not"
    agg = aggregate([perfect, reckless])
    assert agg["accuracy"] == 0.5 and agg["unsafe_rate"] == 0.5


def test_parse_decision_tolerates_prose():
    d = parse_decision('Sure. {"decision": "escalate", "cited": ["CON-031"], "affected": [], "approvers": ["team/finance-data"], "rationale": "locked"} done')
    assert d.decision == "escalate" and d.cited == ["CON-031"]
    assert parse_decision("I don't know").decision == "proceed"
    assert parse_decision('{"decision": "yolo"}').decision == "proceed"


def test_orion_agrees_with_every_scenario(synced_store):
    rows = check_ground_truth(load_scenarios(SCENARIOS), repo_root=ROOT, store=synced_store)
    disagreements = [r for r in rows if not r["agree"]]
    assert disagreements == [], disagreements


def test_conditions_carry_different_material(synced_store):
    s = next(x for x in load_scenarios(SCENARIOS) if x.family == "locked_via_lineage")
    constraints, risk = load_platform(ROOT, synced_store)
    none = build_context("none", s, repo_root=ROOT, store=synced_store)
    repo = build_context("repo", s, repo_root=ROOT, store=synced_store)
    rag = build_context("rag", s, repo_root=ROOT, store=synced_store)
    orion = build_context("orion", s, repo_root=ROOT, store=synced_store, constraints=constraints, risk=risk)
    assert none["text"] == ""
    assert "### file: fixture/orion/constraints/CON-031.yaml" in repo["text"]
    assert rag["chunks"] and "### chunk:" in rag["text"]
    assert orion["verdict"]["severity"] == "block" and "CON-031" in orion["text"]


def test_scripted_baseline_shows_the_gradient(synced_store):
    scenarios = load_scenarios(SCENARIOS)
    res = run(scenarios, [ScriptedAgent()], ["none", "repo", "rag", "orion"], repo_root=ROOT, store=synced_store)
    cells = {c["condition"]: c for c in res["cells"]}
    assert cells["orion"]["accuracy"] >= 0.95 and cells["orion"]["unsafe_rate"] == 0.0
    assert cells["none"]["unsafe_rate"] > 0.3
    assert cells["repo"]["accuracy"] < cells["orion"]["accuracy"]
    assert cells["rag"]["accuracy"] < cells["orion"]["accuracy"]
    lineage = [d for d in res["details"] if d["family"] == "locked_via_lineage"]
    by_cond = {}
    for d in lineage:
        by_cond.setdefault(d["condition"], []).append(d["decision_correct"])
    assert all(by_cond["orion"]) and not any(by_cond["repo"]) and not any(by_cond["rag"]), "only lineage catches upstream column changes"
    md = report(res)
    assert "| scripted | orion |" in md


def test_the_model_agent_goes_through_the_provider_and_counts_tokens(monkeypatch, synced_store):
    from orion import providers
    from pipelinebench.agents import ClaudeAgent

    sent = []

    def post(url, headers, body, **kw):
        sent.append(body)
        if len(sent) == 2:
            raise providers.ProviderError("down")
        return {"content": [{"type": "text", "text": '{"decision": "escalate", "cited": ["CON-031"], "affected": [], "approvers": [], "rationale": "r"}'}], "usage": {"input_tokens": 10, "output_tokens": 3}, "stop_reason": "end_turn"}

    monkeypatch.setattr(providers, "_post", post)
    monkeypatch.setenv("ORION_ANTHROPIC_API_KEY", "k")
    scenarios = load_scenarios(SCENARIOS)[:2]
    res = run(scenarios, [ClaudeAgent("claude-haiku-5-5")], ["none"], repo_root=ROOT, store=synced_store)  # a priced model: an unpriced one is refused
    assert sent[0]["output_config"]["format"]["schema"]["required"]
    first, second = [d for d in res["details"]]
    assert first["decided"] == "escalate" and second["decided"] == "proceed" and "provider error" in second["rationale"]
    assert res["usage"] == {"claude:claude-haiku-5-5": {"input_tokens": 10, "output_tokens": 3, "errors": 1}}
    assert "claude:claude-haiku-5-5: 10 tokens in, 3 out, 1 provider errors (each graded as proceed)" in report(res)


def test_run_refuses_an_empty_graph_for_conditions_that_read_it(tmp_path):
    from typer.testing import CliRunner

    from pipelinebench.cli import app

    (tmp_path / "orion.yaml").write_text("db: empty.duckdb\n")
    cfg = str(tmp_path / "orion.yaml")
    res = CliRunner().invoke(app, ["run", "--condition", "orion", "-c", cfg, "--out", str(tmp_path)])
    assert res.exit_code == 2 and "empty; run" in " ".join(res.output.split())
    res = CliRunner().invoke(app, ["run", "--condition", "none", "-c", cfg, "--out", str(tmp_path), "--name", "x"])
    assert res.exit_code == 0, res.output
    res = CliRunner().invoke(app, ["run", "--condition", "all", "--condition", "none", "-c", cfg, "--out", str(tmp_path)])
    assert res.exit_code == 2 and "empty; run" in " ".join(res.output.split())
    res = CliRunner().invoke(app, ["run", "--condition", "Orion", "-c", cfg, "--out", str(tmp_path)])
    assert res.exit_code == 2 and "unknown condition Orion" in " ".join(res.output.split())
