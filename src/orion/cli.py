"""The ``orion`` command: one binary for sync, explain, history and status."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .config import load_config
from .constraints import load_constraints
from .explain import explain as explain_asset
from .resolver import resolve_ref
from .store import Store
from .sync import run_sync

app = typer.Typer(help="Pre-flight checks for agents touching data pipelines.", no_args_is_help=True)
console = Console()


def _json(obj) -> str:
    return json.dumps(obj, indent=2, default=lambda o: o.isoformat() if isinstance(o, datetime) else str(o))


def _scope(cfg) -> list[str]:
    roots = []
    a = cfg.adapters
    if a.git and a.git.paths:
        roots += a.git.paths
    if a.dbt:
        roots.append(a.dbt.project)
    if a.airflow:
        roots.append(a.airflow.dags)
    return roots


def _ignore(cfg) -> list[str]:
    out = []
    if cfg.constraints:
        out.append(cfg.constraints)
    if cfg.adapters.incidents:
        out.append(cfg.adapters.incidents.dir)
    return out


def _open(config: Path | None):
    cfg = load_config(config)
    store = Store(cfg.path(cfg.db))
    return cfg, store


@app.command()
def sync(
    adapters: list[str] = typer.Argument(None, help="Subset of adapters to run (dbt, airflow, git, incidents, runs)."),
    config: Path | None = typer.Option(None, "--config", "-c"),
    force: bool = typer.Option(False, "--force", help="Re-read sources even when unchanged."),
):
    """Pull the asset graph and ledger from the configured sources."""
    cfg, store = _open(config)
    results = run_sync(cfg, store, only=adapters or None, force=force)
    t = Table("adapter", "nodes", "edges", "events", "status")
    for r in results:
        t.add_row(r.adapter, str(r.nodes), str(r.edges), str(r.events), "unchanged" if r.skipped else "synced")
    console.print(t)
    for r in results:
        for note in r.notes:
            console.print(f"[yellow]{r.adapter}[/]: {note}")
    c = store.counts()
    console.print(f"graph: {c['nodes']} nodes, {c['edges']} edges, {c['provenance']} provenance rows, {c['events']} events")


@app.command()
def explain(
    asset: str = typer.Argument(..., help="Asset name or ref, e.g. fct_orders, source:raw.raw_events, dag:orders_pipeline"),
    column: str | None = typer.Option(None, "--column"),
    as_json: bool = typer.Option(False, "--json"),
    config: Path | None = typer.Option(None, "--config", "-c"),
):
    """What an asset is, who owns it, what depends on it, and what has happened to it."""
    cfg, store = _open(config)
    constraints, problems = load_constraints(cfg.path(cfg.constraints)) if cfg.constraints else ([], [])
    out = explain_asset(store, asset, constraints, column)
    if as_json:
        typer.echo(_json(out))
        return
    if not out["resolved"]:
        console.print(f"[red]No asset matches '{asset}'.[/]")
        for c in out["candidates"]:
            console.print(f"  did you mean [bold]{c['id']}[/]?")
        raise typer.Exit(1)
    a = out["asset"]
    console.print(f"[bold]{a['id']}[/]  tier={a['tier']}  owner={a['owner']}  graph as of {out['graph_as_of']}")
    if a["properties"].get("description"):
        console.print(f"  {a['properties']['description']}")
    if a["properties"].get("path"):
        console.print(f"  defined in {a['properties']['path']}")
    for p in a["provenance"][:2]:
        console.print(f"  provenance: {p['adapter']} ← {p['source']}")
    _list(console, "upstream", out["upstream"])
    _list(console, "downstream (1 hop)", out["downstream"])
    console.print(f"[bold]downstream total:[/] {out['downstream_total']}  tier-1 among them: {len(out['tier1_downstream'])}")
    for d in out["tier1_downstream"]:
        console.print(f"  [red]tier 1[/] {d['id']} ({d['hops']} hops, owner {d['owner']})")
    _list(console, "produced by", out["produced_by"])
    if out["columns"]:
        console.print("[bold]columns[/]")
        for c in out["columns"]:
            src = ", ".join(d["name"] for d in c["derives_from"]) or "—"
            used = len(c["used_by"])
            console.print(f"  {c['name']:<24} ← {src}   (used by {used} downstream column{'s' if used != 1 else ''})")
    if out["constraints"]:
        console.print("[bold]constraints[/]")
        for c in out["constraints"]:
            console.print(f"  [{ 'red' if c['severity']=='block' else 'yellow'}]{c['id']}[/] {c['kind']} ({c['severity']}): {c['reason']}")
    if out["recent_events"]:
        console.print("[bold]recent events[/]")
        for e in out["recent_events"]:
            label = e["payload"].get("title") or e["payload"].get("subject") or ""
            console.print(f"  {e['occurred_at']:%Y-%m-%d} {e['kind']:<18} {e['source_ref'] or ''} {label}  [{e['id']}]")
    for p in problems:
        console.print(f"[yellow]constraint file problem:[/] {p}")


def _list(console: Console, title: str, items: list[dict]) -> None:
    if not items:
        return
    console.print(f"[bold]{title}[/]")
    for i in items:
        tier = f" tier {i['tier']}" if i.get("tier") is not None else ""
        console.print(f"  {i['id']}{tier}  owner={i.get('owner')}  [{i.get('edge','')}]")


@app.command()
def history(
    asset: str,
    kinds: str | None = typer.Option(None, help="Comma-separated event kinds"),
    limit: int = 20,
    as_json: bool = typer.Option(False, "--json"),
    config: Path | None = typer.Option(None, "--config", "-c"),
):
    """Ledger events for an asset, newest first."""
    cfg, store = _open(config)
    r = resolve_ref(store, asset)
    if not r.ok:
        console.print(f"[red]No asset matches '{asset}'.[/]")
        raise typer.Exit(1)
    events = store.events_for([r.node_id], kinds.split(",") if kinds else None, limit=limit)
    if as_json:
        typer.echo(_json(events))
        return
    for e in events:
        label = e["payload"].get("title") or e["payload"].get("subject") or ""
        console.print(f"{e['occurred_at']:%Y-%m-%d %H:%M} {e['kind']:<18} {e['source_ref'] or '':<12} {label}  [{e['id']}]")


@app.command()
def status(config: Path | None = typer.Option(None, "--config", "-c")):
    """Counts and freshness of the local graph."""
    cfg, store = _open(config)
    from .sync import graph_as_of

    c = store.counts()
    console.print(f"db: {cfg.path(cfg.db)}")
    console.print(f"graph as of: {graph_as_of(store)}")
    console.print(f"{c['nodes']} nodes, {c['edges']} edges, {c['provenance']} provenance rows, {c['events']} events")
    kinds = store.con.execute("SELECT kind, count(*) FROM events GROUP BY 1 ORDER BY 1").fetchall()
    console.print("events: " + ", ".join(f"{k}={n}" for k, n in kinds))
    console.print(f"retrieval ranker: {cfg.retrieval.ranker}")


@app.command()
def preflight(
    base_ref: str | None = typer.Option(None, "--git", help="Diff the working tree against this ref, e.g. main or HEAD~1."),
    paths: list[str] | None = typer.Option(None, "--path", help="Paths you intend to change."),
    intent: str | None = typer.Option(None, "--intent", help="A sentence describing the change."),
    ops_file: Path | None = typer.Option(None, "--ops", help="JSON file with explicit ops [{kind, asset, column?, details?}]."),
    fmt: str = typer.Option("text", "--format", help="text | json | markdown"),
    no_record: bool = typer.Option(False, "--no-record", help="Do not write the verdict to the ledger."),
    config: Path | None = typer.Option(None, "--config", "-c"),
):
    """Check a proposed change before making it. Every claim in the verdict carries evidence ids."""
    from .changes import collect_changes
    from .verdict import load_risk, to_markdown
    from .verdict import preflight as run_preflight

    cfg, store = _open(config)
    constraints, problems = load_constraints(cfg.path(cfg.constraints)) if cfg.constraints else ([], [])
    ops = json.loads(ops_file.read_text()) if ops_file else None
    if not any([base_ref, paths, intent, ops]):
        console.print("[red]Give me something to check:[/] --git <ref>, --path <file>, --intent '<sentence>' or --ops <file>.")
        raise typer.Exit(2)
    cs = collect_changes(store, cfg.root, base_ref=base_ref, paths=paths, intent=intent, ops=ops, scope=_scope(cfg), ignore=_ignore(cfg))
    v = run_preflight(store, cs, constraints, load_risk(cfg.path(cfg.risk) if cfg.risk else None), record=not no_record)
    if fmt == "json":
        typer.echo(_json(v))
    elif fmt == "markdown":
        typer.echo(to_markdown(v))
    else:
        _print_verdict(v)
    for p in problems:
        console.print(f"[yellow]constraint file problem:[/] {p}")


def _print_verdict(v: dict) -> None:
    colour = {"inform": "green", "warn": "yellow", "block": "red"}[v["severity"]]
    console.print(f"[bold {colour}]{v['severity'].upper()}[/]  risk {v['risk']['score']}/100  {v['verdict_id']}  graph as of {v['graph_as_of']}" + ("  (reused)" if v.get("reused") else ""))
    console.print(v["summary"])
    if v["change"]["ops"]:
        console.print("[bold]change[/]")
        for o in v["change"]["ops"]:
            col = f".{o['column']}" if o.get("column") else ""
            flag = "  [dim](from intent)[/]" if o.get("from_intent") else ""
            console.print(f"  {o['kind']:<18} {o['asset']}{col} {escape(str(o.get('details') or ''))}{flag}")
    if v["change"]["unresolved"]:
        console.print("[bold yellow]unresolved[/]")
        for u in v["change"]["unresolved"]:
            console.print(f"  {escape(str(u))}")
    if v["affected"]:
        console.print(f"[bold]affected[/] ({len(v['affected'])})")
        for a in v["affected"][:12]:
            t = f"tier {a['tier']}" if a["tier"] is not None else "tier unset"
            name = f"[red]{a['asset']}[/red]" if a["tier"] == 1 else a["asset"]
            console.print(f"  {name} {t}, {a['owner'] or 'no owner'}, {a['hops']} hop{'s' if a['hops'] != 1 else ''} via {a['via']}  {escape('[' + ', '.join(a['evidence']) + ']')}")
        if len(v["affected"]) > 12:
            console.print(f"  … {len(v['affected']) - 12} more")
    fired = [c for c in v["constraints"] if c["status"] == "fires"]
    if fired:
        console.print("[bold]constraints[/]")
        for c in fired:
            console.print(f"  [{'red' if c['severity'] == 'block' else 'yellow'}]{c['id']}[/] {c['kind']} ({c['severity']}): {escape(c['detail'])}")
    if v["history"]:
        console.print("[bold]history[/]")
        for h in v["history"][:5]:
            console.print(f"  {h['occurred_at']:%Y-%m-%d} {h['kind']:<17} {h['ref'] or '':<9} {h['title'] or ''}  ({h['relevance']})  {escape('[' + h['event'] + ']')}")
    if v["checks"]:
        console.print("[bold]checks[/]")
        for c in v["checks"]:
            rest = {k: val for k, val in c.items() if k not in ("kind", "evidence")}
            console.print(f"  {c['kind']:<22} {escape(str(rest))}")
    console.print("[dim]risk: " + "; ".join(f"{f['name']}={f['value']} (+{f['contribution']})" for f in v["risk"]["factors"]) + "[/]")


@app.command()
def gate(
    base_ref: str = typer.Option("origin/main", "--git", help="Base ref to diff against."),
    fail_on: str = typer.Option("block", "--fail-on", help="Exit non-zero at this severity or above: warn | block"),
    fmt: str = typer.Option("markdown", "--format"),
    config: Path | None = typer.Option(None, "--config", "-c"),
):
    """CI entry point: run a pre-flight on the diff and exit non-zero at --fail-on. This is how a team makes the verdict binding."""
    from .changes import collect_changes
    from .verdict import load_risk, to_markdown
    from .verdict import preflight as run_preflight

    cfg, store = _open(config)
    constraints, _ = load_constraints(cfg.path(cfg.constraints)) if cfg.constraints else ([], [])
    cs = collect_changes(store, cfg.root, base_ref=base_ref, scope=_scope(cfg), ignore=_ignore(cfg))
    v = run_preflight(store, cs, constraints, load_risk(cfg.path(cfg.risk) if cfg.risk else None))
    typer.echo(to_markdown(v) if fmt == "markdown" else _json(v))
    order = ["inform", "warn", "block"]
    if order.index(v["severity"]) >= order.index(fail_on):
        raise typer.Exit(1)


@app.command("record-decision")
def record_decision(
    verdict_id: str,
    decision: str = typer.Option(..., "--decision", help="approve | waive | reject"),
    by: str = typer.Option(..., "--by", help="Who decided, e.g. team/finance-data or a handle"),
    note: str = typer.Option("", "--note"),
    config: Path | None = typer.Option(None, "--config", "-c"),
):
    """Record a human decision on a verdict in the ledger."""
    from datetime import UTC, datetime

    from .model import Event, EventKind

    cfg, store = _open(config)
    v = store.get_verdict(verdict_id)
    if v is None:
        console.print(f"[red]unknown verdict {verdict_id}[/]")
        raise typer.Exit(1)
    assets = list(dict.fromkeys(v["change"]["touched"] + [a["asset"] for a in v["affected"]]))
    eid = store.append_event(Event(EventKind.DECISION_RECORDED, datetime.now(UTC), assets, source="orion record-decision", source_ref=verdict_id, actor=by, payload={"decision": decision, "by": by, "note": note}))
    console.print(f"recorded {eid}: {decision} by {by} on {verdict_id} ({len(assets)} assets)")


@app.command()
def ask(
    question: str,
    asset: list[str] | None = typer.Option(None, "--asset", help="Scope retrieval to these assets and their blast radius."),
    verdict_id: str | None = typer.Option(None, "--verdict"),
    as_json: bool = typer.Option(False, "--json"),
    config: Path | None = typer.Option(None, "--config", "-c"),
):
    """Ask a question; the answer cites chunks of text humans wrote and committed."""
    from .retrieval import ask as ask_corpus

    cfg, store = _open(config)
    scope: list[str] = []
    if verdict_id and (v := store.get_verdict(verdict_id)):
        scope = list(dict.fromkeys(v["change"]["touched"] + [a["asset"] for a in v["affected"]]))
    for a in asset or []:
        r = resolve_ref(store, a)
        if r.ok:
            scope.append(r.node_id)
    from .retrieval import make_embedder

    ranker = cfg.retrieval.ranker
    out = ask_corpus(store, question, scope, ranker=ranker, embedder=make_embedder(cfg.retrieval.model) if ranker != "bm25" else None)
    if as_json:
        typer.echo(_json(out))
        return
    console.print(out["answer"])
    for c in out["chunks"]:
        console.print(f"  [dim]{c['chunk']} ({c['score']}) {c['source']}[/]")


@app.command()
def review(
    git: str | None = typer.Option(None, "--git", help="Review the working tree against this base ref."),
    diff_file: Path | None = typer.Option(None, "--diff-file", help="Review a unified diff from a file ('-' for stdin)."),
    provider: str | None = typer.Option(None, "--provider", help="stub | anthropic | openai (default from orion.yaml)."),
    model: str | None = typer.Option(None, "--model"),
    fmt: str = typer.Option("text", "--format", help="text | json | markdown"),
    post_github: str | None = typer.Option(None, "--post-github", help="Also post the review to this pull request as OWNER/REPO#NUMBER (token from GH_TOKEN or GITHUB_TOKEN)."),
    head_sha: str | None = typer.Option(None, "--head-sha", help="With --post-github: the pull request's head commit (default: the working tree's HEAD)."),
    config: Path | None = typer.Option(None, "--config", "-c"),
):
    """Review a diff: deterministic findings from the graph, model findings checked against evidence."""
    import sys

    from .providers import ProviderError, make_provider
    from .review import review as run_review
    from .review import to_markdown as review_md
    from .verdict import load_risk

    if not git and not diff_file:
        raise typer.BadParameter("give --git <base> or --diff-file <path>")
    if post_github and not re.fullmatch(r"[\w.-]+/[\w.-]+#\d+", post_github.strip()):
        raise typer.BadParameter("--post-github takes OWNER/REPO#NUMBER")  # checked before any paid model call
    cfg, store = _open(config)
    rc = cfg.review
    try:
        prov = make_provider(provider or rc.provider, model=model or rc.model, base_url=rc.base_url, api_key_env=rc.api_key_env)
    except ProviderError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(2) from e
    diff_text = None
    if diff_file:
        diff_text = sys.stdin.read() if str(diff_file) == "-" else diff_file.read_text()
    constraints, _ = load_constraints(cfg.path(cfg.constraints)) if cfg.constraints else ([], [])
    r = run_review(store, cfg.root, prov, constraints=constraints, risk_model=load_risk(cfg.path(cfg.risk) if cfg.risk else None), base_ref=git, diff_text=diff_text, scope=_scope(cfg), ignore=_ignore(cfg))
    if post_github:
        _post_github(post_github, head_sha, cfg.root, r, review_md(r), prov.name == "stub")
    if fmt == "json":
        typer.echo(_json(r))
    elif fmt == "markdown":
        typer.echo(review_md(r))
    else:
        colour = {"block": "red", "warn": "yellow", "inform": "green"}[r["severity"]]
        console.print(f"[bold {colour}]{r['severity'].upper()}[/bold {colour}]  {len(r['findings'])} findings  provider {escape(r['provider'])}  {r['review_id']}")
        if r["provider_error"]:
            console.print(f"[yellow]model review unavailable: {escape(r['provider_error'])}[/yellow]")
        for f in r["findings"]:
            loc = f"{f['path']}:{f['line']}" if f["path"] else "(no single location)"
            console.print(f"  [bold]{f['severity']:6}[/bold] {escape(loc)}  {escape(f['title'])}  [dim]{f['source']}[/dim]")
            console.print(f"         {escape(f['body'])}")
            console.print(f"         [dim]{escape(', '.join(f['evidence']))}[/dim]")
        for f in r["uncertain"]:
            console.print(f"  [dim]uncertain {escape(str(f['path']))}:{f['line']} {escape(f['title'])}: {escape('; '.join(f['notes']))}[/dim]")
        if r["rejected"]:
            console.print(f"  [dim]{len(r['rejected'])} model findings rejected[/dim]")
        for u in r["unresolved"]:
            console.print(f"  [yellow]unresolved[/yellow] {escape(str(u.get('path') or u.get('intent')))}")
        if r.get("usage"):
            u = r["usage"]
            console.print(f"  [dim]tokens in {u.get('input_tokens', u.get('prompt_tokens'))} out {u.get('output_tokens', u.get('completion_tokens'))}[/dim]")


def _event_head_sha() -> str | None:
    """Inside GitHub Actions the checkout is a merge commit; the pull request's own head is in the event payload."""
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path or not Path(path).is_file():
        return None
    try:
        return (json.loads(Path(path).read_text()).get("pull_request") or {}).get("head", {}).get("sha")
    except (OSError, json.JSONDecodeError):
        return None


def _post_github(target: str, head_sha: str | None, root: Path, r: dict, markdown: str, stub: bool) -> None:
    """Post the review; a failure here is reported and never hides the review itself (the job is advisory)."""
    import subprocess

    from .github import GitHubClient, post_review

    m = re.fullmatch(r"([\w.-]+/[\w.-]+)#(\d+)", target.strip())
    assert m, "validated by the command"
    note = "_Model review is off: add the `ORION_ANTHROPIC_API_KEY` repository secret to turn it on._" if stub else ""
    try:
        sha = head_sha or _event_head_sha() or subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
        posted = post_review(GitHubClient(m.group(1)), int(m.group(2)), sha, r, markdown, note=note)
    except Exception as e:  # noqa: BLE001 - whatever went wrong while posting, the review itself is still printed below
        console.print(f"[yellow]could not post to {escape(target)}: {escape(str(e))}[/yellow]", highlight=False)
        return
    console.print(f"[dim]posted to {escape(target)}: {posted['new']} new, {posted['carried']} unchanged, {posted['resolved']} resolved; {posted['inline_posted']} inline comments[/dim]", highlight=False)


@app.command()
def postflight(
    runs: int | None = typer.Option(None, "--runs", help="Runs to watch after a change lands (default from orion.yaml, 3)."),
    summary: bool = typer.Option(False, "--summary", help="Predicted severity against observed outcome."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report without writing outcome_observed events."),
    as_json: bool = typer.Option(False, "--json"),
    config: Path | None = typer.Option(None, "--config", "-c"),
):
    """Watch pre-flighted changes after they land and record what production did."""
    from .postflight import calibration, watch

    cfg, store = _open(config)
    rows = watch(store, runs or cfg.postflight_runs, record=not dry_run)
    cal = calibration(store)
    if as_json:
        typer.echo(_json({"verdicts": rows, "calibration": cal}))
        return
    t = Table("verdict", "predicted", "risk", "status", "landed", "outcome", "runs")
    for r in rows:
        landed = r.get("landed_at")
        t.add_row(r["verdict_id"], r["predicted"], str(r["risk"]), r["status"], landed.strftime("%Y-%m-%d %H:%M") if hasattr(landed, "strftime") else (landed or ""), r.get("outcome") or "", f"{r.get('runs_seen', '')}" + (f"/{r['runs_wanted']}" if r.get("runs_wanted") else ""))
    console.print(t)
    if summary or cal["observed"]:
        console.print(f"calibration: {cal['observed']} observed; {cal['missed']} under-called (inform/warn then failed); {cal['blocked_but_clean']} blocked then clean")
        for predicted, outs in sorted(cal["table"].items()):
            console.print(f"  {predicted:7} " + ", ".join(f"{o}={n}" for o, n in sorted(outs.items())))


@app.command()
def propose(
    write: bool = typer.Option(False, "--write", help="Write proposals under <constraints>/proposed/ and record them."),
    window_days: int = typer.Option(90, "--window-days"),
    as_json: bool = typer.Option(False, "--json"),
    config: Path | None = typer.Option(None, "--config", "-c"),
):
    """Draft constraints from ledger patterns for a human to merge. Orion never approves."""
    from .proposals import propose as draft
    from .proposals import write as write_proposals

    cfg, store = _open(config)
    if not cfg.constraints:
        raise typer.BadParameter("orion.yaml has no constraints directory")
    constraints, problems = load_constraints(cfg.path(cfg.constraints))
    for pr in problems:
        console.print(f"[yellow]{escape(pr)}[/yellow]")
    proposals = draft(store, constraints, window_days=window_days)
    if as_json:
        typer.echo(_json([p.to_dict() for p in proposals]))
    else:
        if not proposals:
            console.print("nothing to propose: every pattern in the ledger is already covered by a constraint")
        for p in proposals:
            c = p.constraint
            console.print(f"[bold]{p.id}[/bold] {c['rule']['kind']} ({c['severity']}) on {', '.join(c['scope']['assets'])}")
            console.print(f"  {escape(p.why)}")
            console.print(f"  evidence: {', '.join(p.evidence[:6])}{' ...' if len(p.evidence) > 6 else ''}")
    if write and proposals:
        paths = write_proposals(store, proposals, cfg.path(cfg.constraints), cfg.root)
        for path in paths:
            console.print(f"wrote {path.relative_to(cfg.root)}")
        if not paths:
            console.print("all proposals already exist under proposed/")


@app.command()
def serve(
    transport: str = typer.Option("stdio", "--transport", help="stdio | streamable-http"),
    config: Path | None = typer.Option(None, "--config", "-c"),
):
    """Run the MCP server (tools: preflight, explain, history, record_decision, propose_constraint, ask)."""
    from .server import serve as run_server

    cfg = load_config(config)
    run_server(cfg, transport=transport)


if __name__ == "__main__":
    app()
