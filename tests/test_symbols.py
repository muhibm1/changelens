"""Symbol references, credential scanning, and the hardening around what the model is sent."""

from __future__ import annotations

import io
import json
import subprocess
import urllib.error
from pathlib import Path

import pytest

from orion import providers, syntax
from orion.constraints import load_constraints
from orion.credscan import find_secrets, redact
from orion.diffparse import batches, parse, render_with_omitted
from orion.providers import OpenAICompatibleProvider, ProviderError, StubProvider
from orion.review import FINDINGS_SCHEMA, review
from orion.risk import RiskModel
from orion.symbols import MAX_REFS, changed_definitions, references, search, symbol_changes
from pipelinebench.review_eval import Change

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def constraints():
    cs, _ = load_constraints(ROOT / "fixture" / "orion" / "constraints")
    return cs


@pytest.fixture(scope="module")
def empty_repo(tmp_path_factory):
    """Reference searches in these tests must not see this repository's own test files, which mention the same names."""
    d = tmp_path_factory.mktemp("search")
    subprocess.run(["git", "init", "-q", str(d)], check=True)
    return d


def _run(store, constraints, diff, provider=None, *, search_root, **kw):
    return review(store, ROOT, provider or StubProvider(), constraints=constraints, risk_model=RiskModel.load(None), diff_text=diff, search_root=search_root, **kw)


HTTP_OLD = "import urllib.request\n\n\ndef fetch(url: str, timeout: float = 10.0) -> bytes:\n    with urllib.request.urlopen(url, timeout=timeout) as r:\n        return r.read()\n\n\ndef ping(url: str) -> bool:\n    return fetch(url) == b'ok'\n"
HTTP_RENAMED = HTTP_OLD.replace("def fetch(url: str, timeout: float = 10.0)", "def fetch(url: str, *, timeout_s: float = 10.0)").replace("timeout=timeout)", "timeout=timeout_s)")
HTTP_REMOVED = "import urllib.request\n\n\ndef ping(url: str) -> bool:\n    return fetch(url) == b'ok'\n"
HTTP_MOVED = "import urllib.request\n\n\ndef ping(url: str) -> bool:\n    return fetch(url) == b'ok'\n\n\ndef fetch(url: str, timeout: float = 10.0) -> bytes:\n    with urllib.request.urlopen(url, timeout=timeout) as r:\n        return r.read()\n"


def test_changed_definitions_tell_removed_from_resigned_from_moved():
    renamed = parse(Change("app/http.py", HTTP_OLD, HTTP_RENAMED).diff())[0]
    [sc] = changed_definitions(renamed, HTTP_RENAMED)
    assert (sc.name, sc.kind, sc.line) == ("fetch", "signature_changed", 4)
    assert sc.old.startswith("def fetch(url: str, timeout") and sc.new.startswith("def fetch(url: str, *, timeout_s")

    removed = parse(Change("app/http.py", HTTP_OLD, HTTP_REMOVED).diff())[0]
    [sc] = changed_definitions(removed, HTTP_REMOVED)
    assert (sc.name, sc.kind) == ("fetch", "removed") and sc.new is None

    moved = parse(Change("app/http.py", HTTP_OLD, HTTP_MOVED).diff())[0]
    assert changed_definitions(moved, HTTP_MOVED) == [], "a definition that moves within the file is neither removed nor changed"
    assert changed_definitions(parse(Change("notes.md", "# fetch\n", "# fetch2\n").diff())[0], None) == []


def test_typescript_and_go_definitions():
    ts_old = 'export type UserStatus = "active" | "inactive";\nexport function shouldBill(s: UserStatus) { return s === "active"; }\n'
    ts_new = 'export type UserStatus = "active" | "deleted";\nexport function shouldBill(s: UserStatus) { return s === "active"; }\n'
    [sc] = changed_definitions(parse(Change("web/types.ts", ts_old, ts_new).diff())[0], ts_new)
    assert (sc.name, sc.kind) == ("UserStatus", "signature_changed")
    go_old = "package x\n\nfunc (s *Svc) Load(id int) error { return nil }\n"
    go_new = "package x\n\nfunc (s *Svc) Load(id int, strict bool) error { return nil }\n"
    [sc] = changed_definitions(parse(Change("svc/load.go", go_old, go_new).diff())[0], go_new)
    assert (sc.name, sc.kind) == ("Load", "signature_changed")


def test_function_local_variables_are_not_definitions():
    old = 'export const LIMIT = 10;\nexport function load(url: string) {\n  const res = fetch(url);\n  let headers = {};\n  return res;\n}\n'
    new = 'export const LIMIT = 20;\nexport function load(url: string) {\n  const response = fetch(url);\n  return response;\n}\n'
    changes = changed_definitions(parse(Change("web/load.ts", old, new).diff())[0], new)
    assert [(sc.name, sc.kind) for sc in changes] == [("LIMIT", "signature_changed")]
    old = "export namespace Api {\n  export const VERSION = 1;\n}\n"
    new = "export namespace Api {\n  export const VERSION = 2;\n}\n"
    [sc] = changed_definitions(parse(Change("web/api.ts", old, new).diff())[0], new)
    assert (sc.name, sc.kind) == ("VERSION", "signature_changed")


def test_references_search_the_repository_and_an_overlay(empty_repo, tmp_path):
    # A file in the working tree (untracked is enough) is found through git grep; a non-code file is not.
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "lib").mkdir()
    (repo / "lib" / "a.py").write_text("def render_file(f):\n    return f\n")
    (repo / "lib" / "b.py").write_text("from lib.a import render_file\nrender_file(1)\n")
    (repo / "notes.md").write_text("render_file is documented here\n")
    expected = [("lib/b.py", 1, "from lib.a import render_file"), ("lib/b.py", 2, "render_file(1)")]
    if not syntax.available():  # the text search cannot tell the definition from a reference
        expected.insert(0, ("lib/a.py", 1, "def render_file(f):"))
    assert references(repo, "render_file", {}) == expected
    # Overlay files are searched too, and an empty repository contributes nothing.
    refs = references(empty_repo, "zz_only_in_overlay", {"app/a.py": "x = zz_only_in_overlay()\nzz_only_in_overlay_more = 1\n"})
    assert refs == [("app/a.py", 1, "x = zz_only_in_overlay()")]
    assert references(empty_repo, "zz_only_in_overlay", {"docs/a.md": "zz_only_in_overlay"}) == []


def test_search_reports_a_failed_repository_search_and_truncation(tmp_path):
    # Not a git repository: the search fails, the overlay is still searched, and the failure is reported rather than read as "no callers".
    plain = tmp_path / "plain"
    plain.mkdir()
    res = search(plain, "fetch", {"app/x.py": "fetch(1)\n"})
    assert res.failed and [(r.path, r.line, r.text) for r in res.refs] == [("app/x.py", 1, "fetch(1)")] and not res.truncated
    diff = Change("app/http.py", HTTP_OLD, HTTP_REMOVED).diff()
    [sc] = symbol_changes(parse(diff), plain, {"app/http.py": HTTP_REMOVED, "app/jobs.py": "fetch(2)\n"})
    assert sc.search_failed and sc.to_dict()["search_failed"]
    # More references than MAX_REFS: the result says it stopped.
    many = "".join(f"fetch({i})\n" for i in range(MAX_REFS + 5))
    res = search(plain, "fetch", {"app/many.py": many})
    assert res.truncated and len(res.refs) == MAX_REFS


def test_symbol_facts_say_when_the_search_was_incomplete(synced_store, constraints, tmp_path):
    from orion.review import _symbol_facts, gather

    plain = tmp_path / "plain"
    plain.mkdir()
    old, new = "def helper(a):\n    return a\n", "def helper(a, b=1):\n    return a + b\n"
    diff = Change("app/util.py", old, new).diff()
    ctx = gather(synced_store, ROOT, diff, constraints=constraints, risk_model=RiskModel.load(None), new_files={"app/util.py": new}, search_root=plain)
    [line] = _symbol_facts(ctx)
    assert "could not be searched" in line and "no other reference" in line
    ctx.symbols[0].refs.append({"path": "app/jobs.py", "line": 3, "text": "fetch(u)", "kind": "use", "scope": "unknown", "in_diff": False})
    ctx.symbols[0].truncated = True
    [line] = _symbol_facts(ctx)
    assert "ref:app/jobs.py:3" in line and "could not be" in line and "stopped at 1 references" in line


def test_symbol_changes_mark_which_references_the_diff_touches(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "http.py").write_text(HTTP_OLD)
    (tmp_path / "app" / "health.py").write_text("from app.http import fetch\n\n\ndef ping(url):\n    return fetch(url, timeout=2.0)\n")
    (tmp_path / "app" / "jobs.py").write_text("from app.http import fetch\n\n\ndef warm(url):\n    fetch(url, timeout=9.0)\n")
    health_new = "from app.http import fetch\n\n\ndef ping(url):\n    return fetch(url, timeout_s=2.0)\n"
    diff = Change("app/http.py", HTTP_OLD, HTTP_RENAMED).diff() + Change("app/health.py", (tmp_path / "app" / "health.py").read_text(), health_new).diff()
    files = parse(diff)
    [sc] = symbol_changes(files, tmp_path, {"app/http.py": HTTP_RENAMED, "app/health.py": health_new})
    by = {(r["path"], r["line"]): r for r in sc.refs}
    assert by[("app/health.py", 5)]["in_diff"] and by[("app/health.py", 5)]["kind"] == ("call" if syntax.available() else "use")
    assert not by[("app/jobs.py", 5)]["in_diff"]
    assert by[("app/jobs.py", 1)]["kind"] == "import"
    assert ("app/http.py", 10) in by and not by[("app/http.py", 10)]["in_diff"], "a use further down the same file counts too"
    # A credential on a referencing line never leaves the search: the review JSON quotes reference text.
    (tmp_path / "app" / "cfg.py").write_text('fetch("https://x", key="sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGHIJ")\n')  # pragma: allowlist secret
    [sc] = symbol_changes(files, tmp_path, {"app/http.py": HTTP_RENAMED, "app/health.py": health_new})
    [cfg] = [r for r in sc.refs if r["path"] == "app/cfg.py" and r["line"] == 1]
    assert "abcdefghij" not in cfg["text"] and cfg["text"] == 'fetch("https://x", key="[redacted]")'


def test_removed_symbol_still_referenced_is_a_finding_and_facts_say_who_calls_what(synced_store, constraints, empty_repo):
    diff = Change("app/http.py", HTTP_OLD, HTTP_REMOVED).diff()
    stub = StubProvider()
    r = _run(synced_store, constraints, diff, stub, search_root=empty_repo, new_files={"app/http.py": HTTP_REMOVED}, repo_files={"app/jobs.py": "from app.http import fetch\n\nfetch('u')\n"})
    [f] = [f for f in r["findings"] if f["source"] == "deterministic"]
    assert f["title"] == "`fetch` removed but still referenced at app/http.py:5, app/jobs.py:1, app/jobs.py:3" and f["severity"] == "warn"
    assert f["path"] == "app/http.py" and parse(diff)[0].contains(f["line"]) and "ref:app/jobs.py:3" in f["evidence"]
    user = stub.calls[0][1]
    assert "symbol `fetch` defined in app/http.py:" in user and "(removed; was `def fetch(" in user and "ref:app/jobs.py:3" in user
    assert r["symbols"][0]["name"] == "fetch"
    # The model can cite a listed reference as evidence; one it invents is dropped.
    cite = StubProvider({"findings": [
        {"path": "app/http.py", "line": 4, "severity": "warn", "category": "correctness", "title": "jobs.py breaks", "body": "b", "failure": "a caller passing the old value gets the wrong result", "evidence": ["ref:app/jobs.py:3"]},
        {"path": "app/http.py", "line": 4, "severity": "warn", "category": "correctness", "title": "made up", "body": "b", "failure": "a caller passing the old value gets the wrong result", "evidence": ["ref:app/nowhere.py:1"]},
    ]})
    r = _run(synced_store, constraints, diff, cite, search_root=empty_repo, new_files={"app/http.py": HTTP_REMOVED}, repo_files={"app/jobs.py": "from app.http import fetch\n\nfetch('u')\n"})
    assert [f["title"] for f in r["findings"] if f["source"] == "model"] == ["jobs.py breaks"]
    assert [f["title"] for f in r["uncertain"]] == ["made up"]


def test_resigned_symbol_with_every_use_in_the_diff_is_a_fact_not_a_finding(synced_store, constraints, empty_repo):
    health_old = "from app.http import fetch\n\n\ndef ping(url):\n    return fetch(url, timeout=2.0)\n"
    health_new = health_old.replace("timeout=2.0", "timeout_s=2.0")
    diff = Change("app/http.py", HTTP_OLD, HTTP_RENAMED).diff() + Change("app/health.py", health_old, health_new).diff()
    stub = StubProvider()
    r = _run(synced_store, constraints, diff, stub, search_root=empty_repo, new_files={"app/http.py": HTTP_RENAMED, "app/health.py": health_new})
    assert not [f for f in r["findings"] if f["source"] == "deterministic"]
    user = stub.calls[0][1]
    assert "signature changed to `def fetch(url: str, *, timeout_s" in user
    assert "no use outside this diff" not in user, "fetch is also used inside http.py by ping, outside the hunks"
    assert "on lines this diff changes (app/health.py:5)" in user


def test_credentials_are_found_redacted_and_blocked(synced_store, constraints, empty_repo):
    aws = "AKIA" + "IOSFODNN7ABCDEFG"  # pragma: allowlist secret
    assert find_secrets(f"key = '{aws}'")[0][0] == "AWS access key"
    assert find_secrets("key = 'AKIAIOSFODNN7EXAMPLE'") == [], "the documented example key is a placeholder"
    assert find_secrets(f"key = '{aws}'  # pragma: allowlist secret") == [] and find_secrets(f"key = '{aws}'  // orion: allow-secret") == []
    assert redact(f"key = '{aws}'  # pragma: allowlist secret\n") == "key = '[redacted]'  # pragma: allowlist secret\n", "the pragma silences the finding, never the redaction"
    pw = "hunter2" * 2  # pragma: allowlist secret
    assert find_secrets(f'password = "{pw}"') == [("credential assigned to a literal", pw)]
    assert find_secrets('password = "${DB_PASSWORD}"') == [] and find_secrets("password = os.environ['X']") == []
    assert find_secrets('token = "<your-token-here>"') == [] and find_secrets('secret = "changeme-please"') == []
    assert find_secrets("url = 'postgres://app:prod@db/prod'") == [("database URL with password", "prod")]  # pragma: allowlist secret
    # Redaction replaces the matched span only: the same short word elsewhere on the line is untouched.
    assert redact("url = 'postgres://app:prod@db/prod'  # prod\n") == "url = 'postgres://app:[redacted]@db/prod'  # prod\n"  # pragma: allowlist secret
    assert redact(f"a = '{aws}'\nb = 1\n") == "a = '[redacted]'\nb = 1\n"
    # A project key with dashes and underscores is redacted whole, not just its prefix.
    proj = "sk-proj-" + "Ab_cd-" * 8  # pragma: allowlist secret
    assert redact(f"k = '{proj}'\n") == "k = '[redacted]'\n"
    # The body of a private key is redacted line by line, in a file and in a numbered diff.
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEAu7Zx3vQ2k9\nAbCdEfGhIjKlMnOpQrStUvWxYz01\n-----END RSA PRIVATE KEY-----\nafter = 1\n"  # pragma: allowlist secret
    out = redact(key)
    assert "MIIEowIBAAKCAQEAu7Zx3vQ2k9" not in out and out.count("[redacted]") == 2 and out.endswith("-----END RSA PRIVATE KEY-----\nafter = 1\n")
    numbered = "    3 + -----BEGIN RSA PRIVATE KEY-----\n    4 + MIIEowIBAAKCAQEAu7Zx3vQ2k9\n    5 + -----END RSA PRIVATE KEY-----\n"  # pragma: allowlist secret
    assert redact(numbered) == "    3 + -----BEGIN RSA PRIVATE KEY-----\n    4 + [redacted]\n    5 + -----END RSA PRIVATE KEY-----\n"  # pragma: allowlist secret
    # Token-shaped values skip only the documented placeholder convention; a real token containing a word is still caught.
    assert find_secrets("t = 'ghp_" + "sampleXYZ0123456789abcdefghijklmnopq'")[0][0] == "GitHub token"  # pragma: allowlist secret

    text = "STRIPE = 'sk_live_51HqKZ2" + "abcdefghijklmnopqrstuv'\n"  # pragma: allowlist secret
    diff = Change("app/settings.py", "", text).diff()
    stub = StubProvider()
    r = _run(synced_store, constraints, diff, stub, search_root=empty_repo, new_files={"app/settings.py": text})
    [f] = [f for f in r["findings"] if f["source"] == "deterministic"]
    assert f["severity"] == "block" and f["title"] == "Possible Stripe key committed" and f["evidence"] == ["hunk:app/settings.py:1"]
    assert "sk_live_51HqKZ2" not in stub.calls[0][1] and "[redacted]" in stub.calls[0][1]


def test_long_and_over_budget_files_are_named_not_dropped(synced_store, constraints, empty_repo):
    long_text = "\n".join(f"x{i} = {i}" for i in range(500)) + "\n"
    small = "y = 1\n"
    diff = Change("app/big.py", "", long_text).diff() + Change("app/small.py", "", small).diff()
    stub = StubProvider()
    _run(synced_store, constraints, diff, stub, search_root=empty_repo, new_files={"app/big.py": long_text, "app/small.py": small})
    user = stub.calls[0][1]
    assert "=== app/big.py: whole file not shown (500 lines; only its hunks below)" in user
    assert "=== app/small.py (whole file after the change)" in user, "a later small file still goes after a long one"

    files = parse(diff)
    groups, omitted = batches(files, max_chars=200)
    assert omitted == [] and [[f.path for f in g] for g in groups] == [["app/big.py"], ["app/small.py"]], "a file over the budget gets its own call"
    groups, omitted = batches(files, max_chars=200, hard_cap=1000)
    assert omitted == ["app/big.py"] and [[f.path for f in g] for g in groups] == [["app/small.py"]]
    text, omitted = render_with_omitted(files, max_chars=200)
    assert omitted == [] and "app/small.py (added)" in text and "app/big.py (added)" in text
    r = _run(synced_store, constraints, diff, search_root=empty_repo, new_files={"app/big.py": long_text, "app/small.py": small})
    assert r["omitted"] == []


def test_a_large_diff_is_reviewed_in_several_calls_and_findings_pool(synced_store, constraints, empty_repo, monkeypatch):
    import orion.review as rv

    monkeypatch.setattr(rv, "batches", lambda files, *a, **k: rv_batches(files))
    texts = {f"app/m{i}.py": f"def f{i}():\n    return {i}\n" for i in range(3)}
    diff = "".join(Change(p, "", t).diff() for p, t in texts.items())

    def rv_batches(files):
        return [[f] for f in files], []

    calls = []

    def respond(system, user):
        calls.append(user)
        if "app/m1.py (added)" in user:
            raise ProviderError("HTTP 500")
        p = next(p for p in texts if f"{p} (added)" in user)
        return {"findings": [{"path": p, "line": 1, "severity": "warn", "category": "maintainability", "title": f"finding in {p}", "body": "b", "evidence": [f"hunk:{p}:1"]}]}

    stub = StubProvider(respond)
    stub.last_usage = {"input_tokens": 10, "output_tokens": 1}
    r = _run(synced_store, constraints, diff, stub, search_root=empty_repo, new_files=texts)
    assert len(calls) == 3 and all("(part " in c and "of 3" in c for c in calls)
    assert "app/m0.py (whole file after the change)" in calls[0] and "app/m2.py (whole file" not in calls[0], "each call carries context for its own files only"
    assert [f["title"] for f in r["findings"]] == ["finding in app/m0.py", "finding in app/m2.py"]
    assert r["provider_error"] == "HTTP 500" and r["usage"] == {"input_tokens": 20, "output_tokens": 2}


def test_diff_parser_handles_crlf_and_quoted_paths():
    diff = 'diff --git "a/docs/r\\303\\251sum\\303\\251 notes.py" "b/docs/r\\303\\251sum\\303\\251 notes.py"\r\n--- "a/docs/r\\303\\251sum\\303\\251 notes.py"\r\n+++ "b/docs/r\\303\\251sum\\303\\251 notes.py"\r\n@@ -1 +1 @@\r\n-a = 1\r\n+a = 2\r\n'
    [f] = parse(diff)
    assert f.path == "docs/résumé notes.py" and f.hunks[0].lines == [("-", None, "a = 1"), ("+", 1, "a = 2")]
    [g] = parse("diff --git a/x.py b/x.py\n--- a/x.py\t2026-01-01\n+++ b/x.py\t2026-01-02\n@@ -1 +1 @@\n-a\n+b\n")
    assert g.path == "x.py"


def test_retry_after_header_sets_the_wait(monkeypatch):
    calls, waits = [], []

    def fake(req, timeout):
        calls.append(1)
        if len(calls) == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "slow down", {"retry-after": "7"}, io.BytesIO(b"rate limited"))
        return _Resp(json.dumps({"choices": [{"message": {"content": '{"findings": []}'}}]}).encode())

    monkeypatch.setattr(providers.urllib.request, "urlopen", fake)
    monkeypatch.setattr(providers.time, "sleep", waits.append)
    assert OpenAICompatibleProvider(api_key="x", base_url="http://h/v1").complete_json("s", "u", FINDINGS_SCHEMA) == {"findings": []}
    assert waits == [7.0]
    assert providers._retry_after("999") == 60.0 and providers._retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert providers._retry_after("0") is None and providers._retry_after("-3") is None, "no header value turns off the backoff"


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


needs_parser = pytest.mark.skipif(not syntax.available(), reason="tree-sitter-language-pack (the syntax extra) is not installed")


@needs_parser
def test_syntax_classifies_each_occurrence_of_a_name():
    py = 'from app.http import fetch\n\n\ndef run(client):\n    # fetch the page\n    print("fetch")\n    x = fetch(1)\n    y = client.fetch(2)\n    z = client.fetch\n    return x, y, z, fetch\n'
    fs = syntax.occurrences("app/run.py", py, "fetch")
    assert fs is not None and fs.grammar == "python" and fs.imports_name and not fs.defines_name
    assert [(o.line, o.kind) for o in fs.occurrences] == [(1, "import"), (7, "call"), (8, "method_call"), (9, "attribute"), (10, "use")], "the comment and the string are not occurrences"
    own = syntax.occurrences("lib/other.py", "def fetch(a):\n    return a\n\nfetch(3)\n", "fetch")
    assert own.defines_name and [(o.line, o.kind) for o in own.occurrences] == [(1, "definition"), (4, "call")]
    ts = syntax.occurrences("web/a.ts", 'import { fetch } from "./http";\nexport function go() { return fetch(1) + this.fetch(2); }\n', "fetch")
    assert ts.imports_name and [(o.line, o.kind) for o in ts.occurrences] == [(1, "import"), (2, "call"), (2, "method_call")]
    go = syntax.occurrences("svc/a.go", 'package svc\n\nfunc fetch(id int) int { return id }\n\nfunc Load() int { return fetch(1) }\n', "fetch")
    assert go.defines_name and [(o.line, o.kind) for o in go.occurrences] == [(3, "definition"), (5, "call")]
    assert syntax.occurrences("notes.md", "fetch", "fetch") is None, "an unparsed language falls back to the text search"


@needs_parser
def test_parsed_references_tell_a_caller_from_a_coincidence(synced_store, constraints, tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for d in ("app", "lib", "svc"):
        (tmp_path / d).mkdir()
    (tmp_path / "app" / "http.py").write_text(HTTP_OLD)
    (tmp_path / "app" / "jobs.py").write_text("from app.http import fetch\n\n\ndef warm(url):\n    # fetch everything\n    return fetch(url, timeout=9.0)\n")
    (tmp_path / "lib" / "own.py").write_text("def fetch(x):\n    return x\n\n\nfetch(1)\n")
    (tmp_path / "lib" / "client.py").write_text('class Client:\n    def get(self):\n        return self.session.fetch("fetch")\n')
    (tmp_path / "svc" / "loose.py").write_text("print(fetch)\n")
    (tmp_path / "svc" / "shadow.py").write_text("from app.http import fetch\n\n\ndef fetch(x):\n    return x\n\n\nfetch(1)\n")
    (tmp_path / "svc" / "pathseg.py").write_text("from fetch import x\nimport app.fetch\nfrom app.http import fetch as f\n\nx(fetch)\n")  # fetch only as a module path or behind an alias
    (tmp_path / "svc" / "method.py").write_text("from app.http import fetch\n\n\nclass C:\n    def fetch(self):\n        return fetch(1)\n")
    diff = Change("app/http.py", HTTP_OLD, HTTP_REMOVED).diff()
    [sc] = symbol_changes(parse(diff), tmp_path, {"app/http.py": HTTP_REMOVED})
    assert sc.parsed and not sc.search_failed
    by = {(r["path"], r["line"]): (r["kind"], r["scope"]) for r in sc.refs}
    assert by[("app/jobs.py", 1)] == ("import", "imports_it") and by[("app/jobs.py", 6)] == ("call", "imports_it")
    assert ("app/jobs.py", 5) not in by, "a comment is not a reference"
    assert by[("lib/own.py", 5)] == ("call", "defines_own") and ("lib/own.py", 1) not in by, "a file with its own fetch is not a caller of ours"
    assert by[("lib/client.py", 3)] == ("method_call", "no_import")
    assert by[("svc/loose.py", 1)] == ("use", "no_import")
    assert by[("svc/shadow.py", 8)] == ("call", "defines_own"), "a local definition shadows the import"
    assert by[("svc/method.py", 6)] == ("call", "imports_it"), "a method of the same name does not shadow the import"
    assert by[("svc/pathseg.py", 5)] == ("use", "no_import"), "a module path segment or an aliased-away name is not an import of the symbol"
    # Only the linked references make the deterministic finding; the rest are facts for the model.
    assert [(r["path"], r["line"]) for r in sc.linked_outside] == [("app/http.py", 5), ("app/jobs.py", 1), ("app/jobs.py", 6), ("svc/method.py", 1), ("svc/method.py", 6)]
    stub = StubProvider()
    r = _run(synced_store, constraints, diff, stub, search_root=tmp_path, new_files={"app/http.py": HTTP_REMOVED})
    [f] = [f for f in r["findings"] if f["source"] == "deterministic"]
    assert f["title"] == "`fetch` removed but still referenced at app/http.py:5, app/jobs.py:1, app/jobs.py:6, svc/method.py:1, svc/method.py:6" and "parsing those files" in f["body"]
    user = stub.calls[0][1]
    assert "from parsed syntax" in user and "ref:app/jobs.py:6" in user
    assert "1 method or attribute access `.fetch`" in user and "ref:lib/client.py:3" in user
    assert "8 mentions in files that do not import the name or define their own" in user and "ref:lib/own.py:5" in user and "ref:svc/loose.py:1" in user
    assert [(x["path"], x["scope"]) for x in r["symbols"][0]["refs"]][:3] == [("app/http.py", "same_file"), ("app/jobs.py", "imports_it"), ("app/jobs.py", "imports_it")]


@needs_parser
def test_go_callers_in_the_same_package_are_linked_without_an_import(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "svc").mkdir()
    (tmp_path / "other").mkdir()
    old = "package svc\n\nfunc fetch(id int) int { return id }\n"
    (tmp_path / "svc" / "a.go").write_text(old)
    (tmp_path / "svc" / "b.go").write_text("package svc\n\nfunc Load() int { return fetch(1) }\n")
    (tmp_path / "other" / "c.go").write_text("package other\n\nfunc fetch() {}\n\nfunc X() { fetch() }\n")
    diff = Change("svc/a.go", old, "package svc\n").diff()
    [sc] = symbol_changes(parse(diff), tmp_path, {"svc/a.go": "package svc\n"})
    by = {(r["path"], r["line"]): r["scope"] for r in sc.refs}
    assert by[("svc/b.go", 3)] == "same_package" and by[("other/c.go", 5)] == "defines_own"
    assert [(r["path"], r["line"]) for r in sc.linked_outside] == [("svc/b.go", 3)]


def test_text_search_references_carry_an_unknown_scope(monkeypatch, tmp_path):
    # Without the parser (or for a language it lacks) every reference is a text match and the facts say so.
    monkeypatch.setattr(syntax, "occurrences", lambda *a, **k: None)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "http.py").write_text(HTTP_OLD)
    (tmp_path / "app" / "jobs.py").write_text("from app.http import fetch\n# fetch it\nfetch(1)\n")
    diff = Change("app/http.py", HTTP_OLD, HTTP_REMOVED).diff()
    [sc] = symbol_changes(parse(diff), tmp_path, {"app/http.py": HTTP_REMOVED})
    assert not sc.parsed and {r["scope"] for r in sc.refs} == {"unknown"}
    assert [(r["path"], r["line"], r["kind"]) for r in sc.refs if r["path"] == "app/jobs.py"] == [("app/jobs.py", 1, "import"), ("app/jobs.py", 2, "use"), ("app/jobs.py", 3, "use")]
    assert len(sc.linked_outside) == len(sc.outside), "with no parse every reference counts"


@needs_parser
def test_a_removed_method_called_on_self_in_its_own_file_is_linked(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "app").mkdir()
    gap = "".join(f"    x{i} = {i}\n" for i in range(12))  # keeps the call out of the hunk's context lines
    old = "class Client:\n    def fetch(self, url):\n        return url\n\n" + gap + "    def get(self, url, other):\n        other.fetch(url)\n        return self.fetch(url)\n"
    new = "class Client:\n" + gap + "    def get(self, url, other):\n        other.fetch(url)\n        return self.fetch(url)\n"
    (tmp_path / "app" / "client.py").write_text(old)
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "use.py").write_text("from app.client import Client\n\nClient().fetch('u')\n")
    diff = Change("app/client.py", old, new).diff()
    [sc] = symbol_changes(parse(diff), tmp_path, {"app/client.py": new})
    by = {(r["path"], r["line"]): (r["kind"], r["scope"]) for r in sc.refs}
    assert by[("app/client.py", 15)] == by[("app/client.py", 16)] == ("method_call", "same_file") and by[("other/use.py", 3)] == ("method_call", "no_import")
    assert sc.owner == "Client"
    assert [(r["path"], r["line"]) for r in sc.linked_outside] == [("app/client.py", 16), ("other/use.py", 3)], "self.fetch in the class's own file and Client().fetch where Client is imported are linked; other.fetch is a fact for the model"
    # Ruby: a bare call is a call; a call on self or on a constant in the same file is linked, one on another object is not.
    rb_old = "class Client\n  def fetch(u)\n    u\n  end\n" + "".join(f"  X{i} = {i}\n" for i in range(10)) + "  def get(u, other)\n    other.fetch(u)\n    Client.fetch(u)\n    fetch(u)\n    self.fetch(u)\n  end\nend\n"
    rb_new = "class Client\n" + "".join(f"  X{i} = {i}\n" for i in range(10)) + "  def get(u, other)\n    other.fetch(u)\n    Client.fetch(u)\n    fetch(u)\n    self.fetch(u)\n  end\nend\n"
    (tmp_path / "app" / "client.rb").write_text(rb_old)
    diff = Change("app/client.rb", rb_old, rb_new).diff()
    [sc] = symbol_changes(parse(diff), tmp_path, {"app/client.rb": rb_new})
    kinds = {r["line"]: (r["kind"], r["receiver"]) for r in sc.refs}
    assert kinds[13] == ("method_call", "other") and kinds[14] == ("method_call", "Client") and kinds[15] == ("call", None) and kinds[16] == ("method_call", "self")
    assert [r["line"] for r in sc.linked_outside] == [14, 15, 16]


@needs_parser
def test_qualified_calls_through_an_imported_module_are_linked(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for d in ("app", "svc", "cmd", "web", "rs"):
        (tmp_path / d).mkdir()
    # Python: ``import app.http`` then ``app.http.fetch(...)``; a ``.fetch`` on an unrelated object is not linked.
    (tmp_path / "app" / "http.py").write_text(HTTP_OLD)
    (tmp_path / "app" / "jobs.py").write_text("import app.http\n\n\ndef warm(url, client):\n    client.fetch(url)\n    return app.http.fetch(url, timeout=9.0)\n")
    (tmp_path / "app" / "other_jobs.py").write_text("import other.http\n\nother.http.fetch(1)\n")  # a same-named module in another package
    (tmp_path / "app" / "alias_jobs.py").write_text("import app.http as h\nfrom app import http as hh\n\nh.fetch(1)\nhh.fetch(2)\n")
    diff = Change("app/http.py", HTTP_OLD, HTTP_REMOVED).diff()
    [sc] = symbol_changes(parse(diff), tmp_path, {"app/http.py": HTTP_REMOVED})
    by = {(r["path"], r["line"]): (r["kind"], r["scope"]) for r in sc.refs}
    assert by[("app/jobs.py", 6)] == ("method_call", "imports_module") and by[("app/jobs.py", 5)] == ("method_call", "no_import")
    assert by[("app/other_jobs.py", 3)] == ("method_call", "no_import"), "other.http is not app/http.py"
    assert by[("app/alias_jobs.py", 4)] == by[("app/alias_jobs.py", 5)] == ("method_call", "imports_module"), "an alias of the module counts"
    assert [(r["path"], r["line"]) for r in sc.linked_outside if r["path"] == "app/jobs.py"] == [("app/jobs.py", 6)]
    # Go: an exported function called as ``svc.Fetch`` from a package that imports "x/svc".
    go_old = "package svc\n\nfunc Fetch(id int) int { return id }\n"
    (tmp_path / "svc" / "a.go").write_text(go_old)
    (tmp_path / "cmd" / "main.go").write_text('package main\n\nimport "example.com/x/svc"\n\nfunc main() { svc.Fetch(1) }\n')
    (tmp_path / "cmd" / "alias.go").write_text('package main\n\nimport s "example.com/x/svc"\n\nfunc run() { s.Fetch(1) }\n')
    diff = Change("svc/a.go", go_old, "package svc\n").diff()
    [sc] = symbol_changes(parse(diff), tmp_path, {"svc/a.go": "package svc\n"})
    assert [(r["path"], r["line"], r["scope"]) for r in sc.linked_outside] == [("cmd/alias.go", 5, "imports_module"), ("cmd/main.go", 5, "imports_module")]
    # TypeScript namespace import, and Rust ``http::fetch`` after ``use crate::http``.
    ts_old = "export function fetch(u: string) { return u; }\n"
    (tmp_path / "web" / "http.ts").write_text(ts_old)
    (tmp_path / "web" / "use.ts").write_text('import * as h from "./http";\nexport const go = () => h.fetch("u");\n')
    diff = Change("web/http.ts", ts_old, "export const x = 1;\n").diff()
    [sc] = symbol_changes(parse(diff), tmp_path, {"web/http.ts": "export const x = 1;\n"})
    assert [(r["path"], r["line"], r["kind"], r["scope"]) for r in sc.linked_outside] == [("web/use.ts", 2, "method_call", "imports_module")]
    rs_old = "pub fn fetch(u: u32) -> u32 { u }\n"
    (tmp_path / "rs" / "http.rs").write_text(rs_old)
    (tmp_path / "rs" / "main.rs").write_text("use crate::http as h;\n\nfn main() { h::fetch(1); }\n")
    diff = Change("rs/http.rs", rs_old, "").diff()
    [sc] = symbol_changes(parse(diff), tmp_path, {"rs/http.rs": ""})
    assert [(r["path"], r["line"], r["kind"], r["scope"]) for r in sc.linked_outside] == [("rs/main.rs", 3, "call", "imports_module")]


@needs_parser
def test_a_reference_on_the_line_where_the_removed_definition_stood_is_kept(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "app").mkdir()
    gap = "".join(f"x{i} = {i}\n" for i in range(8))
    old = "def fetch(u):\n    return u\n\n\n" + gap + "fetch(1)\n"
    new = gap + "fetch(1)\n"  # the call now sits where the definition stood
    (tmp_path / "app" / "a.py").write_text(old)
    diff = Change("app/a.py", old, new).diff()
    [sc] = symbol_changes(parse(diff), tmp_path, {"app/a.py": new})
    assert sc.kind == "removed" and [(r["path"], r["line"], r["kind"]) for r in sc.refs] == [("app/a.py", 9, "call")]


@needs_parser
def test_facts_say_which_references_are_text_matches_when_only_some_files_parse(synced_store, constraints, tmp_path):
    from orion.review import _symbol_facts, code_findings, gather

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "http.py").write_text(HTTP_OLD)
    (tmp_path / "app" / "jobs.py").write_text("from app.http import fetch\n\nfetch(1)\n")
    (tmp_path / "app" / "Jobs.java").write_text("// fetch is called here\nclass Jobs { void run() { fetch(); } }\n")  # no grammar for Java: text matches
    diff = Change("app/http.py", HTTP_OLD, HTTP_REMOVED).diff()
    ctx = gather(synced_store, tmp_path, diff, constraints=constraints, risk_model=RiskModel.load(None), new_files={"app/http.py": HTTP_REMOVED})
    [sc] = ctx.symbols
    assert not sc.parsed and {r["scope"] for r in sc.refs if r["path"].endswith(".java")} == {"unknown"}
    [line] = _symbol_facts(ctx)
    assert "from parsed syntax except 2 in files that could not be parsed (app/Jobs.java)" in line
    [f] = [f for f in code_findings(ctx) if f["source"] == "deterministic"]
    assert "found by parsing those files (except app/Jobs.java:1, app/Jobs.java:2, whole-word text matches" in f["body"]


@needs_parser
def test_an_import_of_the_name_from_another_module_is_not_linked(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for d in ("app", "other", "web", "web/lib", "rs"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "app" / "http.py").write_text(HTTP_OLD)
    (tmp_path / "app" / "rel.py").write_text("from .http import fetch\n\nfetch(1)\n")
    (tmp_path / "app" / "abs.py").write_text("from app.http import fetch\n\nfetch(2)\n")
    (tmp_path / "other" / "use.py").write_text("from other.http import fetch\n\nfetch(3)\n")
    (tmp_path / "other" / "rel.py").write_text("from .http import fetch\n\nfetch(4)\n")
    (tmp_path / "app" / "star.py").write_text("from app.http import *\n\nfetch(5)\n")
    (tmp_path / "other" / "star.py").write_text("from other.http import *\n\nfetch(6)\n")
    (tmp_path / "app" / "both.py").write_text("from other.http import fetch\nfrom app.http import *\n\nfetch(9)\n")  # the wildcard may rebind the name
    (tmp_path / "app" / "asmod.py").write_text("import other.client as fetch\n\nfetch.go(10)\n")  # binds a module, not our fetch
    (tmp_path / "app" / "asours.py").write_text("import app.http as fetch\n\nfetch.fetch(11)\n")  # binds our module; the call goes through it
    diff = Change("app/http.py", HTTP_OLD, HTTP_REMOVED).diff()
    [sc] = symbol_changes(parse(diff), tmp_path, {"app/http.py": HTTP_REMOVED})
    by = {r["path"]: r["scope"] for r in sc.refs if r["kind"] == "call" and r["path"] != sc.path}
    assert by == {"app/rel.py": "imports_it", "app/abs.py": "imports_it", "app/star.py": "imports_it", "app/both.py": "imports_it", "other/use.py": "imports_other", "other/rel.py": "imports_other", "other/star.py": "no_import"}
    assert {(r["path"], r["kind"]): r["scope"] for r in sc.refs if r["path"] in ("app/asmod.py", "app/asours.py")} == {("app/asmod.py", "import"): "no_import", ("app/asmod.py", "use"): "no_import", ("app/asours.py", "import"): "no_import", ("app/asours.py", "use"): "no_import", ("app/asours.py", "method_call"): "imports_module"}
    assert sorted(r["path"] for r in sc.linked_outside if r["path"] != "app/http.py") == ["app/abs.py", "app/abs.py", "app/asours.py", "app/both.py", "app/both.py", "app/rel.py", "app/rel.py", "app/star.py"]
    # A definition in a package file is imported by the package's name.
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text(HTTP_OLD)
    (tmp_path / "pkg" / "inner.py").write_text("from . import fetch\n\nfetch(7)\n")
    (tmp_path / "other" / "pkg_user.py").write_text("from pkg import fetch\n\nfetch(8)\n")
    diff = Change("pkg/__init__.py", HTTP_OLD, HTTP_REMOVED).diff()
    [sc] = symbol_changes(parse(diff), tmp_path, {"pkg/__init__.py": HTTP_REMOVED})
    assert {r["path"]: r["scope"] for r in sc.refs if r["kind"] == "call" and r["path"] in ("pkg/inner.py", "other/pkg_user.py")} == {"pkg/inner.py": "imports_it", "other/pkg_user.py": "imports_it"}
    # TypeScript relative sources, and Rust paths.
    ts_old = "export function fetch(u: string) { return u; }\n"
    (tmp_path / "web" / "http.ts").write_text(ts_old)
    (tmp_path / "web" / "a.ts").write_text('import { fetch } from "./http";\nfetch("a");\n')
    (tmp_path / "web" / "lib" / "b.ts").write_text('import { fetch } from "../http";\nfetch("b");\n')
    (tmp_path / "web" / "lib" / "c.ts").write_text('import { fetch } from "./http";\nfetch("c");\n')
    (tmp_path / "web" / "lib" / "index.ts").write_text(ts_old)
    (tmp_path / "web" / "d.ts").write_text('import { fetch } from "./lib";\nfetch("d");\n')
    diff = Change("web/http.ts", ts_old, "export const x = 1;\n").diff()
    [sc] = symbol_changes(parse(diff), tmp_path, {"web/http.ts": "export const x = 1;\n"})
    assert {r["path"]: r["scope"] for r in sc.refs if r["kind"] == "call" and r["path"].endswith(".ts")} == {"web/a.ts": "imports_it", "web/lib/b.ts": "imports_it", "web/lib/c.ts": "imports_other", "web/d.ts": "imports_other"}
    diff = Change("web/lib/index.ts", ts_old, "export const x = 1;\n").diff()
    [sc] = symbol_changes(parse(diff), tmp_path, {"web/lib/index.ts": "export const x = 1;\n"})
    assert {r["path"]: r["scope"] for r in sc.refs if r["kind"] == "call" and r["path"] in ("web/d.ts", "web/lib/c.ts")} == {"web/d.ts": "imports_it", "web/lib/c.ts": "imports_other"}, "lib/index.ts is imported as ./lib"
    rs_old = "pub fn fetch(u: u32) -> u32 { u }\n"
    (tmp_path / "rs" / "http.rs").write_text(rs_old)
    (tmp_path / "rs" / "a.rs").write_text("use crate::http::fetch;\n\nfn a() { fetch(1); }\n")
    (tmp_path / "rs" / "b.rs").write_text("use crate::other::fetch;\n\nfn b() { fetch(2); }\n")
    (tmp_path / "rs" / "c.rs").write_text("use crate::http::*;\n\nfn c() { fetch(3); }\n")
    (tmp_path / "rs" / "d.rs").write_text("use super::http::fetch;\n\nfn d() { fetch(4); }\n")  # rs/d.rs is module d in rs, so super is rs
    (tmp_path / "rs" / "sub").mkdir()
    (tmp_path / "rs" / "sub" / "mod.rs").write_text("use super::http::fetch;\n\nfn m() { fetch(5); }\n")  # rs/sub/mod.rs is module sub, so super is rs
    (tmp_path / "rs" / "sub" / "e.rs").write_text("use super::http::fetch;\n\nfn e() { fetch(6); }\n")  # super is rs/sub, which has no http
    diff = Change("rs/http.rs", rs_old, "").diff()
    [sc] = symbol_changes(parse(diff), tmp_path, {"rs/http.rs": ""})
    assert {r["path"]: r["scope"] for r in sc.refs if r["kind"] == "call" and r["path"].endswith(".rs")} == {"rs/a.rs": "imports_it", "rs/b.rs": "imports_other", "rs/c.rs": "imports_it", "rs/d.rs": "imports_it", "rs/sub/mod.rs": "imports_it", "rs/sub/e.rs": "imports_other"}


@needs_parser
def test_a_removed_method_called_on_a_receiver_typed_as_its_class_is_linked(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for d in ("app", "other", "web", "gosvc", "rs", "rb"):
        (tmp_path / d).mkdir()
    gap = "".join(f"    x{i} = {i}\n" for i in range(12))
    old = "class Client:\n    def fetch(self, url):\n        return url\n\n" + gap + "    def get(self, url):\n        return url\n"
    new = "class Client:\n" + gap + "    def get(self, url):\n        return url\n"
    (tmp_path / "app" / "client.py").write_text(old)
    (tmp_path / "other" / "typed.py").write_text("from app.client import Client\n\n\ndef run(c: Client, d, e: Cache):\n    c.fetch('u')\n    d.fetch('u')\n    e.fetch('u')\n")
    (tmp_path / "other" / "built.py").write_text("from app.client import Client\n\nc = Client(1)\nc.fetch('u')\nClient.fetch(c, 'u')\n")
    (tmp_path / "other" / "value.py").write_text("from app.client import Client\n\nr = Client.fetch(1)\nr.fetch('u')\n")  # r is a result, not a Client
    (tmp_path / "other" / "elsewhere.py").write_text("from other.client import Client\n\nc = Client()\nc.fetch('u')\n")  # a different Client
    (tmp_path / "other" / "own.py").write_text("class Client:\n    pass\n\nc = Client()\nc.fetch('u')\n")
    # A binding holds between the enclosing definition and the access: the same name elsewhere in the file is untyped or typed by its own binding.
    (tmp_path / "other" / "scoped.py").write_text("from app.client import Client\n\n\ndef a(c: Client):\n    if c:\n        c.fetch(1)\n\n\ndef b(c):\n    c.fetch(2)\n\n\ndef d():\n    c = Client()\n    c = Cache()\n    c.fetch(3)\n")
    diff = Change("app/client.py", old, new).diff()
    [sc] = symbol_changes(parse(diff), tmp_path, {"app/client.py": new})
    assert sc.owner == "Client"
    by = {(r["path"], r["line"]): (r["receiver_type"], r["owner_scope"]) for r in sc.refs if r["path"] != sc.path}
    assert by == {
        ("other/typed.py", 5): ("Client", "imports_it"),
        ("other/typed.py", 6): (None, None),
        ("other/typed.py", 7): ("Cache", None),
        ("other/built.py", 4): ("Client", "imports_it"),
        ("other/built.py", 5): ("Client", "imports_it"),
        ("other/value.py", 3): ("Client", "imports_it"),
        ("other/value.py", 4): (None, None),
        ("other/elsewhere.py", 4): ("Client", "imports_other"),
        ("other/own.py", 5): ("Client", "defines_own"),
        ("other/scoped.py", 6): ("Client", "imports_it"),
        ("other/scoped.py", 10): (None, None),
        ("other/scoped.py", 16): ("Cache", None),
    }
    assert sorted((r["path"], r["line"]) for r in sc.linked_outside) == [("other/built.py", 4), ("other/built.py", 5), ("other/scoped.py", 6), ("other/typed.py", 5), ("other/value.py", 3)]
    # A module-level function has no owner, so nothing is typed.
    [sc] = symbol_changes(parse(Change("app/http.py", HTTP_OLD, HTTP_REMOVED).diff()), tmp_path, {"app/http.py": HTTP_REMOVED})
    assert sc.owner is None
    # TypeScript: ``new Client()`` and an annotated parameter; Go: a receiver method and a typed parameter; Rust: ``impl`` and ``let c = Client::new()``; Ruby: ``Client.new``.
    ts_old = "export class Client {\n  fetch(u: string) { return u; }\n" + "".join(f"  x{i} = {i};\n" for i in range(12)) + "  get(u: string) { return u; }\n}\n"
    ts_new = "export class Client {\n" + "".join(f"  x{i} = {i};\n" for i in range(12)) + "  get(u: string) { return u; }\n}\n"
    (tmp_path / "web" / "client.ts").write_text(ts_old)
    (tmp_path / "web" / "use.ts").write_text('import { Client } from "./client";\nconst c = new Client();\nc.fetch("u");\nnew Client().fetch("u");\nfunction f(d: Client, e: Other) { d.fetch("u"); e.fetch("u"); }\n')
    [sc] = symbol_changes(parse(Change("web/client.ts", ts_old, ts_new).diff()), tmp_path, {"web/client.ts": ts_new})
    assert sc.owner == "Client" and sorted(r["line"] for r in sc.linked_outside if r["path"] == "web/use.ts") == [3, 4, 5]
    go_old = "package gosvc\n\ntype Client struct{}\n\nfunc (c *Client) Fetch(u string) string { return u }\n\nfunc (c *Client) Get(u string) string { return u }\n"
    go_new = "package gosvc\n\ntype Client struct{}\n\nfunc (c *Client) Get(u string) string { return u }\n"
    (tmp_path / "gosvc" / "client.go").write_text(go_old)
    (tmp_path / "gosvc" / "use.go").write_text("package gosvc\n\nfunc run(c *Client, d Other) {\n\tc.Fetch(\"u\")\n\td.Fetch(\"u\")\n\te := &Client{}\n\te.Fetch(\"u\")\n}\n")
    [sc] = symbol_changes(parse(Change("gosvc/client.go", go_old, go_new).diff()), tmp_path, {"gosvc/client.go": go_new})
    assert sc.owner == "Client" and sorted(r["line"] for r in sc.linked_outside if r["path"] == "gosvc/use.go") == [4, 7]
    rs_old = "pub struct Client;\n\nimpl Client {\n    pub fn fetch(&self, u: u32) -> u32 { u }\n" + "".join(f"    pub const X{i}: u32 = {i};\n" for i in range(12)) + "    pub fn get(&self, u: u32) -> u32 { u }\n}\n"
    rs_new = "pub struct Client;\n\nimpl Client {\n" + "".join(f"    pub const X{i}: u32 = {i};\n" for i in range(12)) + "    pub fn get(&self, u: u32) -> u32 { u }\n}\n"
    (tmp_path / "rs" / "client.rs").write_text(rs_old)
    (tmp_path / "rs" / "use.rs").write_text("use crate::client::Client;\n\nfn run(d: &Other) {\n    let c = Client::new();\n    c.fetch(1);\n    d.fetch(1);\n    Client::fetch(&c, 1);\n}\n")
    [sc] = symbol_changes(parse(Change("rs/client.rs", rs_old, rs_new).diff()), tmp_path, {"rs/client.rs": rs_new})
    assert sc.owner == "Client" and sorted(r["line"] for r in sc.linked_outside if r["path"] == "rs/use.rs") == [5, 7]
    rb_old = "class Client\n  def fetch(u)\n    u\n  end\n" + "".join(f"  X{i} = {i}\n" for i in range(10)) + "  def get(u)\n    u\n  end\nend\n"
    rb_new = "class Client\n" + "".join(f"  X{i} = {i}\n" for i in range(10)) + "  def get(u)\n    u\n  end\nend\n"
    (tmp_path / "rb" / "client.rb").write_text(rb_old)
    (tmp_path / "rb" / "use.rb").write_text("c = Client.new\nc.fetch(1)\nd.fetch(1)\nClient.new.fetch(1)\n")
    [sc] = symbol_changes(parse(Change("rb/client.rb", rb_old, rb_new).diff()), tmp_path, {"rb/client.rb": rb_new})
    assert sc.owner == "Client" and sorted(r["line"] for r in sc.linked_outside if r["path"] == "rb/use.rb") == [2, 4], "Ruby has no imports to resolve, so a typed receiver is linked fail-open"


@needs_parser
def test_a_removed_method_with_a_typed_caller_elsewhere_is_a_finding(synced_store, constraints, empty_repo):
    gap = "".join(f"    x{i} = {i}\n" for i in range(12))
    old = "class Client:\n    def fetch(self, url):\n        return url\n\n" + gap + "    def get(self, url):\n        return url\n"
    new = "class Client:\n" + gap + "    def get(self, url):\n        return url\n"
    stub = StubProvider()
    r = _run(synced_store, constraints, Change("app/client.py", old, new).diff(), stub, search_root=empty_repo, new_files={"app/client.py": new}, repo_files={"app/jobs.py": "from app.client import Client\n\n\ndef run(c: Client, d, e: Cache):\n    c.fetch('u')\n    d.fetch('u')\n    e.fetch('u')\n"})
    [f] = [f for f in r["findings"] if f["source"] == "deterministic"]
    assert f["title"] == "`fetch` removed but still referenced at app/jobs.py:5" and "calls on an object typed as `Client`, the class it belongs to" in f["body"]
    user = stub.calls[0][1]
    assert "1 of them on an object the file types as `Client`, the class this definition belongs to (ref:app/jobs.py:5)" in user
    assert "1 method or attribute access `.fetch` on some object, which may or may not be this definition (ref:app/jobs.py:6)" in user
    assert "1 access `.fetch` on an object the file types as `Cache`, not this definition's class, so not this definition (ref:app/jobs.py:7)" in user
    assert r["symbols"][0]["owner"] == "Client"
