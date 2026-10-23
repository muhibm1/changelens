"""Cut a document into chunks a search hit can cite: a whole definition, a whole section, a whole file diff.

Code is cut by syntax (tree-sitter, the ``syntax`` extra): one chunk per top-level definition,
a long class split into its methods with the class line kept as context, small neighbours
merged, and the imports and constants between definitions kept together. A file whose
language cannot be parsed falls back to windows aligned on blank lines. Markdown is cut at its
``#``/``##``/``###`` headings, short sections merged forward and long ones split by paragraph.
A commit or PR body that carries a patch is cut per file of the diff, the message kept as the
first chunk. Every chunk keeps its line span, so a citation points at lines, not just a file.

Each chunk also gets a short header (repository, path, symbol or heading). The header is
indexed and embedded with the text, which is what lets ``where is the store walk`` find a
method whose body never says "store".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from orion.record.index import STOPWORDS
from orion.record.index import stem as _stem

from .docs import SourceDoc

MAX_CODE_LINES = 90  # a definition longer than this is split (methods of a class, else windows)
SMALL_CODE_LINES = 12  # neighbouring definitions this short are merged, up to MERGE_CODE_LINES
MERGE_CODE_LINES = 40
WINDOW_LINES = 60
MAX_WORDS = 350
MIN_WORDS = 50

# Wrapper nodes whose real definition sits in a field (decorators, exports).
_WRAPPERS = {"decorated_definition": "definition", "export_statement": "declaration"}
_CONTAINERS = {"class_definition", "class_declaration", "abstract_class_declaration", "class", "module", "impl_item", "trait_item", "interface_declaration"}
_DEFINITIONS = {
    "function_definition", "class_definition",
    "function_declaration", "generator_function_declaration", "class_declaration", "abstract_class_declaration",
    "method_definition", "interface_declaration", "type_alias_declaration", "enum_declaration", "lexical_declaration",
    "method_declaration", "type_declaration",
    "function_item", "struct_item", "enum_item", "trait_item", "impl_item", "type_item", "const_item", "static_item", "mod_item",
    "method", "singleton_method", "class", "module",
}  # fmt: skip


@dataclass
class Chunk:
    id: str
    doc_id: str
    start_line: int  # 1-based, inclusive
    end_line: int
    symbol: str  # definition names for code ("Store.walk"), the heading path for prose
    text: str
    header: str  # indexed and embedded with the text, never shown as part of it

    @property
    def indexed_text(self) -> str:
        return f"{self.header}\n{self.text}"


# ----------------------------------------------------------------- terms
def stem(word: str) -> str:
    """The record's light suffix strip, plus undoubling (``running`` -> ``runn`` -> ``run``)."""
    out = _stem(word)
    if out != word and len(out) >= 4 and out[-1] == out[-2] and out[-1] not in "lsz":
        out = out[:-1]
    return out


_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_PARTS = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|[A-Z]+|\d+")


def terms(text: str) -> list[str]:
    """Search terms: lower-cased, lightly stemmed words; ``snake_case`` and ``camelCase`` names also split into parts."""
    out: list[str] = []
    for raw in _WORD.findall(text):
        low = raw.lower()
        if low not in STOPWORDS:
            out.append(stem(low))
        parts = [p.lower() for p in _PARTS.findall(raw.replace("_", " "))]
        if len(parts) > 1:
            out.extend(stem(p) for p in parts if p not in STOPWORDS)
    return out


# ----------------------------------------------------------------- code
def _lines(text: str) -> list[str]:
    """Lines split on newlines only, as tree-sitter and git count them (``splitlines`` also breaks on form feeds and lone CRs)."""
    lines = [line.removesuffix("\r") for line in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _name(node: Any) -> str | None:
    for fld in ("name", "type"):
        n = node.child_by_field_name(fld)
        if n is not None:
            return n.text.decode(errors="replace")
    if node.type in ("lexical_declaration", "type_declaration"):
        for c in node.named_children:
            n = c.child_by_field_name("name")
            if n is not None:
                return n.text.decode(errors="replace")
    return None


def _unwrap(node: Any) -> Any:
    while node.type in _WRAPPERS:
        inner = node.child_by_field_name(_WRAPPERS[node.type])
        if inner is None:
            inner = next((c for c in node.named_children if c.type in _DEFINITIONS or c.type in _WRAPPERS), None)
        if inner is None:
            return node
        node = inner
    return node


def _segments(nodes: list[Any], prefix: str = "") -> list[tuple[int, int, str | None, Any]]:
    """(first row, last row, symbol or None, definition node) per node, comments attached to the definition below them."""
    segs: list[tuple[int, int, str | None, Any]] = []
    pending_comment: int | None = None
    for node in nodes:
        if node.type in ("comment", "line_comment", "block_comment"):
            if pending_comment is None:
                pending_comment = node.start_point[0]
            continue
        inner = _unwrap(node)
        is_def = inner.type in _DEFINITIONS and (inner.type != "lexical_declaration" or "=>" in inner.text.decode(errors="replace") or "function" in inner.text.decode(errors="replace"))
        start = node.start_point[0]
        if pending_comment is not None:
            if is_def:
                start = pending_comment
            else:
                segs.append((pending_comment, node.start_point[0] - 1, None, None))
            pending_comment = None
        name = _name(inner) if is_def else None
        segs.append((start, node.end_point[0], f"{prefix}{name}" if name else None, inner if is_def else None))
    if pending_comment is not None and nodes:
        segs.append((pending_comment, nodes[-1].end_point[0], None, None))
    return segs


def _body_children(node: Any) -> list[Any]:
    body = node.child_by_field_name("body")
    if body is None:
        body = next((c for c in node.named_children if c.type in ("class_body", "declaration_list", "body_statement", "block")), None)
    return list(body.named_children) if body is not None else []


def _windows(start: int, end: int, lines: list[str], size: int = WINDOW_LINES) -> list[tuple[int, int]]:
    """Row spans of about ``size`` lines, each ending on a blank line where one is near."""
    out = []
    s = start
    while s <= end:
        e = min(end, s + size - 1)
        if e < end:
            for cut in range(e, max(s + size // 2, s), -1):
                if not lines[cut].strip():
                    e = cut
                    break
        out.append((s, e))
        s = e + 1
    return out


def code_spans(text: str, grammar: str | None) -> list[tuple[int, int, str]]:
    """(first row, last row, symbol) spans covering the file, 0-based rows; windows when the file cannot be parsed."""
    lines = _lines(text)
    if not lines:
        return []
    tree = None
    if grammar:
        from orion.syntax import _tree

        tree = _tree(grammar, text)
    if tree is None or tree.root_node.has_error and not tree.root_node.named_children:
        return [(s, e, "") for s, e in _windows(0, len(lines) - 1, lines)]
    raw: list[tuple[int, int, str]] = []
    for s, e, sym, node in _segments(list(tree.root_node.named_children)):
        if e - s + 1 <= MAX_CODE_LINES:
            raw.append((s, e, sym or ""))
            continue
        if node is not None and node.type in _CONTAINERS:
            kids = _segments(_body_children(node), prefix=f"{sym}." if sym else "")
            if kids:
                cursor = s
                for ks, ke, ksym, _ in kids:
                    if ks > cursor and any(line.strip() for line in lines[cursor:ks]):  # the class line, docstring, fields
                        raw.append((cursor, ks - 1, sym or ""))
                    for ws, we in _windows(ks, ke, lines, MAX_CODE_LINES) if ke - ks + 1 > MAX_CODE_LINES else [(ks, ke)]:
                        raw.append((ws, we, ksym or sym or ""))
                    cursor = ke + 1
                if cursor <= e and any(line.strip() for line in lines[cursor : e + 1]):
                    raw.append((cursor, e, sym or ""))
                continue
        raw.extend((ws, we, sym or "") for ws, we in _windows(s, e, lines, MAX_CODE_LINES))
    # Fill gaps (blank lines between segments) and merge small neighbours.
    merged: list[tuple[int, int, str]] = []
    for s, e, sym in raw:
        if merged:
            ps, pe, psym = merged[-1]
            small = (pe - ps + 1) <= SMALL_CODE_LINES and (e - s + 1) <= SMALL_CODE_LINES
            if small and e - ps + 1 <= MERGE_CODE_LINES:
                merged[-1] = (ps, e, ", ".join(dict.fromkeys(x for x in (*psym.split(", "), sym) if x)))
                continue
        merged.append((s, e, sym))
    return [(s, e, sym) for s, e, sym in merged if any(line.strip() for line in lines[s : e + 1])]


# ----------------------------------------------------------------- prose
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")


def markdown_spans(text: str) -> list[tuple[int, int, str]]:
    """(first row, last row, heading path) per section; short sections merged forward, long ones split by paragraph."""
    lines = _lines(text)
    blocks: list[list[Any]] = [[0, -1, []]]
    path: list[str] = []
    fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        m = None if fence else _HEADING.match(line)
        if m:
            level = len(m[1])
            path = path[: level - 1] + [m[2].strip()]
            blocks.append([i, i, list(path)])
        else:
            blocks[-1][1] = i
    blocks = [b for b in blocks if b[1] >= b[0] and any(x.strip() for x in lines[b[0] : b[1] + 1])]
    words = lambda b: sum(len(x.split()) for x in lines[b[0] : b[1] + 1])  # noqa: E731
    merged: list[list[Any]] = []
    for b in blocks:
        if merged and words(merged[-1]) < MIN_WORDS and words(merged[-1]) + words(b) <= MAX_WORDS:
            merged[-1][1] = b[1]
            merged[-1][2] = merged[-1][2] or b[2]
            continue
        merged.append(b)
    out: list[tuple[int, int, str]] = []
    for s, e, hp in merged:
        heading = " > ".join(hp)
        if words([s, e]) <= MAX_WORDS:
            out.append((s, e, heading))
            continue
        cur_s, count = s, 0
        for i in range(s, e + 1):
            count += len(lines[i].split())
            # Cut at a blank line; a run with none (a long list or table) is cut at any line past twice the limit.
            if i > cur_s and ((count >= MAX_WORDS and not lines[i].strip()) or count >= 2 * MAX_WORDS):
                out.append((cur_s, i, heading))
                cur_s, count = i + 1, 0
        if cur_s <= e:
            out.append((cur_s, e, heading))
    return out


def diff_spans(text: str) -> list[tuple[int, int, str]]:
    """The message, then one span per file of the patch (long file diffs windowed)."""
    lines = _lines(text)
    starts = [i for i, line in enumerate(lines) if line.startswith("diff --git ")]
    if not starts:
        return markdown_spans(text)
    out: list[tuple[int, int, str]] = []
    if starts[0] > 0:
        out.extend(markdown_spans("\n".join(lines[: starts[0]])))
    for n, s in enumerate(starts):
        e = (starts[n + 1] - 1) if n + 1 < len(starts) else len(lines) - 1
        m = re.match(r"diff --git a/(\S+) b/(\S+)", lines[s])
        name = m[2] if m else ""
        out.extend((ws, we, name) for ws, we in _windows(s, e, lines, WINDOW_LINES * 2))
    return out


# ----------------------------------------------------------------- entry point
def chunk(doc: SourceDoc) -> list[Chunk]:
    """The chunks of one document."""
    lines = _lines(doc.text)
    if not lines:
        return []
    from orion.syntax import grammar_for

    grammar = doc.language if doc.kind == "code" and doc.language not in (None, "markdown", "diff") else (grammar_for(doc.path) if doc.kind == "code" else None)
    if doc.kind == "code" and not doc.path.endswith(".md"):
        spans = code_spans(doc.text, grammar)
        code = True
    elif doc.language == "diff" or any(line.startswith("diff --git ") for line in lines[:400]):
        spans, code = diff_spans(doc.text), False
    else:
        spans, code = markdown_spans(doc.text), False
    where = f"{doc.repo}/{doc.path}" if doc.path else doc.repo
    out = []
    for n, (s, e, sym) in enumerate(spans):
        body = "\n".join(lines[s : e + 1]).strip("\n")
        if not body.strip():
            continue
        cid = f"{doc.id}#L{s + 1}-{e + 1}" if code else f"{doc.id}#{n}"
        header = " ".join(x for x in (where, doc.title if doc.title and doc.title != doc.path else "", sym) if x)
        out.append(Chunk(cid, doc.id, s + 1, e + 1, sym, body, header))
    return out
