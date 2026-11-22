"""Render and parse every changed warehouse file at the commit and at its parent; report only what newly fails.

A model reading a diff often misses a stray comma or a template branch that renders to broken SQL, because it reads
the template, not what the template produces. This step renders each changed file the way the warehouse would, under
every branch its template switches on, and parses the result in the dialect the file parsed in before the change. It
runs no model and touches no database. A failure counts only when the parent version of the same file, rendered the
same way, parsed: a file that never parsed tells us nothing about this commit.

Templates handled: dbt Jinja (references and simple macros written out, unknown expressions as NULL, each if/else
arm), SqlRender (``{@flag} ? {a} : {b}`` under every combination of up to five conditions, ``@param`` as a name),
and Hive or shell variables (``${var}``, ``${hiveconf:var}``).
"""

from __future__ import annotations

import itertools
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import sqlglot
from sqlglot.errors import ParseError, TokenError

SQL_EXT = (".sql", ".hql", ".q", ".sqlx", ".ddl", ".psql", ".pgsql")
DIALECTS = ("postgres", "tsql", "hive", "spark", "bigquery", "snowflake", "duckdb", "mysql", "redshift", "oracle")
MAX_CONDITIONS = 5


@dataclass
class CompileFailure:
    path: str
    line: int | None
    dialect: str
    variant: str
    error: str


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout


def _sqlrender_conditions(text: str) -> list[str]:
    """The distinct ``{condition}`` heads of SqlRender's ``{cond} ? {a} : {b}`` blocks, in order of first use."""
    seen: list[str] = []
    for m in re.finditer(r"\{([^{}]*@[^{}]*)\}\s*\?", text):
        c = m.group(1).strip()
        if c not in seen:
            seen.append(c)
    return seen


def _match_brace(text: str, i: int) -> int:
    """Index of the ``}`` closing the ``{`` at ``i``, or -1."""
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return -1


def render_sqlrender(text: str, truth: dict[str, bool]) -> str:
    """``text`` with every ``{cond} ? {a} : {b}`` resolved by ``truth`` and every ``@param`` replaced by a name."""
    out = text
    for _ in range(200):
        m = re.search(r"\{([^{}]*@[^{}]*)\}\s*\?\s*\{", out)
        if not m:
            break
        a_open = out.index("{", m.end() - 1)
        a_close = _match_brace(out, a_open)
        if a_close < 0:
            break
        end = a_close + 1
        b = ""
        rest = re.match(r"\s*:\s*\{", out[end:])
        if rest:
            b_open = end + rest.end() - 1
            b_close = _match_brace(out, b_open)
            if b_close < 0:
                break
            b = out[b_open + 1 : b_close]
            end = b_close + 1
        a = out[a_open + 1 : a_close]
        keep = a if truth.get(m.group(1).strip(), False) else b
        out = out[: m.start()] + keep + out[end:]
    return re.sub(r"@(\w+)", r"p_\1", out)


def render_variants(path: str, raw: str) -> list[tuple[str, str]]:
    """(name, rendered SQL) for each way the file can come out; one variant for a file with no templating."""
    from .warehouse_rules import dejinja

    text = re.sub(r"\$\{(?:hiveconf:|hivevar:|env:)?(\w+)\}", r"v_\1", raw)
    variants: list[tuple[str, str]] = []
    if "{%" in text or "{{" in text:
        variants.append(("jinja", dejinja(text, None, unknown="NULL")))
        if "{%" in text:
            variants += [("jinja-first-arm", dejinja(text, None, unknown="NULL", branch=0)), ("jinja-else-arm", dejinja(text, None, unknown="NULL", branch=1))]
        return variants
    conds = _sqlrender_conditions(text)
    if conds:
        conds = conds[:MAX_CONDITIONS]
        for combo in itertools.product([False, True], repeat=len(conds)):
            truth = dict(zip(conds, combo, strict=True))
            name = ",".join(f"{c}={'T' if v else 'F'}" for c, v in truth.items())
            variants.append((name, render_sqlrender(text, truth)))
        return variants
    if "@" in text and re.search(r"@\w+", text):
        return [("params", render_sqlrender(text, {}))]
    return [("plain", text)]


def _error_line(e: Exception) -> int | None:
    errs = getattr(e, "errors", None) or []
    for d in errs:
        if isinstance(d, dict) and d.get("line"):
            return int(d["line"])
    m = re.search(r"[Ll]ine (\d+)", str(e))
    return int(m.group(1)) if m else None


def parse_error(sql: str, dialect: str) -> Exception | None:
    """The parse error for ``sql`` in ``dialect``, or None when every statement parses."""
    if not sql.strip():
        return None
    try:
        sqlglot.parse(sql, read=dialect, error_level=sqlglot.ErrorLevel.RAISE)
    except (ParseError, TokenError) as e:
        return e
    except Exception as e:  # noqa: BLE001 - a sqlglot crash is treated as unparseable, not as a defect
        return e
    return None


def _dialect_order(path: str) -> tuple[str, ...]:
    """DIALECTS with the ones the path itself names (a .hql file, a sql_server folder) tried first."""
    p = path.lower()
    first: list[str] = []
    if p.endswith((".hql", ".q")) or "/hive" in p or "/spark" in p:
        first += ["hive", "spark"]
    for key, d in (("sql_server", "tsql"), ("tsql", "tsql"), ("postgres", "postgres"), ("bigquery", "bigquery"), ("snowflake", "snowflake"), ("redshift", "redshift"), ("oracle", "oracle"), ("duckdb", "duckdb")):
        if key in p:
            first.append(d)
    return tuple(dict.fromkeys([*first, *DIALECTS]))


def _dialect_for(variants: list[tuple[str, str]], path: str = "") -> str | None:
    """The first dialect in which every variant parses."""
    for d in _dialect_order(path):
        if all(parse_error(sql, d) is None for _, sql in variants):
            return d
    return None


def check_file(path: str, new: str, old: str | None, siblings: list[str] | None = None) -> CompileFailure | None:
    """A failure when ``new`` does not parse in the dialect ``old`` (or, for a new file, its siblings) parsed in."""
    if not path.lower().endswith(SQL_EXT):
        return None
    new_v = render_variants(path, new)
    if old is not None:
        dialect = _dialect_for(render_variants(path, old), path)
    else:
        dialect = None
        for sib in siblings or []:
            dialect = _dialect_for(render_variants(path, sib), path)
            if dialect:
                break
    if dialect is None:
        return None
    for name, sql in new_v:
        e = parse_error(sql, dialect)
        if e is not None:
            return CompileFailure(path, _error_line(e), dialect, name, str(e).replace("\x1b[4m", "").replace("\x1b[0m", "")[:600])
    return None


def check_commit(repo: Path, parent: str, commit: str, max_files: int = 40) -> list[CompileFailure]:
    """Every changed SQL file that parsed before this commit and does not now, rendered under every template branch."""
    names = _git(repo, "diff", "--name-status", "-M", parent, commit).splitlines()
    out: list[CompileFailure] = []
    for row in names[:max_files]:
        parts = row.split("\t")
        status, path = parts[0], parts[-1]
        old_path = parts[1] if status.startswith("R") and len(parts) == 3 else path
        if status.startswith("D") or not path.lower().endswith(SQL_EXT):
            continue
        try:
            new = _git(repo, "show", f"{commit}:{path}")
        except subprocess.CalledProcessError:
            continue
        old = None
        siblings: list[str] = []
        if not status.startswith("A"):
            try:
                old = _git(repo, "show", f"{parent}:{old_path}")
            except subprocess.CalledProcessError:
                old = None
        if old is None:
            folder = str(Path(path).parent)
            listing = _git(repo, "ls-tree", "--name-only", parent, folder + "/").splitlines()
            for sib in [s for s in listing if s.lower().endswith(SQL_EXT)][:3]:
                try:
                    siblings.append(_git(repo, "show", f"{parent}:{sib}"))
                except subprocess.CalledProcessError:
                    continue
        f = check_file(path, new, old, siblings)
        if f:
            out.append(f)
    return out
