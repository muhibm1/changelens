"""Code around a change: enclosing-definition windows for long files, and the definitions the added lines call."""

from __future__ import annotations

import subprocess
from pathlib import Path

from fathom.context import called_names, callees, render_windows, windows
from fathom.diffparse import parse
from pipelinebench.review_eval import Change


def _diff(path: str, before: str, after: str):
    return parse(Change(path, before, after).diff())[0]


def test_windows_show_the_enclosing_definition_of_each_hunk():
    body = "\n".join(f"    x{i} = {i}" for i in range(30))
    funcs = [f"def f{k}(a):\n{body}\n    return a + {k}\n" for k in range(20)]
    before = "import os\n\n\n" + "\n\n".join(funcs)
    after = before.replace("def f7(a):\n    x0 = 0", "def f7(a):\n    x0 = 1").replace("def f15(a):\n    x0 = 0", "def f15(a):\n    x0 = 2")
    fd = _diff("app/big.py", before, after)
    wins = windows(after, fd)
    lines = after.splitlines()
    assert len(wins) == 2
    for w, name in zip(wins, ("f7", "f15"), strict=True):
        assert lines[w.start - 1].startswith(f"def {name}(")
        assert lines[w.end - 1].strip().startswith("return a +"), "the window runs to the end of the function"
    text = render_windows(fd.path, after, wins)
    assert "parts of the file after the change" in text and "def f7(a):" in text and "def f15(a):" in text and "def f8(" not in text


def test_windows_fall_back_to_padding_and_skip_whole_file_hunks():
    before = "\n".join(f"x{i} = {i}" for i in range(300)) + "\n"
    after = before.replace("x150 = 150", "x150 = 151")
    fd = _diff("app/flat.py", before, after)
    wins = windows(after, fd, pad=5)
    assert [(w.start, w.end) for w in wins] == [(146, 156)], "no definition encloses the hunk, so the changed line plus the pad"
    added = _diff("app/new.py", "", before)
    assert windows(before, added) == [], "an added file is already shown whole by its hunk"


def test_windows_for_brace_languages_end_where_braces_balance():
    fn = "export function {name}(a: number): number {{\n  const b = a + 1\n  if (b > 2) {{\n    return b\n  }}\n  return 0\n}}\n"
    before = "\n".join(fn.format(name=f"f{k}") for k in range(80))
    after = before.replace("function f40(a: number): number {\n  const b = a + 1", "function f40(a: number): number {\n  const b = a + 2")
    fd = _diff("src/x.ts", before, after)
    wins = windows(after, fd)
    lines = after.splitlines()
    assert len(wins) == 1 and lines[wins[0].start - 1].startswith("export function f40(") and lines[wins[0].end - 1] == "}"


def test_called_names_skip_keywords_builtins_and_comments():
    fd = _diff("app/a.py", "", "def g(x):\n    return parse_amount(x) + len(x) + obj.render(x)  # fetch_rate(x)\n")
    assert called_names([fd]) == ["parse_amount", "render"]


def test_callees_come_from_the_repository_and_skip_ambiguous_names(tmp_path: Path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "money.py").write_text("import decimal\n\n\n@lru_cache\ndef parse_amount(text: str) -> int:\n    \"\"\"Cents.\"\"\"\n    return int(decimal.Decimal(text) * 100)\n\n\ndef other():\n    return 1\n")
    (tmp_path / "lib" / "a.py").write_text("def dup():\n    return 1\n")
    (tmp_path / "lib" / "b.py").write_text("def dup():\n    return 2\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_money.py").write_text("def helper():\n    return 3\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    after = "from lib.money import parse_amount\n\n\ndef total(rows):\n    return sum(parse_amount(r) for r in rows) + dup() + helper() + local()\n\n\ndef local():\n    return 0\n"
    fd = _diff("app/bill.py", "", after)
    found, ok = callees([fd], tmp_path, {"app/bill.py": after})
    assert ok
    assert [(c.name, c.path, c.line) for c in found] == [("parse_amount", "lib/money.py", 5)]
    c = found[0]
    assert "@lru_cache" in c.text and "return int(decimal" in c.text and "def other" not in c.text, "decorator kept, block cut at its end"
    assert not c.truncated


def test_callees_say_when_the_search_cannot_run(tmp_path: Path):
    fd = _diff("app/a.py", "", "def g():\n    return something_else()\n")
    found, ok = callees([fd], tmp_path / "missing", {})
    assert found == [] and ok is False


def test_review_sends_callee_definitions_and_long_file_windows(synced_store, tmp_path: Path):
    """The model sees the definition a change calls into and the function a hunk in a long file sits in; both are citable."""
    from fathom.constraints import load_constraints
    from fathom.providers import StubProvider
    from fathom.review import review
    from fathom.risk import RiskModel

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "money.py").write_text("def parse_amount(text: str) -> int:\n    \"\"\"Cents.\"\"\"\n    return int(text) * 100\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    body = "\n".join(f"    x{i} = {i}" for i in range(30))
    funcs = [f"def f{k}(a):\n{body}\n    return a + {k}\n" for k in range(20)]
    before = "from lib.money import parse_amount\n\n\n" + "\n\n".join(funcs)
    after = before.replace("def f7(a):\n    x0 = 0", "def f7(a):\n    x0 = parse_amount(a)")
    diff = Change("app/big.py", before, after).diff()

    changed_line = after.splitlines().index("    x0 = parse_amount(a)") + 1

    class Cap(StubProvider):
        def __init__(self):
            super().__init__()
            self.users = []

        def complete_json(self, system, user, schema, *, schema_name="result"):
            self.users.append(user if isinstance(user, str) else "\n\n".join(user))
            return {"findings": [{"path": "app/big.py", "line": changed_line, "severity": "warn", "category": "correctness", "title": "cents", "body": "x", "failure": "a='1' gives 100", "evidence": ["ref:lib/money.py:1"]}]}

    cap = Cap()
    constraints, _ = load_constraints(Path(__file__).resolve().parents[1] / "fixture" / "fathom" / "constraints")
    r = review(synced_store, tmp_path, cap, constraints=constraints, risk_model=RiskModel.load(None), diff_text=diff, new_files={"app/big.py": after}, search_root=tmp_path, second_reading=False)
    user = cap.users[0]
    assert "Definitions this change calls" in user and "definition of `parse_amount`" in user and "return int(text) * 100" in user
    assert "parts of the file after the change" in user and "def f7(a):" in user and "def f3(a):" not in user, "a long file shows the hunk's function, not the whole file"
    assert r["callees"] == [{"name": "parse_amount", "path": "lib/money.py", "line": 1}]
    model = [f for f in r["findings"] if f["source"] == "model"]
    assert model and model[0]["evidence"] == ["ref:lib/money.py:1"], "a callee definition's line is valid evidence"
