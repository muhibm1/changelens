"""Which definitions a diff removes or changes, and who still references them.

A reviewer who sees only the diff cannot know whether a renamed function has
callers outside it, so a model warns that "other callers may break" whether
or not any exist. This module answers that from the repository: for each
definition a diff removes or re-signs, it lists every reference by file and
line and says which of those lines the diff touches. Candidate files come
from a word search over the working tree (``git grep``, so ignored files stay
out). Each candidate is then parsed (``syntax``, when the ``syntax`` extra is
installed) so a call is told from a comment, a string, or a same-named
definition elsewhere, and each reference carries a ``scope``: whether the file
is the definition's own, imports the name, defines its own, or merely mentions
it. Without the parser the references are a fact about text and say so
(``scope: unknown``). Nothing here is a type checker.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import syntax
from .credscan import redact
from .diffparse import FileDiff

CODE_EXT = (".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".go", ".rs", ".java", ".kt", ".rb", ".sql")
_ID = r"([A-Za-z_$][\w$]*)"
# Definition line patterns by file extension, matched after leading whitespace is stripped.
# A JS/TS variable counts as a definition only at module level: an indented ``const res = ...`` is a function local, which other files cannot reference. An indented ``export const`` (inside a ``namespace`` or ``declare module``) still counts.
_TOP_LEVEL_VAR = re.compile(r"^(?:export\s+)?(?:const|let|var)\s+" + _ID + r"\s*[:=]")
DEF_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "py": [re.compile(r"^(?:async\s+)?def\s+" + _ID + r"\s*\("), re.compile(r"^class\s+" + _ID)],
    "js": [
        re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*" + _ID),
        re.compile(r"^(?:export\s+)?(?:declare\s+)?(?:abstract\s+)?class\s+" + _ID),
        _TOP_LEVEL_VAR,
        re.compile(r"^(?:export\s+)?(?:declare\s+)?(?:type|interface|enum)\s+" + _ID),
        # A class method: ``fetch(u: string): string {`` with optional modifiers. Parameters with nested parens or strings are left to the parser.
        re.compile(r"^(?:(?:public|private|protected|static|async|readonly|override|get|set)\s+)*(?!(?:if|else|for|while|do|switch|case|catch|with|function|return|constructor|super|new|await|yield|throw|typeof|delete|void|import|export)\b)" + _ID + r"\s*(?:<[^>]*>)?\s*\([^()\"'`]*\)\s*(?::\s*[^{;=]+)?\s*\{"),
    ],
    "go": [re.compile(r"^func\s+(?:\([^)]*\)\s*)?" + _ID + r"\s*\("), re.compile(r"^type\s+" + _ID + r"\s")],
    "rs": [re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+" + _ID), re.compile(r"^(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|type)\s+" + _ID)],
    "rb": [re.compile(r"^def\s+(?:self\.)?" + _ID), re.compile(r"^(?:class|module)\s+" + _ID)],
}
_LANG = {".py": "py", ".pyi": "py", ".ts": "js", ".tsx": "js", ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js", ".go": "go", ".rs": "rs", ".rb": "rb"}
MAX_REFS = 50
# How a referencing file relates to the changed definition. ``unknown`` means the file was not parsed (text search only).
SCOPES = ("same_file", "imports_it", "imports_module", "same_package", "defines_own", "imports_other", "no_import", "unknown")
LINKED = ("same_file", "imports_it", "imports_module", "same_package")
SELF = ("self", "this", "cls", "super", "Self", "@")
IMPORT_RE = re.compile(r"^\s*(?:from\s+\S+\s+import\b|import\b|export\s+.*\bfrom\b|.*\brequire\s*\()")


@dataclass
class SymbolChange:
    name: str
    path: str
    kind: str  # removed | signature_changed
    line: int | None  # new-side line of the definition, or where the removed one stood
    old: str
    new: str | None
    owner: str | None = None  # the class, struct, impl, trait or module the definition sits in, when it is a method
    refs: list[dict[str, Any]] = field(default_factory=list)  # {path, line, text, kind, scope, receiver, receiver_type, owner_scope, in_diff}; kind in syntax.KINDS, scope in SCOPES
    search_failed: bool = False  # git grep could not run, so only the overlay (the diff's own files) was searched
    truncated: bool = False  # the search stopped at MAX_REFS references; more exist

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "path": self.path, "kind": self.kind, "line": self.line, "old": self.old, "new": self.new, "owner": self.owner, "refs": self.refs, "search_failed": self.search_failed, "truncated": self.truncated}

    @property
    def outside(self) -> list[dict[str, Any]]:
        return [r for r in self.refs if not r["in_diff"]]

    @property
    def linked_outside(self) -> list[dict[str, Any]]:
        """References outside the diff that resolve to this definition as far as syntax can tell (see ``is_linked``)."""
        return [r for r in self.outside if is_linked(r)]

    @property
    def parsed(self) -> bool:
        """Every reference came from parsed syntax (true when there are none)."""
        return all(r["scope"] != "unknown" for r in self.refs)


def _lang(path: str) -> str | None:
    return _LANG.get(Path(path).suffix.lower())


def _definition(lang: str, text: str) -> str | None:
    s = text.strip()
    for pat in DEF_PATTERNS[lang]:
        if pat is _TOP_LEVEL_VAR and _indent(text) and not s.startswith("export"):
            continue
        if m := pat.match(s):
            return m.group(1)
    return None


# A line that opens a class-like block, after leading whitespace: ``class X``, ``export abstract class X``, ``impl<T> Trait for X``, ``type X struct``, ``module X``.
OWNER_RE = re.compile(r"^(?:(?:export|default|declare|abstract|public|private|protected|static|final|data|sealed|open|unsafe|pub(?:\([^)]*\))?)\s+)*(?:class|interface|trait|module|object|struct|enum|type|impl(?:<[^>]*>)?(?:\s+\S+\s+for)?)\s+" + _ID)
GO_RECEIVER_RE = re.compile(r"^func\s+\([^)]*?\*?\s*" + _ID + r"(?:\[[^\]]*\])?\s*\)")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _owner(lang: str, text: str, before: list[str]) -> str | None:
    """The class-like block a definition line sits in: for Go, the receiver type on the line; otherwise the nearest less-indented line above it, when that opens a class, impl, trait or module (a function or a brace does not)."""
    if lang == "go":
        m = GO_RECEIVER_RE.match(text.strip())
        return m.group(1) if m else None
    indent = _indent(text)
    if indent == 0:
        return None
    for line in reversed(before):
        if line.strip() and _indent(line) < indent:
            m = OWNER_RE.match(line.strip())
            return m.group(1) if m else None
    return None


def changed_definitions(fd: FileDiff, new_text: str | None) -> list[SymbolChange]:
    """Definitions on removed lines of ``fd`` that no added line defines again (removed), or defines differently (signature changed)."""
    lang = _lang(fd.path)
    if not lang or fd.binary or fd.status == "deleted":
        return []
    old: dict[str, tuple[str, int | None, str | None]] = {}
    new: dict[str, str] = {}
    new_lines = new_text.splitlines() if new_text is not None else None
    for h in fd.hunks:
        for i, (op, _n, text) in enumerate(h.lines):
            name = _definition(lang, text)
            if not name:
                continue
            if op == "-":
                where = next((m for o, m, _ in h.lines[i:] if m is not None), h.new_end)
                # The enclosing class is looked for on the hunk's old side first, then above the spot in the new text (the class is still there when only a method went).
                owner = _owner(lang, text, [t for o, _, t in h.lines[:i] if o != "+"])
                if owner is None and new_lines is not None and where and _indent(text) and lang != "go":
                    owner = _owner(lang, text, new_lines[: where - 1])
                old.setdefault(name, (text.strip(), where, owner))
            elif op == "+":
                new.setdefault(name, text.strip())
    still_defined = {_definition(lang, t) for t in new_text.splitlines()} - {None} if new_text is not None else set(new)
    out = []
    for name, (old_line, where, owner) in old.items():
        if name in new:
            if new[name] != old_line:
                out.append(SymbolChange(name, fd.path, "signature_changed", where, old_line, new[name], owner))
        elif name not in still_defined:
            out.append(SymbolChange(name, fd.path, "removed", where, old_line, None, owner))
    return out


def _git_grep_files(repo_root: Path, name: str) -> list[str] | None:
    """Files that mention ``name`` as a whole word, or None when the search itself failed (not a git repository, git missing, timeout)."""
    cmd = ["git", "-C", str(repo_root), "grep", "-l", "-w", "-F", "-I", "--untracked", "-e", name, "--", *[f"*{e}" for e in CODE_EXT]]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode not in (0, 1):  # 1 is "no match"
        return None
    return [p for p in res.stdout.splitlines() if p]


@dataclass
class Ref:
    path: str
    line: int
    text: str
    kind: str = "use"  # one of syntax.KINDS
    parsed: bool = False  # kind came from the parser; otherwise this is a whole-word text match
    imports_name: bool = False  # the file names the symbol in an import statement
    defines_name: bool = False  # the file has its own definition of the symbol
    receiver: str | None = None  # for a qualified access, the text before the name (``http`` in ``http.fetch``)
    imported_modules: set[str] = field(default_factory=set)  # module paths, names and aliases the file's imports mention, as written
    import_sources: set[str] = field(default_factory=set)  # the modules the file's imports of the symbol name, as written
    wildcard_sources: set[str] = field(default_factory=set)  # modules the file imports wholesale, as written
    unsourced_import: bool = False  # an import binds the symbol but names no module that can be resolved
    receiver_type: str | None = None  # for a member access, the class the file types the receiver as (see ``_receiver_type``)
    owner_syntax: Any = None  # when that class is the definition's owner: the file's imports and definitions of it (a ``syntax.FileSyntax``), for scoping


@dataclass
class Search:
    refs: list[Ref] = field(default_factory=list)
    failed: bool = False  # the repository could not be searched; refs come from ``files`` only
    truncated: bool = False  # stopped at MAX_REFS


_NAME_RE = re.compile(r"[A-Za-z_$][\w$]*")


_CLASS = r"([A-Z][\w$]*)"
_AFTER_CLASS = r"(?![\w$])(?!\.(?!new\b))(?!::(?!new\b))"  # ``Client.fetch()`` or ``Client::other()`` is a value, not the class


def _enclosing(lines: list[str], line: int, lang: str | None) -> int:
    """Index of the definition line the access on ``line`` (1-based) sits in: the nearest line above with less indentation that defines something, walking out through blocks; 0 when there is none."""
    indent = _indent(lines[line - 1]) if line <= len(lines) else 0
    for i in range(min(line, len(lines)) - 2, -1, -1):
        if lines[i].strip() and _indent(lines[i]) < indent:
            indent = _indent(lines[i])
            if lang and _definition(lang, lines[i]):
                return i
    return 0


_CLASS_DEF = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s")


def _class_body(lines: list[str], line: int) -> list[str]:
    """The lines of the class the access on ``line`` (1-based) sits in, by indentation, so ``self``/``this`` members are looked up in that class only; the whole file when no class encloses it (Go, free functions)."""
    indent = _indent(lines[line - 1]) if line <= len(lines) else 0
    for i in range(min(line, len(lines)) - 2, -1, -1):
        if lines[i].strip() and _indent(lines[i]) < indent:
            indent = _indent(lines[i])
            if _CLASS_DEF.match(lines[i]):
                end = next((j for j in range(i + 1, len(lines)) if lines[j].strip() and _indent(lines[j]) <= indent and not lines[j].strip().startswith("}")), len(lines))
                return lines[i:end]
    return lines


def _receiver_type(lines: list[str], receiver: str, line: int, lang: str | None) -> str | None:
    """The class the receiver of a member access on ``line`` is, as far as the file says: the class itself or a construction of it (``Client.fetch``, ``Client().fetch()``, ``new Client()``, ``Client::new()``, ``Client.new``), or a plain name bound to a capitalised type between the enclosing definition and the access (``c: Client``, ``c = Client(...)``, ``c := &Client{}``, ``let c = Client::new()``, a Go parameter ``c *Client``), the nearest such binding winning. Nothing else is inferred, so a lower-case receiver with no such binding is None."""
    r = receiver.strip().lstrip("&*")
    while r.startswith("(") and r.endswith(")"):
        r = r[1:-1].strip()
    if r.startswith("new "):
        m = re.match(_CLASS, r[4:].lstrip())
        return m.group(1) if m else None
    if m := re.fullmatch(_CLASS + r"(?:\(.*\)|::new\b.*|\.new\b.*)?", r, re.DOTALL):
        return m.group(1)
    if m := re.fullmatch(r"((?:self|this)\.)?([a-z_$][\w$]*)\(.*\)", r, re.DOTALL):
        return _returns(_class_body(lines, line) if m.group(1) else lines, m.group(2))  # ``make_client().fetch()``, ``self.make().fetch()``
    if m := re.fullmatch(r"(?:(self|this)\.|@|([a-z_]\w*)\.)([A-Za-z_$][\w$]*)", r):
        if m.group(2) and lang != "go":
            return None  # ``a.b.fetch()``: only a field of self, or of a Go method's receiver, is looked up
        return _field_type(lines, m.group(3), lang, _class_body(lines, line) if not m.group(2) else lines)
    if not _NAME_RE.fullmatch(r) or r in SELF:
        return None
    name = re.escape(r)
    binding = rf"(?<![\w$.]){name}\s*(?::\s*(?:&(?:'\w+\s+)?|\*|mut\s+)*|=\s*(?:new\s+|&)?|:=\s*&?){_CLASS}{_AFTER_CLASS}"
    if lang == "go":
        binding += rf"|(?<![\w$.]){name}\s+\*?{_CLASS}{_AFTER_CLASS}"
    # ``c = make_client()``: the type the called function in this file declares it returns; any other call leaves the type unknown
    call = rf"(?<![\w$.]){name}\s*(?::=|=)\s*(?:await\s+|try\s+)?((?:[\w$]+\.)*)([a-z_$][\w$]*)\s*\("
    window = "\n".join(lines[_enclosing(lines, line, lang) : line])
    found = [(m.start(), next(g for g in m.groups() if g)) for m in re.finditer(binding, window)]
    for m in re.finditer(call, window):
        found.append((m.start(), _returns(lines if not m.group(1) else _class_body(lines, line), m.group(2)) if m.group(1) in ("", "self.", "this.") else None))
    return max(found, key=lambda x: x[0])[1] if found else None


def _returns(lines: list[str], func: str) -> str | None:
    """The class a function or method defined in this file declares it returns (``-> Client``, ``): Client``, Go ``*Client``, Rust ``-> Client``); None when it declares none, or two definitions disagree."""
    f = re.escape(func)
    text = "\n".join(lines)
    opt = r"(?:['\"]|Optional\[|Awaitable\[|Promise<)*"
    pats = (
        rf"\bdef\s+{f}\s*\([^)]*\)\s*->\s*{opt}{_CLASS}",
        rf"(?:\bfunction\s+|^\s*(?:(?:public|private|protected|static|async)\s+)*){f}\s*(?:<[^>]*>)?\([^)]*\)\s*:\s*{opt}{_CLASS}",
        rf"\bfunc\s+(?:\([^)]*\)\s*)?{f}\s*\([^)]*\)\s*\(?\*?{_CLASS}",
        rf"\bfn\s+{f}\s*(?:<[^>]*>)?\([^)]*\)\s*->\s*(?:Option<|Result<)?{_CLASS}",
    )
    types = {m.group(1) for p in pats for m in re.finditer(p, text, re.M)}
    return types.pop() if len(types) == 1 else None


def _field_type(lines: list[str], field_name: str, lang: str | None, body: list[str] | None = None) -> str | None:
    """The class this file declares a field as (``self.client: Client``, ``self.client = Client()``, a ``client: Client`` field or
    constructor parameter property, a Go struct field ``client *Client``), or as the annotated parameter assigned to it
    (``self.client = client`` with ``client: Client``); None when the file says nothing, or two declarations disagree.
    ``body``: the enclosing class's lines, where the field is looked up; free functions are still looked up in the whole file."""
    f = re.escape(field_name)
    body = body if body is not None else lines
    text = "\n".join(body)
    mods = r"(?:(?:private|public|protected|readonly|static|declare|pub|override)\s+)*"
    pats = [
        rf"(?:self|this)\.{f}\s*:\s*{_CLASS}{_AFTER_CLASS}",
        rf"(?:(?:self|this)\.|@){f}\s*=\s*(?:new\s+|&)?{_CLASS}{_AFTER_CLASS}",
        rf"^\s*{mods}{f}\??\s*:\s*{_CLASS}{_AFTER_CLASS}",
        rf"(?:private|public|protected|readonly)\s+{f}\??\s*:\s*{_CLASS}{_AFTER_CLASS}",
    ]
    if lang == "go":
        pats.append(rf"^\s*{f}\s+\*?{_CLASS}{_AFTER_CLASS}")
    types = {m.group(1) for p in pats for m in re.finditer(p, text, re.M)}
    for m in re.finditer(rf"(?:self|this)\.{f}\s*=\s*([a-z_]\w*)\s*$", text, re.M):
        line = text.count("\n", 0, m.start()) + 1  # the parameter is looked up only in the function this assignment sits in
        scope = "\n".join(body[_enclosing(body, line, lang) : line])
        types |= {a.group(1) for a in re.finditer(rf"(?<![\w$.]){re.escape(m.group(1))}\s*:\s*{_CLASS}{_AFTER_CLASS}", scope)}
    for m in re.finditer(rf"(?:self|this)\.{f}\s*=\s*(self\.|this\.)?([a-z_]\w*)\s*\(", text):
        if t := _returns(body if m.group(1) else lines, m.group(2)):
            types.add(t)
    return types.pop() if len(types) == 1 else None


def _file_refs(path: str, text: str, name: str, word: re.Pattern[str], owner: str | None = None) -> list[Ref]:
    """References to ``name`` in one file: by syntax when the file parses here, else by whole-word text match. A definition of the name is not a reference to it."""
    fs = syntax.occurrences(path, text, name)
    lines = text.splitlines()
    if fs is not None:
        seen: set[tuple[int, str, str | None]] = set()
        out = []
        owner_syntax: Any = None
        for occ in fs.occurrences:
            if occ.kind == "definition" or (occ.line, occ.kind, occ.receiver) in seen:
                continue
            seen.add((occ.line, occ.kind, occ.receiver))
            line = lines[occ.line - 1] if occ.line <= len(lines) else ""
            typed = _receiver_type(lines, occ.receiver, occ.line, _lang(path)) if owner and occ.receiver else None  # only a method has a class to compare with
            if typed and typed == owner and owner_syntax is None:
                owner_syntax = syntax.occurrences(path, text, owner)
            out.append(Ref(path, occ.line, line.strip()[:160], occ.kind, True, fs.imports_name, fs.defines_name, occ.receiver, fs.imported_modules, fs.import_sources, fs.wildcard_sources, fs.unsourced_import, typed, owner_syntax if typed == owner else None))
        return out
    return [Ref(path, i, line.strip()[:160], "import" if IMPORT_RE.match(line) else "use") for i, line in enumerate(lines, 1) if word.search(line)]


def search(repo_root: Path, name: str, files: dict[str, str], owner: str | None = None) -> Search:
    """Every reference to ``name`` in the repository or in ``files`` (text that overrides or extends the tree), and whether that search was complete. Member accesses carry the class their file types the receiver as; with ``owner``, those typed as that class also carry its imports for scoping."""
    word = re.compile(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])")
    found = _git_grep_files(repo_root, name)
    result = Search(failed=found is None)
    paths = list(dict.fromkeys([*(found or []), *files]))
    for p in paths:
        if not p.lower().endswith(CODE_EXT):
            continue
        text = files.get(p)
        if text is None:
            try:
                full = (repo_root / p).resolve()
                if not full.is_relative_to(repo_root.resolve()) or not full.is_file():
                    continue
                text = full.read_text(errors="replace")
            except OSError:
                continue
        for ref in _file_refs(p, text, name, word, owner):
            result.refs.append(ref)
            if len(result.refs) >= MAX_REFS:
                result.truncated = True
                return result
    return result


def references(repo_root: Path, name: str, files: dict[str, str]) -> list[tuple[str, int, str]]:
    return [(r.path, r.line, r.text) for r in search(repo_root, name, files).refs]


def is_linked(r: dict[str, Any]) -> bool:
    """Whether a reference resolves to the changed definition as far as syntax can tell. A ``.name`` access in another file is not linked even when that file imports the name: it may be a method on some other object, so it reaches the model as a fact instead."""
    if r["scope"] in ("unknown", "imports_module"):
        return True
    # A qualified access is linked when the file types its receiver as the class the definition belongs to and that class resolves here (``c: Client`` with
    # ``Client`` imported from the definition's module, ``Client::name``).
    if r.get("owner_scope") in LINKED:
        return True
    if r["kind"] in ("use", "call", "import"):
        return r["scope"] in LINKED
    # Otherwise a member access is linked only in the definition's own file, on the instance itself (``self.name``, ``this.name``) or on a type or module
    # named there (``Foo.name``, ``Self::name``). ``obj.name`` anywhere else is a fact for the model.
    receiver = r.get("receiver") or ""
    return r["scope"] == "same_file" and (receiver in SELF or receiver[:1].isupper())


def _imports_module(imported: str, module_path: Path) -> bool:
    """Whether an import written as ``imported`` can name the module at ``module_path``: its trailing segments match the module's trailing path parts as far as both go (``other.http`` does not name ``app/http.py``; ``./http`` and ``example.com/x/svc`` are accepted)."""
    segs = [x for x in re.findall(r"[\w$]+", imported) if x not in ("crate", "self", "super")]
    parts = list(module_path.parts)
    if not segs or segs[-1] != parts[-1]:
        return False
    return all(a == b for a, b in zip(reversed(segs), reversed(parts), strict=False))


PACKAGE_FILES = ("__init__", "index", "mod")


def _module_path(path: str) -> Path:
    """The module a definition file stands for, without suffix: ``app/http.py`` is ``app/http``; a package file (``app/__init__.py``, ``lib/index.ts``, ``http/mod.rs``) is its directory."""
    p = Path(path).with_suffix("")
    return p.parent if p.name in PACKAGE_FILES else p


def _resolves(source: str, ref_path: Path, module_path: Path) -> bool:
    """Whether the module an import names, written as ``source`` in the file ``ref_path``, is ``module_path`` (a repo path without suffix). Relative imports are resolved against the importing file; absolute ones match by trailing segments, since the import root is not known."""
    targets = {module_path, *(module_path / f for f in PACKAGE_FILES)}
    from_dir = ref_path.parent
    if source.startswith("."):
        if source.startswith(("./", "../")):  # JavaScript and TypeScript
            resolved = Path(os.path.normpath(from_dir / source))
        else:  # Python: one dot is the current package, each further dot a parent
            dots = len(source) - len(source.lstrip("."))
            base = from_dir
            for _ in range(dots - 1):
                base = base.parent
            resolved = base.joinpath(*source[dots:].split(".")) if source[dots:] else base
        return resolved in targets
    segs = [x for x in re.findall(r"[\w$]+", source) if x != "crate"]
    if segs and segs[0] in ("super", "self"):
        # Rust: a file ``foo/a.rs`` is module ``a`` inside ``foo``, so ``self`` is ``foo/a`` and ``super`` is ``foo``; for ``foo/mod.rs`` the module is ``foo`` itself.
        own = _module_path(str(ref_path))
        base = own if segs[0] == "self" else own.parent
        return base.joinpath(*segs[1:]) in targets if len(segs) > 1 else False
    return _imports_module(source, module_path) or any(_imports_module(source, t) for t in targets - {module_path})


def _scope(sc: SymbolChange, ref: Ref) -> str:
    if not ref.parsed:
        return "unknown"
    if ref.path == sc.path:
        return "same_file"
    if _lang(ref.path) != _lang(sc.path):  # a file in another language cannot import this one
        return "no_import"
    if ref.receiver is not None:
        # ``http.fetch`` / ``http::fetch`` / ``pkg.Fetch``: linked when the qualifier is the definition's module (Go: its package directory) and the file imports something by that name.
        module_path = Path(sc.path).parent if sc.path.endswith(".go") else _module_path(sc.path)
        qualifier = re.split(r"[.:]", ref.receiver)[-1]
        for imported in ref.imported_modules:
            alias, _, path = imported.partition("=")
            if path:  # ``alias=path`` from ``import app.http as h`` and the like
                if alias == qualifier and _imports_module(path, module_path):
                    return "imports_module"
            elif qualifier == module_path.name and _imports_module(imported, module_path):
                return "imports_module"
    if ref.defines_name:  # a local definition shadows an import of the same name
        return "defines_own"
    module_path = _module_path(sc.path)
    # Resolve the import to a file: ``from other.http import fetch`` is a different fetch; ``from .http import fetch`` next to app/http.py is ours.
    # An import that names no module (Go and Ruby import packages and files, not names) cannot be resolved, so it keeps counting as an import of the symbol.
    if ref.imports_name and (ref.unsourced_import or any(_resolves(src, Path(ref.path), module_path) for src in ref.import_sources)):
        return "imports_it"
    if any(_resolves(src, Path(ref.path), module_path) for src in ref.wildcard_sources):  # ``from app.http import *`` (may rebind a name imported from elsewhere)
        return "imports_it"
    if ref.imports_name:
        return "imports_other"
    if sc.path.endswith(".go") and ref.path.endswith(".go") and Path(ref.path).parent == Path(sc.path).parent:
        return "same_package"
    return "no_import"


def _owner_scope(sc: SymbolChange, ref: Ref) -> str | None:
    """How the referencing file relates to the definition's owner class, for a member access whose receiver the file types as that class."""
    if not sc.owner or ref.receiver_type != sc.owner:
        return None
    if ref.path == sc.path:
        return "same_file"
    o = ref.owner_syntax
    if o is None:
        return "unknown"
    autoloaded = not syntax.IMPORTS.get(o.grammar)  # Ruby names a class without importing it, so the class name is taken to mean this class
    return _scope(sc, Ref(ref.path, ref.line, "", "use", True, o.imports_name or autoloaded, o.defines_name, None, o.imported_modules, o.import_sources, o.wildcard_sources, o.unsourced_import or autoloaded))


def symbol_changes(files: list[FileDiff], repo_root: Path, overlay: dict[str, str]) -> list[SymbolChange]:
    """Removed and re-signed definitions across the diff, each with its references, how each referencing file relates to the definition, and whether the diff touches the reference."""
    by_path = {f.path: f for f in files}
    out = []
    try:
        for fd in files:
            for sc in changed_definitions(fd, overlay.get(fd.path)):
                found = search(repo_root, sc.name, overlay, sc.owner)
                sc.search_failed, sc.truncated = found.failed, found.truncated
                for ref in found.refs:
                    # The parser already leaves definitions out. For a text match, skip the definition's own line and the old definition's replacement when it was renamed in place.
                    if not ref.parsed and ((ref.path, ref.line) == (sc.path, sc.line) or (ref.path == sc.path and _definition(_lang(ref.path) or "py", ref.text) == sc.name)):
                        continue
                    fd2 = by_path.get(ref.path)
                    # Reference text is quoted in the facts and the review JSON, so a credential on that line is redacted here, once.
                    sc.refs.append({"path": ref.path, "line": ref.line, "text": redact(ref.text), "kind": ref.kind, "scope": _scope(sc, ref), "receiver": ref.receiver, "receiver_type": ref.receiver_type, "owner_scope": _owner_scope(sc, ref), "in_diff": bool(fd2 and fd2.contains(ref.line))})
                sc.refs.sort(key=lambda r: (r["path"], r["line"]))
                out.append(sc)
    finally:
        syntax.clear_cache()  # trees are only useful across the symbols of one review
    return out
