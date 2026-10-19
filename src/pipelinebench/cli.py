"""The ``pipelinebench`` command."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from orion.config import load_config
from orion.store import Store

from .agents import make_agent
from .conditions import CONDITIONS
from .generate import write as write_scenarios
from .runner import check_ground_truth, report, run, write_results
from .scenarios import load_scenarios

app = typer.Typer(help="PipelineBench: does context make agents safer on data pipelines?", no_args_is_help=True)
console = Console()
ROOT = Path(__file__).resolve().parents[2]
SCENARIOS = ROOT / "pipelinebench" / "scenarios"
RESULTS = ROOT / "pipelinebench" / "results"
REVIEW_CASES = ROOT / "pipelinebench" / "review" / "cases.yaml"


def _store(config: Path | None, need_graph: bool = True) -> tuple[Path, Store]:
    """The platform's root and store. The rag and orion conditions and the ground-truth check read the graph, so an
    unsynced store is refused rather than letting them run on nothing (which looks like a result)."""
    cfg = load_config(config, cwd=ROOT)
    store = Store(cfg.path(cfg.db))
    if need_graph and not store.counts()["nodes"]:
        console.print(f"[red]the graph at {cfg.path(cfg.db)} is empty; run `orion sync -c {cfg.root / 'orion.yaml'}` first[/red]")
        raise typer.Exit(2)
    return cfg.root, store


@app.command()
def generate(directory: Path = typer.Option(SCENARIOS, "--dir")):
    """Write the sixty scenario files from the generator (ground truth is hand-written there)."""
    paths = write_scenarios(directory)
    console.print(f"wrote {len(paths)} scenarios to {directory}")


@app.command("list")
def list_cmd(directory: Path = typer.Option(SCENARIOS, "--dir")):
    t = Table("id", "family", "expected", "cite", "title")
    for s in load_scenarios(directory):
        t.add_row(s.id, s.family, s.expected.decision, ",".join(s.expected.must_cite), s.title)
    console.print(t)


@app.command("check-ground-truth")
def check(directory: Path = typer.Option(SCENARIOS, "--dir"), config: Path | None = typer.Option(None, "--config", "-c"), as_json: bool = typer.Option(False, "--json")):
    """Compare Orion's verdicts to the hand-written expectations; list every disagreement."""
    root, store = _store(config)
    rows = check_ground_truth(load_scenarios(directory), repo_root=root, store=store)
    if as_json:
        typer.echo(json.dumps(rows, indent=1))
        return
    agree = sum(r["agree"] for r in rows)
    for r in rows:
        if not r["agree"]:
            console.print(f"[yellow]{r['scenario']}[/yellow] {r['title']}: expected {r['expected']}, orion {r['orion']}; fired {r['fired']}; missing cites {r['missing_cites']}; missing names {r['missing_names']}; unresolved {r['unresolved']}")
    console.print(f"{agree}/{len(rows)} scenarios agree with Orion")


@app.command("run")
def run_cmd(
    agent: list[str] = typer.Option(["scripted"], "--agent", help="scripted | claude[:model] | command:<exe>"),
    condition: list[str] = typer.Option(["all"], "--condition", help="none | repo | rag | orion | all"),
    seeds: int = typer.Option(1, "--seeds"),
    name: str | None = typer.Option(None, "--name", help="Results file name (default: timestamp)."),
    directory: Path = typer.Option(SCENARIOS, "--dir"),
    out: Path = typer.Option(RESULTS, "--out"),
    config: Path | None = typer.Option(None, "--config", "-c"),
):
    """Run agents across conditions and write results JSON and a markdown report."""
    unknown = sorted(set(condition) - set(CONDITIONS) - {"all"})
    if unknown:
        console.print(f"[red]unknown condition {', '.join(unknown)}; use one of {', '.join(CONDITIONS)} or all[/red]")
        raise typer.Exit(2)
    conds = list(CONDITIONS) if "all" in condition else list(dict.fromkeys(condition))
    root, store = _store(config, need_graph=bool({"rag", "orion"} & set(conds)))
    agents = [make_agent(a) for a in agent]
    scenarios = load_scenarios(directory)
    with console.status("running") as status:
        res = run(scenarios, agents, conds, repo_root=root, store=store, seeds=seeds, progress=lambda a, c, s, g: status.update(f"{a} / {c} / {s}: {g['decided']} ({'ok' if g['decision_correct'] else 'wrong'})"))
    j, m = write_results(res, out, name)
    console.print(report(res))
    console.print(f"wrote {j} and {m}")


@app.command("review-eval")
def review_eval_cmd(
    provider: str | None = typer.Option(None, "--provider", help="stub | anthropic | openai (default from orion.yaml)."),
    model: str | None = typer.Option(None, "--model"),
    repeat: int = typer.Option(1, "--repeat", help="Run every case this many times."),
    file_context: bool = typer.Option(True, "--file-context/--no-file-context", help="Send whole changed files to the model, not just the hunks."),
    second_reading: bool = typer.Option(True, "--second-reading/--no-second-reading", help="Check model warn/block findings with a second call."),
    verify_model: str | None = typer.Option(None, "--verify-model", help="Model for the second reading (same provider); default: the review model."),
    effort: str | None = typer.Option(None, "--effort", help="Model effort for the review passes (Anthropic)."),
    verify_effort: str | None = typer.Option(None, "--verify-effort", help="Model effort for the second reading (Anthropic)."),
    case: list[str] = typer.Option([], "--case", help="Only these case ids."),
    cases_file: Path = typer.Option(REVIEW_CASES, "--cases"),
    name: str | None = typer.Option(None, "--name", help="Results file name (default: timestamp)."),
    out: Path = typer.Option(RESULTS, "--out"),
    config: Path | None = typer.Option(None, "--config", "-c"),
):
    """Score `orion review` on hand-labelled diffs: bugs caught, false alarms, tokens."""
    from datetime import UTC, datetime

    from orion.cli import _ignore, _scope
    from orion.constraints import load_constraints
    from orion.providers import make_provider
    from orion.verdict import load_risk

    from .review_eval import load_cases, run_eval
    from .review_eval import to_markdown as eval_md

    cfg = load_config(config, cwd=ROOT)
    store = Store(cfg.path(cfg.db))
    rc = cfg.review
    prov = make_provider(provider or rc.provider, model=model or rc.model, base_url=rc.base_url, api_key_env=rc.api_key_env, effort=effort)
    verifier = make_provider(provider or rc.provider, model=verify_model or model or rc.model, base_url=rc.base_url, api_key_env=rc.api_key_env, effort=verify_effort or effort) if verify_model or verify_effort else None
    cases = [c for c in load_cases(cases_file, cfg.root) if not case or c.id in case]
    constraints, _ = load_constraints(cfg.path(cfg.constraints)) if cfg.constraints else ([], [])
    with console.status("reviewing") as status:
        res = run_eval(cases, prov, store=store, repo_root=cfg.root, constraints=constraints, risk_model=load_risk(cfg.path(cfg.risk) if cfg.risk else None), scope=_scope(cfg), ignore=_ignore(cfg), repeat=repeat, file_context=file_context, second_reading=second_reading, verifier=verifier, progress=lambda g: status.update(f"{g['case']}: {g['caught']}/{g['expected']} caught, {len(g['false_alarms'])} false alarms"))
    out.mkdir(parents=True, exist_ok=True)
    stem = name or "review-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    (out / f"{stem}.json").write_text(json.dumps(res, indent=1, default=str))
    md = eval_md(res)
    (out / f"{stem}.md").write_text(md)
    console.print(md)
    console.print(f"wrote {out / stem}.json and .md")


@app.command("replay-eval")
def replay_eval_cmd(
    workdir: Path = typer.Option(..., "--workdir", help="Where the repositories are cloned (reused between runs)."),
    provider: str = typer.Option("stub", "--provider"),
    model: str | None = typer.Option(None, "--model"),
    repo: list[str] = typer.Option([], "--repo", help="Only these repositories."),
    cases_file: Path = typer.Option(REVIEW_CASES.parent / "replay.yaml", "--cases"),
    name: str | None = typer.Option(None, "--name", help="Results file name (default: timestamp)."),
    out: Path = typer.Option(RESULTS, "--out"),
):
    """Replay merged commits from public repositories through `orion review` and count what it warns about."""
    import shutil
    from datetime import UTC, datetime

    from .replay_eval import load as load_replay
    from .replay_eval import run as run_replay
    from .replay_eval import to_markdown as replay_md

    repos, cases = load_replay(cases_file)
    unknown = sorted(set(repo) - set(repos))
    if unknown:
        console.print(f"[red]unknown repository {', '.join(unknown)}; use one of {', '.join(sorted(repos))}[/red]")
        raise typer.Exit(2)
    cases = [c for c in cases if not repo or c.repo in repo]
    orion = shutil.which("orion") or str(Path(sys.executable).with_name("orion"))
    workdir.mkdir(parents=True, exist_ok=True)
    with console.status("replaying") as status:
        res = run_replay(cases, repos, workdir.resolve(), orion=orion, provider=provider, model=model, progress=lambda r: status.update(f"{r['repo']} {r['commit']}: {len(r['warns'])} warnings"))
    out.mkdir(parents=True, exist_ok=True)
    stem = name or "replay-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    (out / f"{stem}.json").write_text(json.dumps(res, indent=1, default=str))
    md = replay_md(res)
    (out / f"{stem}.md").write_text(md)
    console.print(md)
    console.print(f"wrote {out / stem}.json and .md")


@app.command("recall-eval")
def recall_eval_cmd(
    workdir: Path = typer.Option(..., "--workdir", help="Where the repositories are cloned (reused between runs)."),
    provider: str = typer.Option("stub", "--provider"),
    model: str | None = typer.Option(None, "--model"),
    orion: str | None = typer.Option(None, "--orion", help="The `orion` executable to run (default: this environment's), so an older build can be measured."),
    repo: list[str] = typer.Option([], "--repo", help="Only these repositories."),
    case: list[str] = typer.Option([], "--case", help="Only these introducing commits."),
    cases_file: Path = typer.Option(REVIEW_CASES.parent / "recall.yaml", "--cases"),
    review_arg: list[str] = typer.Option([], "--review-arg", help="Extra argument passed to `orion review` (repeatable)."),
    name: str | None = typer.Option(None, "--name", help="Results file name (default: timestamp)."),
    out: Path = typer.Option(RESULTS, "--out"),
    raw_dir: Path | None = typer.Option(None, "--raw-dir", help="Keep each case's full review JSON here (not committed), to read a miss without another model run."),
    jobs: int = typer.Option(1, "--jobs", help="Cases reviewed at once, each in its own worktree of the clone."),
    judge_model: str | None = typer.Option(None, "--judge-model", help="Judge 'about the bug' with this model (same provider) reading each case's hand-written label, instead of the must_mention words."),
    rejudge_dir: Path | None = typer.Option(None, "--rejudge", help="Judge the raw reviews kept in this folder again instead of reviewing (no review calls)."),
):
    """Review real bug-introducing commits from public repositories and count how many bugs the review catches."""
    import shutil
    from datetime import UTC, datetime

    from .recall_eval import load as load_recall
    from .recall_eval import run as run_recall
    from .recall_eval import to_markdown as recall_md

    repos, cases = load_recall(cases_file)
    cases = [c for c in cases if (not repo or c.repo in repo) and (not case or any(c.commit.startswith(x) for x in case))]
    exe = orion or shutil.which("orion") or str(Path(sys.executable).with_name("orion"))
    from orion.providers import make_provider

    judge_provider = make_provider(provider, model=judge_model, effort="medium") if judge_model else None
    if rejudge_dir:
        from .recall_eval import rejudge, summarise

        rows = rejudge(cases, rejudge_dir, judge_provider)
        res = {"provider": provider, "model": model, "args": review_arg, "rejudged": rejudge_dir.name, "judge_model": judge_model, "cases": rows, "skipped": [], "summary": summarise(rows)}
    else:
        workdir.mkdir(parents=True, exist_ok=True)
        with console.status("reviewing") as status:
            res = run_recall(cases, repos, workdir.resolve(), orion=exe, provider=provider, model=model, extra_args=review_arg, raw_dir=raw_dir, jobs=jobs, judge_provider=judge_provider, progress=lambda r: status.update(f"{r['repo']} {r['commit']}: {r['outcome']}"))
        res["judge_model"] = judge_model
    out.mkdir(parents=True, exist_ok=True)
    stem = name or "recall-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    (out / f"{stem}.json").write_text(json.dumps(res, indent=1, default=str))
    md = recall_md(res)
    (out / f"{stem}.md").write_text(md)
    console.print(md)
    console.print(f"wrote {out / stem}.json and .md")


@app.command("mine-introducers")
def mine_introducers_cmd(
    clone: Path = typer.Argument(..., help="A clone of the repository (a full clone is much faster than a blob-less one)."),
    repo: str = typer.Option(..., "--repo", help="The repository's name in recall.yaml."),
    limit: int = typer.Option(400, "--limit", help="How many recent commits to scan for fixes."),
    max_lines: int = typer.Option(400, "--max-lines", help="Largest introducing commit to keep, in changed source lines."),
    out: Path | None = typer.Option(None, "--out", help="Write candidates as YAML here (default: print)."),
):
    """List commits that introduced the lines later fix commits changed: candidates for recall.yaml, to read and label by hand."""
    import yaml

    from .introducers import mine

    with console.status("blaming") as status:
        found = mine(clone.resolve(), repo, limit=limit, max_lines=max_lines, progress=lambda sha, subject, c: status.update(f"{sha} {subject[:60]}" + (" -> candidate" if c else "")))
    rows = [c.to_yaml() | {"share": c.share, "size": c.changed_lines} for c in found]
    text = yaml.safe_dump(rows, sort_keys=False, width=160, allow_unicode=True)
    if out:
        out.write_text(text)
        console.print(f"wrote {len(rows)} candidates to {out}")
    else:
        console.print(text)


@app.command("report")
def report_cmd(results: Path):
    console.print(report(json.loads(results.read_text())))


if __name__ == "__main__":
    app()
