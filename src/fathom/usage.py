"""The usage log: one JSON line per call Fathom answers, for ``fathom usage``.

Standard library only, and never raises: logging must never fail the call it records. ``v`` is the format's version. Lines are appended with one write each,
so processes that log at the same time do not interleave; the file is rotated once to ``usage.jsonl.1`` past
``MAX_BYTES``. ``FATHOM_USAGE`` names another file, or ``off`` turns logging off.

The MCP server does not see the harness's session id, so the session-start hook leaves it under the the harness
process's pid (``~/.fathom/sessions/<pid>``) and the server's records pick it up from there (``session_for_parent``).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

VERSION = 1
MAX_BYTES = 20 * 1024 * 1024


def home() -> Path:
    """``FATHOM_HOME``, else ``~/.fathom``: the usage log and the session map."""
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
    """Append one record (``source`` hook, mcp, daemon or feedback); returns its id, or None when it was not written."""
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
    """Per event (and per MCP tool): calls, outcomes, latency p50/p95/max, and how often injected context was used."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        if r.get("source") == "feedback":
            continue
        detail = r.get("tool") if r.get("source") == "mcp" else r.get("job") if r.get("source") == "daemon" else r.get("event")
        groups.setdefault(f"{r.get('source')}.{detail}", []).append(r)
    used = {r.get("ref"): bool(r.get("used")) for r in records if r.get("source") == "feedback" and r.get("event") == "used"}
    out: dict[str, Any] = {}
    for key, rs in sorted(groups.items()):
        ms = [float(r["latency_ms"]) for r in rs if isinstance(r.get("latency_ms"), int | float)]
        outcomes: dict[str, int] = {}
        for r in rs:
            outcomes[str(r.get("outcome"))] = outcomes.get(str(r.get("outcome")), 0) + 1
        row: dict[str, Any] = {"calls": len(rs), "outcomes": outcomes, "p50_ms": _pct(ms, 0.5), "p95_ms": _pct(ms, 0.95), "max_ms": round(max(ms), 1) if ms else None}
        judged = [used[r["id"]] for r in rs if r.get("id") in used]
        if judged:
            row["used"] = f"{sum(judged)}/{len(judged)}"
        out[key] = row
    return out


# ------------------------------------------------------------------------------------------------- feedback
SETTLE = timedelta(minutes=10)  # a session quiet this long is judged; one older than GIVE_UP is judged regardless
GIVE_UP = timedelta(hours=6)


def _assistant_text(transcript: Path) -> str | None:
    """Everything the assistant wrote or passed to a tool in a harness transcript (JSONL), or None if unreadable."""
    try:
        lines = transcript.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    parts = []
    for line in lines:
        if '"assistant"' not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("type") == "assistant":
            parts.append(json.dumps(rec.get("message", {}).get("content", ""), ensure_ascii=False))
    return "\n".join(parts)


def judge_outcomes(now: datetime | None = None) -> dict[str, int]:
    """Write a ``feedback.used`` record for each prompt hook that injected ids and has not been judged yet: used when
    an assistant turn of that session cites or reads one of the ids. Deterministic; reads only the transcript."""
    now = now or datetime.now(UTC)
    records = read()
    judged = {r.get("ref") for r in records if r.get("source") == "feedback" and r.get("event") == "used"}
    counts = {"judged": 0, "used": 0, "waiting": 0}
    texts: dict[str, str | None] = {}
    for r in records:
        if r.get("source") != "hook" or r.get("event") != "prompt" or not r.get("ids") or r.get("id") in judged:
            continue
        transcript = r.get("transcript")
        try:
            at = datetime.fromtimestamp(float(r["ts"]) / 1000, UTC)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            continue
        if not transcript:
            continue
        tp = Path(transcript)
        try:
            quiet = now - datetime.fromtimestamp(tp.stat().st_mtime, UTC)
        except OSError:
            quiet = GIVE_UP  # the transcript is gone: judge on nothing
        if quiet < SETTLE and now - at < GIVE_UP:
            counts["waiting"] += 1
            continue
        if transcript not in texts:
            texts[transcript] = _assistant_text(tp)
        text = texts[transcript]
        hit = [i for i in r["ids"] if text and re.search(re.escape(str(i)), text)]
        log("feedback", "used", ref=r["id"], session_id=r.get("session_id"), used=bool(hit), used_ids=hit, method="transcript" if text is not None else "unreadable")
        counts["judged"] += 1
        counts["used"] += bool(hit)
    return counts


# ------------------------------------------------------------------------------------------------- sessions
def _sessions_dir() -> Path:
    return home() / "sessions"


def _parent_of(pid: int) -> int | None:
    try:
        res = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=1)
        return int(res.stdout.strip()) if res.returncode == 0 and res.stdout.strip() else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def remember_session(session_id: str) -> None:
    """From the session-start hook: the session id under the hook's parent and grandparent pids (the harness runs a
    hook through a shell, which may or may not stay in between). A new session in the same process (/clear) overwrites it."""
    if not session_id or path() is None:
        return
    try:
        folder = _sessions_dir()
        folder.mkdir(parents=True, exist_ok=True)
        parent = os.getppid()
        for pid in {parent, _parent_of(parent)} - {None, 0, 1}:
            (folder / str(pid)).write_text(session_id, encoding="utf-8")
    except OSError:
        pass


def session_for_parent() -> str | None:
    """The the harness session this process (an MCP server it started) serves, when the hook left it."""
    try:
        return (_sessions_dir() / str(os.getppid())).read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def forget_old_sessions(max_age_s: float = 7 * 86400) -> int:
    """Drop session-map entries older than ``max_age_s`` (their processes are long gone); the daemon calls it."""
    n = 0
    try:
        for f in _sessions_dir().iterdir():
            if time.time() - f.stat().st_mtime > max_age_s:
                f.unlink()
                n += 1
    except OSError:
        pass
    return n
