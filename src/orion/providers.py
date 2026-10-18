"""Model providers behind one small interface.

Review logic calls ``Provider.complete_json`` and never a vendor SDK. Each
provider turns (system, user, JSON schema) into one parsed JSON object, with a
timeout and retries on rate limits and server errors. HTTP is plain
``urllib`` so no vendor package is a dependency.

* ``stub``: offline and deterministic; tests and demos.
* ``anthropic``: the Messages API, structured output via ``output_config.format``
  (forced tool use is rejected by the current Claude models).
* ``openai``: any OpenAI-compatible ``/chat/completions`` endpoint (OpenAI,
  Azure, vLLM, Ollama, LM Studio), structured output via ``response_format``.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol


class ProviderError(RuntimeError):
    pass


class Provider(Protocol):
    name: str

    def complete_json(self, system: str, user: str, schema: dict[str, Any], *, schema_name: str = "result") -> dict[str, Any]: ...


# Orion's own name first so a key scoped to Orion wins over a general one.
ANTHROPIC_KEY_ENVS = ("ORION_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")

RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


def _post(url: str, headers: dict[str, str], body: dict[str, Any], *, timeout: float, retries: int, sleep: Callable[[float], None] | None = None) -> dict[str, Any]:
    sleep = sleep or time.sleep
    data = json.dumps(body).encode()
    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers={"content-type": "application/json", **headers}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500] if hasattr(e, "read") else ""
            last = ProviderError(f"HTTP {e.code} from {url}: {detail}")
            if e.code not in RETRY_STATUS:
                raise last from e
            wait = _retry_after(e.headers.get("retry-after") if e.headers else None)
        except (urllib.error.URLError, TimeoutError) as e:
            last = ProviderError(f"request to {url} failed: {e}")
            wait = None
        if attempt < retries:
            sleep(wait if wait is not None else min(2 ** attempt, 16))
    raise last or ProviderError("request failed")


def _retry_after(value: str | None, cap: float = 60.0) -> float | None:
    """Seconds from a Retry-After header (only the delay-seconds form), capped; None for anything else, so backoff applies."""
    if not value:
        return None
    try:
        secs = float(value)
    except ValueError:
        return None
    return min(secs, cap) if secs > 0 else None


def _parse_json_text(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise ProviderError("model returned no JSON object")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise ProviderError(f"model returned invalid JSON: {e}") from e


@dataclass
class AnthropicProvider:
    model: str = "claude-sonnet-5-5"
    api_key: str | None = None
    base_url: str = "https://api.anthropic.com"
    max_tokens: int = 16000
    effort: str | None = "medium"
    timeout: float = 300.0
    retries: int = 3
    last_usage: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.api_key = self.api_key or next((os.environ[k] for k in ANTHROPIC_KEY_ENVS if os.environ.get(k)), None)
        if not self.api_key:
            raise ProviderError(f"no Anthropic API key; set {' or '.join(ANTHROPIC_KEY_ENVS)}")
        self.name = f"anthropic:{self.model}"

    def complete_json(self, system: str, user: str, schema: dict[str, Any], *, schema_name: str = "result") -> dict[str, Any]:
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if self.effort:
            output_config["effort"] = self.effort
        body = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": output_config,
        }
        headers = {"x-api-key": self.api_key or "", "anthropic-version": "2023-06-01"}
        resp = _post(f"{self.base_url.rstrip('/')}/v1/messages", headers, body, timeout=self.timeout, retries=self.retries)
        self.last_usage = resp.get("usage")
        stop = resp.get("stop_reason")
        if stop == "refusal":
            raise ProviderError(f"model declined the request ({(resp.get('stop_details') or {}).get('category')})")
        if stop == "max_tokens":
            raise ProviderError("model output hit max_tokens before the JSON was complete")
        text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        return _parse_json_text(text)


def strict_schema(schema: Any) -> Any:
    """The strict form OpenAI-compatible structured output needs (some endpoints accept nothing else): every object
    closed and every property required, with the ones that were optional made nullable. Validation treats null as absent."""
    if isinstance(schema, list):
        return [strict_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: strict_schema(v) for k, v in schema.items()}
    if out.get("type") == "object" and isinstance(out.get("properties"), dict):
        required = set(schema.get("required") or [])
        for name, prop in out["properties"].items():
            if name not in required and isinstance(prop, dict) and "type" in prop:
                types = prop["type"] if isinstance(prop["type"], list) else [prop["type"]]
                prop["type"] = types + ([] if "null" in types else ["null"])
                if "enum" in prop:
                    prop["enum"] = list(prop["enum"]) + ([] if None in prop["enum"] else [None])
        out["required"] = list(out["properties"])
        out["additionalProperties"] = False
    return out


@dataclass
class OpenAICompatibleProvider:
    model: str = "gpt-5"
    api_key: str | None = None
    base_url: str = "https://api.openai.com/v1"
    timeout: float = 120.0
    retries: int = 3
    api_key_env: str = "OPENAI_API_KEY"
    last_usage: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.environ.get(self.api_key_env)
        self.name = f"openai:{self.model}"

    def complete_json(self, system: str, user: str, schema: dict[str, Any], *, schema_name: str = "result") -> dict[str, Any]:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": schema_name, "schema": strict_schema(schema), "strict": True}},
        }
        headers = {"authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        resp = _post(f"{self.base_url.rstrip('/')}/chat/completions", headers, body, timeout=self.timeout, retries=self.retries)
        self.last_usage = resp.get("usage")
        try:
            text = resp["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError(f"unexpected response shape: {str(resp)[:300]}") from e
        return _parse_json_text(text)


class StubProvider:
    """Deterministic and offline. Give it a function of (system, user) or a fixed response."""

    def __init__(self, respond: Callable[[str, str], dict[str, Any]] | dict[str, Any] | None = None):
        self.name = "stub"
        self._respond = respond
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str, schema: dict[str, Any], *, schema_name: str = "result") -> dict[str, Any]:
        self.calls.append((system, user))
        if callable(self._respond):
            return self._respond(system, user)
        if isinstance(self._respond, dict):
            return self._respond
        return {"findings": []}


def make_provider(spec: str | None, *, model: str | None = None, base_url: str | None = None, api_key_env: str | None = None) -> Provider:
    """``stub`` | ``anthropic`` | ``openai``; model and endpoint from arguments or config."""
    spec = (spec or "stub").lower()
    if spec == "stub":
        return StubProvider()
    if spec == "anthropic":
        kw: dict[str, Any] = {"model": model} if model else {}
        if base_url:
            kw["base_url"] = base_url
        if api_key_env and os.environ.get(api_key_env):
            kw["api_key"] = os.environ[api_key_env]
        return AnthropicProvider(**kw)
    if spec in ("openai", "openai-compatible"):
        kw = {"model": model} if model else {}
        if base_url:
            kw["base_url"] = base_url
        if api_key_env:
            kw["api_key_env"] = api_key_env
        return OpenAICompatibleProvider(**kw)
    raise ProviderError(f"unknown provider {spec}; use stub, anthropic or openai")
