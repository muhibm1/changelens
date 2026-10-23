"""Citations: build an answer that cites chunk ids, and check any answer's citations against the index.

``check`` is how an agent (or the eval) finds out whether an answer is faithful to what was
retrieved: every ``[chunk:<id>]`` must name a chunk the index holds, and the sentence citing it
must be supported by that chunk, measured as the share of the sentence's content terms the
chunk contains. A sentence with no citation is reported, never assumed supported. Nothing here
calls a model, so the check is cheap enough to run on every answer.
"""

from __future__ import annotations

import re
from typing import Any

from .chunking import terms

CITE = re.compile(r"\[(?:chunk:)?([^\[\]\s]+#[^\[\]\s]+)\]")
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z`])|(?<=\])\s+(?=[A-Z`])|\n+")
SUPPORT = 0.6  # share of a sentence's terms the cited chunk must contain


def sentences(answer: str) -> list[str]:
    return [s.strip() for s in SENTENCE.split(answer) if s.strip()]


def check(answer: str, index: Any, *, retrieved: set[str] | None = None, support: float = SUPPORT) -> dict[str, Any]:
    """Per-sentence verdicts on an answer's citations, with totals.

    ``faithfulness`` is supported cited sentences over cited sentences; ``validity`` is citation
    ids the index holds over all citation ids; ``uncited`` lists sentences that cite nothing.
    With ``retrieved``, a valid id that was not among the retrieved chunks is flagged too."""
    out: dict[str, Any] = {"sentences": [], "uncited": [], "invalid": [], "unsupported": [], "not_retrieved": []}
    n_ids = n_valid = n_cited = n_supported = 0
    for s in sentences(answer):
        ids = CITE.findall(s)
        claim = CITE.sub("", s).strip()
        words = set(terms(claim))
        if not words:  # a bare citation or a claim of stopwords asserts nothing, but its ids must still exist
            for cid in ids:
                n_ids += 1
                if "error" in index.read(cid):
                    out["invalid"].append(cid)
                else:
                    n_valid += 1
            continue
        if not ids:
            out["uncited"].append(s)
            continue
        n_cited += 1
        best = 0.0
        for cid in ids:
            n_ids += 1
            chunk = index.read(cid)
            if "error" in chunk:
                out["invalid"].append(cid)
                continue
            n_valid += 1
            if retrieved is not None and cid not in retrieved:
                out["not_retrieved"].append(cid)
            have = set(terms(f"{chunk.get('title') or ''} {chunk.get('symbol') or ''} {chunk['text']}"))
            best = max(best, len(words & have) / len(words) if words else 1.0)
        ok = best >= support
        n_supported += ok
        if not ok:
            out["unsupported"].append(s)
        out["sentences"].append({"text": s, "ids": ids, "support": round(best, 3), "supported": ok})
    out["faithfulness"] = round(n_supported / n_cited, 4) if n_cited else None
    out["validity"] = round(n_valid / n_ids, 4) if n_ids else None
    return out


def extractive_answer(query: str, hits: list[Any], n: int = 3) -> str:
    """An answer made of the line or sentence of each top hit that shares most terms with the query, each citing its chunk."""
    want = set(terms(query))
    parts = []
    for h in hits[:n]:
        pieces = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n", h.text) if len(p.strip()) > 20]
        if not pieces:
            continue
        best = max(pieces, key=lambda p: (len(want & set(terms(p))), -abs(len(p) - 120)))
        parts.append(f"{best.rstrip('.')}. [chunk:{h.chunk_id}]")
    return "\n".join(parts) if parts else "not recorded"
