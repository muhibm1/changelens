"""Symbol references, credential scanning, and the hardening around what the model is sent."""

from __future__ import annotations

import io
import json
import subprocess
import urllib.error
from pathlib import Path

import pytest

from orion import providers
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


def test_references_search_the_repository_and_an_overlay(empty_repo, tmp_path):
    # A file in the working tree (untracked is enough) is found through git grep; a non-code file is not.
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "lib").mkdir()
    (repo / "lib" / "a.py").write_text("def render_file(f):\n    return f\n")
    (repo / "lib" / "b.py").write_text("from lib.a import render_file\nrender_file(1)\n")
    (repo / "notes.md").write_text("render_file is documented here\n")
    assert references(repo, "render_file", {}) == [("lib/a.py", 1, "def render_file(f):"), ("lib/b.py", 1, "from lib.a import render_file"), ("lib/b.py", 2, "render_file(1)")]
    # Overlay files are searched too, and an empty repository contributes nothing.
    refs = references(empty_repo, "zz_only_in_overlay", {"app/a.py": "x = zz_only_in_overlay()\nzz_only_in_overlay_more = 1\n"})
    assert refs == [("app/a.py", 1, "x = zz_only_in_overlay()")]
    assert references(empty_repo, "zz_only_in_overlay", {"docs/a.md": "zz_only_in_overlay"}) == []


def test_search_reports_a_failed_repository_search_and_truncation(tmp_path):
    # Not a git repository: the search fails, the overlay is still searched, and the failure is reported rather than read as "no callers".
    plain = tmp_path / "plain"
    plain.mkdir()
    res = search(plain, "fetch", {"app/x.py": "fetch(1)\n"})
    assert res.failed and res.refs == [("app/x.py", 1, "fetch(1)")] and not res.truncated
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
    ctx.symbols[0].refs.append({"path": "app/jobs.py", "line": 3, "text": "fetch(u)", "kind": "use", "in_diff": False})
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
    assert by[("app/health.py", 5)]["in_diff"] and by[("app/health.py", 5)]["kind"] == "use"
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
        {"path": "app/http.py", "line": 4, "severity": "warn", "category": "correctness", "title": "jobs.py breaks", "body": "b", "evidence": ["ref:app/jobs.py:3"]},
        {"path": "app/http.py", "line": 4, "severity": "warn", "category": "correctness", "title": "made up", "body": "b", "evidence": ["ref:app/nowhere.py:1"]},
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
