"""DuckDB-backed store for the asset graph, provenance, and the ledger.

The graph is a property graph in ordinary tables. Local mode is one DuckDB
file next to the checkout; the schema is plain SQL so a Postgres backend can
share it later.
"""

from __future__ import annotations

import contextlib
import json
from collections import deque
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from .model import Edge, EdgeKind, Event, Node, Provenance

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id          VARCHAR PRIMARY KEY,
    kind        VARCHAR NOT NULL,
    name        VARCHAR NOT NULL,
    properties  JSON    NOT NULL DEFAULT '{}',
    tier        INTEGER,
    owner       VARCHAR,
    updated_at  TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS edges (
    id          VARCHAR PRIMARY KEY,
    src         VARCHAR NOT NULL,
    dst         VARCHAR NOT NULL,
    kind        VARCHAR NOT NULL,
    confidence  VARCHAR NOT NULL,
    properties  JSON    NOT NULL DEFAULT '{}',
    updated_at  TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS provenance (
    subject_id  VARCHAR NOT NULL,
    adapter     VARCHAR NOT NULL,
    source      VARCHAR NOT NULL,
    ref         VARCHAR,
    observed_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id          VARCHAR PRIMARY KEY,
    kind        VARCHAR NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    assets      JSON    NOT NULL,
    actor       VARCHAR,
    payload     JSON    NOT NULL DEFAULT '{}',
    source      VARCHAR NOT NULL,
    source_ref  VARCHAR,
    recorded_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_state (
    adapter     VARCHAR NOT NULL,
    source      VARCHAR NOT NULL,
    digest      VARCHAR NOT NULL,
    synced_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (adapter, source)
);
CREATE TABLE IF NOT EXISTS verdicts (
    id          VARCHAR PRIMARY KEY,
    issued_at   TIMESTAMPTZ NOT NULL,
    severity    VARCHAR NOT NULL,
    body        JSON    NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id          VARCHAR PRIMARY KEY,
    text        VARCHAR NOT NULL,
    source      VARCHAR NOT NULL,
    asset_ids   VARCHAR[] NOT NULL,
    tokens      VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS chunk_vectors (
    chunk_id    VARCHAR NOT NULL,
    model       VARCHAR NOT NULL,
    vec         FLOAT[] NOT NULL,
    PRIMARY KEY (chunk_id, model)
);
CREATE TABLE IF NOT EXISTS review_cache (
    cache_key   VARCHAR PRIMARY KEY,
    findings    JSON    NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL,
    hit_count   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS edges_src ON edges (src);
CREATE INDEX IF NOT EXISTS edges_dst ON edges (dst);
CREATE INDEX IF NOT EXISTS provenance_subject ON provenance (subject_id);
"""


class ProvenanceRequired(ValueError):
    pass


def _json_default(o: Any) -> Any:
    return o.isoformat() if isinstance(o, datetime) else str(o)


class Store:
    def __init__(self, path: str | Path = ".fathom/graph.duckdb"):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Fathom's own folder in a checkout is never part of a change.
            ignore = self.path.parent / ".gitignore"
            if self.path.parent.name == ".fathom" and not ignore.exists():
                with contextlib.suppress(OSError):
                    ignore.write_text("*\n")
        self.con = duckdb.connect(str(self.path))
        self.con.execute(SCHEMA)

    # ------------------------------------------------------------------ review cache
    def cache_get(self, key: str) -> Any | None:
        """A model answer kept under ``key``, counting the hit; None when there is none."""
        row = self.con.execute("SELECT findings FROM review_cache WHERE cache_key = ?", [key]).fetchone()
        if row is None:
            return None
        self.con.execute("UPDATE review_cache SET hit_count = hit_count + 1 WHERE cache_key = ?", [key])
        return json.loads(row[0])

    def cache_put(self, key: str, value: Any) -> None:
        self.con.execute("INSERT INTO review_cache VALUES (?, ?, now(), 0) ON CONFLICT (cache_key) DO NOTHING", [key, json.dumps(value)])

    def cache_stats(self) -> dict[str, int]:
        entries, hits = self.con.execute("SELECT count(*), coalesce(sum(hit_count), 0) FROM review_cache").fetchone()
        return {"entries": int(entries), "hits": int(hits)}

    # ------------------------------------------------------------------ writes
    def upsert_node(self, node: Node) -> None:
        if not node.provenance:
            raise ProvenanceRequired(f"node {node.id} has no provenance")
        self.con.execute(
            """
            INSERT INTO nodes (id, kind, name, properties, tier, owner, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, now())
            ON CONFLICT (id) DO UPDATE SET
                properties = excluded.properties,
                tier = COALESCE(excluded.tier, nodes.tier),
                owner = COALESCE(excluded.owner, nodes.owner),
                updated_at = now()
            """,
            [node.id, str(node.kind), node.name, json.dumps(node.properties), node.tier, node.owner],
        )
        self._add_provenance(node.id, node.provenance)

    def upsert_edge(self, edge: Edge) -> None:
        if not edge.provenance:
            raise ProvenanceRequired(f"edge {edge.id} has no provenance")
        self.con.execute(
            """
            INSERT INTO edges (id, src, dst, kind, confidence, properties, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, now())
            ON CONFLICT (id) DO UPDATE SET
                confidence = excluded.confidence,
                properties = excluded.properties,
                updated_at = now()
            """,
            [edge.id, edge.src, edge.dst, str(edge.kind), str(edge.confidence), json.dumps(edge.properties)],
        )
        self._add_provenance(edge.id, edge.provenance)

    def write_graph(self, nodes: list[Node], edges: list[Edge], transaction: bool = True) -> None:
        """Bulk upsert with the same meaning as ``upsert_node`` / ``upsert_edge`` one by one, in one transaction.

        Rows are staged through newline-delimited JSON files and merged with set-based SQL: a dbt project with tens
        of thousands of columns would otherwise take one statement per row plus one provenance scan per row.
        Duplicate ids in the batch merge as successive upserts would: last properties, last non-null tier and owner.
        ``transaction=False`` runs inside a transaction the caller holds.
        """
        import tempfile

        for x in [*nodes, *edges]:
            if not x.provenance:
                raise ProvenanceRequired(f"{'node' if isinstance(x, Node) else 'edge'} {x.id} has no provenance")
        merged: dict[str, dict[str, Any]] = {}
        for n in nodes:
            row = merged.setdefault(n.id, {"id": n.id, "kind": str(n.kind), "name": n.name, "tier": None, "owner": None})
            row["properties"] = json.dumps(n.properties)
            row["tier"] = n.tier if n.tier is not None else row["tier"]
            row["owner"] = n.owner if n.owner is not None else row["owner"]
        erows = {e.id: {"id": e.id, "src": e.src, "dst": e.dst, "kind": str(e.kind), "confidence": str(e.confidence), "properties": json.dumps(e.properties)} for e in edges}
        prov: dict[tuple[str, str, str, str | None], str] = {}
        for x in [*nodes, *edges]:
            for p in x.provenance:
                prov[(x.id, p.adapter, p.source, p.ref)] = p.observed_at.isoformat()
        prows = [{"subject_id": k[0], "adapter": k[1], "source": k[2], "ref": k[3], "observed_at": v} for k, v in prov.items()]

        with tempfile.TemporaryDirectory() as tmp:
            def stage(name: str, rows: list[dict[str, Any]], columns: dict[str, str]) -> None:
                f = Path(tmp) / f"{name}.ndjson"
                f.write_text("".join(json.dumps(r) + "\n" for r in rows))
                cols = "{" + ", ".join(f"'{k}': '{v}'" for k, v in columns.items()) + "}"
                self.con.execute(f"CREATE OR REPLACE TEMP TABLE {name} AS SELECT * FROM read_json(?, format = 'newline_delimited', columns = {cols}, maximum_object_size = 1073741824)", [str(f)])

            if transaction:
                self.con.execute("BEGIN TRANSACTION")
            try:
                if merged:
                    stage("stage_nodes", list(merged.values()), {"id": "VARCHAR", "kind": "VARCHAR", "name": "VARCHAR", "properties": "VARCHAR", "tier": "INTEGER", "owner": "VARCHAR"})
                    self.con.execute(
                        """
                        INSERT INTO nodes (id, kind, name, properties, tier, owner, updated_at)
                        SELECT id, kind, name, properties, tier, owner, now() FROM stage_nodes
                        ON CONFLICT (id) DO UPDATE SET
                            properties = excluded.properties,
                            tier = COALESCE(excluded.tier, nodes.tier),
                            owner = COALESCE(excluded.owner, nodes.owner),
                            updated_at = now()
                        """
                    )
                if erows:
                    stage("stage_edges", list(erows.values()), {"id": "VARCHAR", "src": "VARCHAR", "dst": "VARCHAR", "kind": "VARCHAR", "confidence": "VARCHAR", "properties": "VARCHAR"})
                    self.con.execute(
                        """
                        INSERT INTO edges (id, src, dst, kind, confidence, properties, updated_at)
                        SELECT id, src, dst, kind, confidence, properties, now() FROM stage_edges
                        ON CONFLICT (id) DO UPDATE SET
                            confidence = excluded.confidence,
                            properties = excluded.properties,
                            updated_at = now()
                        """
                    )
                if prows:
                    stage("stage_prov", prows, {"subject_id": "VARCHAR", "adapter": "VARCHAR", "source": "VARCHAR", "ref": "VARCHAR", "observed_at": "TIMESTAMPTZ"})
                    self.con.execute(
                        """
                        DELETE FROM provenance USING stage_prov s
                        WHERE provenance.subject_id = s.subject_id AND provenance.adapter = s.adapter
                          AND provenance.source = s.source AND provenance.ref IS NOT DISTINCT FROM s.ref
                        """
                    )
                    self.con.execute("INSERT INTO provenance (subject_id, adapter, source, ref, observed_at) SELECT subject_id, adapter, source, ref, observed_at FROM stage_prov")
                if transaction:
                    self.con.execute("COMMIT")
            except Exception:
                if transaction:
                    self.con.execute("ROLLBACK")
                raise
            for t in ("stage_nodes", "stage_edges", "stage_prov"):
                self.con.execute(f"DROP TABLE IF EXISTS {t}")

    def replace_adapter_graph(self, adapter: str, node_kinds: Iterable[str], edge_kinds: Iterable[str], nodes: list[Node], edges: list[Edge]) -> None:
        """Drop every node and edge of these kinds that ``adapter`` wrote, with their provenance, and write the new set, in one transaction: a re-sync that removes a file removes its facts, and a failed write leaves the old graph."""
        nk, ek = [str(k) for k in node_kinds], [str(k) for k in edge_kinds]
        if not nk or not ek:
            raise ValueError("replace_adapter_graph needs the node and edge kinds the adapter owns")
        mine = "id IN (SELECT subject_id FROM provenance WHERE adapter = ?)"
        self.con.execute("BEGIN TRANSACTION")
        try:
            self.con.execute("DELETE FROM edges WHERE kind IN ({}) AND {}".format(",".join("?" for _ in ek), mine), [*ek, adapter])
            self.con.execute("DELETE FROM nodes WHERE kind IN ({}) AND {}".format(",".join("?" for _ in nk), mine), [*nk, adapter])
            # only the provenance of what was just deleted: a subject of a kind not listed keeps its record
            self.con.execute("DELETE FROM provenance p WHERE adapter = ? AND NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = p.subject_id) AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.id = p.subject_id)", [adapter])
            self.write_graph(nodes, edges, transaction=False)
            self.con.execute("COMMIT")
        except Exception:
            self.con.execute("ROLLBACK")
            raise

    def set_tier(self, node_id: str, tier: int) -> None:
        self.con.execute("UPDATE nodes SET tier = ? WHERE id = ?", [tier, node_id])

    def append_event(self, event: Event) -> str:
        """Ledger writes are append-only; an existing id is left untouched."""
        self.con.execute(
            """
            INSERT INTO events (id, kind, occurred_at, assets, actor, payload, source, source_ref, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, now())
            ON CONFLICT (id) DO NOTHING
            """,
            [
                event.id,
                str(event.kind),
                event.occurred_at,
                json.dumps(event.assets),
                event.actor,
                json.dumps(event.payload, default=str),
                event.source,
                event.source_ref,
            ],
        )
        return event.id

    def _add_provenance(self, subject_id: str, records: Iterable[Provenance]) -> None:
        for p in records:
            # one row per (subject, adapter, source, ref): a re-sync refreshes observed_at rather than duplicating
            self.con.execute(
                "DELETE FROM provenance WHERE subject_id = ? AND adapter = ? AND source = ? AND ref IS NOT DISTINCT FROM ?",
                [subject_id, p.adapter, p.source, p.ref],
            )
            self.con.execute(
                "INSERT INTO provenance (subject_id, adapter, source, ref, observed_at) VALUES (?, ?, ?, ?, ?)",
                [subject_id, p.adapter, p.source, p.ref, p.observed_at],
            )

    def put_verdict(self, verdict_id: str, body: dict[str, Any]) -> None:
        self.con.execute(
            "INSERT INTO verdicts (id, issued_at, severity, body) VALUES (?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
            [verdict_id, body["issued_at"], body["severity"], json.dumps(body, default=_json_default)],
        )

    def get_verdict(self, verdict_id: str) -> dict[str, Any] | None:
        row = self.con.execute("SELECT body FROM verdicts WHERE id = ?", [verdict_id]).fetchone()
        return json.loads(row[0]) if row else None

    def mark_synced(self, adapter: str, source: str, digest: str) -> None:
        self.con.execute(
            """
            INSERT INTO sync_state (adapter, source, digest, synced_at) VALUES (?, ?, ?, now())
            ON CONFLICT (adapter, source) DO UPDATE SET digest = excluded.digest, synced_at = now()
            """,
            [adapter, source, digest],
        )

    def last_digest(self, adapter: str, source: str) -> str | None:
        row = self.con.execute(
            "SELECT digest FROM sync_state WHERE adapter = ? AND source = ?", [adapter, source]
        ).fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------- reads
    def node(self, node_id: str) -> dict[str, Any] | None:
        row = self.con.execute(
            "SELECT id, kind, name, properties, tier, owner FROM nodes WHERE id = ?", [node_id]
        ).fetchone()
        return self._node_row(row) if row else None

    def find_nodes(self, name: str | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT id, kind, name, properties, tier, owner FROM nodes WHERE 1=1"
        args: list[Any] = []
        if name is not None:
            sql += " AND name = ?"
            args.append(name)
        if kind is not None:
            sql += " AND kind = ?"
            args.append(kind)
        return [self._node_row(r) for r in self.con.execute(sql + " ORDER BY id", args).fetchall()]

    def search_nodes(self, needle: str, limit: int = 10) -> list[dict[str, Any]]:
        rows = self.con.execute(
            """
            SELECT id, kind, name, properties, tier, owner FROM nodes
            WHERE kind <> 'column' AND lower(name) LIKE '%' || lower(?) || '%'
            ORDER BY length(name), name LIMIT ?
            """,
            [needle, limit],
        ).fetchall()
        return [self._node_row(r) for r in rows]

    def edges_from(self, node_id: str, kinds: Iterable[EdgeKind | str] | None = None) -> list[dict[str, Any]]:
        return self._edges("src", node_id, kinds)

    def edges_to(self, node_id: str, kinds: Iterable[EdgeKind | str] | None = None) -> list[dict[str, Any]]:
        return self._edges("dst", node_id, kinds)

    def provenance_for(self, subject_id: str) -> list[dict[str, Any]]:
        rows = self.con.execute(
            "SELECT adapter, source, ref, observed_at FROM provenance WHERE subject_id = ? ORDER BY observed_at DESC",
            [subject_id],
        ).fetchall()
        return [dict(adapter=a, source=s, ref=r, observed_at=o) for a, s, r, o in rows]

    def events_for(
        self, asset_ids: Iterable[str], kinds: Iterable[str] | None = None, since: datetime | None = None, limit: int = 50, causes: Iterable[str] | None = None
    ) -> list[dict[str, Any]]:
        """``causes``: only events whose payload names one of these causes."""
        ids = list(asset_ids)
        if not ids:
            return []
        sql = """
            SELECT e.id, e.kind, e.occurred_at, e.assets, e.actor, e.payload, e.source, e.source_ref
            FROM events e, json_each(e.assets) a
            WHERE a.value::VARCHAR IN ({})
        """.format(",".join("?" for _ in ids))
        args: list[Any] = [json.dumps(i) for i in ids]
        if kinds:
            ks = list(kinds)
            sql += " AND e.kind IN ({})".format(",".join("?" for _ in ks))
            args.extend(ks)
        if since is not None:
            sql += " AND e.occurred_at >= ?"
            args.append(since)
        if causes is not None:
            cs = list(causes) or [""]
            sql += " AND lower(json_extract_string(e.payload, '$.cause')) IN ({})".format(",".join("?" for _ in cs))
            args.extend(c.lower() for c in cs)
        sql += " GROUP BY ALL ORDER BY e.occurred_at DESC LIMIT ?"
        args.append(limit)
        rows = self.con.execute(sql, args).fetchall()
        return [
            dict(
                id=i, kind=k, occurred_at=o, assets=json.loads(a), actor=ac,
                payload=json.loads(p), source=s, source_ref=sr,
            )
            for i, k, o, a, ac, p, s, sr in rows
        ]

    def counts(self) -> dict[str, int]:
        out = {}
        for table in ("nodes", "edges", "provenance", "events", "verdicts", "chunks"):
            out[table] = self.con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        return out

    # ------------------------------------------------------------------- walks
    def walk(
        self,
        start: Iterable[str],
        direction: str = "downstream",
        kinds: Iterable[EdgeKind | str] = (EdgeKind.CONSUMES, EdgeKind.DEPENDS_ON, EdgeKind.DERIVES_FROM),
        max_hops: int = 5,
        always_include_tier: int | None = 1,
        hard_cap: int = 50,
    ) -> dict[str, dict[str, Any]]:
        """Breadth-first walk. Returns {node_id: {hops, via_edge, path, edge_kind}}.

        ``downstream`` follows edges that point *at* the start nodes (consumers,
        dependants); ``upstream`` follows edges the start nodes point at.
        Nodes within ``max_hops`` are returned. Beyond that the walk continues
        (up to ``hard_cap``) but only tier-``always_include_tier`` nodes are
        returned, so a long chain never hides the consumer that matters.
        """
        kinds = [str(k) for k in kinds]
        starts = set(start)
        seen: dict[str, dict[str, Any]] = {}
        visited: set[str] = set(starts)
        queue: deque[tuple[str, int, list[str]]] = deque((s, 0, [s]) for s in starts)
        limit = max_hops if always_include_tier is None else hard_cap
        while queue:
            nid, hops, path = queue.popleft()
            if hops >= limit:
                continue
            neighbours = self.edges_to(nid, kinds) if direction == "downstream" else self.edges_from(nid, kinds)
            for e in neighbours:
                nxt = e["src"] if direction == "downstream" else e["dst"]
                if nxt in visited:
                    continue
                visited.add(nxt)
                node = self.node(nxt)
                tier = node["tier"] if node else None
                if hops + 1 <= max_hops or tier == always_include_tier:
                    seen[nxt] = {"hops": hops + 1, "via_edge": e["id"], "path": path + [nxt], "edge_kind": e["kind"]}
                queue.append((nxt, hops + 1, path + [nxt]))
        return seen

    # ----------------------------------------------------------------- helpers
    def _edges(self, col: str, node_id: str, kinds) -> list[dict[str, Any]]:
        sql = f"SELECT id, src, dst, kind, confidence, properties FROM edges WHERE {col} = ?"
        args: list[Any] = [node_id]
        if kinds:
            ks = [str(k) for k in kinds]
            sql += " AND kind IN ({})".format(",".join("?" for _ in ks))
            args.extend(ks)
        rows = self.con.execute(sql + " ORDER BY id", args).fetchall()
        return [dict(id=i, src=s, dst=d, kind=k, confidence=c, properties=json.loads(p)) for i, s, d, k, c, p in rows]

    @staticmethod
    def _node_row(row) -> dict[str, Any]:
        i, k, n, p, t, o = row
        return dict(id=i, kind=k, name=n, properties=json.loads(p), tier=t, owner=o)

    def close(self) -> None:
        self.con.close()
