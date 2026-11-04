"""The usage log: one JSON line per call Fathom answers, for ``fathom usage``.

Standard library only, and never raises: logging must never fail the call it records. ``v`` is the format's version. Lines are appended with one write each,
so processes that log at the same time do not interleave; the file is rotated once to ``usage.jsonl.1`` past
``MAX_BYTES``. ``FATHOM_USAGE`` names another file, or ``off`` turns logging off.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

VERSION = 1
MAX_BYTES = 20 * 1024 * 1024


def home() -> Path:
    """``FATHOM_HOME``, else ``~/.fathom``: where the usage log lives."""
    return Path(os.environ.get("FATHOM_HOME") or Path.home() / ".fathom").expanduser()


def path() -> Path | None:
    """``FATHOM_USAGE``, else ``~/.fathom/usage.jsonl``; None when turned off."""
    configured = (os.environ.get("FATHOM_USAGE") or "").strip()
    if configured.lower() in ("off", "0", "false", "no"):
        return None
    if configured:
        return Path(configured).expanduser()
    return home() / "usage.jsonl"


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now_ms() -> int:
    return int(time.time() * 1000)


def log(source: str, event: str, **fields: Any) -> str | None:
    """Append one record (``source`` is ``mcp`` for the server's tool calls); returns its id, or None when it was not written."""
    target = path()
    if target is None:
        return None
    rec = {"v": VERSION, "ts": now_ms(), "id": fields.pop("id", None) or new_id(), "source": source, "event": event, **fields}
    try:
        line = json.dumps(rec, default=str, ensure_ascii=False) + "\n"
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if target.stat().st_size > MAX_BYTES:
                os.replace(target, target.with_name(target.name + ".1"))
        except OSError:
            pass
        fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:  # noqa: BLE001 - logging must never break the call it records
        return None
    return rec["id"]


def read(since: datetime | None = None, *, target: Path | None = None) -> list[dict[str, Any]]:
    """The log's records (the rotated file first), oldest first; lines that do not parse are skipped."""
    target = target or path()
    if target is None:
        return []
    out: list[dict[str, Any]] = []
    for p in (target.with_name(target.name + ".1"), target):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            if since is not None and not (isinstance(rec.get("ts"), int | float) and rec["ts"] >= since.timestamp() * 1000):
                continue
            out.append(rec)
    return out


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    return round(s[min(len(s) - 1, int(q * len(s)))], 1)


def stats(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Per event (and per MCP tool): calls, outcomes and latency p50/p95/max."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        detail = r.get("tool") if r.get("source") == "mcp" else r.get("event")
        groups.setdefault(f"{r.get('source')}.{detail}", []).append(r)
    out: dict[str, Any] = {}
    for key, rs in sorted(groups.items()):
        ms = [float(r["latency_ms"]) for r in rs if isinstance(r.get("latency_ms"), int | float)]
        outcomes: dict[str, int] = {}
        for r in rs:
            outcomes[str(r.get("outcome"))] = outcomes.get(str(r.get("outcome")), 0) + 1
        row: dict[str, Any] = {"calls": len(rs), "outcomes": outcomes, "p50_ms": _pct(ms, 0.5), "p95_ms": _pct(ms, 0.95), "max_ms": round(max(ms), 1) if ms else None}
        out[key] = row
    return out
