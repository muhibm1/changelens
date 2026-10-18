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
DEF_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "py": [re.compile(r"^(?:async\s+)?def\s+" + _ID + r"\s*\("), re.compile(r"^class\s+" + _ID)],
    "js": [
        re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*" + _ID),
        re.compile(r"^(?:export\s+)?(?:declare\s+)?(?:abstract\s+)?class\s+" + _ID),
        re.compile(r"^(?:export\s+)?(?:const|let|var)\s+" + _ID + r"\s*[:=]"),
        re.compile(r"^(?:export\s+)?(?:declare\s+)?(?:type|interface|enum)\s+" + _ID),
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
    refs: list[dict[str, Any]] = field(default_factory=list)  # {path, line, text, kind, scope, receiver, in_diff}; kind in syntax.KINDS, scope in SCOPES
    search_failed: bool = False  # git grep could not run, so only the overlay (the diff's own files) was searched
    truncated: bool = False  # the search stopped at MAX_REFS references; more exist

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "path": self.path, "kind": self.kind, "line": self.line, "old": self.old, "new": self.new, "refs": self.refs, "search_failed": self.search_failed, "truncated": self.truncated}

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
        if m := pat.match(s):
            return m.group(1)
    return None


def changed_definitions(fd: FileDiff, new_text: str | None) -> list[SymbolChange]:
    """Definitions on removed lines of ``fd`` that no added line defines again (removed), or defines differently (signature changed)."""
    lang = _lang(fd.path)
    if not lang or fd.binary or fd.status == "deleted":
        return []
    old: dict[str, tuple[str, int | None]] = {}
    new: dict[str, str] = {}
    for h in fd.hunks:
        for i, (op, _n, text) in enumerate(h.lines):
            name = _definition(lang, text)
            if not name:
                continue
            if op == "-":
                where = next((m for o, m, _ in h.lines[i:] if m is not None), h.new_end)
                old.setdefault(name, (text.strip(), where))
            elif op == "+":
                new.setdefault(name, text.strip())
    still_defined = {_definition(lang, t) for t in new_text.splitlines()} - {None} if new_text is not None else set(new)
    out = []
    for name, (old_line, where) in old.items():
        if name in new:
            if new[name] != old_line:
                out.append(SymbolChange(name, fd.path, "signature_changed", where, old_line, new[name]))
        elif name not in still_defined:
            out.append(SymbolChange(name, fd.path, "removed", where, old_line, None))
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


@dataclass
class Search:
    refs: list[Ref] = field(default_factory=list)
    failed: bool = False  # the repository could not be searched; refs come from ``files`` only
    truncated: bool = False  # stopped at MAX_REFS


def _file_refs(path: str, text: str, name: str, word: re.Pattern[str]) -> list[Ref]:
    """References to ``name`` in one file: by syntax when the file parses here, else by whole-word text match. A definition of the name is not a reference to it."""
    fs = syntax.occurrences(path, text, name)
    lines = text.splitlines()
    if fs is not None:
        seen: set[tuple[int, str, str | None]] = set()
        out = []
        for occ in fs.occurrences:
            if occ.kind == "definition" or (occ.line, occ.kind, occ.receiver) in seen:
                continue
            seen.add((occ.line, occ.kind, occ.receiver))
            line = lines[occ.line - 1] if occ.line <= len(lines) else ""
            out.append(Ref(path, occ.line, line.strip()[:160], occ.kind, True, fs.imports_name, fs.defines_name, occ.receiver, fs.imported_modules, fs.import_sources, fs.wildcard_sources, fs.unsourced_import))
        return out
    return [Ref(path, i, line.strip()[:160], "import" if IMPORT_RE.match(line) else "use") for i, line in enumerate(lines, 1) if word.search(line)]


def search(repo_root: Path, name: str, files: dict[str, str]) -> Search:
    """Every reference to ``name`` in the repository or in ``files`` (text that overrides or extends the tree), and whether that search was complete."""
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
        for ref in _file_refs(p, text, name, word):
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
    if r["kind"] in ("use", "call", "import"):
        return r["scope"] in LINKED
    # A member access is linked only in the definition's own file, on the instance itself (``self.name``, ``this.name``) or on a type or module named there
    # (``Foo.name``, ``Self::name``); ``obj.name`` anywhere else is a fact for the model.
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
    if ref.receiver is not None and _lang(ref.path) == _lang(sc.path):
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


def symbol_changes(files: list[FileDiff], repo_root: Path, overlay: dict[str, str]) -> list[SymbolChange]:
    """Removed and re-signed definitions across the diff, each with its references, how each referencing file relates to the definition, and whether the diff touches the reference."""
    by_path = {f.path: f for f in files}
    out = []
    try:
        for fd in files:
            for sc in changed_definitions(fd, overlay.get(fd.path)):
                found = search(repo_root, sc.name, overlay)
                sc.search_failed, sc.truncated = found.failed, found.truncated
                for ref in found.refs:
                    # The parser already leaves definitions out. For a text match, skip the definition's own line and the old definition's replacement when it was renamed in place.
                    if not ref.parsed and ((ref.path, ref.line) == (sc.path, sc.line) or (ref.path == sc.path and _definition(_lang(ref.path) or "py", ref.text) == sc.name)):
                        continue
                    fd2 = by_path.get(ref.path)
                    # Reference text is quoted in the facts and the review JSON, so a credential on that line is redacted here, once.
                    sc.refs.append({"path": ref.path, "line": ref.line, "text": redact(ref.text), "kind": ref.kind, "scope": _scope(sc, ref), "receiver": ref.receiver, "in_diff": bool(fd2 and fd2.contains(ref.line))})
                sc.refs.sort(key=lambda r: (r["path"], r["line"]))
                out.append(sc)
    finally:
        syntax.clear_cache()  # trees are only useful across the symbols of one review
    return out
