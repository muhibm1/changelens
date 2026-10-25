"""What a search is about: seeds that pull the blast radius forward, and filters that fence the index.

``paths`` and ``assets`` are seeds. The index expands seed paths over its import edges
(importers out to two hops, a seed's own imports at one) and ranks chunks in that
neighbourhood up, fading with distance. ``from_change`` adds the data graph: the assets the
changed files build, everything downstream of them out to three hops and their direct inputs,
from the same walk the pre-flight verdict uses. ``from_impact`` takes the code graph's impact
result as is. ``repos``, ``kinds``, ``since`` and ``path_prefixes`` are hard filters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Scope:
    paths: list[str] = field(default_factory=list)  # seeds: repo-relative paths ("src/a.py"); ``repo`` names their repository
    assets: list[str] = field(default_factory=list)  # seeds: graph asset ids
    repos: list[str] = field(default_factory=list)  # filter
    kinds: list[str] = field(default_factory=list)  # filter: code, doc, commit, pr, issue, review, comment, session, graph
    since: str | None = None  # filter: ISO date; undated chunks pass
    path_prefixes: list[str] = field(default_factory=list)  # filter
    only_in_scope: bool = False
    repo: str = ""  # the repository the seed paths belong to; empty matches the path in any repository
    path_hops: dict[str, tuple[int, str]] = field(default_factory=dict)  # expanded seeds: path -> (hops, reason)
    asset_hops: dict[str, tuple[int, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Seeds are matched to indexed paths as strings, so './a.py', '/a.py' and 'a\\b.py' are written the index's way.
        self.paths = [q for q in (_norm(p) for p in self.paths) if q]  # an empty line from a file list is not a seed
        self.path_prefixes = [_norm(p) for p in self.path_prefixes]

    @property
    def seeded(self) -> bool:
        return bool(self.paths or self.assets or self.path_hops or self.asset_hops)

    def repo_of(self, path: str) -> str:
        return self.repo

    def key(self) -> str:
        """A stable string for caching a search under this scope."""
        import json

        return json.dumps([sorted(self.paths), sorted(self.assets), sorted(self.repos), sorted(self.kinds), self.since, sorted(self.path_prefixes), self.only_in_scope, self.repo,
                           sorted(self.path_hops.items()), sorted(self.asset_hops.items())])  # fmt: skip

    @classmethod
    def from_change(cls, store: Any | None, changed_paths: list[str], *, repo: str = "", max_hops: int = 3, **filters: Any) -> Scope:
        """Seeds for a change: the changed files, plus (with a store) the data assets they build and that asset's blast radius."""
        scope = cls(paths=list(changed_paths), repo=repo, **filters)
        if store is None:
            return scope
        try:
            from fathom.resolver import PathResolver

            resolver = PathResolver(store)
        except Exception:  # a store without a data graph (an app repository): import edges carry the scope
            return scope
        seeds = sorted({a for p in scope.paths for a in resolver.resolve(p)})
        for a in seeds:
            scope.asset_hops[a] = (0, "built by the changed file")
        for direction, hops, label in (("downstream", max_hops, "downstream of the change"), ("upstream", 1, "read by the change")):
            for nid, info in store.walk(seeds, direction, max_hops=hops, always_include_tier=None).items():
                if nid not in scope.asset_hops or scope.asset_hops[nid][0] > info["hops"]:
                    scope.asset_hops[nid] = (info["hops"], label)
        # The files that build assets in the blast radius are in scope too.
        for nid, (h, why) in list(scope.asset_hops.items()):
            node = store.node(nid)
            path = (node or {}).get("properties", {}).get("path")
            if path and path not in scope.paths and (path not in scope.path_hops or scope.path_hops[path][0] > h):
                scope.path_hops[path] = (max(h, 1), why)
        scope.merge_impact(_impact(store, scope.paths, repo))
        return scope

    @classmethod
    def from_impact(cls, result: Any, **filters: Any) -> Scope:
        """Seeds from the code graph's impact result (``fathom.codegraph.impact``)."""
        scope = cls(**filters)
        scope.merge_impact(result)
        return scope

    def merge_impact(self, result: Any) -> None:
        """Add the files of impacted ``file:`` and ``symbol:`` nodes, at their hop counts."""
        items = result.get("impacted", []) if isinstance(result, dict) else (result or [])
        for item in items:
            if not isinstance(item, dict):
                continue
            nid = item.get("id") or ""
            props = item.get("properties") or {}
            path = props.get("file") or item.get("file")
            if not path and nid.startswith(("file:", "symbol:")):
                rest = nid.split(":", 1)[1].split("::", 1)[0]
                path = rest.split("/", 1)[1] if "/" in rest else None
            if not path:
                continue
            h = int(item.get("hops") or 1)
            if path not in self.path_hops or self.path_hops[path][0] > h:
                self.path_hops[path] = (h, f"{item.get('edge_kind') or 'depends on'} the change (code graph)")


def _norm(path: str) -> str:
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.lstrip("/")


def _impact(store: Any, changed_paths: list[str], repo: str) -> Any:
    """The code graph's impact of the change, when that module is installed and the store holds code nodes."""
    try:
        from fathom.codegraph import impact  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        return impact(store, targets=list(changed_paths), repo=repo or None)  # impact resolves repo-relative paths itself
    except Exception:  # an older or empty code graph: the index's own import edges still apply
        return None
