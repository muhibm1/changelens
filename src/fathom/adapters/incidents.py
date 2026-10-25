"""Postmortems as code: ``fathom/incidents/*.md`` with YAML front matter.

Each file becomes an ``incident_opened`` and a ``postmortem_filed`` event
linked to the assets it names. The narrative body is kept in the payload so a
verdict can quote it with a citation.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from ..model import Event, EventKind
from ..resolver import resolve_ref
from ..store import Store
from . import Emitted, repo_relative

ADAPTER = "incidents"
FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def parse_postmortem(text: str) -> tuple[dict[str, Any], str]:
    m = FRONT_RE.match(text)
    if not m:
        raise ValueError("no YAML front matter")
    meta = yaml.safe_load(m.group(1)) or {}
    return meta, m.group(2).strip()


def _dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def load(incidents_dir: Path, repo_root: Path, store: Store) -> Emitted:
    out = Emitted()
    for path in sorted(incidents_dir.glob("*.md")):
        try:
            meta, body = parse_postmortem(path.read_text())
        except ValueError as e:
            out.notes.append(f"{path.name}: {e}")
            continue
        rel = repo_relative(path, repo_root)
        assets: list[str] = []
        for ref in meta.get("assets", []) or []:
            r = resolve_ref(store, str(ref))
            if r.ok:
                assets.append(r.node_id)
            else:
                out.notes.append(f"{path.name}: unresolved asset {ref}")
        inc_id = meta.get("id") or path.stem
        started = _dt(meta.get("started_at"))
        resolved = _dt(meta.get("resolved_at")) if meta.get("resolved_at") else None
        base_payload = {
            "incident": inc_id,
            "title": meta.get("title"),
            "severity": meta.get("severity"),
            "cause": meta.get("cause"),
            "trigger_change": meta.get("trigger_change"),
            "constraints_proposed": meta.get("constraints_proposed", []),
            "owner": meta.get("owner"),
            "path": rel,
        }
        out.events.append(
            Event(EventKind.INCIDENT_OPENED, started, assets, source=rel, source_ref=inc_id, actor=meta.get("owner"), payload=base_payload)
        )
        out.events.append(
            Event(
                EventKind.POSTMORTEM_FILED,
                resolved or started,
                assets,
                source=rel,
                source_ref=inc_id,
                actor=meta.get("owner"),
                payload={**base_payload, "duration_minutes": int((resolved - started).total_seconds() // 60) if resolved else None, "body": body},
            )
        )
    return out
