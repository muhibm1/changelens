"""System context for code that is not a data platform: who owns the changed files, which sensitive areas they
fall in, and the fixes and reverts that touched them before.

All three are read from files and git, never guessed. Owners come from the repository's CODEOWNERS, areas from a
file a team writes (``review.areas``, ``--areas``), and history from ``git log``. What none of them records is
reported as not recorded.

An areas file is YAML or JSON: a list of areas, ``{"areas": [...]}``, or a mapping from project name to a list, read
as ``file#project``. Each area has ``pattern`` (a regular expression over repository-relative paths) or ``paths``
(globs), a ``why`` or ``reason``, and optionally ``id``, ``severity`` (inform, warn or block; default inform) and
``approvers``. Keys starting with ``_`` are notes and are skipped.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .diffparse import FileDiff

CODEOWNERS_PLACES = (".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS")  # GitHub's order; it uses the first it finds
FIX_RE = re.compile(r"^(revert|hotfix|fix)\b|\brevert(s|ed)?\b|\bfix(es|ed)?\b|\bregression\b|\bincident\b|\brollback\b", re.I)
HISTORY_COMMITS = 300  # how far back the history search reads, per review
HISTORY_SHOWN = 8


class AreasError(ValueError):
    pass


@dataclass
class Area:
    id: str
    reason: str
    pattern: str | None = None  # regular expression over the whole path
    paths: list[str] = field(default_factory=list)  # globs, as an alternative
    severity: str = "inform"
    approvers: list[str] = field(default_factory=list)
    source: str = ""

    def matches(self, path: str) -> bool:
        if self.pattern and re.search(self.pattern, path):
            return True
        # Globs follow the CODEOWNERS rules from the repository root: `*` stays inside one folder, `**` crosses folders.
        return any(_codeowners_regex("/" + g.lstrip("/")).match(path) for g in self.paths)


def load_areas(spec: str, base: Path | None = None) -> list[Area]:
    """The areas in ``spec`` (``file`` or ``file#project``), a path relative to ``base``. Raises AreasError on a bad file."""
    name, _, key = spec.partition("#")
    path = Path(name) if base is None or Path(name).is_absolute() else base / name
    try:
        text = path.read_text()
        data = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    except (OSError, ValueError, yaml.YAMLError) as e:
        raise AreasError(f"cannot read areas file {path}: {e}") from e
    if key:
        if not isinstance(data, dict) or key not in data:
            raise AreasError(f"{path} has no areas for {key}")
        data = data[key]
    elif isinstance(data, dict) and "areas" in data:
        data = data["areas"]
    if not isinstance(data, list):
        raise AreasError(f"{path}: expected a list of areas" + ("" if key else "; name one with file#project"))
    prefix = (key or path.stem).upper()
    areas = []
    for n, item in enumerate(data, 1):
        if isinstance(item, dict) and item and all(str(k).startswith("_") for k in item):
            continue  # a note between areas
        if not isinstance(item, dict):
            raise AreasError(f"{path}: area {n} is not a mapping")
        pattern, globs, approvers = item.get("pattern"), item.get("paths") or [], item.get("approvers") or []
        globs = [globs] if isinstance(globs, str) else globs
        approvers = [approvers] if isinstance(approvers, str) else approvers
        if pattern is not None and not isinstance(pattern, str):
            raise AreasError(f"{path}: area {n} pattern is not a string")
        if not isinstance(globs, list) or not all(isinstance(g, str) for g in globs):
            raise AreasError(f"{path}: area {n} paths is not a list of globs")
        if not isinstance(approvers, list):
            raise AreasError(f"{path}: area {n} approvers is not a list")
        if not pattern and not globs:
            raise AreasError(f"{path}: area {n} has no pattern or paths")
        if pattern:
            try:
                re.compile(pattern)
            except re.error as e:
                raise AreasError(f"{path}: area {n} pattern does not compile: {e}") from e
        severity = item.get("severity", "inform")
        if severity not in ("inform", "warn", "block"):
            raise AreasError(f"{path}: area {n} severity {severity!r}; use inform, warn or block")
        reason = str(item.get("why") or item.get("reason") or "").strip()
        if not reason:
            raise AreasError(f"{path}: area {n} says nothing about why it is sensitive")
        areas.append(Area(id=str(item.get("id") or f"{prefix}-{n}"), reason=reason, pattern=pattern, paths=list(globs), severity=severity, approvers=[str(a) for a in approvers], source=f"{path.name}{'#' + key if key else ''}"))
    if len({a.id for a in areas}) != len(areas):
        raise AreasError(f"{path}: two areas share an id")
    return areas


@dataclass
class OwnerRule:
    line: int
    pattern: str
    owners: list[str]
    regex: re.Pattern[str]


def _codeowners_regex(pattern: str) -> re.Pattern[str]:
    """CODEOWNERS uses gitignore rules: a leading or inner ``/`` anchors to the root, a trailing ``/`` means a folder,
    ``*`` stays inside one folder and ``**`` crosses folders. A pattern that names a folder covers what is inside it,
    except one ending in ``/*``, which covers only the files directly in that folder."""
    anchored = pattern.startswith("/") or "/" in pattern.rstrip("/")
    folder = pattern.endswith("/")
    body = pattern.strip("/")
    out, i = "", 0
    while i < len(body):
        if body.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif body.startswith("**", i):
            out, i = out + ".*", i + 2
        elif body[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif body[i] == "?":
            out, i = out + "[^/]", i + 1
        else:
            out, i = out + re.escape(body[i]), i + 1
    inside = "/.*" if folder else "" if body.endswith("/*") else "(?:/.*)?"
    return re.compile(("^" if anchored else "^(?:.*/)?") + out + inside + "$")


def load_codeowners(root: Path) -> tuple[str | None, list[OwnerRule]]:
    for place in CODEOWNERS_PLACES:
        p = root / place
        if p.is_file():
            rules = []
            for n, raw in enumerate(p.read_text(errors="replace").splitlines(), 1):
                line = re.split(r"\s#", raw, maxsplit=1)[0].strip()
                if not line or line.startswith("#"):
                    continue
                pattern, *owners = line.split()
                rules.append(OwnerRule(n, pattern, owners, _codeowners_regex(pattern)))
            return place, rules
    return None, []


def owners_of(rules: list[OwnerRule], path: str) -> OwnerRule | None:
    """The last rule that matches wins, as on GitHub; a rule with no owners clears ownership."""
    hit = None
    for r in rules:
        if r.regex.match(path):
            hit = r
    return hit if hit and hit.owners else None


def file_history(root: Path, paths: list[str], rev: str | None) -> list[dict[str, Any]] | None:
    """Fixes, reverts and incident commits that touched ``paths`` before ``rev`` (default HEAD), newest first; None when
    git could not be read, so no one is told there is no history when it was never looked at."""
    if not paths:
        return []
    if (rev or "").startswith("-"):
        return None
    try:
        # Paths are literal (a `[id].tsx` is not a pattern) and printed unquoted, so names with any characters match back.
        out = subprocess.run(["git", "-C", str(root), "--literal-pathspecs", "-c", "core.quotePath=false", "log", f"-n{HISTORY_COMMITS}", "--format=%x1e%H%x09%ad%x09%s", "--date=short", "--name-only", rev or "HEAD", "--", *paths], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    wanted, found = set(paths), []
    for block in out.stdout.split("\x1e"):
        head, _, names = block.strip().partition("\n")
        parts = head.split("\t", 2)
        if len(parts) < 3 or not FIX_RE.search(parts[2]):
            continue
        touched = sorted(wanted & {n.strip() for n in names.splitlines() if n.strip()})
        if touched:
            found.append({"commit": parts[0], "date": parts[1], "subject": parts[2][:200], "paths": touched})
    return found


@dataclass
class CodeContext:
    codeowners: str | None = None  # where the CODEOWNERS file is, or None when the repository has none
    owners: dict[str, dict[str, Any]] = field(default_factory=dict)  # path -> {"owners", "ref"}
    areas: list[dict[str, Any]] = field(default_factory=list)  # one per area the change falls in: {"area", "paths", "line"}
    history: list[dict[str, Any]] = field(default_factory=list)
    history_searched: bool = False  # True only when git was read, so an empty history means none was found

    def known(self) -> dict[str, set[str]]:
        return {
            "area": {a["area"].id for a in self.areas},
            "commit": {h["commit"] for h in self.history} | {h["commit"][:12] for h in self.history},
            "ref": {o["ref"] for o in self.owners.values()},
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "codeowners": self.codeowners,
            "owners": self.owners,
            "areas": [{"id": a["area"].id, "severity": a["area"].severity, "reason": a["area"].reason, "approvers": a["area"].approvers, "source": a["area"].source, "paths": a["paths"]} for a in self.areas],
            "history": self.history,
        }


def code_context(root: Path, files: list[FileDiff], areas: list[Area], rev: str | None = None, *, history: bool = True) -> CodeContext:
    paths = [f.path for f in files]
    ctx = CodeContext()
    ctx.codeowners, rules = load_codeowners(root)
    for p in paths:
        if rule := owners_of(rules, p):
            ctx.owners[p] = {"owners": rule.owners, "ref": f"{ctx.codeowners}:{rule.line}"}
    for area in areas:
        hit = [f for f in files if area.matches(f.path) or (f.old_path and area.matches(f.old_path))]
        if hit:
            hit.sort(key=lambda f: f.first_changed_line() is None)  # point at a file with a changed line when one has one
            ctx.areas.append({"area": area, "paths": [f.path for f in hit], "line": hit[0].first_changed_line()})
    if history:
        found = file_history(root, list(dict.fromkeys(paths + [f.old_path for f in files if f.old_path])), rev)
        ctx.history, ctx.history_searched = (found or [])[:HISTORY_SHOWN], found is not None
    return ctx


def facts(ctx: CodeContext) -> list[str]:
    lines = []
    for a in ctx.areas:
        area = a["area"]
        sign = f" Sign-off: {', '.join(area.approvers)}." if area.approvers else ""
        lines.append(f"- area:{area.id} ({area.severity}) covers {', '.join(a['paths'][:5])}: {area.reason}.{sign}")
    if ctx.codeowners:
        for p, o in ctx.owners.items():
            lines.append(f"- owner of {p}: {' '.join(o['owners'])} (ref:{o['ref']})")
        unowned = sorted({p for a in ctx.areas for p in a["paths"]} - set(ctx.owners))
        if unowned:
            lines.append(f"- owner of {', '.join(unowned[:5])}: not recorded in {ctx.codeowners}")
    else:
        lines.append("- owners: not recorded (the repository has no CODEOWNERS file)")
    for h in ctx.history:
        lines.append(f"- history commit:{h['commit'][:12]} {h['date']} touched {', '.join(h['paths'][:3])}: {h['subject']}")
    if ctx.history_searched and not ctx.history:
        lines.append(f"- history: no fix, revert or incident commit among the last {HISTORY_COMMITS} commits that touched these files")
    return lines


def area_findings(ctx: CodeContext, owners: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """One deterministic finding per area the change falls in. It is context for a reviewer, not a defect: its
    severity is the one the area's authors chose."""
    out = []
    for a in ctx.areas:
        area, path = a["area"], a["paths"][0]
        who = (owners or ctx.owners).get(path)
        body = f"{area.reason}." + (f" Sign-off needed from {', '.join(area.approvers)}." if area.approvers else "") + (f" Owners: {' '.join(who['owners'])}." if who else "")
        more = f" and {len(a['paths']) - 1} more" if len(a["paths"]) > 1 else ""
        evidence = [f"area:{area.id}"] + ([f"hunk:{path}:{a['line']}"] if a["line"] else []) + ([f"ref:{who['ref']}"] if who else [])
        out.append({"source": "deterministic", "path": path, "line": a["line"], "end_line": None, "severity": area.severity, "category": "operational", "title": f"Touches sensitive area {area.id}: {path}{more}", "body": body, "evidence": evidence, "status": "valid", "notes": []})
    return out
