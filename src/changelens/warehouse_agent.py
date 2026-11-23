"""Agent review of a warehouse change: a model reads the diff, investigates the repository, and files findings.

The deterministic rules in ``warehouse_rules`` are cheap and never call a model, but on warehouses they were not
tuned on they catch few real bugs (about 10% on two unseen sets). This module puts a model-based reviewer on top:

1. The rules run first, as a cheap filter. Their findings are passed to the reviewer as leads, never as verdicts.
2. A reviewer agent (Claude Code in print mode, read-only tools: Read, Grep, Glob) gets the diff and a copy of the
   repository exactly as it was at the commit, with no git history, so it can read the models a change joins to,
   their unique tests, the YAML that documents them and every model that reads a changed column. It files
   findings as JSON.
3. A second, skeptical pass reads each finding marked ``bug`` and tries to refute it in the same snapshot. Only
   findings it confirms keep the ``bug`` severity; the rest drop to ``risk``.

``bug`` findings are what an agent or CI should act on. ``risk`` findings are notes.

The model runs through the user's Claude Code login (``claude -p``), not an API key.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import time
from dataclasses import asdict, dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any

AGENT_VERSION = "wa-11"
DOC_DIFF_CHARS = 15_000
DIFF_CHARS = 90_000

FINDING = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "line": {"type": "integer"},
        "end_line": {"type": "integer"},
        "severity": {"type": "string", "enum": ["bug", "risk"]},
        "category": {
            "type": "string",
            "enum": [
                "row_count_change",
                "nullability_or_cast",
                "dropped_or_renamed_column_with_readers",
                "documented_column_not_selected",
                "schema_contract",
                "broken_reference_or_syntax",
                "business_logic",
                "other",
            ],
        },
        "title": {"type": "string"},
        "explanation": {"type": "string"},
        "evidence": {"type": "string"},
    },
    "required": ["path", "line", "severity", "category", "title", "explanation", "evidence"],
}
AUDIT = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "line": {"type": "integer"},
        "kind": {"type": "string", "enum": ["join", "dedup", "union", "group_by", "filter"]},
        "target": {"type": "string"},
        "key": {"type": "string"},
        "grain": {"type": "string"},
        "grain_source": {"type": "string"},
        "safe": {"type": "boolean"},
    },
    "required": ["path", "line", "kind", "target", "key", "grain", "grain_source", "safe"],
}
REVIEW_SCHEMA = {"type": "object", "properties": {"findings": {"type": "array", "items": FINDING}}, "required": ["findings"]}
GRAIN_SCHEMA = {"type": "object", "properties": {"audit": {"type": "array", "items": AUDIT}, "findings": {"type": "array", "items": FINDING}}, "required": ["audit", "findings"]}
VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "verdict": {"type": "string", "enum": ["bug", "risk", "reject"]},
                    "reason": {"type": "string"},
                    "witness": {"type": "string"},
                    "doubt": {"type": "string", "enum": ["none", "fact", "intent", "external"]},
                    "cause": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}, "line": {"type": "integer"}, "removed": {"type": "boolean"}},
                        "required": ["path", "line", "removed"],
                    },
                },
                "required": ["index", "verdict", "reason", "witness", "doubt", "cause"],
            },
        }
    },
    "required": ["verdicts"],
}

BASE_PROMPT = """You are reviewing one commit to a SQL data warehouse (dbt, SQLMesh or plain SQL) before it merges.
Your job is to catch defects this change introduces that would make the warehouse produce wrong data or fail to build.

The working directory is a copy of the repository exactly as it is AFTER this commit (no git history). Use Read, Grep
and Glob to investigate: open the full changed files, the models they select from or join to, the YAML that documents
and tests them, macros they call, and the models downstream that read what changed. Go and look rather than guess.

{focus}

Rules:
- Only defects this change introduces or exposes, never ones that were already there before the commit.
- No style, performance, naming, missing tests or documentation gaps unless they break something.
- severity "bug": the repository shows the change produces wrong rows or values, breaks the build, or breaks a
  documented contract or test. severity "risk": plausible, but the repository neither shows it nor rules it out.
- Report every distinct defect you find in your area, up to 6, the ones that corrupt data first. Most commits are
  fine; if nothing in your area is wrong, return an empty findings list.
- For each finding give path and line (line numbers in the file AFTER the commit, as shown in the numbered diff), an
  end_line if it spans lines, a category, a short title naming the model and column involved, an explanation of the
  mechanism (which rows are duplicated, dropped or nulled, or what fails, and why), and the evidence you read
  (file:line).

{hints}
Commit message: {message}

The diff (against the commit's parent; numbers are line numbers after the commit):
```diff
{diff}
```
"""

FOCUS = {
    "grain": """Your area: ROW COUNT and GRAIN. Audit every join, union, dedup, GROUP BY, DISTINCT and filter that the change
adds, edits or removes (return the audit):
- Join: the model or CTE joined, the join key, and that side's grain (the columns that make one row unique there),
  read from its YAML tests (unique, unique_combination_of_columns, primary key, grain()), its GROUP BY, QUALIFY or
  DISTINCT, the surrogate key it builds, or what its rows plainly are (a per-run, per-feed, per-version, per-platform
  or history table repeats a natural id). If the key misses a grain column, rows fan out: for example joining on
  block_id when the model is one row per block per course run, on username when usernames repeat, on a product id
  when the model has a row per platform, on an SCD table without its current-row or validity filter. A join whose
  key is compared to itself, or uses the wrong column, also fans out. An INNER join (or a WHERE on the right side of
  a LEFT join) to a side that may lack the key drops rows that used to be kept.
- Dedup (ROW_NUMBER, RANK, DENSE_RANK, QUALIFY, DISTINCT ON): whether PARTITION BY is the entity the model is unique
  on. Too narrow drops real rows (for example ranking whole files and keeping only the latest file, which loses rows
  that exist only in an earlier file); too wide or on the wrong columns keeps duplicates.
- Union: whether branches carry the same entity twice, or a branch carries rows the grain forbids (deleted,
  superseded or test records).
- GROUP BY: whether a new grouping column (a title, a name, a status) splits an entity into several rows that
  downstream joins or the documented grain expect once.
- Removed DISTINCT, GROUP BY, filter or dedup; a declared grain or unique test the output no longer satisfies.""",
    "values": """Your area: VALUES. Check every expression the change adds or edits:
- Casts: SAFE_CAST or TRY_CAST that silently turns unparseable input (non-ISO timestamps, formatted numbers) into NULL,
  a CAST to a type too small (for example uint256 or large integers into DECIMAL(38,0) or BIGINT, integer
  multiplication that overflows before a cast), a cast of a non-numeric string.
- Nulls: a LEFT join whose unmatched rows now carry NULLs into a not_null or key column, a removed COALESCE, a
  comparison or filter against NULL, NULLs in a hash or concatenated key.
- Arithmetic: division that can divide by zero, integer division, wrong units or scale, sums of distinct counts.
- Logic that is plainly inconsistent with the model's own documentation, name or the rest of the change: a CASE that
  maps to the wrong value, a filter on the wrong column, a date boundary off by one, the wrong aggregation, a column
  filled from the wrong source column, a macro called with literals where columns were meant.""",
    "contract": """Your area: COLUMNS, CONTRACTS and REFERENCES. Check:
- A column dropped or renamed while another model, a test, an exposure or the YAML still reads it (grep the whole
  repository for the old name).
- A column documented or tested in YAML (including YAML anchors and shared blocks) that the model does not select,
  or that the model selects under a different name; a primary key or surrogate key column removed from the select.
- A column referenced that the upstream model does not output; an alias missing, misspelled or not resolving.
- ref() or source() to a model that does not exist or is disabled, a hardcoded schema.table in place of ref(),
  reading a raw or input-layer model where the project has a staging model for it, a typo in a model name.
- A model's declared grain, contract, unique key or column types broken by the change; a test or YAML block that
  dbt or SQLMesh will reject; a SQL syntax error.""",
    "edges": """Your area: EDGE CASES, NULL SEMANTICS and CONSISTENCY WITH SIBLINGS. For every model the change adds or edits:
- NULLs in join keys: an equality join, USING or FULL OUTER JOIN on a column that can be NULL (it comes from a LEFT
  join, a nullable source column with no not_null test, a CASE without ELSE, a parsed or optional field). NULL never
  equals NULL, so those rows never match: a FULL OUTER join splits one group into two rows, a LEFT join loses the
  metrics, an INNER join drops the rows. Say where the NULL comes from.
- NULLs in filters and arithmetic: a WHERE on a column of the right-hand side of a LEFT join (unmatched rows are
  dropped), `col <> 'x'` or `col NOT IN (...)` that silently drops NULL rows, `a + b` or a running total in a
  recursive CTE or window where one term can be NULL (the whole result becomes NULL), a missing COALESCE.
- Boundaries: `>` vs `>=` and `<` vs `<=` against dates or timestamps (comparing a midnight date to a timestamp
  drops the first day; an inclusive end double counts), a date spine or calendar that starts or ends one day off,
  BETWEEN on timestamps, a lookback window or incremental filter that skips or repeats the edge.
- "Latest", "first", "current" or "most recent" selections (MAX, ORDER BY ... LIMIT 1, ROW_NUMBER = 1) that do not
  filter to the kind of record the model means (for example the latest invocation of any command when only runs
  count, the latest row including deleted or test rows), so the result can be empty or wrong.
- Consistency with siblings: open two or three sibling models (same folder, same naming pattern, same purpose) and
  check whether every sibling applies something this model omits: a macro (exception filters, enabled flags,
  deduplication, surrogate keys), a WHERE on deleted or inactive rows, a config, a COALESCE. Report the omission only
  when the siblings consistently do it and this model's documented purpose is the same.""",
}

VERIFY_PROMPT = """Reviewers filed the candidate findings below against one commit to a SQL data warehouse. The working
directory is the repository exactly as it is after the commit. Decide each candidate independently and skeptically,
by reading the files yourself with Read, Grep and Glob.

Verdicts:
- "bug": after checking, the change as written really does produce wrong rows or values, break the build, or break a
  documented contract or test, and this commit introduces it (see the diff). For a join fan-out it is enough that the
  joined side's grain, as the repository documents (tests, grain, keys), builds (GROUP BY, QUALIFY, the key it
  constructs) or plainly holds (a per-run, per-feed, per-version or per-platform table repeating a natural id), is
  wider than the join key; data is not needed. For a dropped or missing column it is enough that a reader, test or
  YAML entry names it. For lost rows it is enough that the change adds an INNER join, or a WHERE on the right-hand
  table of a LEFT join, to a side that the repository shows can lack the key (no relationships test, an optional or
  differently sourced table, names matched as text across sources), in a model whose documented purpose covers
  those rows. For a NULL join key, NULL filter or NULL arithmetic it is enough that the repository shows the column
  can be NULL (it comes from the outer side of a join, a CASE without ELSE, a nullable source column, or the YAML
  says so) and nothing in the model handles it. For a skipped convention it is enough that every sibling model with
  the same purpose applies it and this one does not.
- "risk": a real concern you could neither confirm nor rule out from the repository.
- "reject": the repository shows it does not hold (the joined side is unique on the key, the column is selected
  after all, the reader does not exist, the NULL is handled), it is style or speculation, or it predates the commit.
When two candidates describe the same defect, give the better one its verdict and reject the other as a duplicate.

For every candidate also give a `witness`: one concrete input (a row, a second source or connection, a NULL, an
edge date, a build setting or template flag) and what goes wrong with it (the wrong output row or value, or the
error). A candidate whose witness needs only inputs the repository's own sources, tests or documentation allow is a
"bug" when everything else above holds; one whose witness needs an input the repository suggests never occurs is a
"risk"; if you cannot write a witness at all, it is not a bug.

Three more rules:
- Introduced or exposed by this commit. Name the line this commit changed that causes the defect, as `cause`: the
  path and the numbered line of an added line in the diff, or for a defect caused by a line the commit removed, the
  path and 0 with removed true. A changed line that makes an older, untouched line wrong counts (a new grain or key
  column, a new union branch, a changed type, a changed default): name the changed line. If nothing this commit adds
  or removes causes it or exposes it, reject it as predating.
- Deliberate. If the commit message, or documentation the same commit changes (shown below), says the behaviour
  change is intended (a switch of units, a removed switch, a changed default, a new filter), reject a finding that
  only says the behaviour changed. Keep it only when the change also breaks the build, a test, or a reader that the
  commit left behind.
- Evidence. Files in the repository count as evidence even when a target is defined elsewhere: a sibling script,
  another version of the same model, the repository's own DDL, spec or seeds. A claim that rests only on what an
  external package version contains, which no file shows, is at most "risk". Candidates marked [compile] come from
  rendering the changed file under each template branch and parsing it in the dialect the file parsed in before this
  commit; the parser's error is real, but check it is not an artefact of the rendering before you confirm it. Judge
  it on the rendered SQL shown with it, which is what the warehouse receives: in a template, a clause that looks
  nested under one branch may sit at the same query level as a SELECT from another branch once rendered.

For every candidate also give `doubt`, what stops it being a confirmed bug: "none" (it is a bug, or plainly not
one), "fact" (you could not establish a fact such as a grain, a key or whether a column can be NULL), "intent" (the
mechanism holds and the witness is real, but you cannot tell whether the author meant it), or "external" (it depends
on something outside the repository, such as a package version or the live data).

Commit message: {message}
{docs}
The diff:
```diff
{diff}
```

Candidates (index: finding):
{findings}
"""

SECOND_LOOK_SCHEMA = {
    "type": "object",
    "properties": {
        "looks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "contradiction": {"type": "boolean"},
                    "stated_intent": {"type": "boolean"},
                    "defines": {"type": "object", "properties": {"path": {"type": "string"}, "line": {"type": "integer"}}, "required": ["path", "line"]},
                    "contradicts": {"type": "object", "properties": {"path": {"type": "string"}, "line": {"type": "integer"}}, "required": ["path", "line"]},
                    "reason": {"type": "string"},
                },
                "required": ["index", "contradiction", "stated_intent", "defines", "contradicts", "reason"],
            },
        }
    },
    "required": ["looks"],
}

SECOND_LOOK_PROMPT = """A reviewer of one commit to a SQL data warehouse confirmed how each finding below would change the
output, and wrote a concrete input that shows it, but held each back as a "risk" because it could not tell whether the
author meant the behaviour. The working directory is the repository exactly as it is after the commit. Read the files
yourself with Read, Grep and Glob.

Intent can often be read from the code itself. For each finding, decide whether the repository contradicts itself:
one line defines a concept one way (a CTE or column named for what it holds, the positive branch of a filter, a
numerator, a sibling model's mapping of the same column, a block this one was copied from) and a line this commit
changed computes it another way that does not fit that definition (a "former contributors" set built from different
events than the ranked set it is compared with, a negated branch that is not the complement of the positive branch at
the same grain, a numerator and denominator over different populations, a block copied from elsewhere without
adapting the one part that had to change, a name the commit gives a parameter or column that states its meaning, such
as a range's start that the filter leaves out or a "distinct" count that is not distinct).

For each finding give:
- `contradiction`: true only if you can point at both lines and the mismatch is plain from them.
- `defines`: the path and line number (in the files as they are now) of the line that sets the expectation.
- `contradicts`: the path and line number of the line this commit changed that breaks it (it must be a line of the
  diff below that starts with +, numbered as shown).
- `stated_intent`: true if the commit message, a comment, or documentation says this behaviour is intended or not
  yet implemented. A finding whose behaviour is stated as intended is not a contradiction.
- `reason`: two or three sentences.
Most findings held back on intent were rightly held back. Say contradiction false unless both lines make it plain.

Commit message: {message}
{docs}
The diff:
```diff
{diff}
```

Findings (index: finding, then the reviewer's witness):
{findings}
"""

MAX_PROMOTIONS = 1

@dataclass
class AgentFinding:
    path: str
    line: int | None
    end_line: int | None
    severity: str
    category: str
    title: str
    explanation: str
    evidence: str = ""
    verified: bool | None = None
    verify_reason: str = ""
    source: str = ""
    cause: str = ""
    witness: str = ""
    doubt: str = ""
    cause_ok: bool | None = None

    @property
    def body(self) -> str:
        return f"{self.explanation}\n{self.evidence}"


@dataclass
class AgentResult:
    findings: list[AgentFinding] = field(default_factory=list)
    seconds: float = 0.0
    rule_findings: int = 0
    error: str | None = None
    turns: int = 0
    audit: list[dict[str, Any]] = field(default_factory=list)
    candidates: int = 0
    site_checks: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[AgentFinding] = field(default_factory=list)
    proposed: list[dict[str, Any]] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    second_looks: list[dict[str, Any]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _git(repo: Path, *args: str, text: bool = True) -> Any:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=text, check=True).stdout


def snapshot(repo: Path, commit: str, dest: Path) -> None:
    """The tree at ``commit`` written to ``dest`` with no git history (so nothing later in the repo can be read)."""
    data = _git(repo, "archive", "--format=tar", commit, text=False)
    with tarfile.open(fileobj=BytesIO(data)) as tf:
        members = [m for m in tf.getmembers() if not m.issym() and not m.islnk()]
        tf.extractall(dest, members=members, filter="data")


# Warehouse code is not only .sql: Hive and Spark keep queries in .hql, Dataform in .sqlx, OHDSI and other R
# packages render .sql templates from R, and some projects keep Jinja templates or DDL under other names.
CODE_GLOBS = ("*.sql", "*.hql", "*.q", "*.sqlx", "*.ddl", "*.psql", "*.pgsql", "*.jinja", "*.j2", "*.yml", "*.yaml", "*.py", "*.R", "*.r")


def _is_dataform(repo: Path, commit: str) -> bool:
    """Whether the repository is a Dataform project, where SQL also lives in JavaScript files."""
    try:
        names = set(_git(repo, "ls-tree", "--name-only", commit).split())
    except subprocess.CalledProcessError:
        return False
    return bool(names & {"dataform.json", "workflow_settings.yaml"})


def change_diff(repo: Path, parent: str, commit: str) -> str:
    globs = CODE_GLOBS + (("*.js",) if _is_dataform(repo, commit) else ())
    return _git(repo, "diff", "-M", "--unified=6", "--no-color", parent, commit, "--", *globs)


def touched_files(repo: Path, parent: str, commit: str) -> set[str]:
    """Every path the commit adds, changes, renames or deletes, of any type."""
    return {ln.split("\t")[-1] for ln in _git(repo, "diff", "--name-status", "-M", parent, commit).splitlines() if ln.strip()}


def compile_findings(repo: Path, parent: str, commit: str, unresolved: list[str] | None = None) -> list[AgentFinding]:
    """The render-and-parse step's failures as candidates for the verifier (no model; see ``compile_check``).

    Changed SQL files the step could not read are named in ``unresolved`` rather than passed over silently."""
    from .compile_check import check_commit

    unchecked: list[str] = []
    try:
        fails = check_commit(repo, parent, commit, unchecked=unchecked)
    except Exception as e:  # noqa: BLE001 - the step only adds candidates; a crash in it must not stop the review
        if unresolved is not None:
            unresolved.append(f"compile step failed: {str(e)[:200]}")
        return []
    if unresolved is not None:
        unresolved += [f"compile step could not read {p} at the parent commit, so did not check it" for p in unchecked]
    out = []
    for f in fails:
        if f.kind == "scope":
            out.append(AgentFinding(
                f.path, f.line, None, "bug", "broken_reference_or_syntax", f"{f.path} filters on a window-function alias",
                f"Rendered with template branch '{f.variant}' and read as {f.dialect}: {f.error}. The parent commit's version of the file did not do this.",
                evidence=f"compile step scope lint: {f.path} at this commit; not present at the parent. Rendered SQL (line numbers are of the rendered file, not the template):\n{f.snippet}", source="compile",
            ))
        else:
            out.append(AgentFinding(
                f.path, f.line, None, "bug", "broken_reference_or_syntax", f"{f.path} no longer parses as {f.dialect} after rendering",
                f"Rendered with template branch '{f.variant}', the file fails to parse as {f.dialect}, while the parent commit's version of it, rendered the same way, parsed. Parser error: {f.error}",
                evidence=f"compile step: {f.path} at the parent parses as {f.dialect}; at this commit it does not. Rendered SQL (line numbers are of the rendered file, not the template):\n{f.snippet}", source="compile",
            ))
    return out


def doc_diff(repo: Path, parent: str, commit: str) -> str:
    """The commit's changes to documentation (Markdown, reStructuredText, changelogs), which often state its intent."""
    d = _git(repo, "diff", "-M", "--unified=3", "--no-color", parent, commit, "--", "*.md", "*.rst", "*.txt", "CHANGELOG*")
    return d[:DOC_DIFF_CHARS] + ("\n... (documentation diff truncated)" if len(d) > DOC_DIFF_CHARS else "")


def changed_lines(diff: str) -> dict[str, tuple[set[int], bool]]:
    """For each file in a unified diff: the new-side numbers of its added lines, and whether it removes any line."""
    out: dict[str, tuple[set[int], bool]] = {}
    path = ""
    new = 0
    for ln in diff.splitlines():
        if ln.startswith("diff --git "):
            path = ln.split(" b/", 1)[-1]
            out.setdefault(path, (set(), False))
        elif ln.startswith("@@"):
            try:
                new = int(ln.split("+")[1].split(",")[0].split(" ")[0])
            except (IndexError, ValueError):
                new = 0
        elif ln.startswith(("+++", "---", "index ", "similarity", "rename ", "new file", "deleted file")):
            continue
        elif ln.startswith("+") and path:
            out[path][0].add(new)
            new += 1
        elif ln.startswith("-") and path:
            out[path] = (out[path][0], True)
        elif ln.startswith(" "):
            new += 1
    return out


def cause_in_change(cause: dict[str, Any] | None, changed: dict[str, tuple[set[int], bool]], slack: int = 2) -> bool:
    """Whether the line a verifier names as a defect's cause is one this commit added or removed (checked in code)."""
    if not isinstance(cause, dict):
        return False
    path = str(cause.get("path", "")).removeprefix("./").lstrip("/")
    hit = changed.get(path) or next((v for k, v in changed.items() if k.endswith("/" + path) or path.endswith("/" + k)), None)
    if hit is None:
        return False
    added, removed = hit
    if cause.get("removed"):
        return removed
    try:
        line = int(cause.get("line") or 0)
    except (TypeError, ValueError):
        return False
    return any(abs(line - a) <= slack for a in added)


def _claude(prompt: str, cwd: Path, schema: dict[str, Any], model: str, effort: str | None, timeout: int) -> tuple[dict[str, Any], int]:
    cmd = [
        os.environ.get("CHANGELENS_CLAUDE_BIN", "claude"), "-p", "--model", model, "--tools", "Read,Grep,Glob", "--setting-sources", "", "--strict-mcp-config",
        "--no-session-persistence", "--output-format", "json", "--json-schema", json.dumps(schema),
    ]
    if effort:
        cmd += ["--effort", effort]
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "AGENTOPS_TEST_ANTHROPIC_API_KEY")}
    last = ""
    for attempt in range(3):
        try:
            p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=cwd, timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            last = "timeout"
            continue
        try:
            d = json.loads(p.stdout)
        except json.JSONDecodeError:
            last = (p.stdout + p.stderr)[-400:]
            time.sleep(20 * (attempt + 1))
            continue
        out = d.get("structured_output")
        if out is None and d.get("result"):
            try:
                out = json.loads(d["result"])
            except (json.JSONDecodeError, TypeError):
                out = None
        if isinstance(out, dict):
            return out, int(d.get("num_turns") or 0)
        last = str(d.get("result") or d)[-400:]
        time.sleep(20 * (attempt + 1))
    raise RuntimeError(f"claude failed: {last}")


def _number_diff(diff: str) -> str:
    """The diff with the new-side line number in front of every added or context line, so findings can cite lines."""
    out: list[str] = []
    new = 0
    for ln in diff.splitlines():
        if ln.startswith("@@"):
            try:
                new = int(ln.split("+")[1].split(",")[0].split(" ")[0])
            except (IndexError, ValueError):
                new = 0
            out.append(ln)
        elif ln.startswith(("diff --git", "index ", "--- ", "+++ ", "similarity", "rename ", "new file", "deleted file")):
            out.append(ln)
        elif ln.startswith("-"):
            out.append(f"      {ln}")
        elif ln.startswith("+") or ln.startswith(" "):
            out.append(f"{new:5d} {ln}")
            new += 1
        else:
            out.append(ln)
    return "\n".join(out)


def _hints(rule_findings: list[Any]) -> str:
    if not rule_findings:
        return ""
    lines = ["Leads from a cheap pattern checker (often false alarms; check each, report only what holds up):"]
    for f in rule_findings[:12]:
        lines.append(f"- {f.path}:{f.line} [{f.rule}] {f.title}")
    return "\n".join(lines) + "\n"


def _merge(found: list[tuple[str, AgentFinding]]) -> list[AgentFinding]:
    """Candidates from every pass, with near-duplicates (same file, lines within 3, same category) folded together."""
    out: list[AgentFinding] = []
    for _, f in found:
        dup = any(g.path == f.path and g.category == f.category and g.line is not None and f.line is not None and abs(g.line - f.line) <= 3 for g in out)
        if not dup:
            out.append(f)
    return out


def _parse(out: dict[str, Any], source: str) -> list[AgentFinding]:
    fs = []
    for f in out.get("findings", [])[:6]:
        fs.append(
            AgentFinding(
                path=str(f.get("path", "")).removeprefix("./").lstrip("/"),
                line=f.get("line"),
                end_line=f.get("end_line"),
                severity=f.get("severity", "risk"),
                category=f.get("category", "other"),
                title=f.get("title", ""),
                explanation=f.get("explanation", ""),
                evidence=f.get("evidence", ""),
                source=source,
            )
        )
    return fs


SITE_PROMPT = """One change in a commit to a SQL data warehouse needs a careful row-count check. The working directory is
the repository exactly as it is after the commit. Investigate with Read, Grep and Glob until you can answer; do not
stop at the first file.

The site: {kind} at {path}:{line}, on {target}, keyed or partitioned by {key}.
A first reviewer guessed the grain there is "{grain}" (source: {source}). Do not trust it; establish it yourself.

What to establish:
- join: the grain of the joined side, meaning the set of columns that makes one of its rows unique. Read that model's
  SQL (what its rows are: a GROUP BY, QUALIFY, DISTINCT, the surrogate key it builds, a UNION of platforms or
  sources, a history or SCD table with validity columns, one row per run, feed, version, file or event), its YAML
  tests (unique, not_null, unique_combination_of_columns, primary key, grain), and if needed the models it reads.
  If the join key does not cover that grain, the join fans out ("fans_out"). If it is an INNER join, or a LEFT join
  filtered on the right side, and the right side can lack the key, rows are lost ("drops_rows").
- dedup: what PARTITION BY and ORDER BY keep and what they drop, against the entity the model must be unique on. Too
  narrow or ranking whole files or batches drops real rows ("drops_rows"); wrong or too wide keeps duplicates
  ("keeps_duplicates").
- union: whether two branches can carry the same entity, or a branch carries rows the grain forbids
  ("keeps_duplicates").
- group_by: whether a grouping column splits one entity into several rows that the model's documented grain or the
  joins downstream expect once ("fans_out").
Only the change made by this commit counts (see the diff). If the repository shows the site is fine say "safe"; if
you cannot tell, "unknown". When the verdict is a defect, fill `finding` (path and line after the commit, category
row_count_change, severity bug, a title naming the model and columns, the mechanism, and the evidence file:line).

Commit message: {message}

The diff:
```diff
{diff}
```
"""
SITE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["fans_out", "drops_rows", "keeps_duplicates", "safe", "unknown"]},
        "grain": {"type": "string"},
        "evidence": {"type": "string"},
        "finding": FINDING,
    },
    "required": ["verdict", "grain", "evidence"],
}
MAX_SITES = 6


def _sites(audit: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The joins, dedups, unions and groupings the grain pass audited, unsafe first, one per location, at most MAX_SITES."""
    seen: set[tuple[str, int]] = set()
    out = []
    for a in sorted(audit, key=lambda a: bool(a.get("safe"))):
        if a.get("kind") not in ("join", "dedup", "union", "group_by"):
            continue
        k = (str(a.get("path")), int(a.get("line") or 0))
        if k in seen:
            continue
        seen.add(k)
        out.append(a)
    return out[:MAX_SITES]


def _check_site(site: dict[str, Any], cwd: Path, message: str, diff: str, model: str, effort: str | None, timeout: int) -> tuple[dict[str, Any], int]:
    prompt = SITE_PROMPT.format(
        kind=site.get("kind"), path=site.get("path"), line=site.get("line"), target=site.get("target"), key=site.get("key"),
        grain=site.get("grain"), source=site.get("grain_source"), message=message, diff=diff,
    )
    try:
        return _claude(prompt, cwd, SITE_SCHEMA, model, effort, timeout)
    except RuntimeError as e:
        return {"verdict": "unknown", "grain": "", "evidence": f"check failed: {e}"[:300]}, 0


EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
CHUNK_FILES = 4
MAX_CHUNKS = 3


def _split_files(numbered: str) -> list[tuple[str, str]]:
    """The numbered diff cut into (path, that file's diff) pairs, in diff order."""
    parts: list[tuple[str, str]] = []
    cur: list[str] = []
    path = ""
    for ln in numbered.splitlines():
        if ln.startswith("diff --git "):
            if cur:
                parts.append((path, "\n".join(cur)))
            cur = [ln]
            path = ln.split(" b/", 1)[-1]
        else:
            cur.append(ln)
    if cur:
        parts.append((path, "\n".join(cur)))
    return parts


def _chunks(numbered: str) -> list[str]:
    """The diff as one part, or for a commit touching many files, up to MAX_CHUNKS parts of about CHUNK_FILES files each.

    A reviewer given twelve files tends to find two problems and stop; giving each reviewer a few files keeps the
    attention on every file. Files past the last chunk go into it, so nothing is dropped."""
    files = _split_files(numbered)
    if len(files) <= CHUNK_FILES:
        return [numbered]
    groups = [files[i : i + CHUNK_FILES] for i in range(0, len(files), CHUNK_FILES)]
    if len(groups) > MAX_CHUNKS:
        groups = groups[: MAX_CHUNKS - 1] + [sum(groups[MAX_CHUNKS - 1 :], [])]
    return ["\n".join(d for _, d in g) for g in groups]


def _is_doc(path: str) -> bool:
    """Whether a finding sits in prose documentation (a README, a docs folder), where it cannot break data or a build."""
    p = path.lower()
    return p.endswith((".md", ".rst", ".txt", ".adoc")) or p.startswith("docs/") or "/docs/" in p


def _cause_checkable(cause: dict[str, Any] | None, changed: dict[str, tuple[set[int], bool]], touched: set[str]) -> bool:
    """Whether code can hold the verifier to its named cause: the file is in the diff it read, or the commit did not touch it at all.

    A file the commit changed but the verifier never saw as a diff (a type the diff leaves out, or past a cut) cannot
    be checked, so the verdict stands rather than being lowered on a technicality."""
    if not isinstance(cause, dict):
        return True
    path = str(cause.get("path", "")).removeprefix("./").lstrip("/")
    def match(k: str) -> bool:
        return k == path or k.endswith("/" + path) or path.endswith("/" + k)
    if any(match(k) for k in changed):
        return True
    return not any(match(k) for k in touched)


def _verify(res: AgentResult, cwd: Path, message: str, numbered: str, docs: str, changed: dict[str, tuple[set[int], bool]], model: str, effort: str | None, timeout: int, touched: set[str] | None = None) -> None:
    """The skeptical pass over ``res.findings``: each is kept as bug or risk, or rejected.

    A bug verdict also has to name the line of this commit that causes the defect, and code checks that line is one
    the commit added or removed; a bug whose named cause is not in the change is lowered to risk."""
    touched = touched or set()
    listing = "\n".join(f"{i}: [{f.category}]{' [compile]' if f.source == 'compile' else ''} {f.path}:{f.line} {f.title}\n   {f.explanation}\n   evidence: {f.evidence}" for i, f in enumerate(res.findings))
    doc_block = f"\nDocumentation this commit changes:\n```diff\n{docs}\n```\n" if docs.strip() else ""
    vout, vturns = _claude(VERIFY_PROMPT.format(message=message, docs=doc_block, diff=numbered, findings=listing), cwd, VERIFY_SCHEMA, model, effort, timeout)
    res.turns += vturns
    verdicts = {v.get("index"): v for v in vout.get("verdicts", [])}
    kept = []
    for i, f in enumerate(res.findings):
        v = verdicts.get(i) or {"verdict": "risk", "reason": "no verdict"}
        verdict = v.get("verdict", "risk")
        cause = v.get("cause")
        f.cause = f"{cause.get('path')}:{'removed' if cause.get('removed') else cause.get('line')}" if isinstance(cause, dict) else ""
        f.verify_reason = v.get("reason", "")
        f.witness = str(v.get("witness", ""))[:600]
        f.doubt = str(v.get("doubt", "")) if verdict != "bug" else "none"
        f.cause_ok = cause_in_change(cause, changed)
        if verdict == "bug" and not cause_in_change(cause, changed) and _cause_checkable(cause, changed, touched):
            verdict = "risk"
            f.verify_reason = f"{f.verify_reason} [lowered: the named cause {f.cause or 'none'} is not a line this commit changed]"
        if verdict == "bug" and _is_doc(f.path):
            verdict = "risk"
            f.verify_reason = f"{f.verify_reason} [lowered: documentation only]"
        f.verified = verdict == "bug"
        if verdict == "reject":
            res.rejected.append(f)
            continue
        f.severity = verdict
        kept.append(f)
    res.findings = kept


def _line_exists(cwd: Path, ref: dict[str, Any] | None) -> bool:
    """Whether ``ref`` names a real line of a file in the snapshot."""
    if not isinstance(ref, dict):
        return False
    path = str(ref.get("path", "")).removeprefix("./").lstrip("/")
    line = ref.get("line")
    f = cwd / path
    if not path or not isinstance(line, int) or line < 1 or not f.is_file():
        return False
    try:
        return line <= len(f.read_text(errors="replace").splitlines())
    except OSError:
        return False


def _second_look(res: AgentResult, cwd: Path, message: str, numbered: str, docs: str, changed: dict[str, tuple[set[int], bool]], model: str, effort: str | None, timeout: int) -> None:
    """One more look at findings held back only on intent: promote to bug when the repository contradicts itself.

    Eligible: verdict risk, doubt "intent", a witness, and a cause the commit changed. The model must cite the line that
    defines the expectation and the changed line that breaks it; code checks both lines exist, that the second is a
    line this commit added, that the two differ, and that no stated intent was found. At most MAX_PROMOTIONS per commit."""
    pool = [f for f in res.findings if f.severity == "risk" and f.doubt == "intent" and f.witness.strip() and f.cause_ok and not _is_doc(f.path)]
    if not pool:
        return
    listing = "\n".join(f"{i}: [{f.category}] {f.path}:{f.line} {f.title}\n   {f.explanation}\n   witness: {f.witness}\n   why held back: {f.verify_reason}" for i, f in enumerate(pool))
    doc_block = f"\nDocumentation this commit changes:\n```diff\n{docs}\n```\n" if docs.strip() else ""
    out, turns = _claude(SECOND_LOOK_PROMPT.format(message=message, docs=doc_block, diff=numbered, findings=listing), cwd, SECOND_LOOK_SCHEMA, model, effort, timeout)
    res.turns += turns
    promoted = 0
    for look in sorted(out.get("looks", []), key=lambda x: x.get("index", 0)):
        i = look.get("index")
        if not isinstance(i, int) or not 0 <= i < len(pool):
            continue
        f = pool[i]
        d, c = look.get("defines"), look.get("contradicts")
        ok = (
            look.get("contradiction") is True
            and look.get("stated_intent") is False
            and _line_exists(cwd, d)
            and _line_exists(cwd, c)
            and cause_in_change({**c, "removed": False}, changed, slack=0)
            and (d.get("path"), d.get("line")) != (c.get("path"), c.get("line"))
        )
        res.second_looks.append({"path": f.path, "line": f.line, "title": f.title, **look, "promoted": ok and promoted < MAX_PROMOTIONS})
        if ok and promoted < MAX_PROMOTIONS:
            promoted += 1
            f.severity = "bug"
            f.verified = True
            f.verify_reason = f"{f.verify_reason} [promoted on second look: {d.get('path')}:{d.get('line')} defines it, {c.get('path')}:{c.get('line')} contradicts it. {str(look.get('reason', ''))[:400]}]"


def reverify(repo: Path, commit: str, proposed: list[dict[str, Any]], *, model: str = "opus", effort: str | None = None, timeout: int = 1200, second_look: bool = True) -> AgentResult:
    """Run only the skeptical pass again over findings an earlier review proposed (for changing the verifier without re-running the reviewers)."""
    t0 = time.monotonic()
    res = AgentResult(findings=[AgentFinding(**{**f, "severity": "bug", "verified": None, "verify_reason": "", "cause": "", "witness": "", "doubt": "", "cause_ok": None}) for f in proposed if f.get("source") != "compile"])
    res.proposed = proposed
    full = _git(repo, "rev-parse", commit).strip()
    try:
        parent = _git(repo, "rev-parse", f"{full}^").strip()
    except subprocess.CalledProcessError:
        parent = EMPTY_TREE
    message = _git(repo, "log", "-1", "--format=%s%n%n%b", full).strip()[:2000]
    diff = change_diff(repo, parent, full)
    numbered = _number_diff(diff)
    if len(numbered) > DIFF_CHARS:
        numbered = numbered[:DIFF_CHARS] + "\n... (diff truncated; read the changed files in full)"
    tmp = Path(tempfile.mkdtemp(prefix="wa-"))
    try:
        snapshot(repo, full, tmp)
        res.findings += compile_findings(repo, parent, full, res.unresolved)
        res.proposed = [asdict(f) for f in res.findings]
        if res.findings:
            docs, changed = doc_diff(repo, parent, full), changed_lines(diff)
            _verify(res, tmp, message, numbered, docs, changed, model, effort, timeout, touched_files(repo, parent, full))
            if second_look:
                _second_look(res, tmp, message, numbered, docs, changed, model, effort, timeout)
    except Exception as e:  # noqa: BLE001 - recorded, never mistaken for a clean result
        res.error = str(e)[:500]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    res.seconds = round(time.monotonic() - t0, 1)
    return res


PLAIN_PROMPT = """You are reviewing one commit to a SQL data warehouse (dbt, SQLMesh or plain SQL) before it merges. Find the
defects this commit introduces that would make the warehouse produce wrong data or fail to build.

The working directory is a copy of the repository exactly as it is AFTER this commit (no git history). You may use
Read, Grep and Glob to look at any file.

severity "bug" means you are confident the change produces wrong rows or values, breaks the build, or breaks a
documented contract or test; "risk" means plausible but not shown. Most commits are fine; return an empty list when
nothing is wrong. For each finding give the path and line (after the commit, as numbered in the diff), a category, a
short title, the explanation and the evidence you read.

Commit message: {message}

The diff (against the commit's parent; numbers are line numbers after the commit):
```diff
{diff}
```
"""


def review_plain(repo: Path, commit: str, *, model: str = "sonnet", effort: str | None = None, timeout: int = 1200) -> AgentResult:
    """A baseline: one general review call with the same snapshot, tools, diff and message, and nothing else."""
    t0 = time.monotonic()
    res = AgentResult()
    full = _git(repo, "rev-parse", commit).strip()
    try:
        parent = _git(repo, "rev-parse", f"{full}^").strip()
    except subprocess.CalledProcessError:
        parent = EMPTY_TREE
    message = _git(repo, "log", "-1", "--format=%s%n%n%b", full).strip()[:2000]
    numbered = _number_diff(change_diff(repo, parent, full))
    if len(numbered) > DIFF_CHARS:
        numbered = numbered[:DIFF_CHARS] + "\n... (diff truncated; read the changed files in full)"
    tmp = Path(tempfile.mkdtemp(prefix="wa-"))
    try:
        snapshot(repo, full, tmp)
        out, turns = _claude(PLAIN_PROMPT.format(message=message, diff=numbered), tmp, REVIEW_SCHEMA, model, effort, timeout)
        res.turns = turns
        res.findings = [f for f in _parse({"findings": out.get("findings", [])[:24]}, "plain")]
    except Exception as e:  # noqa: BLE001 - recorded, never mistaken for a clean result
        res.error = str(e)[:500]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    res.seconds = round(time.monotonic() - t0, 1)
    return res


def free_checks(repo: Path, commit: str) -> AgentResult:
    """A baseline with no model: the deterministic warehouse rules, a SQL parse of every changed model, a YAML parse of every changed YAML file."""
    import yaml

    from .diffparse import parse
    from .warehouse_rules import GitView, check_change_full, parse_sql

    t0 = time.monotonic()
    res = AgentResult()
    full = _git(repo, "rev-parse", commit).strip()
    try:
        parent = _git(repo, "rev-parse", f"{full}^").strip()
    except subprocess.CalledProcessError:
        parent = EMPTY_TREE
    files = parse(_git(repo, "diff", "-M", "--unified=3", "--no-color", parent, full, "--", "*.sql", "*.yml", "*.yaml"))
    try:
        for f in check_change_full(files, GitView(repo, parent, full)).findings:
            if f.severity == "warn":
                res.findings.append(AgentFinding(f.path, f.line, f.end_line, "bug", "other", f.title, f.body, source=f"rule:{f.rule}"))
    except Exception as e:  # noqa: BLE001 - a crash in the rules leaves the parse checks
        res.error = f"rules: {e}"[:300]
    flagged = {f.path for f in res.findings}
    for fd in files:
        if fd.status == "deleted" or fd.binary or fd.path in flagged:
            continue
        try:
            new = _git(repo, "show", f"{full}:{fd.path}")
        except subprocess.CalledProcessError:
            continue
        old = None
        if fd.status != "added":
            try:
                old = _git(repo, "show", f"{parent}:{fd.old_path or fd.path}")
            except subprocess.CalledProcessError:
                old = None
        line = fd.first_changed_line()
        if fd.path.endswith(".sql") and "macros/" not in fd.path:
            if parse_sql(new)[0] is None and (old is None or parse_sql(old)[0] is not None):
                res.findings.append(AgentFinding(fd.path, line, None, "bug", "broken_reference_or_syntax", f"{fd.path} does not parse", "The SQL does not parse in any supported dialect after this change.", source="parse"))
        elif fd.path.endswith((".yml", ".yaml")):
            try:
                yaml.safe_load(new)
            except yaml.YAMLError as e:
                mark = getattr(e, "problem_mark", None)
                res.findings.append(AgentFinding(fd.path, (mark.line + 1) if mark else line, None, "bug", "schema_contract", f"{fd.path} is not valid YAML", str(e)[:300], source="yaml"))
    res.seconds = round(time.monotonic() - t0, 1)
    return res


def review(repo: Path, commit: str, *, model: str = "sonnet", verify_model: str | None = "opus", effort: str | None = None, verify: bool = True, use_rules: bool = True, timeout: int = 1200, passes: tuple[str, ...] = ("grain", "values", "contract", "edges"), deep_sites: bool = False, second_look: bool = True, generalist: str | None = "opus") -> AgentResult:
    """Review ``commit`` against its first parent: specialist passes and one generalist propose findings, a skeptical pass decides them.

    The generalist is one whole-diff review on a stronger model (``generalist``; None turns it off). The specialists
    and the generalist miss different bugs, so both feed the same skeptical pass."""
    from concurrent.futures import ThreadPoolExecutor

    t0 = time.monotonic()
    res = AgentResult()
    full = _git(repo, "rev-parse", commit).strip()
    try:
        parent = _git(repo, "rev-parse", f"{full}^").strip()
    except subprocess.CalledProcessError:
        parent = EMPTY_TREE  # a root commit: review everything it adds
    message = _git(repo, "log", "-1", "--format=%s%n%n%b", full).strip()[:2000]
    diff = change_diff(repo, parent, full)
    rule_findings: list[Any] = []
    if use_rules:
        try:
            from .diffparse import parse
            from .warehouse_rules import GitView, check_change_full

            rule_findings = check_change_full(parse(_git(repo, "diff", "-M", "--unified=3", "--no-color", parent, full, "--", "*.sql", "*.yml", "*.yaml")), GitView(repo, parent, full)).findings
        except Exception:  # noqa: BLE001 - the rules are only leads; a crash there must not stop the review
            rule_findings = []
    res.rule_findings = len(rule_findings)
    numbered = _number_diff(diff)
    if len(numbered) > DIFF_CHARS:
        numbered = numbered[:DIFF_CHARS] + "\n... (diff truncated; read the changed files in full)"
    tmp = Path(tempfile.mkdtemp(prefix="wa-"))
    try:
        snapshot(repo, full, tmp)

        chunks = _chunks(numbered)
        all_files = [p for p, _ in _split_files(numbered)]

        def one(job: tuple[str, str]) -> tuple[str, dict[str, Any], int]:
            name, part = job
            if name == "general":
                out, turns = _claude(PLAIN_PROMPT.format(message=message, diff=numbered), tmp, REVIEW_SCHEMA, generalist or model, effort, timeout)
                return name, out, turns
            if len(chunks) > 1:
                part = (
                    f"This commit changes {len(all_files)} files: {', '.join(all_files)}.\n"
                    "Below is the part you are responsible for; review these files closely (you may read any other file).\n" + part
                )
            prompt = BASE_PROMPT.format(focus=FOCUS[name], hints=_hints(rule_findings), message=message, diff=part)
            out, turns = _claude(prompt, tmp, GRAIN_SCHEMA if name == "grain" else REVIEW_SCHEMA, model, effort, timeout)
            return name, out, turns

        jobs = [(name, part) for part in chunks for name in passes] + ([("general", "")] if generalist else [])
        with ThreadPoolExecutor(min(len(jobs), 8)) as pool:
            outs = list(pool.map(one, jobs))
        audit: list[dict[str, Any]] = []
        for name, out, _ in outs:
            if name == "grain":
                audit += out.get("audit", [])
        res.audit = audit
        found: list[tuple[str, AgentFinding]] = []
        for name, out, turns in outs:
            res.turns += turns
            found += [(name, f) for f in _parse(out, name)]
        sites = _sites(res.audit) if deep_sites else []
        if sites:
            with ThreadPoolExecutor(min(4, len(sites))) as pool:
                checks = list(pool.map(lambda s: _check_site(s, tmp, message, numbered, model, effort, timeout), sites))
            for s, (out, turns) in zip(sites, checks, strict=True):
                res.turns += turns
                res.site_checks.append({**s, "verdict": out.get("verdict"), "grain": out.get("grain", ""), "evidence": out.get("evidence", "")})
                if out.get("verdict") in ("fans_out", "drops_rows", "keeps_duplicates") and out.get("finding"):
                    found += [("site", f) for f in _parse({"findings": [out["finding"]]}, "site")]
        compiled = compile_findings(repo, parent, full, res.unresolved)
        res.candidates = len(found) + len(compiled)
        res.findings = (compiled + _merge(found))[:24]
        res.proposed = [asdict(f) for f in res.findings]
        if verify and res.findings:
            docs, changed = doc_diff(repo, parent, full), changed_lines(diff)
            _verify(res, tmp, message, numbered, docs, changed, verify_model or model, effort, timeout, touched_files(repo, parent, full))
            if second_look:
                _second_look(res, tmp, message, numbered, docs, changed, verify_model or model, effort, timeout)
    except Exception as e:  # noqa: BLE001 - a failed review is recorded, never mistaken for a clean one
        res.error = str(e)[:500]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    res.seconds = round(time.monotonic() - t0, 1)
    return res
