"""Scenario files and their ground truth."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

Decision = Literal["proceed", "proceed_with_checks", "escalate"]
DECISIONS: tuple[str, ...] = ("proceed", "proceed_with_checks", "escalate")


class Change(BaseModel):
    """What the agent is asked to do, in a form Fathom and the file-based conditions can both read."""

    ops: list[dict[str, Any]] = Field(default_factory=list)
    intent: str | None = None
    files: list[str] = Field(default_factory=list)  # repo-relative paths the change would touch


class Expected(BaseModel):
    decision: Decision
    severity: Literal["inform", "warn", "block"]
    must_cite: list[str] = Field(default_factory=list)  # constraint and incident ids
    must_name: list[str] = Field(default_factory=list)  # downstream assets that must be named
    approvers: list[str] = Field(default_factory=list)
    must_not: list[str] = Field(default_factory=list)  # decisions that are unsafe here
    why: str = ""


class Scenario(BaseModel):
    id: str
    title: str
    family: str
    task: str
    change: Change
    expected: Expected
    tags: list[str] = Field(default_factory=list)
    path: str | None = None


def load_scenarios(directory: Path) -> list[Scenario]:
    out = []
    for p in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(p.read_text()) or {}
        out.append(Scenario.model_validate({**data, "path": str(p)}))
    return out


def known_ids(fixture_root: Path) -> set[str]:
    """Every constraint and incident id that exists; anything else an agent cites is fabricated."""
    ids: set[str] = set()
    for p in (fixture_root / "fathom" / "constraints").glob("*.yaml"):
        ids.add(p.stem)
    for p in (fixture_root / "fathom" / "incidents").glob("*.md"):
        ids.add(p.stem)
    return ids
