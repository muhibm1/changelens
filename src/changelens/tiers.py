"""Tier inference.

Declared tiers win. Where a node has none, anything a tier-1 exposure or a
contracted model depends on becomes tier 1, because a break there is noticed
by someone outside the team. Everything else stays unset and is reported as
such in verdicts.
"""

from __future__ import annotations

from .model import EdgeKind
from .store import Store


def infer_tiers(store: Store) -> int:
    seeds = [
        r[0]
        for r in store.con.execute(
            "SELECT id FROM nodes WHERE tier = 1 OR json_extract_string(properties, '$.contract') = 'true'"
        ).fetchall()
    ]
    if not seeds:
        return 0
    upstream = store.walk(seeds, direction="upstream", kinds=(EdgeKind.CONSUMES, EdgeKind.DEPENDS_ON), max_hops=10, always_include_tier=None)
    changed = 0
    for nid in upstream:
        node = store.node(nid)
        if node and node["tier"] is None and node["kind"] not in ("team", "person", "column"):
            store.set_tier(nid, 1)
            store.con.execute(
                "UPDATE nodes SET properties = json_merge_patch(properties, '{\"tier_source\": \"inferred\"}') WHERE id = ?",
                [nid],
            )
            changed += 1
    return changed
