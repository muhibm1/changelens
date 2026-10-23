"""Hybrid, graph-scoped retrieval over code and the engineering record.

    from orion.rag import RagIndex, Scope
    idx = RagIndex.open()                      # ~/.cache/orion/rag.duckdb, or ORION_RAG
    idx.index_checkout(".")                    # incremental; a no-op when nothing changed
    hits = idx.search("where is the blast radius walked", k=8,
                      scope=Scope.from_change(store, ["src/orion/store.py"]))
    idx.read(hits[0].chunk_id)

See ``index.py`` for how a search ranks, ``chunking.py`` for how documents are cut and
``evaluate.py`` for how retrieval is measured.
"""

from .docs import SourceDoc, from_record, repo_name
from .index import Hit, RagIndex, default_path
from .scope import Scope

__all__ = ["Hit", "RagIndex", "Scope", "SourceDoc", "default_path", "from_record", "repo_name"]
