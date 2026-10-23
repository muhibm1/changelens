"""Local cross-encoder reranking (``ce:<model>``), through sentence-transformers (the ``embed`` extra).

A cross-encoder reads the query and a candidate together, so it judges relevance better than
two separately embedded vectors can, at the cost of one model pass per candidate. The index
runs it only over the top few dozen fused candidates.
"""

from __future__ import annotations

from typing import Any


class CrossEncoderReranker:
    def __init__(self, model: str):
        from sentence_transformers import CrossEncoder  # type: ignore[import-not-found]

        self.name = f"ce:{model}"
        self._m: Any = CrossEncoder(model)

    def score(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        return [float(s) for s in self._m.predict([(query, t) for t in texts], batch_size=32)]


def make_reranker(spec: str) -> CrossEncoderReranker:
    return CrossEncoderReranker(spec.removeprefix("ce:"))
