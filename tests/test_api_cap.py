"""Paid calls are off until a dollar cap is set, every call lands in the ledger, and a spent cap stops the next run too."""

from __future__ import annotations

import pytest
from flywheel import spend

from fathom import providers, review
from fathom.providers import AnthropicProvider, ProviderError, SpendCapError, make_provider

SCHEMA = {"type": "object", "properties": {"findings": {"type": "array"}}, "required": ["findings"]}


def answer(usage: dict) -> dict:
    return {"stop_reason": "end_turn", "usage": usage, "content": [{"type": "text", "text": '{"findings": []}'}]}


def test_no_cap_means_the_anthropic_provider_refuses_to_start(monkeypatch):
    monkeypatch.delenv("FATHOM_API_CAP")
    monkeypatch.setenv("FATHOM_ANTHROPIC_API_KEY", "k")
    with pytest.raises(SpendCapError, match="set FATHOM_API_CAP") as refused:
        make_provider("anthropic")
    assert isinstance(refused.value, ProviderError), "the CLI and the review treat it like any provider failure"
    assert make_provider("claude-code").name == "claude-code", "the subscription route needs no cap"


def test_each_call_is_priced_and_added_to_the_ledger(monkeypatch, tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    monkeypatch.setenv("FATHOM_API_LEDGER", str(ledger))
    sent = []

    def post(url, headers, body, **kw):
        sent.append(body)
        return answer({"input_tokens": 1000, "cache_read_input_tokens": 4000, "output_tokens": 200})

    monkeypatch.setattr(providers, "_post", post)
    p = AnthropicProvider(model="claude-haiku-5-5", api_key="k")
    assert p.complete_json("sys", ["shared change", "focus"], SCHEMA, schema_name="report_findings") == {"findings": []}
    assert sent[0]["system"][0]["cache_control"] == {"type": "ephemeral"}, "the request is sent as built, cache markers and all"
    [row] = spend.read_ledger([ledger])
    assert (row["project"], row["model"], row["route"], row["label"]) == ("fathom", "claude-haiku-5-5", "api", "fathom report_findings")
    assert (row["input"], row["cache_read"], row["output"]) == (1000, 4000, 200)
    assert row["billed"] == pytest.approx((1000 * 0.10 + 4000 * 0.01 + 200 * 0.50) / 1e6)
    assert p.last_usage == {"input_tokens": 1000, "cache_read_input_tokens": 4000, "output_tokens": 200}


def test_a_spent_cap_refuses_the_next_call_and_the_next_run(monkeypatch, tmp_path):
    monkeypatch.setenv("FATHOM_API_LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.setenv("FATHOM_API_CAP", "0.001")
    calls = []

    def post(url, headers, body, **kw):
        calls.append(body)
        return answer({"input_tokens": 0, "output_tokens": 4000})  # $0.002 on Haiku 5.5, twice the cap

    monkeypatch.setattr(providers, "_post", post)
    p = AnthropicProvider(model="claude-haiku-5-5", api_key="k")
    p.complete_json("s", "u", SCHEMA)
    with pytest.raises(SpendCapError, match="already spent"):
        p.complete_json("s", "u", SCHEMA)
    with pytest.raises(SpendCapError, match="already spent"):
        AnthropicProvider(model="claude-haiku-5-5", api_key="k")  # a later run reads the same ledger
    out, usage, error = review._pass(p, "shared", "focus")
    assert (out, usage) == (None, None) and "already spent" in error, "a review keeps its deterministic findings"
    assert len(calls) == 1


def test_a_model_without_a_price_is_refused_before_any_call(monkeypatch):
    monkeypatch.setattr(providers, "_post", lambda *a, **k: pytest.fail("no call may be sent"))
    with pytest.raises(SpendCapError, match="no price"):
        AnthropicProvider(model="claude-imaginary-9", api_key="k")
    assert AnthropicProvider(model="claude-haiku-5-5-20261001", api_key="k").name == "anthropic:claude-haiku-5-5-20261001", "a dated id is priced as its model"
