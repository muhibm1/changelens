"""Find the commits that introduced bugs which later fix commits removed.

A real-world recall test for ``orion review``: would the reviewer have caught the bug when it was
introduced? For each fix commit in a repository, the lines the fix removed (or, for a pure addition,
the lines around it) are blamed at the fix's parent. When one earlier commit wrote most of those lines
and that commit is small enough to review, it is a candidate case: review the introducing commit, and
expect a warning at the lines the fix later touched, in the introducing commit's own numbering.

The output is a list of candidates for a person to read and label (``pipelinebench/review/recall.yaml``).
Nothing here reads Orion's output; the cases come from the upstream history alone.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from orion.diffparse import parse

SOURCE_RE = re.compile(r"\.(py|ts|tsx|js|jsx|go|rs|rb|java|kt|cs|c|cc|cpp|h|hpp|sql)$")
TEST_RE = re.compile(r"(^|/)(tests?|__tests__|spec|testdata)(/|$)|_test\.|\.test\.|\.spec\.|test_", re.I)
FIX_RE = re.compile(r"^(fix|bug|hotfix|correct|repair)\b|\bfix(es|ed)?\b", re.I)


@dataclass
class Anchor:
    path: str  # in the introducing commit
    lines: list[int]  # new-side lines of the introducing commit that the fix later touched

    @property
    def line(self) -> int:
        return min(self.lines)

    @property
    def end_line(self) -> int:
        return max(self.lines)


@dataclass
class Candidate:
    repo: str
    fix: str
    fix_title: str
    commit: str  # the introducing commit
    title: str  # its subject
    share: float  # share of the fix's anchor lines this commit wrote
    changed_lines: int  # size of the introducing commit
    files: int
    anchors: list[Anchor] = field(default_factory=list)

    def to_yaml(self) -> dict:
        return {
            "repo": self.repo,
            "commit": self.commit,
            "title": self.title,
            "fix": self.fix,
            "fix_title": self.fix_title,
            "expect": [{"path": a.path, "line": a.line, **({"end_line": a.end_line} if a.end_line != a.line else {})} for a in self.anchors],
        }


def _git(clone: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(clone), *args], check=True, capture_output=True, text=True, errors="replace").stdout


def fix_commits(clone: Path, limit: int = 400, since: str | None = None) -> list[tuple[str, str]]:
    args = ["log", "--no-merges", f"--max-count={limit}", "--format=%H%x00%s"]
    if since:
        args.append(f"--since={since}")
    out = []
    for row in _git(clone, *args).splitlines():
        sha, _, subject = row.partition("\0")
        if FIX_RE.search(subject):
            out.append((sha, subject))
    return out


def _stat(clone: Path, sha: str) -> tuple[int, int]:
    """(changed lines, files) of a commit over source files that are not tests."""
    lines = files = 0
    for row in _git(clone, "show", "--numstat", "--format=", sha).splitlines():
        parts = row.split("\t")
        if len(parts) != 3 or not SOURCE_RE.search(parts[2]) or TEST_RE.search(parts[2]):
            continue
        try:
            lines += int(parts[0]) + int(parts[1])
        except ValueError:
            continue
        files += 1
    return lines, files


def _anchor_old_lines(clone: Path, fix: str) -> dict[str, list[int]]:
    """Old-side lines of the fix, per source path: the removed lines, or the context around a pure addition."""
    diff = _git(clone, "show", "--format=", "--unified=2", "--no-color", "--no-renames", fix)
    out: dict[str, list[int]] = {}
    for fd in parse(diff):
        if fd.status != "modified" or not SOURCE_RE.search(fd.path) or TEST_RE.search(fd.path):
            continue
        for h in fd.hunks:
            old_no = h.old_start
            removed, around = [], []
            for i, (op, _, _) in enumerate(h.lines):
                if op == "-":
                    removed.append(old_no)
                    old_no += 1
                elif op == " ":
                    nearby = any(o == "+" for o, _, _ in h.lines[max(0, i - 2) : i + 3])
                    if nearby:
                        around.append(old_no)
                    old_no += 1
            out.setdefault(fd.path, []).extend(removed or around)
    return out


def _blame(clone: Path, rev: str, path: str, lines: list[int]) -> list[tuple[str, int]]:
    """(commit, line in that commit) for each of ``lines`` of ``path`` at ``rev``."""
    if not lines:
        return []
    args = ["blame", "--porcelain", "-w"]
    for n in lines:
        args += ["-L", f"{n},{n}"]
    try:
        out = _git(clone, *args, rev, "--", path)
    except subprocess.CalledProcessError:
        return []
    hits = []
    for row in out.splitlines():
        m = re.match(r"^([0-9a-f]{40}) (\d+) (\d+)(?: (\d+))?$", row)
        if m:
            hits.append((m.group(1), int(m.group(2))))
    return hits


def introducer(clone: Path, repo: str, fix: str, fix_title: str, *, max_lines: int = 400, max_files: int = 8, min_share: float = 0.6) -> Candidate | None:
    anchors = _anchor_old_lines(clone, fix)
    if not anchors:
        return None
    by_commit: dict[str, dict[str, list[int]]] = {}
    total = 0
    for path, lines in anchors.items():
        for sha, line in _blame(clone, f"{fix}^", path, lines):
            by_commit.setdefault(sha, {}).setdefault(path, []).append(line)
            total += 1
    if not total:
        return None
    sha, per_path = max(by_commit.items(), key=lambda kv: sum(len(v) for v in kv[1].values()))
    share = sum(len(v) for v in per_path.values()) / total
    if share < min_share or sha.startswith(fix):
        return None
    parents = _git(clone, "rev-list", "--parents", "-n", "1", sha).split()
    if len(parents) != 2:  # a merge or a root commit
        return None
    changed, files = _stat(clone, sha)
    if changed == 0 or changed > max_lines or files > max_files:
        return None
    subject = _git(clone, "log", "-1", "--format=%s", sha).strip()
    if FIX_RE.search(subject) and "revert" in subject.lower():
        return None
    # blame reports each line under the path the file has at the fix; a file renamed since is a candidate a person will notice when reading it
    return Candidate(repo, fix[:10], fix_title, sha[:10], subject, round(share, 2), changed, files, [Anchor(p, sorted(set(ls))) for p, ls in sorted(per_path.items())])


def mine(clone: Path, repo: str, *, limit: int = 400, since: str | None = None, max_lines: int = 400, max_files: int = 8, progress=None) -> list[Candidate]:
    out: list[Candidate] = []
    seen: set[str] = set()
    for sha, subject in fix_commits(clone, limit, since):
        c = introducer(clone, repo, sha, subject, max_lines=max_lines, max_files=max_files)
        if progress:
            progress(sha[:10], subject, c)
        if c and c.commit not in seen:
            seen.add(c.commit)
            out.append(c)
    return out
