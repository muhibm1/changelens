from datetime import UTC, datetime

import pytest

from changelens.model import Confidence, Edge, EdgeKind, Event, EventKind, Node, NodeKind, Provenance
from changelens.store import ProvenanceRequired, Store


def prov() -> Provenance:
    return Provenance(adapter="test", source="unit test")


def test_provenance_is_required():
    s = Store(":memory:")
    with pytest.raises(ProvenanceRequired):
        s.upsert_node(Node(NodeKind.MODEL, "a"))
    with pytest.raises(ProvenanceRequired):
        s.upsert_edge(Edge("model:a", "model:b", EdgeKind.CONSUMES))


def test_walk_keeps_tier_one_past_max_hops():
    s = Store(":memory:")
    names = ["a", "b", "c", "d", "e"]
    for n in names:
        s.upsert_node(Node(NodeKind.MODEL, n, provenance=[prov()]))
    s.upsert_node(Node(NodeKind.DASHBOARD, "dash", tier=1, provenance=[prov()]))
    # a <- b <- c <- d <- e <- dash   (each consumes the previous)
    chain = names + ["dash"]
    for up, down in zip(chain, chain[1:], strict=False):
        kind_up = "dashboard" if up == "dash" else "model"
        kind_down = "dashboard" if down == "dash" else "model"
        s.upsert_edge(Edge(f"{kind_down}:{down}", f"{kind_up}:{up}", EdgeKind.CONSUMES, Confidence.PARSED, provenance=[prov()]))
    found = s.walk(["model:a"], max_hops=2)
    assert set(found) == {"model:b", "model:c", "dashboard:dash"}
    assert found["dashboard:dash"]["hops"] == 5


def test_ledger_is_append_only():
    s = Store(":memory:")
    ev = Event(EventKind.CHANGE_LANDED, datetime(2026, 1, 1, tzinfo=UTC), ["model:a"], source="git", source_ref="abc")
    first = s.append_event(ev)
    ev.payload = {"changed": True}
    second = s.append_event(ev)
    assert first == second
    stored = s.events_for(["model:a"])
    assert len(stored) == 1 and stored[0]["payload"] == {}


def test_bulk_write_matches_upserts_one_by_one():
    def rows(s):
        return (
            s.con.execute("SELECT id, kind, name, properties, tier, owner FROM nodes ORDER BY id").fetchall(),
            s.con.execute("SELECT id, src, dst, kind, confidence, properties FROM edges ORDER BY id").fetchall(),
            s.con.execute("SELECT subject_id, adapter, source, ref FROM provenance ORDER BY ALL").fetchall(),
        )

    first = [Node(NodeKind.MODEL, "a", properties={"v": 1}, tier=2, provenance=[prov()]), Node(NodeKind.MODEL, "b", owner="team/x", provenance=[prov()])]
    again = [Node(NodeKind.MODEL, "a", properties={"v": 2}, provenance=[prov(), Provenance("dbt", "manifest.json", "abc")]), Node(NodeKind.MODEL, "b", owner="team/y", provenance=[prov()])]
    edges = [Edge("model:b", "model:a", EdgeKind.CONSUMES, Confidence.PARSED, provenance=[prov()])]
    one, bulk = Store(":memory:"), Store(":memory:")
    for n in first + again:
        one.upsert_node(n)
    for e in edges + edges:
        one.upsert_edge(e)
    bulk.write_graph(first, edges)
    bulk.write_graph(again, edges)
    assert rows(one) == rows(bulk)
    assert rows(bulk)[0][0][3:5] == ('{"v": 2}', 2), "properties replaced, tier kept when the new row has none"
    with pytest.raises(ProvenanceRequired):
        bulk.write_graph([Node(NodeKind.MODEL, "c")], [])

    dupes = Store(":memory:")
    dupes.write_graph(first + again, edges + edges)
    assert rows(dupes) == rows(one), "duplicate ids within one batch merge like successive upserts"


def test_source_resolves_by_warehouse_schema_as_well_as_source_name():
    from changelens.resolver import resolve_ref

    s = Store(":memory:")
    s.upsert_node(Node(NodeKind.SOURCE_TABLE, "odoo.load_runs", {"schema": "raw", "identifier": "load_runs"}, provenance=[prov()]))
    s.upsert_node(Node(NodeKind.SOURCE_TABLE, "odoo.account_move", {"schema": "raw", "identifier": "account_move"}, provenance=[prov()]))
    assert resolve_ref(s, "source:odoo.load_runs").node_id == "source_table:odoo.load_runs"
    r = resolve_ref(s, "source:raw.load_runs")
    assert (r.node_id, r.method) == ("source_table:odoo.load_runs", "relation")
    assert resolve_ref(s, "raw.account_move").node_id == "source_table:odoo.account_move"
    miss = resolve_ref(s, "source:raw.nothing_here")
    assert miss.node_id is None


def test_source_relation_match_must_be_unambiguous():
    from changelens.resolver import resolve_ref

    s = Store(":memory:")
    for src in ("odoo", "stripe"):
        s.upsert_node(Node(NodeKind.SOURCE_TABLE, f"{src}.events", {"schema": "raw", "identifier": "events"}, provenance=[prov()]))
    r = resolve_ref(s, "source:raw.events")
    assert r.node_id is None
    assert {c["id"] for c in r.candidates} == {"source_table:odoo.events", "source_table:stripe.events"}
