"""What a dbt project's schema files say about its models: documented columns, tests, keys.

Read by ``warehouse_rules`` so a row-count, cast or documentation check can name the yml line it rests on. Nothing here
runs dbt or a warehouse: the files are parsed as YAML, YAML anchors and aliases are resolved, and each documented
column keeps the line it is written on (the alias line when the column comes in through ``*anchor``).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import yaml

NULLABLE_WORDS = re.compile(r"\b(null|nullable|optional|blank|missing|unknown|empty)\b", re.I)


@dataclass
class ColumnDoc:
    name: str
    line: int | None
    description: str = ""
    tests: list[str] = field(default_factory=list)  # short test names: unique, not_null, relationships, ...
    constraints: list[str] = field(default_factory=list)  # primary_key, not_null, unique

    @property
    def not_null(self) -> bool:
        return "not_null" in self.tests or "not_null" in self.constraints or "primary_key" in self.constraints

    @property
    def says_nullable(self) -> bool:
        """The description itself says the value can be missing (``null``, ``optional``, ``blank`` ...)."""
        return bool(NULLABLE_WORDS.search(self.description)) and not self.not_null


@dataclass
class ModelDoc:
    name: str
    path: str
    line: int | None
    columns: dict[str, ColumnDoc] = field(default_factory=dict)
    unique_keys: list[frozenset[str]] = field(default_factory=list)  # each a set of columns declared unique together
    contract_enforced: bool = False
    primary_key_line: int | None = None


@dataclass
class SchemaIndex:
    models: dict[str, ModelDoc] = field(default_factory=dict)
    unreadable: list[str] = field(default_factory=list)  # yml paths that did not parse; what they say is unknown

    def get(self, name: str) -> ModelDoc | None:
        return self.models.get(name.lower())


class _Loader(yaml.SafeLoader):
    """SafeLoader that remembers the line of every alias (``*name``), keyed by its parent node and position."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self.alias_lines: dict[tuple[int, Any], int] = {}

    def compose_node(self, parent: Any, index: Any) -> Any:
        if self.check_event(yaml.events.AliasEvent):
            self.alias_lines[(id(parent), index)] = self.peek_event().start_mark.line + 1
        return super().compose_node(parent, index)


def _short(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _tests(raw: Any) -> list[tuple[str, dict[str, Any]]]:
    """(short name, its config) for every entry in a ``tests`` / ``data_tests`` list."""
    out: list[tuple[str, dict[str, Any]]] = []
    for t in raw if isinstance(raw, list) else []:
        if isinstance(t, str):
            out.append((_short(t), {}))
        elif isinstance(t, dict):
            for k, v in t.items():
                out.append((_short(str(k)), v if isinstance(v, dict) else {}))
    return out


def _combination(cfg: dict[str, Any]) -> list[str]:
    cols = cfg.get("combination_of_columns") or (cfg.get("arguments") or {}).get("combination_of_columns") or []
    return [str(c) for c in cols] if isinstance(cols, list) else []


def _node_for(mapping: yaml.Node, key: str) -> yaml.Node | None:
    if isinstance(mapping, yaml.MappingNode):
        for k, v in mapping.value:
            if getattr(k, "value", None) == key:
                return v
    return None


def _model_from(data: dict[str, Any], node: yaml.Node | None, path: str, loader: _Loader) -> ModelDoc | None:
    name = data.get("name")
    if not isinstance(name, str):
        return None
    doc = ModelDoc(name, path, node.start_mark.line + 1 if node is not None else None)
    cfg = data.get("config") if isinstance(data.get("config"), dict) else {}
    contract = cfg.get("contract") if isinstance(cfg.get("contract"), dict) else {}
    doc.contract_enforced = bool(contract.get("enforced"))
    for tname, tcfg in _tests(data.get("tests") or data.get("data_tests")):
        if tname == "unique_combination_of_columns" and (cols := _combination(tcfg)):
            doc.unique_keys.append(frozenset(cols))
    for c in data.get("constraints") or []:
        if isinstance(c, dict) and c.get("type") in ("primary_key", "unique") and isinstance(c.get("columns"), list):
            doc.unique_keys.append(frozenset(str(x) for x in c["columns"]))
    cols_node = _node_for(node, "columns") if node is not None else None
    cols = data.get("columns") if isinstance(data.get("columns"), list) else []
    for i, col in enumerate(cols):
        if not isinstance(col, dict) or not isinstance(col.get("name"), str):
            continue
        line = None
        if isinstance(cols_node, yaml.SequenceNode) and i < len(cols_node.value):
            line = loader.alias_lines.get((id(cols_node), i)) or cols_node.value[i].start_mark.line + 1
        desc = col.get("description")
        cd = ColumnDoc(col["name"], line, desc if isinstance(desc, str) else "")
        cd.tests = [n for n, _ in _tests(col.get("tests") or col.get("data_tests"))]
        cd.constraints = [str(c.get("type")) for c in col.get("constraints") or [] if isinstance(c, dict)]
        doc.columns[cd.name] = cd
        if "unique" in cd.tests or "primary_key" in cd.constraints or "unique" in cd.constraints:
            doc.unique_keys.append(frozenset([cd.name]))
    return doc


def parse_schema(path: str, text: str) -> list[ModelDoc] | None:
    """The models one yml file documents, or None when the file is not readable YAML (Jinja in a value, for one)."""
    if "models:" not in text:
        return []
    loader = _Loader(text)
    try:
        root = loader.get_single_node()
        data = loader.construct_document(root) if root is not None else None
    except yaml.YAMLError:
        return None
    finally:
        loader.dispose()
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        return []
    models_node = _node_for(root, "models")
    out: list[ModelDoc] = []
    for i, m in enumerate(data["models"]):
        if not isinstance(m, dict):
            continue
        node = models_node.value[i] if isinstance(models_node, yaml.SequenceNode) and i < len(models_node.value) else None
        if doc := _model_from(m, node, path, loader):
            out.append(doc)
    return out


def build_index(files: Mapping[str, str] | Iterable[tuple[str, str]]) -> SchemaIndex:
    """An index of every model the given yml files (path -> text) document. The first file to document a name wins."""
    idx = SchemaIndex()
    for path, text in files.items() if isinstance(files, Mapping) else files:
        docs = parse_schema(path, text)
        if docs is None:
            idx.unreadable.append(path)
            continue
        for d in docs:
            idx.models.setdefault(d.name.lower(), d)
    return idx
