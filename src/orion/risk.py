"""Risk scoring: a transparent weighted sum, tunable from ``orion/risk.yaml``."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_RISK = {
    "weights": {
        "tier1_consumers": 30,
        "block_constraint": 25,
        "incident_recency": 20,
        "destructive_op": 15,
        "blast_radius": 10,
    },
    "incident_horizon_days": 180,
    "thresholds": {"warn": 30, "block": 70},
}


@dataclass
class RiskModel:
    weights: dict[str, float]
    incident_horizon_days: int
    thresholds: dict[str, int]
    source: str = "built-in defaults"

    @classmethod
    def load(cls, path: Path | None) -> RiskModel:
        data = dict(DEFAULT_RISK)
        source = "built-in defaults"
        if path and path.exists():
            user = yaml.safe_load(path.read_text()) or {}
            data = {**data, **user, "weights": {**DEFAULT_RISK["weights"], **(user.get("weights") or {})}}
            source = str(path)
        return cls(data["weights"], int(data["incident_horizon_days"]), data["thresholds"], source)


@dataclass
class RiskScore:
    score: int
    factors: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"score": self.score, "factors": self.factors, "model": self.model}


def score(
    model: RiskModel,
    *,
    tier1_consumers: int,
    block_constraints: int,
    newest_incident_days: int | None,
    destructive_ops: int,
    blast_radius: int,
) -> RiskScore:
    w = model.weights
    factors = []

    def add(name: str, value: Any, contribution: float, why: str) -> None:
        factors.append({"name": name, "value": value, "weight": w.get(name, 0), "contribution": round(contribution, 1), "why": why})

    add("tier1_consumers", tier1_consumers, w["tier1_consumers"] if tier1_consumers else 0, "someone outside the team notices within a day")
    add("block_constraint", block_constraints, w["block_constraint"] if block_constraints else 0, "a human already decided this is dangerous")
    if newest_incident_days is None:
        add("incident_recency", None, 0, "no incident from this kind of change on this path")
    else:
        decay = max(0.0, 1 - newest_incident_days / model.incident_horizon_days)
        add("incident_recency", newest_incident_days, w["incident_recency"] * decay, "recent pain from the same kind of change predicts repeat pain")
    add("destructive_op", destructive_ops, w["destructive_op"] if destructive_ops else 0, "not reversible by rerun")
    add("blast_radius", blast_radius, w["blast_radius"] * min(1.0, math.log10(blast_radius + 1) / 2), "breadth matters, but less than depth")
    total = int(round(min(100, sum(f["contribution"] for f in factors))))
    return RiskScore(total, factors, model.source)


def severity(model: RiskModel, risk: int, constraint_severities: list[str]) -> str:
    if "block" in constraint_severities or risk >= model.thresholds["block"]:
        return "block"
    if "warn" in constraint_severities or risk >= model.thresholds["warn"]:
        return "warn"
    return "inform"
