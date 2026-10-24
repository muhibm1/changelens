"""Unified diffs to files, hunks and changed lines with new-side line numbers."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


@dataclass
class Hunk:
    new_start: int
    new_len: int
    header: str
    old_start: int = 0
    old_len: int = 0
    lines: list[tuple[str, int | None, str]] = field(default_factory=list)  # (op ' '|'+'|'-', new line no, text)

    @property
    def new_end(self) -> int:
        return self.new_start + max(self.new_len, 1) - 1

    def added(self) -> list[int]:
        return [n for op, n, _ in self.lines if op == "+" and n is not None]


@dataclass
class FileDiff:
    path: str
    old_path: str | None
    status: str  # added | deleted | modified | renamed
    hunks: list[Hunk] = field(default_factory=list)
    binary: bool = False

    def contains(self, line: int, slack: int = 0) -> bool:
        return any(h.new_start - slack <= line <= h.new_end + slack for h in self.hunks)

    def first_changed_line(self) -> int | None:
        """First added line; for a pure deletion, the new-side line where the removed lines stood."""
        for h in self.hunks:
            for i, (op, n, _) in enumerate(h.lines):
                if op == "+":
                    return n
                if op == "-":
                    nxt = next((m for o, m, _ in h.lines[i:] if m is not None), None)
                    return nxt if nxt is not None else h.new_end
        return self.hunks[0].new_start if self.hunks else None


def _unquote(path: str) -> str:
    """Git quotes paths with spaces or non-ASCII bytes as C strings."""
    path = path.strip()
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        try:
            return path[1:-1].encode("latin-1").decode("unicode_escape").encode("latin-1").decode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return path[1:-1]
    return path


def parse(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    hunk: Hunk | None = None
    new_no = 0
    for raw in text.splitlines():
        if raw.endswith("\r"):
            raw = raw[:-1]
        if raw.startswith("diff --git "):
            m = re.match(r'diff --git "?a/(.*?)"? "?b/(.*?)"?$', raw)
            old, new = (_unquote(m.group(1)), _unquote(m.group(2))) if m else (None, raw.split()[-1])
            cur = FileDiff(path=new, old_path=old, status="modified")
            files.append(cur)
            hunk = None
            continue
        if cur is None:
            continue
        if raw.startswith("new file mode"):
            cur.status = "added"
        elif raw.startswith("deleted file mode"):
            cur.status = "deleted"
        elif raw.startswith("rename from "):
            cur.status = "renamed"
            cur.old_path = _unquote(raw[len("rename from "):])
        elif raw.startswith("rename to "):
            cur.path = _unquote(raw[len("rename to "):])
        elif raw.startswith("Binary files"):
            cur.binary = True
        elif raw.startswith("--- ") and hunk is None:
            continue
        elif raw.startswith("+++ ") and hunk is None:
            p = _unquote(raw[4:].split("\t")[0].strip())
            if p != "/dev/null":
                cur.path = p[2:] if p.startswith("b/") else p
        elif (m := HUNK_RE.match(raw)):
            hunk = Hunk(int(m.group(3)), int(m.group(4)) if m.group(4) is not None else 1, m.group(5).strip(), int(m.group(1)), int(m.group(2)) if m.group(2) is not None else 1)
            cur.hunks.append(hunk)
            new_no = hunk.new_start
        elif hunk is not None:
            if raw.startswith("+"):
                hunk.lines.append(("+", new_no, raw[1:]))
                new_no += 1
            elif raw.startswith("-"):
                hunk.lines.append(("-", None, raw[1:]))
            elif raw.startswith("\\"):
                continue
            else:
                hunk.lines.append((" ", new_no, raw[1:] if raw.startswith(" ") else raw))
                new_no += 1
    return files


def _apply(base: list[str], hunks: list[Hunk], reverse: bool) -> list[str] | None:
    """Apply hunks to ``base`` (forward: old side to new; reverse: new side to old). None if they do not fit."""
    out: list[str] = []
    pos = 0
    for h in hunks:
        start = (h.new_start if reverse else h.old_start) - 1
        if h.new_len == 0 and reverse or h.old_len == 0 and not reverse:
            start += 1
        start = max(start, 0)
        if start < pos or start > len(base):
            return None
        out.extend(base[pos:start])
        pos = start
        keep, drop = ("-", "+") if reverse else ("+", "-")
        for op, _, text in h.lines:
            if op == " ":
                if pos >= len(base) or base[pos] != text:
                    return None
                out.append(text)
                pos += 1
            elif op == drop:
                if pos >= len(base) or base[pos] != text:
                    return None
                pos += 1
            elif op == keep:
                out.append(text)
    out.extend(base[pos:])
    return out


def both_sides(fd: FileDiff, current: str | None) -> tuple[str | None, str | None] | None:
    """(old, new) text for a file, from its current contents and the diff, whichever side the file is on."""
    if fd.status == "added":
        new = "\n".join(t for h in fd.hunks for op, _, t in h.lines if op != "-") + "\n"
        return None, new
    if current is None:
        return None
    base = current.splitlines()
    new = _apply(base, fd.hunks, reverse=False)
    if new is not None:
        return current, "\n".join(new) + "\n"
    old = _apply(base, fd.hunks, reverse=True)
    if old is not None:
        return "\n".join(old) + "\n", current
    return None


def git_diff(repo_root: Path, base_ref: str, paths: list[str] | None = None) -> str:
    """Working tree against ``base_ref``, plus untracked files as additions."""
    cmd = ["git", "-C", str(repo_root), "diff", "--unified=3", "--no-color", base_ref]
    if paths:
        cmd += ["--", *paths]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    untracked = subprocess.run(["git", "-C", str(repo_root), "ls-files", "-z", "--others", "--exclude-standard", *(["--", *paths] if paths else [])], check=True, capture_output=True, text=True).stdout.split("\0")
    for u in filter(None, untracked):
        res = subprocess.run(["git", "-C", str(repo_root), "diff", "--no-index", "--unified=3", "--no-color", "--", "/dev/null", u], capture_output=True, text=True)
        out += res.stdout
    return out


def git_messages(repo_root: Path, base_ref: str) -> str | None:
    """The commit messages between ``base_ref`` and HEAD, oldest first, or None when there are none or git cannot say."""
    res = subprocess.run(["git", "-C", str(repo_root), "log", "--reverse", "--no-merges", "--format=%B%x00", f"{base_ref}..HEAD"], capture_output=True, text=True)
    if res.returncode != 0:
        return None
    text = "\n\n".join(m.strip() for m in res.stdout.split("\0") if m.strip())
    return text or None


def render_file(f: FileDiff) -> str:
    """One file's hunks with new-side line numbers, the form the model sees and cites from."""
    block = [f"=== {f.path} ({f.status})"]
    for h in f.hunks:
        block.append(f"@@ lines {h.new_start}-{h.new_end} {h.header}".rstrip())
        for op, n, text in h.lines:
            block.append(f"{n if n is not None else '':>5} {op} {text}")
    return "\n".join(block) + "\n"


def batches(files: list[FileDiff], max_chars: int = 60_000, hard_cap: int = 240_000) -> tuple[list[list[FileDiff]], list[str]]:
    """Group files so each group's rendered hunks fit one model call.

    A file larger than ``max_chars`` on its own still gets a call of its own, up to ``hard_cap``;
    beyond that it is omitted and named, since a single hunk list that long is not reviewable in one pass.
    """
    groups: list[list[FileDiff]] = []
    omitted: list[str] = []
    cur: list[FileDiff] = []
    used = 0
    for f in files:
        if f.binary:
            continue
        size = len(render_file(f))
        if size > hard_cap:
            omitted.append(f.path)
            continue
        if cur and used + size > max_chars:
            groups.append(cur)
            cur, used = [], 0
        cur.append(f)
        used += size
    if cur:
        groups.append(cur)
    return groups, omitted


def render_with_omitted(files: list[FileDiff], max_chars: int = 60_000) -> tuple[str, list[str]]:
    """Every file that fits one call, rendered, and the files left out of the budget (see ``batches``)."""
    groups, omitted = batches(files, max_chars)
    parts = [render_file(f) for g in groups for f in g]
    parts += [f"=== {p}: omitted, diff too large for one review call\n" for p in omitted]
    return "\n".join(parts), omitted


def render(files: list[FileDiff], max_chars: int = 60_000) -> str:
    return render_with_omitted(files, max_chars)[0]
