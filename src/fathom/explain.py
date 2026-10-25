"""``explain``: what an asset is, who owns it, what touches it, and what has happened to it.

Every section of the answer carries ids (node, edge, event, constraint) so the
caller can cite rather than paraphrase.
"""

from __future__ import annotations

from typing import Any

from .constraints import Constraint, constraints_for
from .model import EdgeKind
from .resolver import resolve_ref
from .store import Store
from .sync import graph_as_of


def _brief(store: Store, nid: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    n = store.node(nid) or {"id": nid, "kind": "?", "name": nid, "tier": None, "owner": None}
    out = {"id": n["id"], "kind": n["kind"], "name": n["name"], "tier": n["tier"], "owner": n["owner"]}
    if extra:
        out.update(extra)
    return out


def explain(store: Store, ref: str, constraints: list[Constraint] | None = None, column: str | None = None) -> dict[str, Any]:
    res = resolve_ref(store, ref)
    if not res.ok:
        return {"resolved": False, "ref": ref, "candidates": res.candidates}
    nid = res.node_id
    node = store.node(nid)
    assert node is not None

    upstream = [
        _brief(store, e["dst"], {"edge": e["id"], "confidence": e["confidence"]})
        for e in store.edges_from(nid, (EdgeKind.CONSUMES, EdgeKind.DEPENDS_ON))
    ]
    downstream = [
        _brief(store, e["src"], {"edge": e["id"], "confidence": e["confidence"]})
        for e in store.edges_to(nid, (EdgeKind.CONSUMES, EdgeKind.DEPENDS_ON))
    ]
    full_downstream = store.walk([nid], "downstream")
    tier1_downstream = [
        _brief(store, k, {"hops": v["hops"], "via": v["via_edge"]})
        for k, v in full_downstream.items()
        if (store.node(k) or {}).get("tier") == 1
    ]
    produced_by = [_brief(store, e["src"], {"edge": e["id"], "via": e["properties"].get("via")}) for e in store.edges_to(nid, (EdgeKind.PRODUCES,))]
    owners = [_brief(store, e["dst"], {"edge": e["id"]}) for e in store.edges_from(nid, (EdgeKind.OWNED_BY,))]

    columns: list[dict[str, Any]] = []
    for e in store.edges_to(nid, (EdgeKind.COLUMN_OF,)):
        col = store.node(e["src"])
        if not col:
            continue
        cname = col["name"][len(node["name"]) + 1 :] if col["name"].startswith(node["name"] + ".") else col["name"].split(".", 1)[1]
        if column and cname != column:
            continue
        derives = [_brief(store, d["dst"], {"edge": d["id"]}) for d in store.edges_from(col["id"], (EdgeKind.DERIVES_FROM,))]
        used_by = [_brief(store, u["src"], {"edge": u["id"]}) for u in store.edges_to(col["id"], (EdgeKind.DERIVES_FROM,))]
        columns.append({"id": col["id"], "name": cname, "derives_from": derives, "used_by": used_by})
    columns.sort(key=lambda c: c["name"])

    hits: list[dict[str, Any]] = []
    if constraints:
        resolve = lambda r: (resolve_ref(store, r).node_id or r)
        for c in constraints_for(constraints, {nid}, resolve):
            hits.append({"id": c.id, "kind": c.rule.kind, "severity": c.severity, "reason": c.reason, "source": c.source, "expired": c.expired})

    events = store.events_for([nid], limit=5)
    for ev in events:
        ev["payload"].pop("body", None)

    return {
        "resolved": True,
        "graph_as_of": graph_as_of(store),
        "asset": {**node, "provenance": store.provenance_for(nid)},
        "owners": owners,
        "upstream": upstream,
        "downstream": downstream,
        "downstream_total": len(full_downstream),
        "tier1_downstream": tier1_downstream,
        "produced_by": produced_by,
        "columns": columns,
        "constraints": hits,
        "recent_events": events,
    }
