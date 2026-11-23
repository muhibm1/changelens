"""Render and parse every changed warehouse file at the commit and at its parent; report only what newly fails.

A model reading a diff often misses a stray comma or a template branch that renders to broken SQL, because it reads
the template, not what the template produces. This step renders each changed file the way the warehouse would, under
every branch its template switches on, and parses the result in the dialect the file parsed in before the change. It
runs no model and touches no database. A failure counts only when the parent version of the same file, rendered the
same way, parsed: a file that never parsed tells us nothing about this commit.

It also runs one scope lint that a parser cannot: a WHERE or HAVING clause that names an alias the same SELECT
defines with a window function (no warehouse allows that; the alias does not exist until after filtering). Like a
parse failure, it counts only when the parent version of the file did not do the same.

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
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

SQL_EXT = (".sql", ".hql", ".q", ".sqlx", ".ddl", ".psql", ".pgsql")
DIALECTS = ("postgres", "tsql", "hive", "spark", "bigquery", "snowflake", "duckdb", "mysql", "redshift", "oracle")
MAX_CONDITIONS = 6


@dataclass
class CompileFailure:
    path: str
    line: int | None
    dialect: str
    variant: str
    error: str
    kind: str = "parse"
    snippet: str = ""


def _snippet(sql: str, line: int | None, around: int = 10) -> str:
    """The rendered SQL around ``line``, numbered as rendered, so a reader sees what the warehouse would get."""
    rows = sql.splitlines()
    if not rows:
        return ""
    mid = (line or 1) - 1
    lo, hi = max(0, mid - around), min(len(rows), mid + around + 1)
    return "\n".join(f"{i + 1:>5}  {rows[i]}" for i in range(lo, hi) if rows[i].strip())[:1800]


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


def _eval_condition(cond: str, values: dict[str, object]) -> bool:
    """A SqlRender condition evaluated with parameter values (``==``, ``!=``, ``|``, ``&``, ``!``, ``IN``)."""
    expr = re.sub(r"@(\w+)", lambda m: repr(values.get(m.group(1), "")), cond)
    expr = expr.replace("!=", " NE ").replace("==", " EQ ").replace("!", " not ")
    expr = expr.replace(" NE ", " != ").replace(" EQ ", " == ").replace("|", " or ").replace("&", " and ")
    expr = re.sub(r"(?i)\bIN\s*\(", " in (", expr)
    try:
        return bool(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307 - built only from the template's own condition text and literals
    except Exception:  # noqa: BLE001 - an expression this reader cannot evaluate is treated as false
        return False


def _truth_tables(conds: list[str]) -> list[dict[str, bool]]:
    """Condition truth values for every consistent setting of the parameters the conditions read.

    Each parameter takes the empty value, each literal it is compared with, or (when it is used bare) true and false,
    so ``@x == 'a'`` and ``@x == 'b'`` are never both true and a compound condition agrees with its parts. At most
    MAX_CONDITIONS parameters vary; the rest stay empty."""
    params: list[str] = []
    options: dict[str, list[object]] = {}
    for c in conds:
        for name in re.findall(r"@(\w+)", c):
            if name not in options:
                params.append(name)
                options[name] = [""]
            for lit in re.findall(rf"@{name}\s*[!=]=\s*('([^']*)'|-?\d+)", c):
                v: object = lit[1] if lit[0].startswith("'") else int(lit[0])
                if v not in options[name]:
                    options[name].append(v)
            if re.search(rf"(?<![=!]\s)@{name}\b(?!\s*[!=]=)", c) and True not in options[name]:
                options[name] += [True, False]
    for name in params:
        if "" in options[name] and len(options[name]) == 1:
            options[name].append("x")
    varying = params[:MAX_CONDITIONS]
    out: list[dict[str, bool]] = []
    seen: set[tuple[bool, ...]] = set()
    for combo in itertools.product(*(options[n] for n in varying)):
        values = dict(zip(varying, combo, strict=True))
        truth = {c: _eval_condition(c, values) for c in conds}
        key = tuple(truth.values())
        if key not in seen:
            seen.add(key)
            out.append(truth)
    return out


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


_SQLRENDER_DEFAULT = re.compile(r"(?im)^[ \t]*\{DEFAULT\s+@\w+\s*=[^{}\n]*\}[ \t]*;?[ \t]*$")


def strip_sqlrender_defaults(text: str) -> str:
    """``text`` with SqlRender's ``{DEFAULT @x = ...}`` header lines blanked (line numbers kept); they set defaults, not SQL."""
    return _SQLRENDER_DEFAULT.sub("", text)


def render_variants(path: str, raw: str) -> list[tuple[str, str]]:
    """(name, rendered SQL) for each way the file can come out; one variant for a file with no templating."""
    from .warehouse_rules import dejinja

    raw = strip_sqlrender_defaults(raw)
    text = re.sub(r"\$\{(?:hiveconf:|hivevar:|env:)?(\w+)\}", r"v_\1", raw)
    variants: list[tuple[str, str]] = []
    conds = _sqlrender_conditions(text)
    if ("{%" in text or "{{" in text) and not conds:
        variants.append(("jinja", dejinja(text, None, unknown="NULL")))
        if "{%" in text:
            variants += [("jinja-first-arm", dejinja(text, None, unknown="NULL", branch=0)), ("jinja-else-arm", dejinja(text, None, unknown="NULL", branch=1))]
        return variants
    if conds:
        for truth in _truth_tables(conds):
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


def window_alias_filters(sql: str, dialect: str) -> set[str]:
    """Aliases of window-function columns that a WHERE or HAVING of the same SELECT refers to by name."""
    out: set[str] = set()
    try:
        trees = sqlglot.parse(sql, read=dialect)
    except Exception:  # noqa: BLE001 - unparseable SQL is the parse check's business, not this lint's
        return out
    for tree in trees:
        if tree is None:
            continue
        for sel in tree.find_all(exp.Select):
            aliases = {e.alias.lower() for e in sel.expressions if isinstance(e, exp.Alias) and e.this.find(exp.Window)}
            if not aliases:
                continue
            for key in ("where", "having"):
                node = sel.args.get(key)
                if node is None:
                    continue
                for col in node.find_all(exp.Column):
                    if not col.table and col.name.lower() in aliases and col.find_ancestor(exp.Select) is sel:
                        out.add(col.name.lower())
    return out


def _scope_snippet(sql: str, alias: str, where_line: int | None) -> str:
    """The rendered lines that define the window alias and the filter that uses it, with the SELECT they share between."""
    rows = sql.splitlines()
    define = next((i + 1 for i, ln in enumerate(rows) if re.search(r"(?i)\bOVER\b.*\bAS\s+" + re.escape(alias) + r"\b", ln)), None)
    if define is None or where_line is None or define >= where_line:
        return _snippet(sql, where_line)
    lo, hi = max(1, define - 6), min(len(rows), where_line + 2)
    keep = [i for i in range(lo, hi + 1) if rows[i - 1].strip()]
    if len(keep) > 40:
        keep = keep[:12] + keep[-20:]
    return "\n".join(f"{i:>5}  {rows[i - 1]}" for i in keep)[:2400]


def _scope_failure(path: str, new_v: list[tuple[str, str]], old_v: list[tuple[str, str]], dialect: str) -> CompileFailure | None:
    """A failure when the new file filters on a window alias in a way the parent version did not."""
    before: set[str] = set()
    for _, sql in old_v:
        before |= window_alias_filters(sql, dialect)
    for name, sql in new_v:
        added = sorted(window_alias_filters(sql, dialect) - before)
        if added:
            line = None
            for i, ln in enumerate(sql.splitlines(), 1):
                if re.search(r"(?i)\b(where|having|and|or)\b.*\b" + re.escape(added[0]) + r"\b", ln):
                    line = i
                    break
            return CompileFailure(
                path, line, dialect, name,
                f"WHERE or HAVING refers to {', '.join(added)}, an alias the same SELECT defines with a window function; "
                "the alias does not exist when the filter runs, so the warehouse rejects the query",
                kind="scope", snippet=_scope_snippet(sql, added[0], line),
            )
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
            ln = _error_line(e)
            return CompileFailure(path, ln, dialect, name, str(e).replace("\x1b[4m", "").replace("\x1b[0m", "")[:600], snippet=_snippet(sql, ln))
    return _scope_failure(path, new_v, render_variants(path, old) if old is not None else [], dialect)


def checkable(path: str, text: str) -> bool:
    """Whether the step can read ``text`` at all: some dialect parses every rendered variant of it."""
    return _dialect_for(render_variants(path, text), path) is not None


def check_commit(repo: Path, parent: str, commit: str, max_files: int = 40, unchecked: list[str] | None = None) -> list[CompileFailure]:
    """Every changed SQL file that parsed before this commit and does not now, rendered under every template branch.

    Changed SQL files the step could not read at the parent (so cannot judge) are appended to ``unchecked``."""
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
        elif unchecked is not None and old is not None and not checkable(path, old):
            unchecked.append(path)
    return out
