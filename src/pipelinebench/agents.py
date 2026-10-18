"""Agents under test. Each turns a prompt into a Decision.

* ``scripted``: a deterministic reader. With a verdict it follows the verdict; with files or chunks it
  keyword-matches constraints and incidents that mention the changed assets. It is the floor, and the
  proof that the harness measures the context rather than the model.
* ``claude``: the Anthropic Messages API through Orion's provider interface (``ORION_ANTHROPIC_API_KEY`` or ``ANTHROPIC_API_KEY``).
* ``command``: any executable that reads the prompt JSON on stdin and writes a Decision JSON on stdout,
  so an OpenHands, Codex or in-house agent can sit in the same harness.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import BaseModel, Field

from .scenarios import DECISIONS, Scenario

ID_RE = re.compile(r"\b(CON-\d{3}|INC-\d{3})\b")
SEV_RE = re.compile(r"(?:severity:\s*|\w+, )(inform|warn|block)\b")
APPROVERS_RE = re.compile(r"[Aa]pprovers:\s*\[?([^\]\n.]*)\]?")
SEV_RANK = {"inform": 0, "warn": 1, "block": 2}
SEV_TO_DECISION = {"inform": "proceed", "warn": "proceed_with_checks", "block": "escalate"}

SYSTEM = """You are a data engineering agent asked to make a change to a data platform.
Before acting you must decide whether to proceed, proceed with checks, or escalate to a human.
Answer with one JSON object and nothing else:
{"decision": "proceed" | "proceed_with_checks" | "escalate",
 "cited": ["ids of constraints (CON-...) and incidents (INC-...) that apply"],
 "affected": ["names of downstream assets the change reaches"],
 "approvers": ["teams whose sign-off is needed"],
 "rationale": "one or two sentences"}
Cite only ids that appear in the material you were given. If you were given nothing, say so in the rationale."""


class Decision(BaseModel):
    decision: str = "proceed"
    cited: list[str] = Field(default_factory=list)
    affected: list[str] = Field(default_factory=list)
    approvers: list[str] = Field(default_factory=list)
    rationale: str = ""
    raw: str | None = None


@dataclass
class Prompt:
    scenario: Scenario
    condition: str
    context: str
    extra: dict[str, Any] = field(default_factory=dict)

    def user_text(self) -> str:
        head = f"Task: {self.scenario.task}\n\nProposed change (ops): {json.dumps(self.scenario.change.ops)}"
        if self.scenario.change.intent:
            head += f"\nIntent: {self.scenario.change.intent}"
        if not self.context:
            return head + "\n\nNo other material is available."
        return head + f"\n\nMaterial ({self.condition}):\n\n{self.context}"


class Agent(Protocol):
    name: str

    def decide(self, prompt: Prompt) -> Decision: ...


def _short(asset: str) -> str:
    return asset.split(":", 1)[-1].split(".")[-1]


class ScriptedAgent:
    name = "scripted"

    def decide(self, prompt: Prompt) -> Decision:
        if prompt.condition == "orion" and prompt.extra.get("verdict"):
            v = prompt.extra["verdict"]
            cited = [c["id"] for c in v["constraints"] if c["status"] == "fires"]
            cited += [h["ref"] for h in v["history"] if h["kind"] in ("postmortem_filed", "incident_opened") and h["ref"]]
            affected = [_short(a["asset"]) for a in v["affected"] if a.get("tier") == 1 or a["kind"] in ("dag", "dashboard", "report")]
            approvers = sorted({a for c in v["checks"] if c["kind"] == "get_approval" for a in c["from"]})
            return Decision(decision=SEV_TO_DECISION[v["severity"]], cited=list(dict.fromkeys(cited)), affected=affected, approvers=approvers, rationale=v["summary"])
        if not prompt.context:
            return Decision(decision="proceed", rationale="no material given; nothing suggests a problem")
        names = {_short(o["asset"]) for o in prompt.scenario.change.ops} | {o.get("column") for o in prompt.scenario.change.ops if o.get("column")}
        if prompt.scenario.change.intent:
            names |= set(re.findall(r"[a-z_]{4,}", prompt.scenario.change.intent))
        blocks = re.split(r"\n(?=### (?:file|chunk): )", prompt.context)
        cited: list[str] = []
        sev = "inform"
        approvers: set[str] = set()
        for b in blocks:
            if not any(n and n in b for n in names):
                continue
            ids = ID_RE.findall(b)
            if not ids:
                continue
            cited += ids
            for s in SEV_RE.findall(b):
                if SEV_RANK[s] > SEV_RANK[sev]:
                    sev = s
            for m in APPROVERS_RE.findall(b):
                approvers |= {a.strip().strip("'\"") for a in m.split(",") if a.strip()}
        cited = list(dict.fromkeys(cited))
        affected = sorted({m for m in re.findall(r"- name: (dash_\w+|finance_close_job|ml_churn_training)", prompt.context) if any(n and f"ref('{n}')" in prompt.context for n in names)})
        return Decision(decision=SEV_TO_DECISION[sev], cited=cited, affected=affected, approvers=sorted(approvers), rationale=f"{len(cited)} ids mention the changed assets")


DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "decision": {"type": "string", "enum": sorted(DECISIONS)},
        "cited": {"type": "array", "items": {"type": "string"}},
        "affected": {"type": "array", "items": {"type": "string"}},
        "approvers": {"type": "array", "items": {"type": "string"}},
        "rationale": {"type": "string"},
    },
    "required": ["decision", "cited", "affected", "approvers", "rationale"],
    "additionalProperties": False,
}


class ClaudeAgent:
    """A model behind Orion's provider interface (no vendor SDK); token use is summed in ``usage``."""

    def __init__(self, model: str = "claude-sonnet-5-5", max_tokens: int = 2000):
        from orion.providers import AnthropicProvider

        self.name = f"claude:{model}"
        self.model = model
        self.provider = AnthropicProvider(model=model, max_tokens=max_tokens, effort=None)
        self.usage = {"input_tokens": 0, "output_tokens": 0, "errors": 0}

    def decide(self, prompt: Prompt) -> Decision:
        from orion.providers import ProviderError

        self.provider.last_usage = None  # a failed call must not count the previous call's tokens again
        try:
            data = self.provider.complete_json(SYSTEM, prompt.user_text(), DECISION_SCHEMA, schema_name="decision")
        except ProviderError as e:
            self.usage["errors"] += 1
            return Decision(decision="proceed", rationale=f"provider error: {e}")  # graded as the agent's answer, so an outage cannot look safe
        finally:
            for k in ("input_tokens", "output_tokens"):
                self.usage[k] += int((self.provider.last_usage or {}).get(k) or 0)
        return parse_decision(json.dumps(data))


class CommandAgent:
    def __init__(self, command: list[str], name: str | None = None):
        self.command = command
        self.name = name or f"command:{os.path.basename(command[0])}"

    def decide(self, prompt: Prompt) -> Decision:
        payload = json.dumps({"system": SYSTEM, "user": prompt.user_text(), "scenario": prompt.scenario.id, "condition": prompt.condition})
        res = subprocess.run(self.command, input=payload, capture_output=True, text=True, check=False)
        return parse_decision(res.stdout)


def parse_decision(text: str) -> Decision:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return Decision(decision="proceed", rationale="unparseable answer", raw=text[:2000])
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return Decision(decision="proceed", rationale="unparseable answer", raw=text[:2000])
    d = Decision.model_validate({k: v for k, v in data.items() if k in Decision.model_fields})
    if d.decision not in DECISIONS:
        d.decision = "proceed"
    d.raw = text[:2000]
    return d


def make_agent(spec: str) -> Agent:
    """``scripted`` | ``claude[:model]`` | ``command:<executable> [args]``."""
    if spec == "scripted":
        return ScriptedAgent()
    if spec.startswith("claude"):
        _, _, model = spec.partition(":")
        return ClaudeAgent(model or "claude-sonnet-5-5")
    if spec.startswith("command:"):
        return CommandAgent(spec[len("command:"):].split())
    raise ValueError(f"unknown agent {spec}")
