"""Constraints as code: ``fathom/constraints/*.yaml``.

Git is the store and the approval workflow. This module only loads and
validates; the pre-flight evaluates the closed predicate set against typed
change operations.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

PREDICATES = (
    "columns_locked",
    "no_backfill_window",
    "partition_key",
    "schedule_locked",
    "requires_approval",
    "freshness_sla",
    "deprecated",
)


class Scope(BaseModel):
    assets: list[str] = Field(default_factory=list)
    columns: str | list[str] | None = None


class Rule(BaseModel):
    kind: Literal[PREDICATES]  # type: ignore[valid-type]
    allow: list[str] = Field(default_factory=list)
    key: str | None = None
    window: dict[str, str] | None = None
    approvers: list[str] = Field(default_factory=list)
    minutes: int | None = None


class Constraint(BaseModel):
    id: str
    scope: Scope
    rule: Rule
    severity: Literal["inform", "warn", "block"] = "warn"
    reason: str
    source: str | None = None
    approvers: list[str] = Field(default_factory=list)
    expires: date | None = None
    path: str | None = None

    @field_validator("expires", mode="before")
    @classmethod
    def _none(cls, v: Any) -> Any:
        return None if v in ("", "null", None) else v

    @property
    def expired(self) -> bool:
        return self.expires is not None and self.expires < date.today()


def load_constraints(directory: Path) -> tuple[list[Constraint], list[str]]:
    constraints: list[Constraint] = []
    problems: list[str] = []
    if not directory.exists():
        return constraints, [f"{directory} does not exist"]
    for path in sorted(list(directory.glob("*.yaml")) + list(directory.glob("*.yml"))):
        try:
            data = yaml.safe_load(path.read_text()) or {}
            data.setdefault("path", str(path))
            constraints.append(Constraint.model_validate(data))
        except Exception as e:  # noqa: BLE001
            problems.append(f"{path.name}: {e}")
    return constraints, problems


def constraints_for(constraints: list[Constraint], asset_ids: set[str], resolve) -> list[Constraint]:
    """Constraints whose scope names any of ``asset_ids`` (refs resolved with ``resolve``)."""
    hits = []
    for c in constraints:
        scoped = {resolve(a) for a in c.scope.assets}
        if scoped & asset_ids:
            hits.append(c)
    return hits
