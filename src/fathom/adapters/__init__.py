"""Adapters are the only writers to the graph.

Each adapter is a pull job that reads one source and emits nodes, edges and
events with provenance. Adapters never reason about what they read; the
resolver, the walk and the constraint checks do that.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from ..model import Edge, Event, Node


@dataclass
class SyncResult:
    adapter: str
    nodes: int = 0
    edges: int = 0
    events: int = 0
    skipped: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass
class Emitted:
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def digest_paths(paths: list[Path]) -> str:
    h = hashlib.sha1()
    for p in sorted(paths):
        if p.is_file():
            h.update(str(p).encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def repo_relative(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(path)
