"""Where a name occurs in a file, by syntax rather than by text.

A word search finds ``fetch`` in a comment, in a string, in a docstring, in a
file that defines its own ``fetch`` and in a method call on some other object.
Parsing the file tells those apart. This module classifies every occurrence of
a name in a file (definition, import, call, method call, attribute, other use)
and says whether the file imports that name or defines one of its own, which
is what the symbol facts need to tell a caller from a coincidence.

Parsing uses tree-sitter through ``tree-sitter-language-pack`` when it is
installed (the ``syntax`` extra); without it ``occurrences`` returns ``None``
and the caller falls back to the text search. Nothing here is a type checker:
"imports it" means the file names the symbol in an import statement, not that
the import resolves to the changed definition.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

# tree-sitter grammar name by file suffix.
GRAMMARS = {".py": "python", ".pyi": "python", ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".ts": "typescript", ".tsx": "tsx", ".go": "go", ".rs": "rust", ".rb": "ruby"}
# Node types whose ``name`` field is a definition, per grammar.
DEFINITIONS = {
    "python": {"function_definition", "class_definition"},
    "javascript": {"function_declaration", "generator_function_declaration", "class_declaration", "method_definition", "variable_declarator"},
    "typescript": {"function_declaration", "generator_function_declaration", "class_declaration", "method_definition", "variable_declarator", "abstract_class_declaration", "interface_declaration", "type_alias_declaration", "enum_declaration", "method_signature"},
    "go": {"function_declaration", "method_declaration", "type_spec"},
    "rust": {"function_item", "function_signature_item", "struct_item", "enum_item", "trait_item", "type_item", "const_item", "static_item"},
    "ruby": {"method", "singleton_method", "class", "module"},
}
DEFINITIONS["tsx"] = DEFINITIONS["typescript"]
IMPORTS = {
    "python": {"import_statement", "import_from_statement"},
    "javascript": {"import_statement"},
    "typescript": {"import_statement"},
    "tsx": {"import_statement"},
    "go": {"import_declaration"},
    "rust": {"use_declaration"},
    "ruby": set(),
}
# (container node, field of the member, field of the object it is accessed on) for attribute and method access, per grammar.
MEMBERS = {
    "python": ("attribute", "attribute", "object"),
    "javascript": ("member_expression", "property", "object"),
    "typescript": ("member_expression", "property", "object"),
    "tsx": ("member_expression", "property", "object"),
    "go": ("selector_expression", "field", "operand"),
    "rust": ("field_expression", "field", "value"),
    "ruby": ("call", "method", "receiver"),
}
CALLS = {"python": "call", "javascript": "call_expression", "typescript": "call_expression", "tsx": "call_expression", "go": "call_expression", "rust": "call_expression", "ruby": "call"}
IDENTIFIERS = {"identifier", "property_identifier", "field_identifier", "type_identifier", "shorthand_property_identifier", "shorthand_property_identifier_pattern", "constant", "scoped_identifier"}
KINDS = ("definition", "import", "call", "method_call", "attribute", "use")
# Definitions that do not shadow a module-level import of the same name: methods, and anything nested in another definition.
METHODS = {"method_definition", "method_signature", "method_declaration", "method", "singleton_method"}


@dataclass
class Occurrence:
    line: int
    kind: str  # one of KINDS
    receiver: str | None = None  # for a qualified access (``http.fetch``, ``http::fetch``): the text before the name


@dataclass
class FileSyntax:
    grammar: str
    occurrences: list[Occurrence]
    imports_name: bool  # an import statement names the symbol
    defines_name: bool  # the file defines the symbol at module level (a method or nested definition does not shadow an import)
    imported_modules: set[str] = field(default_factory=set)  # module paths, names and aliases the file's import statements mention, as written


@cache
def _parser(grammar: str) -> Any | None:
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        return None
    try:
        return get_parser(grammar)
    except Exception as e:  # an unknown or broken grammar in the pack: the text search takes over and the facts say so
        logging.getLogger(__name__).warning("tree-sitter grammar %r unavailable (%s); symbol references for its files come from text search", grammar, e)
        return None


@lru_cache(maxsize=64)
def _tree(grammar: str, text: str) -> Any | None:
    """The parse tree of ``text``, cached so a file searched for several symbols in one review is parsed once."""
    parser = _parser(grammar)
    if parser is None:
        return None
    try:
        return parser.parse(text.encode("utf-8", errors="replace"))
    except Exception as e:
        logging.getLogger(__name__).debug("tree-sitter could not parse a %s file (%s); its references come from text search", grammar, e)
        return None


def clear_cache() -> None:
    """Drop cached parse trees; called once a review has finished with them so a long-running server does not hold file text and trees."""
    _tree.cache_clear()


def available() -> bool:
    return _parser("python") is not None


def grammar_for(path: str) -> str | None:
    return GRAMMARS.get(Path(path).suffix.lower())


def _walk(node: Any):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def _in(node: Any, types: set[str]) -> bool:
    p = node.parent
    while p is not None:
        if p.type in types:
            return True
        p = p.parent
    return False


def _is(a: Any, b: Any) -> bool:
    """The same node, by id rather than wrapper identity (bindings may hand out fresh wrappers)."""
    return a is not None and b is not None and a.id == b.id


def _shadows(grammar: str, definition: Any) -> bool:
    """Whether this definition node is module-level (not a method, not nested in another definition), so it would shadow an import of its name."""
    if definition.type in METHODS:
        return False
    return not _in(definition, DEFINITIONS[grammar])


def _binds(node: Any) -> bool:
    """Whether this identifier inside an import statement is the name the import binds, rather than a module path segment or the original name behind an alias."""
    p = node.parent
    if p is None:
        return True
    if p.type in ("aliased_import", "use_as_clause", "import_specifier"):
        alias = p.child_by_field_name("alias")
        return _is(alias, node) if alias is not None else _is(p.child_by_field_name("name"), node)
    if p.type == "dotted_name":
        g = p.parent
        if g is not None and (_is(g.child_by_field_name("module_name"), p) or g.type == "aliased_import"):
            return False
        if g is not None and g.type == "import_statement":  # ``import app.fetch`` binds ``app``
            return len(p.named_children) == 1
        return _is(p.named_children[-1], node)
    if p.type == "scoped_identifier":
        g = p.parent
        aliased_or_path = g is not None and (g.type == "use_as_clause" or _is(g.child_by_field_name("path"), p))
        return _is(p.child_by_field_name("name"), node) and not aliased_or_path
    return True


def _classify(grammar: str, node: Any) -> tuple[str, str | None]:
    """The kind of this occurrence and, for a qualified access, the text before the name."""
    parent = node.parent
    if parent is None:
        return "use", None
    if _in(node, IMPORTS[grammar]):
        return "import", None
    if parent.type in DEFINITIONS[grammar] and _is(parent.child_by_field_name("name"), node):
        return "definition", None
    call = CALLS[grammar]
    if parent.type == call and _is(parent.child_by_field_name("function"), node):
        return "call", None
    grand = parent.parent
    called = grand is not None and grand.type == call and _is(grand.child_by_field_name("function"), parent)
    if grammar == "rust" and parent.type == "scoped_identifier" and _is(parent.child_by_field_name("name"), node):
        path = parent.child_by_field_name("path")
        return ("call" if called else "use"), (path.text.decode(errors="replace") if path is not None else None)
    container, member, obj = MEMBERS[grammar]
    if parent.type == container and _is(parent.child_by_field_name(member), node):
        receiver = parent.child_by_field_name(obj)
        if receiver is None:  # Ruby ``fetch(1)``: a call with no receiver
            return "call", None
        if grammar == "ruby" or called:
            return "method_call", receiver.text.decode(errors="replace")
        return "attribute", receiver.text.decode(errors="replace")
    return "use", None


_PATHS = {"dotted_name", "scoped_identifier", "string_fragment", "interpreted_string_literal_content", "raw_string_literal_content", "identifier", "package_identifier"}


def _imported_modules(grammar: str, root: Any) -> set[str]:
    """The module paths, names and aliases the file's import statements mention, each as written (``app.http``, ``./http``, ``example.com/x/svc``, ``crate::http``). An alias is recorded as ``alias=path`` so a qualified call through it can be matched to the module."""
    out: set[str] = set()
    for n in _walk(root):
        if n.type not in IMPORTS[grammar]:
            continue
        for d in _walk(n):
            if d.type in _PATHS and (d.parent is None or d.parent.type not in _PATHS):
                out.add(d.text.decode(errors="replace"))
            alias = path = None
            if d.type in ("aliased_import", "use_as_clause"):
                alias, path = d.child_by_field_name("alias"), d.child_by_field_name("name") or d.child_by_field_name("path")
                if d.type == "aliased_import" and n.child_by_field_name("module_name") is not None and path is not None:  # from a import b as c
                    out.add(f"{alias.text.decode(errors='replace')}={n.child_by_field_name('module_name').text.decode(errors='replace')}.{path.text.decode(errors='replace')}")
                    continue
            elif d.type == "import_spec":
                alias, path = d.child_by_field_name("name"), d.child_by_field_name("path")
            elif d.type == "namespace_import":
                alias = next((c for c in d.children if c.type == "identifier"), None)
                path = next((c for c in _walk(n) if c.type == "string_fragment"), None)
            if alias is not None and path is not None:
                out.add(f"{alias.text.decode(errors='replace')}={path.text.decode(errors='replace')}")
    return out


def occurrences(path: str, text: str, name: str) -> FileSyntax | None:
    """Every occurrence of ``name`` in ``text`` with its kind, or None when the file's language cannot be parsed here."""
    grammar = grammar_for(path)
    if grammar is None:
        return None
    tree = _tree(grammar, text)
    if tree is None:
        return None
    target = name.encode()
    out: list[Occurrence] = []
    imports_name = defines_name = False
    for n in _walk(tree.root_node):
        if n.type not in IDENTIFIERS or n.text != target:
            continue
        kind, receiver = _classify(grammar, n)
        imports_name |= kind == "import" and _binds(n)
        defines_name |= kind == "definition" and _shadows(grammar, n.parent)
        out.append(Occurrence(n.start_point[0] + 1, kind, receiver))
    return FileSyntax(grammar, out, imports_name, defines_name, _imported_modules(grammar, tree.root_node) if out else set())


__all__ = ["available", "clear_cache", "occurrences", "grammar_for", "FileSyntax", "Occurrence", "KINDS"]
