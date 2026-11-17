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

AGENT_VERSION = "wa-6"
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
                },
                "required": ["index", "verdict", "reason"],
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
  those rows.
- "risk": a real concern you could neither confirm nor rule out from the repository.
- "reject": the repository shows it does not hold (the joined side is unique on the key, the column is selected
  after all, the reader does not exist, the NULL is handled), it is style or speculation, or it predates the commit.
When two candidates describe the same defect, give the better one its verdict and reject the other as a duplicate.

Commit message: {message}

The diff:
```diff
{diff}
```

Candidates (index: finding):
{findings}
"""

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


def change_diff(repo: Path, parent: str, commit: str) -> str:
    return _git(repo, "diff", "-M", "--unified=6", "--no-color", parent, commit, "--", "*.sql", "*.yml", "*.yaml", "*.py")


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


def review(repo: Path, commit: str, *, model: str = "sonnet", verify_model: str | None = "opus", effort: str | None = None, verify: bool = True, use_rules: bool = True, timeout: int = 1200, passes: tuple[str, ...] = ("grain", "values", "contract"), deep_sites: bool = False) -> AgentResult:
    """Review ``commit`` against its first parent: specialist passes propose findings, a skeptical pass decides them."""
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
            if len(chunks) > 1:
                part = (
                    f"This commit changes {len(all_files)} files: {', '.join(all_files)}.\n"
                    "Below is the part you are responsible for; review these files closely (you may read any other file).\n" + part
                )
            prompt = BASE_PROMPT.format(focus=FOCUS[name], hints=_hints(rule_findings), message=message, diff=part)
            out, turns = _claude(prompt, tmp, GRAIN_SCHEMA if name == "grain" else REVIEW_SCHEMA, model, effort, timeout)
            return name, out, turns

        jobs = [(name, part) for part in chunks for name in passes]
        with ThreadPoolExecutor(min(len(jobs), 6)) as pool:
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
        res.candidates = len(found)
        res.findings = _merge(found)[:24]
        if verify and res.findings:
            listing = "\n".join(f"{i}: [{f.category}] {f.path}:{f.line} {f.title}\n   {f.explanation}\n   evidence: {f.evidence}" for i, f in enumerate(res.findings))
            vout, vturns = _claude(VERIFY_PROMPT.format(message=message, diff=numbered, findings=listing), tmp, VERIFY_SCHEMA, verify_model or model, effort, timeout)
            res.turns += vturns
            verdicts = {v.get("index"): v for v in vout.get("verdicts", [])}
            kept = []
            for i, f in enumerate(res.findings):
                v = verdicts.get(i) or {"verdict": "risk", "reason": "no verdict"}
                f.verified = v["verdict"] == "bug"
                f.verify_reason = v.get("reason", "")
                if v["verdict"] == "reject":
                    res.rejected.append(f)
                    continue
                f.severity = v["verdict"]
                kept.append(f)
            res.findings = kept
    except Exception as e:  # noqa: BLE001 - a failed review is recorded, never mistaken for a clean one
        res.error = str(e)[:500]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    res.seconds = round(time.monotonic() - t0, 1)
    return res
