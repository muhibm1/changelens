"""``fathom serve``: the MCP server. Seven tools, each returning a judgement or a record.

The server has no write path into the platform. It writes the ledger and
drafts constraint files; nothing else.
"""

from __future__ import annotations

import functools
import json
import os
import re
import time
from datetime import UTC, datetime
from typing import Any

from mcp.server.mcpserver import MCPServer

from .changes import collect_changes
from .config import FathomConfig, load_config
from .constraints import load_constraints
from .explain import explain as explain_asset
from .model import Event, EventKind
from .resolver import resolve_ref
from .retrieval import ask as ask_corpus
from .retrieval import make_embedder
from .store import Store
from .verdict import load_risk, preflight, to_markdown

INSTRUCTIONS = """Fathom is the pre-flight check for changes to data pipelines.
Call `preflight` once with your diff, paths or intent BEFORE writing a change, and again before opening a PR.
Every claim in a verdict carries evidence ids; cite them rather than paraphrasing. Fathom never blocks on its own:
a `block` severity means a human or CI gate should decide. Use `explain`, `history` and `ask` to understand an asset,
`record_decision` after a human approves or waives a check, and `propose_constraint` to lock in something learned.
Call `review` on a finished diff for a full code review: deterministic findings from the platform first, then model
findings that were checked against the diff and the store."""


_ID_LINE = re.compile(r"^\s*[-*]\s+\[([^\]\s]+)\]", re.M)


def _log_calls(server: MCPServer) -> MCPServer:
    """Every tool registered from here on writes one ``mcp.call`` record to the usage log (``fathom.usage``): which
    tool, how long it took, how many ids it returned and which. The answer itself is not changed."""
    from . import usage

    register = server.tool

    def tool(*args: Any, **kwargs: Any):
        def deco(fn):
            name = kwargs.get("name") or fn.__name__

            @functools.wraps(fn)
            def logged(*a: Any, **kw: Any):
                t0 = time.monotonic()
                outcome, result = "error", None
                try:
                    result = fn(*a, **kw)
                    outcome = "error" if isinstance(result, dict) and result.get("error") else "ok"
                    return result
                finally:
                    ids = _result_ids(result)
                    usage.log("mcp", "call", tool=name, session_id=usage.session_for_parent(), cwd=os.getcwd(), latency_ms=round((time.monotonic() - t0) * 1000, 1), outcome=outcome, hits=len(ids), ids=ids[:20], timed_out=False, failed=outcome == "error")

            return register(*args, **kwargs)(logged)

        return deco

    server.tool = tool  # type: ignore[method-assign]
    return server


def _result_ids(result: Any) -> list[str]:
    """The ids an answer cites: its items' (JSON), or the ``- [id]`` lines (Markdown)."""
    if isinstance(result, str):
        return _ID_LINE.findall(result)
    if isinstance(result, dict):
        items = result.get("items") or result.get("events") or []
        return [str(i.get("id")) for i in items if isinstance(i, dict) and i.get("id")]
    if isinstance(result, list):
        return [str(i.get("id")) for i in result if isinstance(i, dict) and i.get("id")]
    return []


def _jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=lambda o: o.isoformat() if isinstance(o, datetime) else str(o)))


def build_server(cfg: FathomConfig | None = None, review_provider: Any = None) -> MCPServer:
    cfg = cfg or load_config()
    store = Store(cfg.path(cfg.db))
    constraints, _ = load_constraints(cfg.path(cfg.constraints)) if cfg.constraints else ([], [])
    risk_model = load_risk(cfg.path(cfg.risk) if cfg.risk else None)
    server = _log_calls(MCPServer("fathom", instructions=INSTRUCTIONS, version="0.1.0"))

    @server.tool(name="preflight", description="Check a proposed change before making it. Give a git base ref (diff of the working tree against it), file paths, a sentence of intent, or explicit ops. Returns a verdict whose every claim carries evidence ids.")
    def preflight_tool(base_ref: str | None = None, paths: list[str] | None = None, intent: str | None = None, ops: list[dict[str, Any]] | None = None, format: str = "json") -> dict[str, Any] | str:
        from .cli import _ignore, _scope

        cs = collect_changes(store, cfg.root, base_ref=base_ref, paths=paths, intent=intent, ops=ops, scope=_scope(cfg), ignore=_ignore(cfg))
        v = preflight(store, cs, constraints, risk_model)
        return to_markdown(v) if format == "markdown" else _jsonable(v)

    @server.tool(name="explain", description="What an asset is: owner, tier, upstream and downstream, tier-1 consumers in its blast radius, which task builds it, column lineage, constraints in scope, recent events. Asset can be a name (fct_orders), a ref (source:raw.raw_events, dag:orders_pipeline) or a path.")
    def explain_tool(asset: str, column: str | None = None) -> dict[str, Any]:
        return _jsonable(explain_asset(store, asset, constraints, column))

    @server.tool(name="history", description="Ledger events for an asset or path, newest first: incidents, postmortems, landed changes, pre-flights, decisions, observed outcomes.")
    def history_tool(asset: str, kinds: list[str] | None = None, limit: int = 20) -> dict[str, Any]:
        r = resolve_ref(store, asset)
        if not r.ok:
            return {"resolved": False, "asset": asset, "candidates": [c["id"] for c in r.candidates]}
        events = store.events_for([r.node_id], kinds=kinds, limit=limit)
        for e in events:
            body = e["payload"].pop("body", None)
            if body:
                e["payload"]["excerpt"] = body[:600]
        return _jsonable({"resolved": True, "asset": r.node_id, "events": events})

    @server.tool(name="record_decision", description="Record that a human approved, waived or rejected a pre-flight check. Writes a decision_recorded event to the ledger linked to the verdict's assets.")
    def record_decision_tool(verdict_id: str, decision: str, by: str, note: str = "", check: str | None = None) -> dict[str, Any]:
        v = store.get_verdict(verdict_id)
        if v is None:
            return {"ok": False, "error": f"unknown verdict {verdict_id}"}
        if decision not in ("approve", "waive", "reject"):
            return {"ok": False, "error": "decision must be approve, waive or reject"}
        assets = list(dict.fromkeys(v["change"]["touched"] + [a["asset"] for a in v["affected"]]))
        ev = Event(EventKind.DECISION_RECORDED, datetime.now(UTC), assets, source="fathom record_decision", source_ref=verdict_id, actor=by, payload={"decision": decision, "by": by, "note": note, "check": check})
        return {"ok": True, "event": store.append_event(ev), "verdict_id": verdict_id, "assets": assets}

    @server.tool(name="propose_constraint", description="Draft a constraint file under the constraints directory's proposed/ folder for a human to review and merge. Returns the path. Fathom never approves a constraint itself.")
    def propose_constraint_tool(constraint: dict[str, Any], reason: str) -> dict[str, Any]:
        import yaml

        from .constraints import Constraint

        if not cfg.constraints:
            return {"ok": False, "error": "no constraints directory configured"}
        data = dict(constraint)
        data.setdefault("reason", reason)
        data.setdefault("severity", "warn")
        try:
            c = Constraint.model_validate({**data, "path": "proposed"})
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"invalid constraint: {e}"}
        out_dir = cfg.path(cfg.constraints) / "proposed"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{c.id}.yaml"
        body = c.model_dump(mode="json", exclude={"path"})
        body["status"] = "proposed"
        body["proposed_at"] = datetime.now(UTC).isoformat()
        path.write_text(yaml.safe_dump(body, sort_keys=False))
        return {"ok": True, "path": str(path.relative_to(cfg.root)), "next": "open a pull request with this file; merging it is the approval"}

    @server.tool(name="ask", description="Ask a free-form question about the platform. Retrieval is scoped by the graph: pass assets or a verdict_id and only text attached to those assets (and their blast radius) is searched first. Every sentence of the answer cites a chunk id; unknown returns 'not recorded'.")
    def ask_tool(question: str, assets: list[str] | None = None, verdict_id: str | None = None, limit: int = 5) -> dict[str, Any]:
        scope: list[str] = []
        if verdict_id:
            v = store.get_verdict(verdict_id)
            if v:
                scope = list(dict.fromkeys(v["change"]["touched"] + [a["asset"] for a in v["affected"]]))
        for a in assets or []:
            r = resolve_ref(store, a)
            if r.ok:
                scope.append(r.node_id)
        ranker = cfg.retrieval.ranker
        return _jsonable(ask_corpus(store, question, scope, limit, ranker=ranker, embedder=make_embedder(cfg.retrieval.model) if ranker != "bm25" else None))

    @server.tool(name="review", description="Review a diff: deterministic findings from the platform graph and constraints first, then findings from the configured model provider, each checked against the diff and the store (unsupported ones are marked uncertain, ones outside the diff rejected). Give a git base ref (working tree against it) or a unified diff, and optionally `intent`: what the change is meant to do, so an intended behaviour change is not reported as a bug (default with a base ref: the commit messages since it). The provider and the sensitive areas of the code are the ones configured in fathom.yaml. Set `agent` to have the model investigate the repository with read-only tools (files, search, definitions, references, git history, blast radius, the record) before it reports (default: review.agent in fathom.yaml). Every finding carries a location and evidence.")
    def review_tool(base_ref: str | None = None, diff: str | None = None, format: str = "json", intent: str | None = None, agent: bool | None = None) -> dict[str, Any] | str:
        from .cli import _ignore, _scope, _verifier
        from .codectx import AreasError, load_areas
        from .providers import ProviderError, make_provider
        from .review import review as run_review
        from .review import to_markdown as review_md

        if not base_ref and not diff:
            return {"ok": False, "error": "give base_ref or diff"}
        rc = cfg.review
        agentic = rc.agent if agent is None else bool(agent)
        try:
            # The provider and model come from fathom.yaml only, so a calling agent cannot redirect the diff or the spend.
            prov = review_provider or make_provider(rc.provider, model=rc.model, base_url=rc.base_url, api_key_env=rc.api_key_env, effort=rc.effort, command=rc.command)
            areas = load_areas(rc.areas, cfg.root) if rc.areas else []
        except (ProviderError, AreasError) as e:
            return {"ok": False, "error": str(e)}
        try:
            r = run_review(store, cfg.root, prov, constraints=constraints, risk_model=risk_model, base_ref=base_ref, diff_text=diff, scope=_scope(cfg), ignore=_ignore(cfg), intent=intent, second_reading=cfg.review.second_reading, verifier=_verifier(cfg.review), lenses=([] if agentic else cfg.review.lenses), areas=areas, escalation=cfg.review.escalation, cache=None if os.environ.get("FATHOM_REVIEW_CACHE", "1").strip().lower() in ("0", "false", "f", "no", "n", "off") else store, warehouse=cfg.path(cfg.review.warehouse) if cfg.review.warehouse else None, agent=agentic, agent_steps=cfg.review.agent_steps)
        except Exception as e:  # noqa: BLE001 - a bad ref or diff is the caller's to fix; say so in the tool's own shape
            return {"ok": False, "error": f"review failed: {e}"}
        return review_md(r) if format == "markdown" else _jsonable(r)

    return server


def serve(cfg: FathomConfig | None = None, transport: str = "stdio", **kwargs: Any) -> None:
    build_server(cfg).run(transport=transport, **kwargs)


if __name__ == "__main__":
    serve()
