"""Read a checkout into ``SourceDoc``s, and the file-level import edges between its code files.

Files come from ``git ls-files`` (tracked files only, so build output and secrets in ignored
files stay out), with the blob hash as the content hash: re-indexing a checkout re-chunks only
the files whose content changed. Markdown files get the ingestion's ``md:<repo>:<path>`` id, so
a file the record ingested and the checkout also holds is indexed once.

Import edges are what scope a search to a change's blast radius in code: ``a.py`` importing
``b.py`` is an edge ``a -> b``, so a change to ``b.py`` reaches ``a.py`` at one hop. Edges are
resolved by path within the repository; an import that names a package outside it (the
standard library, a dependency) is not an edge.
"""

from __future__ import annotations

import posixpath
import subprocess
from pathlib import Path
from typing import Any

from .docs import SourceDoc, repo_name

CODE_SUFFIXES = {
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".rs", ".rb", ".java", ".kt", ".swift", ".c", ".h",
    ".cc", ".cpp", ".hpp", ".cs", ".php", ".scala", ".sql", ".sh", ".yml", ".yaml", ".toml", ".tf", ".proto", ".graphql",
}  # fmt: skip
DOC_SUFFIXES = {".md", ".mdx", ".rst", ".txt"}
MAX_BYTES = 200_000
SKIP_PARTS = {"node_modules", "vendor", "dist", "build", ".venv", "__pycache__", "target", ".git"}
SKIP_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "Cargo.lock", "go.sum"}


def _tracked(root: Path) -> list[tuple[str, str]]:
    """(path, blob sha) of every tracked file; a folder that is not a git checkout lists its files with no sha."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-s", "-z"], capture_output=True, timeout=60, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        # Not a checkout: there is no ignore list, so hidden folders (credentials, tool state) and links stay out.
        files = (p.relative_to(root) for p in sorted(root.rglob("*")) if p.is_file() and not p.is_symlink())
        return [(p.as_posix(), "") for p in files if not any(part.startswith(".") for part in p.parts)]
    # The index's blob hash is stale for a file edited since it was staged; such files are hashed by their content.
    try:
        dirty = set(subprocess.run(["git", "-C", str(root), "diff", "--name-only", "--relative", "-z"], capture_output=True, timeout=60, check=True).stdout.decode(errors="replace").split("\0"))
    except (OSError, subprocess.SubprocessError):
        dirty = set()
    found = []
    for entry in out.split(b"\0"):
        if not entry:
            continue
        meta, _, path = entry.partition(b"\t")
        parts = meta.split()
        if len(parts) >= 2 and parts[0] != b"160000":  # skip submodules
            rel = path.decode(errors="replace")
            found.append((rel, "" if rel in dirty else parts[1].decode()))
    return found


def wanted(path: str) -> bool:
    p = Path(path)
    if p.name in SKIP_NAMES or any(part in SKIP_PARTS for part in p.parts) or p.name.startswith(".env"):
        return False
    return p.suffix.lower() in CODE_SUFFIXES or p.suffix.lower() in DOC_SUFFIXES or p.name in ("Dockerfile", "Makefile")


def checkout_documents(root: Path, repo: str | None = None, *, only: set[str] | None = None, known: dict[str, str] | None = None) -> list[SourceDoc]:
    """The checkout's tracked text files as documents. ``known`` maps doc id to content hash; a file whose hash matches is skipped."""
    root = Path(root)
    repo = repo or repo_name(root)
    docs = []
    for rel, sha in _tracked(root):
        if (only is not None and rel not in only) or not wanted(rel):
            continue
        is_doc = Path(rel).suffix.lower() in DOC_SUFFIXES
        doc_id = f"md:{repo}:{rel}" if is_doc else f"code:{repo}/{rel}"
        if known is not None and sha and known.get(doc_id) == f"git:{sha}":
            continue
        f = root / rel
        try:
            if f.is_symlink() or f.stat().st_size > MAX_BYTES:  # a link can point outside the checkout
                continue
            data = f.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8000]:
            continue
        from fathom.syntax import grammar_for

        docs.append(
            SourceDoc(
                id=doc_id,
                text=data.decode("utf-8", errors="replace"),
                kind="doc" if is_doc else "code",
                repo=repo,
                path=rel,
                title=rel,
                language="markdown" if is_doc else grammar_for(rel),
                content_hash=f"git:{sha}" if sha else "",
            )
        )
    return docs


# ----------------------------------------------------------------- imports
def _python_imports(root: Any) -> list[str]:
    out: list[str] = []
    stack = [root]
    while stack:
        n = stack.pop()
        if n.type == "import_from_statement":
            mod = n.child_by_field_name("module_name")
            text = mod.text.decode(errors="replace") if mod is not None else ""
            if text and set(text) == {"."}:  # from . import a, b: each name is a module
                for c in n.children_by_field_name("name"):
                    name = (c.child_by_field_name("name") or c).text.decode(errors="replace")
                    out.append(text + name)
            elif text:
                out.append(text)
            continue
        if n.type == "import_statement":
            for c in n.children_by_field_name("name"):
                out.append((c.child_by_field_name("name") or c).text.decode(errors="replace"))
            continue
        stack.extend(n.children)
    return out


def _other_imports(grammar: str, root: Any) -> list[str]:
    out: list[str] = []
    stack = [root]
    while stack:
        n = stack.pop()
        if grammar in ("javascript", "typescript", "tsx") and n.type in ("import_statement", "export_statement"):
            src = n.child_by_field_name("source")
            if src is not None:
                out.append(src.text.decode(errors="replace").strip("'\"`"))
            continue
        if grammar == "go" and n.type == "import_spec":
            p = n.child_by_field_name("path")
            if p is not None:
                out.append(p.text.decode(errors="replace").strip('"`'))
            continue
        if grammar == "rust" and n.type == "use_declaration":
            arg = n.child_by_field_name("argument")
            if arg is not None:
                out.append(arg.text.decode(errors="replace").split("::{")[0])
            continue
        stack.extend(n.children)
    return out


def imports_of(path: str, text: str) -> list[str]:
    """The modules a code file imports, as written."""
    from fathom.syntax import _tree, grammar_for

    grammar = grammar_for(path)
    tree = _tree(grammar, text) if grammar else None
    if tree is None:
        return []
    return _python_imports(tree.root_node) if grammar == "python" else _other_imports(grammar, tree.root_node)


def resolve_import(importer: str, module: str, files: set[str]) -> list[str]:
    """Repository files an import names; empty when it names something outside the repository."""
    suffix = Path(importer).suffix.lower()
    here = posixpath.dirname(importer)
    if suffix in (".py", ".pyi"):
        dots = len(module) - len(module.lstrip("."))
        rest = module[dots:].replace(".", "/")
        if dots:
            base = here
            for _ in range(dots - 1):
                base = posixpath.dirname(base)
            stem = posixpath.join(base, rest) if rest else base
            cands = [f"{stem}.py", f"{stem}/__init__.py"]
            return [c for c in cands if c in files]
        if not rest:
            return []
        exact = [f for base in ("", "src/", "lib/") for f in (f"{base}{rest}.py", f"{base}{rest}/__init__.py") if f in files]
        if exact or "/" not in rest:
            # A one-word import (``json``, ``utils``) matched by suffix would tie the standard library to any local file of that name.
            return exact
        ends = (f"/{rest}.py", f"/{rest}/__init__.py")
        return sorted(f for f in files if f.endswith(ends))[:3]
    if suffix in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
        if not module.startswith("."):
            return []
        stem = posixpath.normpath(posixpath.join(here, module))
        if posixpath.splitext(stem)[1] in (".js", ".jsx", ".mjs") and stem not in files:
            stem = posixpath.splitext(stem)[0]  # ESM TypeScript imports './util.js' for util.ts
        exts = ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", "/index.ts", "/index.tsx", "/index.js")
        return [stem + e for e in exts if stem + e in files][:1]
    if suffix == ".go":
        # A Go import names a package (a folder); match the folder by its trailing path segments.
        segs = module.split("/")
        if len(segs) < 2:  # ``errors``, ``fmt``: the standard library
            return []
        for n in range(len(segs), 1, -1):
            tail = "/".join(segs[-n:])
            hits = sorted(f for f in files if f.endswith(".go") and (posixpath.dirname(f) == tail or posixpath.dirname(f).endswith("/" + tail)))
            if hits:
                return hits
        return []
    if suffix == ".rs":
        parts = [p for p in module.split("::") if p not in ("crate", "self", "super")]
        for n in range(len(parts), 0, -1):
            stem = "/".join(parts[:n])
            hits = [f for f in files if f.endswith((f"src/{stem}.rs", f"src/{stem}/mod.rs"))]
            if hits:
                return hits
        return []
    return []


def import_edges(docs: list[SourceDoc], files: set[str] | None = None) -> list[tuple[str, str, str]]:
    """(repo, importer path, imported path) for the code documents, resolved within each repository."""
    by_repo: dict[str, set[str]] = {}
    for d in docs:
        by_repo.setdefault(d.repo, set()).add(d.path)
    edges = []
    for d in docs:
        if d.kind != "code":
            continue
        known = files if files is not None else by_repo[d.repo]
        for module in imports_of(d.path, d.text):
            for target in resolve_import(d.path, module, known):
                if target != d.path:
                    edges.append((d.repo, d.path, target))
    return sorted(set(edges))
