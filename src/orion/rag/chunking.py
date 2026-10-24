"""Cut a document into chunks a search hit can cite: a whole definition, a whole section, a whole file diff.

Code is cut by syntax (tree-sitter, the ``syntax`` extra): one chunk per top-level definition,
a long class split into its methods with the class line kept as context, small neighbours
merged, and the imports and constants between definitions kept together. A file whose
language cannot be parsed falls back to windows aligned on blank lines. Markdown is cut at its
``#``/``##``/``###`` headings, one chunk per section (a short title is kept with its first subsection) and long
ones split by paragraph.
A commit or PR body that carries a patch is cut per file of the diff, the message kept as the
first chunk. Every chunk keeps its line span, so a citation points at lines, not just a file.

Each chunk also gets a short header (repository, path, symbol or heading). The header is
indexed and embedded with the text, which is what lets ``where is the store walk`` find a
method whose body never says "store". A chunk also carries views, extra texts embedded for it and
never shown: each definition's signature with the first sentence of its doc (or, for small
neighbours merged into one chunk, its own code), and a prose section's heading path. A question
phrased like the doc sentence or the heading finds the chunk even when the body's words differ.
"""

from __future__ import annotations

import codecs
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
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
    views: list[str] = field(default_factory=list)  # extra texts embedded for this chunk (see ``_views``); a dense match on any one finds it

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
    return [(s, e, sym) for s, e, sym, _ in _code_spans(text, grammar)]


def _code_spans(text: str, grammar: str | None) -> list[tuple[int, int, str, list[tuple[int, int, str]]]]:
    """``code_spans`` with the parts each span was merged from (one per definition), so each can be embedded on its own."""
    lines = _lines(text)
    if not lines:
        return []
    tree = None
    if grammar:
        from orion.syntax import _tree

        tree = _tree(grammar, text)
    if tree is None or tree.root_node.has_error and not tree.root_node.named_children:
        return [(s, e, "", [(s, e, "")]) for s, e in _windows(0, len(lines) - 1, lines)]
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
    merged: list[tuple[int, int, str, list[tuple[int, int, str]]]] = []
    for s, e, sym in raw:
        if merged:
            ps, pe, psym, parts = merged[-1]
            small = (pe - ps + 1) <= SMALL_CODE_LINES and (e - s + 1) <= SMALL_CODE_LINES
            if small and e - ps + 1 <= MERGE_CODE_LINES:
                merged[-1] = (ps, e, ", ".join(dict.fromkeys(x for x in (*psym.split(", "), sym) if x)), [*parts, (s, e, sym)])
                continue
        merged.append((s, e, sym, [(s, e, sym)]))
    return [m for m in merged if any(line.strip() for line in lines[m[0] : m[1] + 1])]


# ----------------------------------------------------------------- prose
_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*#*\s*$")


def markdown_spans(text: str) -> list[tuple[int, int, str]]:
    """(first row, last row, heading path) per section; a short title kept with its first subsection, long sections split by paragraph."""
    lines = _lines(text)
    blocks: list[list[Any]] = [[0, -1, []]]
    stack: list[tuple[int, str]] = []  # (level, heading): a heading's parents are the shallower headings above it
    fence = False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            fence = not fence
        m = None if fence else _HEADING.match(line)
        if m:
            level = len(m[1])
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, m[2].strip()))
            blocks.append([i, i, [h for _, h in stack]])
        else:
            blocks[-1][1] = i
    blocks = [b for b in blocks if b[1] >= b[0] and any(x.strip() for x in lines[b[0] : b[1] + 1])]
    words = lambda b: sum(len(x.split()) for x in lines[b[0] : b[1] + 1])  # noqa: E731
    merged: list[list[Any]] = []
    for b in blocks:
        # Only a title (or untitled preamble) with little under it merges into its first subsection, and takes the
        # subsection's name. Siblings never merge, so editing one section's length cannot move another's text to a new id.
        prev = merged[-1] if merged else None
        if prev and words(prev) < MIN_WORDS and b[2][: len(prev[2])] == prev[2] and words(prev) + words(b) <= MAX_WORDS:
            prev[1], prev[2] = b[1], b[2]
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


def _unquote(name: str) -> str:
    """A path as git printed it, quoted (C escapes, octal bytes) or not."""
    name = name.rstrip("\t")
    if len(name) > 1 and name[0] == name[-1] == '"':
        try:
            return codecs.escape_decode(name[1:-1].encode("utf-8"))[0].decode("utf-8", "replace")
        except ValueError:  # a malformed escape (text that only looks like a patch): keep the name as written
            return name[1:-1]
    return name


def _diff_name(line: str) -> str:
    """The new-side file name of a ``diff --git`` line, used when the patch has no ``+++`` line (a mode change, a
    pure rename, a binary file). Git quotes a name with unusual bytes (octal escapes, decoded
    here) and leaves one with spaces unquoted, so an unquoted name containing `` b/`` is read as the two equal halves."""
    rest = line.removeprefix("diff --git ")
    if rest.endswith('"') and (m := re.search(r' ("b/(?:[^"\\]|\\.)*")$', rest)):
        return _unquote(m[1])[2:]
    half = (len(rest) - 1) // 2
    if rest.startswith("a/") and rest[half:half + 3] == " b/" and rest[2:half] == rest[half + 3 :]:
        return rest[half + 3 :]  # old and new names equal: no rename
    m = re.match(r'"?a/.*?"? "?b/(.+?)"?$', rest)
    return m[1] if m else ""


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
        new = next((x[4:] for x in lines[s + 1 : min(e + 1, s + 12)] if x.startswith("+++ ") and x[4:] != "/dev/null"), "")
        name = _unquote(new)[2:] if _unquote(new).startswith("b/") else _diff_name(lines[s])
        out.extend((ws, we, name) for ws, we in _windows(s, e, lines, WINDOW_LINES * 2))
    return out


# ----------------------------------------------------------------- views
_COMMENT = ("#", "//", "/*", "*", "--", ";;")
_DOCSTRING = re.compile(r"""^[rRbBuU]?("{3}|'{3})""")


def summary(lines: list[str]) -> str | None:
    """A definition's signature and what its author wrote about it (the comment above it, a docstring below the signature); None when it has neither.

    Embedded on its own, this is the closest the code comes to the words a developer asks with:
    the body of a short helper is mostly operators, which pull its vector away from the question."""
    i, n = 0, len(lines)
    doc: list[str] = []
    while i < n and (not lines[i].strip() or lines[i].lstrip().startswith(_COMMENT) and not lines[i].lstrip().startswith("#[")):
        doc.append(lines[i].strip())
        i += 1
    while i < n and lines[i].lstrip().startswith(("@", "#[")):  # decorators and attributes
        i += 1
    sig_start = i
    while i < n and i - sig_start < 12:
        line = _TRAILING_COMMENT.sub("", lines[i]).rstrip()  # ``def walk(self):  # pragma: no cover``
        i += 1
        if line.endswith((":", "{", "=>", "}", ";")) or line.endswith(")") and i < n and _DOCSTRING.match(lines[i].lstrip()):
            break
    signature = [x.strip() for x in lines[sig_start:i]]
    while i < n and not lines[i].strip():
        i += 1
    if i < n and (m := _DOCSTRING.match(lines[i].lstrip())):
        quote = m[1]
        first = lines[i].strip()
        doc.append(first)
        if first.count(quote) < 2 or first == quote:
            for line in lines[i + 1 : i + 40]:
                doc.append(line.strip())
                if quote in line:
                    break
    doc = [x for x in doc if x and not _DIRECTIVE.search(x)]
    if not doc or not signature:
        return None
    return "\n".join([*signature, _first_sentence(doc)])


_TRAILING_COMMENT = re.compile(r"\s+(?:#|//)[^\"'`]*$")
_DIRECTIVE = re.compile(
    r"^\W*(?:noqa\b|pragma\b|type:\s*ignore\b|fmt:|pylint:|eslint[-\s]|@?ts-(?:ignore|expect-error|nocheck)\b|nolint\b|istanbul\s|prettier-ignore\b|clippy::|rubocop:|mypy:)", re.I
)
_MARKS = re.compile(r"^(?:#+|//+|/\*+|\*+/?|--|;;)\s?|\*+/$")
_END = re.compile(r"(?<![.\s]\w[.:])(?<=[^.\s][.:])(?:\s|$)")  # not after ``e.g.`` or ``i.e.``


def _first_sentence(doc: list[str]) -> str:
    """The summary sentence of a comment or docstring, without its quote and comment marks. A long docstring's later
    sentences are mostly edge cases written in the code's own terms, which pull the vector away from what it is for."""
    doc = [_DOCSTRING.sub(" ", doc[0]), *doc[1:]] if doc else doc  # an ``r``/``b``/``u`` prefix is not a word
    text = " ".join(_MARKS.sub("", line.replace('"""', " ").replace("'''", " ").strip()) for line in doc)
    text = " ".join(text.split())
    m = _END.search(text, 6)  # a sentence shorter than this is an abbreviation (``e.g.``), not a summary
    return text[: m.start()] if m else text[:400]


def _views(lines: list[str], parts: list[tuple[int, int, str]], where: str, chunk_text: str) -> list[str]:
    """Extra texts to embed for a code chunk, one per definition in it: its signature and doc where it has them,
    else, when small neighbours were merged into the chunk, the definition's own code. A dense match on any of
    them finds the chunk, so merging three helpers no longer averages their meanings into one vector."""
    out: list[str] = []
    for s, e, sym in parts:
        own = lines[s : e + 1]
        text = summary(own)
        name = sym.split(".")[-1]
        if text and name and name not in text.split("\n", 1)[0]:
            text = None  # a window from inside a long definition: its first line is body code, not the signature
        text = text or ("\n".join(own).strip("\n") if len(parts) > 1 and sym else None)
        if text and text != chunk_text:
            out.append(f"{where} {sym}".rstrip() + "\n" + text)
    return list(dict.fromkeys(out))


def _slug(heading: str) -> str:
    """A heading path as an id part: ``Orion > Running the tests`` -> ``orion/running-the-tests``."""
    keep = lambda c: c.isalnum() or c == "+" or unicodedata.category(c).startswith("M")  # noqa: E731
    # Any script's letters, digits and combining marks are kept (in NFC form), ``+`` and ``#`` (as ``sharp``, so an id holds
    # one ``#``) so C++ and C# differ; every other run becomes one dash.
    norm = unicodedata.normalize("NFC", heading).lower().replace("#", " sharp ")
    parts = ["-".join("".join(c if keep(c) else " " for c in p).split()) for p in norm.split(" > ")]
    return "/".join(p or "_" for p in parts) or "_"


# ----------------------------------------------------------------- entry point
def chunk(doc: SourceDoc) -> list[Chunk]:
    """The chunks of one document.

    A code chunk's id is its line span (``#L12-40``), which is what a citation of code points at.
    A prose chunk's id is its heading path (``#orion/running-the-tests``, a diff's file name for a
    patch), with ``~2``, ``~3`` for a second section of the same name or a long section's later
    parts, so editing or inserting a section leaves every other section's citations valid. The one
    exception is a heading that repeats: a new section with that same heading above existing ones
    renumbers their ``~N``, since nothing but order tells same-named sections apart."""
    lines = _lines(doc.text)
    if not lines:
        return []
    from orion.syntax import grammar_for

    grammar = doc.language if doc.kind == "code" and doc.language not in (None, "markdown", "diff") else (grammar_for(doc.path) if doc.kind == "code" else None)
    if doc.kind == "code" and not doc.path.endswith(".md"):
        spans = _code_spans(doc.text, grammar)
        code = True
    elif doc.language == "diff" or any(line.startswith("diff --git ") for line in lines[:400]):
        spans, code = [(s, e, sym, []) for s, e, sym in diff_spans(doc.text)], False
    else:
        spans, code = [(s, e, sym, []) for s, e, sym in markdown_spans(doc.text)], False
    where = f"{doc.repo}/{doc.path}" if doc.path else doc.repo
    out = []
    seen: Counter[str] = Counter()
    for s, e, sym, parts in spans:
        body = "\n".join(lines[s : e + 1]).strip("\n")
        if not body.strip():
            continue
        if code:
            cid = f"{doc.id}#L{s + 1}-{e + 1}"
            views = _views(lines, parts, where, body)
        else:
            slug = _slug(sym) if sym else "_"
            seen[slug] += 1
            cid = f"{doc.id}#{slug}" + (f"~{seen[slug]}" if seen[slug] > 1 else "")
            # The heading alone, embedded: a question phrased like a heading finds a long section whose body words differ.
            views = [sym] if sym and sym != body.strip() else []
        header = " ".join(x for x in (where, doc.title if doc.title and doc.title != doc.path else "", sym) if x)
        out.append(Chunk(cid, doc.id, s + 1, e + 1, sym, body, header, views))
    return out
