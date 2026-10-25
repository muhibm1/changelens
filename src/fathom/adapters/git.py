"""Git adapter: commit history to ``change_landed`` ledger events.

Each commit that touches a mapped path becomes one event linked to the assets
defined in those paths. Unmapped paths are kept in the payload so a later
sync can attach them once an adapter learns the path.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from ..model import Event, EventKind
from ..resolver import PathResolver
from . import Emitted

ADAPTER = "git"
SEP = "\x1f"


def load(repo_root: Path, resolver: PathResolver, paths: list[str] | None = None, max_commits: int = 500) -> Emitted:
    out = Emitted()
    fmt = SEP.join(["%H", "%an", "%ae", "%aI", "%s"])
    cmd = ["git", "-C", str(repo_root), "log", f"--max-count={max_commits}", f"--format=__COMMIT__{fmt}", "--name-only"]
    if paths:
        cmd += ["--", *paths]
    try:
        raw = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or "").strip().splitlines()
        out.notes.append("no commits yet" if any("does not have any commits" in m for m in msg) else f"git log failed: {msg[-1] if msg else e}")
        return out
    except FileNotFoundError:
        out.notes.append("git is not installed")
        return out
    for block in raw.split("__COMMIT__")[1:]:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        sha, author, email, when, subject = lines[0].split(SEP, 4)
        files = lines[1:]
        assets: set[str] = set()
        unmapped: list[str] = []
        for f in files:
            ids = resolver.resolve(f)
            if ids:
                assets.update(ids)
            else:
                unmapped.append(f)
        if not assets:
            continue
        out.events.append(
            Event(
                kind=EventKind.CHANGE_LANDED,
                occurred_at=datetime.fromisoformat(when),
                assets=sorted(assets),
                source="git",
                source_ref=sha,
                actor=author,
                payload={"subject": subject, "email": email, "files": files, "unmapped": unmapped},
            )
        )
    return out
