"""Code around a change that the hunks alone do not show.

A diff hunk shows three lines on each side. A reviewer reading a real change
reads more: the function the hunk sits in, and what the new lines call. This
module gathers both, from the repository and by code:

* ``windows``: for a changed file too long to send whole, the enclosing
  definition of each hunk (by indentation and definition lines, the same
  notion ``symbols`` uses), merged and capped, numbered like the diff.
* ``callees``: for each name the added lines call that no changed file
  defines, its definition elsewhere in the repository, found by ``git grep``
  on definition lines and cut at the end of the definition's block. A name
  with several definitions is ambiguous and left out, so nothing shown is a
  guess. The locations are facts the model may cite (``ref:<path>:<line>``).

Everything here is text and line numbers; nothing is a type checker, and the
facts say so where it matters.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .diffparse import FileDiff
from .symbols import CODE_EXT, _definition, _indent, _lang

WINDOW_PAD = 20  # lines shown around a hunk when no enclosing definition is found
WINDOW_MAX = 160  # lines of one window
FILE_WINDOWS_MAX = 400  # lines of windows for one file
CALLEE_MAX_LINES = 60
CALLEE_MAX = 12
CALLEE_MAX_CHARS = 24_000
CALLEE_MAX_DEFS = 3  # a name defined in more places than this is ambiguous and left out

_CALL_RE = re.compile(r"(?<![\w.$])([A-Za-z_][\w]*)\s*\(")
_METHOD_RE = re.compile(r"(?<![\"'`])\.([A-Za-z_][\w]*)\s*\(")  # not a method on a string literal
_KEYWORDS = frozenset(
    "if elif else for while do switch case catch with function return def class new await yield throw typeof delete void import export from as in is not and or lambda print len range str int float bool list dict set tuple super self this cls isinstance issubclass hasattr getattr setattr type assert raise try except finally pass break continue match func go defer select chan make append cap copy panic recover fn let const var pub impl struct enum trait use mod where loop unsafe async static sizeof alignof require module exports console Error String Number Boolean Array Object Promise Map Set Symbol JSON Math Date RegExp parseInt parseFloat setTimeout clearTimeout setInterval clearInterval describe it test expect beforeEach afterEach beforeAll afterAll vi jest mock spyOn assertEquals assertTrue Ok Err Some None Box Vec Rc Arc Option Result format println eprintln vec write writeln min max abs sum any all sorted reversed enumerate zip map filter open iter next repr hash id input ord chr round divmod pow format".split()  # noqa: SIM905
)
_DEF_WORDS = r"(?:async\s+)?(?:def|func(?:\s*\([^)]*\))?|function\s*\*?|fn|class|type|struct|enum|trait|interface|macro)"
_MODIFIERS = r"(?:(?:export|default|pub(?:\([^)]*\))?|public|private|protected|static|abstract|declare|unsafe|const|extern)\s+)*"


@dataclass
class Callee:
    name: str
    path: str
    line: int  # 1-based line of the definition
    text: str  # the definition's lines, numbered
    truncated: bool = False


@dataclass
class Window:
    start: int
    end: int  # inclusive, 1-based


def _block_end(lines: list[str], start: int, lang: str | None, cap: int) -> int:
    """Index (0-based, inclusive) of the last line of the block opened at ``start``: for indented languages the last line more indented than the opener; for brace languages the line where the opener's braces balance; ``cap`` lines at most."""
    limit = min(len(lines), start + cap)
    opener = lines[start]
    if lang in ("py", "rb") or (lang is None and opener.rstrip().endswith(":")):
        indent = _indent(opener)
        end = start
        for i in range(start + 1, limit):
            if lines[i].strip() and _indent(lines[i]) <= indent and not (lang == "rb" and lines[i].strip() in ("end", "}")):
                break
            if lines[i].strip():
                end = i
        if lang == "rb" and end + 1 < len(lines) and lines[end + 1].strip() == "end":
            end += 1
        return end
    depth = 0
    seen = False
    for i in range(start, limit):
        for ch in lines[i]:
            if ch == "{":
                depth += 1
                seen = True
            elif ch == "}":
                depth -= 1
        if seen and depth <= 0:
            return i
    return limit - 1


def _enclosing_definition(lines: list[str], line: int, lang: str | None) -> int | None:
    """0-based index of the nearest definition line at or above ``line`` (1-based) with less indentation than the lines below it, or None."""
    i = min(line, len(lines)) - 1
    if i < 0:
        return None
    indent = _indent(lines[i]) if lines[i].strip() else 10**6
    if lang and lines[i].strip() and _definition(lang, lines[i]):
        return i
    for j in range(i - 1, -1, -1):
        if lines[j].strip() and _indent(lines[j]) < indent:
            indent = _indent(lines[j])
            if lang and _definition(lang, lines[j]):
                return j
            if indent == 0:
                break
    return None


def windows(text: str, fd: FileDiff, *, pad: int = WINDOW_PAD, max_window: int = WINDOW_MAX, max_lines: int = FILE_WINDOWS_MAX) -> list[Window]:
    """The parts of a long file a reviewer needs for its hunks: each hunk's enclosing definition, else the hunk with ``pad`` lines around it. Overlaps are merged; the total is capped at ``max_lines`` by trimming the last windows."""
    lines = text.splitlines()
    if fd.status == "added" or sum(h.new_len for h in fd.hunks) >= len(lines):
        return []  # the hunks already show the whole file
    lang = _lang(fd.path)
    spans: list[tuple[int, int]] = []
    for h in fd.hunks:
        changed = [n for op, n, _ in h.lines if op == "+" and n is not None]
        if not changed:  # a pure removal: the new-side line where the removed lines stood
            idx = next((i for i, (op, _, _) in enumerate(h.lines) if op == "-"), 0)
            changed = [next((n for _, n, _ in h.lines[idx:] if n is not None), h.new_end)]
        first, last = min(changed), max(changed)
        start = _enclosing_definition(lines, first, lang)
        if start is not None:
            end = _block_end(lines, start, lang, max_window)
            if end + 1 < last:  # the hunk runs past the definition; fall back to padding
                start, end = max(first - 1 - pad, 0), min(last - 1 + pad, len(lines) - 1)
        else:
            start, end = max(first - 1 - pad, 0), min(last - 1 + pad, len(lines) - 1)
        spans.append((start, end))
    spans.sort()
    merged: list[list[int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out: list[Window] = []
    used = 0
    for s, e in merged:
        if used >= max_lines:
            break
        e = min(e, s + (max_lines - used) - 1)
        out.append(Window(s + 1, e + 1))
        used += e - s + 1
    return out


def render_windows(path: str, text: str, wins: list[Window]) -> str:
    lines = text.splitlines()
    parts = [f"=== {path} (parts of the file after the change: {', '.join(f'{w.start}-{w.end}' for w in wins)} of {len(lines)} lines)"]
    for w in wins:
        if w is not wins[0]:
            parts.append("      ...")
        parts.extend(f"{i:>5}   {lines[i - 1]}" for i in range(w.start, w.end + 1))
    return "\n".join(parts) + "\n"


def called_names(files: list[FileDiff]) -> list[str]:
    """Names the added lines call, in first-seen order, without keywords, builtins and one- or two-letter names."""
    return [n for n, _ in _calls(files)]


def _calls(files: list[FileDiff]) -> list[tuple[str, bool]]:
    """(name, is_method_call) for each name the added lines call, first occurrence first; a name called both ways counts as a free call."""
    seen: dict[str, bool] = {}
    for fd in files:
        if fd.binary:
            continue
        for h in fd.hunks:
            for op, _, t in h.lines:
                if op != "+":
                    continue
                code = t.split("#", 1)[0] if fd.path.endswith((".py", ".rb")) else t.split("//", 1)[0]
                for method, pat in ((False, _CALL_RE), (True, _METHOD_RE)):
                    for m in pat.finditer(code):
                        name = m.group(1)
                        if len(name) > 2 and name not in _KEYWORDS and not name.isupper() and not name.startswith("__"):
                            seen[name] = seen.get(name, True) and method
    return list(seen.items())


def _defined_in(texts: dict[str, str]) -> set[str]:
    out: set[str] = set()
    for path, text in texts.items():
        lang = _lang(path)
        if not lang:
            continue
        for line in text.splitlines():
            if line.strip() and (name := _definition(lang, line)):
                out.add(name)
    return out


def _grep_definitions(repo_root: Path, names: list[str]) -> dict[str, list[tuple[str, int]]] | None:
    """{name: [(path, line)]} for definition lines of ``names`` in the working tree, or None when git grep cannot run."""
    if not names:
        return {}
    alt = "|".join(re.escape(n) for n in names)
    pats = [
        rf"^\s*{_MODIFIERS}{_DEF_WORDS}\s+({alt})\b",
        rf"^\s*{_MODIFIERS}(?:const|let|var)\s+({alt})\s*[:=]",
        rf"^\s*{_MODIFIERS}(?:async\s+)?(?:get\s+|set\s+)?({alt})\s*(?:<[^>]*>)?\s*\([^()]*\)\s*(?::\s*[^{{;=]+)?\s*\{{\s*$",  # a class method in TS/JS/Java/Kotlin
        rf"^\s*def\s+self\.({alt})\b",
    ]
    cmd = ["git", "-C", str(repo_root), "grep", "-n", "-I", "-P", "--no-color"]
    for p in pats:
        cmd += ["-e", p]
    cmd += ["--", *(f"*{ext}" for ext in CODE_EXT), "*.java", "*.kt", "*.cs"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if res.returncode not in (0, 1):
        return None
    out: dict[str, list[tuple[str, int]]] = {}
    word = re.compile(r"\b(" + alt + r")\b")
    for row in res.stdout.splitlines():
        path, _, rest = row.partition(":")
        no, _, text = rest.partition(":")
        if not no.isdigit():
            continue
        m = word.search(text)
        if m:
            out.setdefault(m.group(1), []).append((path, int(no)))
    return out


def _read(repo_root: Path, path: str) -> str | None:
    p = (repo_root / path)
    try:
        return p.read_text(errors="replace") if p.is_file() and p.stat().st_size <= 2_000_000 else None
    except OSError:
        return None


_IMPORT_SOURCE_RE = re.compile(r"""(?:from\s+|import\s+|require\s*\(\s*)['"]?([\w./@-]+)['"]?""")


def _imported_stems(texts: dict[str, str]) -> set[str]:
    """Last path components of the modules the changed files import (``./request`` -> ``request``, ``httpx._models`` -> ``_models``), so a definition in such a module is one the change can see."""
    out: set[str] = set()
    for text in texts.values():
        for line in text.splitlines():
            if not re.match(r"\s*(?:from|import)\b|.*\brequire\s*\(", line):
                continue
            for m in _IMPORT_SOURCE_RE.finditer(line):
                stem = m.group(1).rstrip("/").rsplit("/", 1)[-1].rsplit(".", 1)[-1] if "/" in m.group(1) else m.group(1).rsplit(".", 1)[-1]
                out.add(stem)
                out.add(m.group(1).rstrip("/").rsplit("/", 1)[-1])
    return out


def _visible(def_path: str, changed: set[str], imported: set[str]) -> int:
    """How plausibly the changed files refer to a definition in ``def_path``: 2 for a module one of them imports by name, 1 for the same directory (same package), 0 otherwise."""
    d = Path(def_path)
    if d.stem in imported or (d.parent.name in imported and d.stem in ("index", "__init__", "mod")):
        return 2
    if any(Path(c).parent == d.parent for c in changed):
        return 1
    return 0


def callees(files: list[FileDiff], repo_root: Path, new_text: dict[str, str], *, max_defs: int = CALLEE_MAX, max_lines: int = CALLEE_MAX_LINES, max_chars: int = CALLEE_MAX_CHARS) -> tuple[list[Callee], bool]:
    """Definitions, elsewhere in the repository, of the names the added lines call. Returns them and whether the search could run.

    A free call (``parse_amount(x)``) is shown when the repository has one definition of the name, or one in a module the
    changed file imports or sits beside. A method call (``obj.render(x)``) says nothing about which class it lands on, so
    its definition is shown only when it is in such a module; a same-named method elsewhere is not assumed to be it.
    """
    calls = _calls(files)
    changed = {fd.path for fd in files}
    shown = _defined_in(new_text)
    calls = [(n, m) for n, m in calls if n not in shown]
    if not calls:
        return [], True
    found = _grep_definitions(repo_root, [n for n, _ in calls])
    if found is None:
        return [], False
    imported = _imported_stems(new_text)
    out: list[Callee] = []
    used = 0
    for name, method in calls:
        hits = [(p, n) for p, n in found.get(name, []) if p not in changed and not re.search(r"(^|/)(tests?|__tests__|testdata|spec)/|_test\.|\.test\.|\.spec\.", p)]
        ranked = sorted(((_visible(p, changed, imported), p, n) for p, n in hits), key=lambda t: -t[0])
        best = ranked[0][0] if ranked else 0
        if best:
            hits = [(p, n) for v, p, n in ranked if v == best]  # an imported module beats the same package beats the rest
        elif method or not hits or len(hits) > CALLEE_MAX_DEFS:
            continue
        if len({p for p, _ in hits}) > 1:  # several definitions in different files: ambiguous, so not shown
            continue
        path, line = hits[0]
        text = _read(repo_root, path)
        if text is None:
            continue
        lines = text.splitlines()
        if line > len(lines):
            continue
        start = line - 1
        # decorators and doc comments just above the definition belong to it
        while start > 0 and lines[start - 1].strip().startswith(("@", "///", "//!", "#[", "/**", "*", "*/")):
            start -= 1
        # the budget counts from the definition line; the decorator lines above it come on top
        end = _block_end(lines, line - 1, _lang(path), max_lines + 1)
        truncated = end - (line - 1) + 1 > max_lines
        end = min(end, (line - 1) + max_lines - 1)
        body = "\n".join(f"{i + 1:>5}   {lines[i]}" for i in range(start, end + 1))
        if used + len(body) > max_chars:
            break
        used += len(body)
        out.append(Callee(name, path, line, body, truncated))
        if len(out) >= max_defs:
            break
    return out, True


def render_callees(cs: list[Callee]) -> str:
    parts = []
    for c in cs:
        shown = c.text.count("\n") + 1
        head = f"=== {c.path}: definition of `{c.name}` (line {c.line}; from the repository, not changed by this diff{f'; cut after {shown} lines' if c.truncated else ''})"
        parts.append(head + "\n" + c.text + "\n")
    return "\n".join(parts)


__all__ = ["Callee", "Window", "windows", "render_windows", "callees", "render_callees", "called_names"]
