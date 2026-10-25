"""What the index takes in: one ``SourceDoc`` per thing someone wrote, code or prose.

Code files come straight from a checkout (``fathom.rag.code``) and the platform's prose from the
graph store; both become ``SourceDoc`` here, so the chunker and the index see one shape.
``from_record`` reads any document-store row by attribute, so the index depends on no ingestion module.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SourceDoc:
    """One document to index. ``id`` is stable and source-prefixed (``code:<repo>/<path>``, ``gh:...#pr/40``)."""

    id: str
    text: str
    kind: str  # code | doc | commit | pr | issue | review | comment | session | graph
    repo: str = ""
    path: str = ""
    title: str = ""
    url: str = ""
    date: str | None = None  # ISO, the source's last update
    language: str | None = None  # a tree-sitter grammar name, "markdown", "diff", or None
    refs: list[str] = field(default_factory=list)  # "path:<repo>/<rel>" for files, plain ids for other docs
    asset_ids: list[str] = field(default_factory=list)  # graph assets this text is about
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not self.content_hash:
            self.content_hash = hashlib.sha256(self.text.encode("utf-8", errors="replace")).hexdigest()


def from_record(doc: Any) -> SourceDoc | None:
    """A ``SourceDoc`` from an ingestion ``Document``; None for a deleted doc or a duplicate of another."""
    if getattr(doc, "deleted", False) or getattr(doc, "duplicate_of", None):
        return None
    meta = getattr(doc, "meta", None) or {}
    title = getattr(doc, "title", "") or ""
    text = getattr(doc, "text", "") or ""
    path = getattr(doc, "path", "") or ""
    language = meta.get("language") or ("markdown" if path.endswith(".md") or getattr(doc, "kind", "") in ("session", "doc") else None)
    return SourceDoc(
        id=doc.id,
        text=text,
        kind=getattr(doc, "kind", "doc") or "doc",
        repo=getattr(doc, "repo", "") or "",
        path=path,
        title=title,
        url=getattr(doc, "url", "") or "",
        date=getattr(doc, "updated_at", None) or getattr(doc, "created_at", None),
        language=language,
        refs=list(getattr(doc, "refs", None) or []),
        asset_ids=list(meta.get("assets") or []),
        content_hash=getattr(doc, "content_hash", "") or "",
    )


_GH_REMOTE = re.compile(r"github\.com[:/][^/]+/([^/]+?)(?:\.git)?/?$")


def repo_name(root: Path) -> str:
    """The repository's name as the record uses it: the GitHub name from ``origin``, else the folder name, lower-cased."""
    try:
        url = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"], capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        url = ""
    m = _GH_REMOTE.search(url)
    return (m[1] if m else Path(root).resolve().name).lower()
