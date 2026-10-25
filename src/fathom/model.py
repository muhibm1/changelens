"""Core types for the asset graph and the operational ledger.

Every node and edge carries at least one provenance record. The store refuses
writes without one, so a fact in Fathom can always answer "says who, and when".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class NodeKind(StrEnum):
    SOURCE_TABLE = "source_table"
    MODEL = "model"
    SEED = "seed"
    SNAPSHOT = "snapshot"
    ICEBERG_TABLE = "iceberg_table"
    DAG = "dag"
    TASK = "task"
    SPARK_JOB = "spark_job"
    DASHBOARD = "dashboard"
    REPORT = "report"
    TEAM = "team"
    PERSON = "person"
    COLUMN = "column"
    # the code graph (fathom.codegraph)
    REPO = "repo"
    FILE = "file"
    SYMBOL = "symbol"
    PACKAGE = "package"
    ROUTE = "route"
    ENV_VAR = "env"
    SERVICE = "service"


class EdgeKind(StrEnum):
    PRODUCES = "produces"
    CONSUMES = "consumes"
    DEPENDS_ON = "depends_on"
    TRIGGERS = "triggers"
    OWNED_BY = "owned_by"
    HAS_SLA = "has_sla"
    COLUMN_OF = "column_of"
    DERIVES_FROM = "derives_from"
    # the code graph: every edge points from the dependant to what it depends on
    IMPORTS = "imports"
    CALLS = "calls"
    REQUIRES = "requires"  # manifest -> package
    BUILT_FROM = "built_from"  # package or service -> repo
    ROUTES_TO = "routes_to"  # route -> handler
    CALLS_ROUTE = "calls_route"  # HTTP client -> route
    READS_ENV = "reads_env"
    SET_BY = "set_by"  # env var -> config file that sets it
    USES_SERVICE = "uses_service"
    DEFINED_IN = "defined_in"  # containment, not a dependency
    PART_OF = "part_of"  # containment, not a dependency


class Confidence(StrEnum):
    DECLARED = "declared"  # written down by a human (yml, CODEOWNERS)
    PARSED = "parsed"  # read from code or an artifact deterministically
    INFERRED = "inferred"  # a heuristic or a model produced it


class EventKind(StrEnum):
    RUN_SUCCEEDED = "run_succeeded"
    RUN_FAILED = "run_failed"
    SLA_MISSED = "sla_missed"
    SCHEMA_CHANGED = "schema_changed"
    BACKFILL_RAN = "backfill_ran"
    CHANGE_LANDED = "change_landed"
    INCIDENT_OPENED = "incident_opened"
    POSTMORTEM_FILED = "postmortem_filed"
    PREFLIGHT_ISSUED = "preflight_issued"
    DECISION_RECORDED = "decision_recorded"
    OUTCOME_OBSERVED = "outcome_observed"
    SNAPSHOT_COMMITTED = "snapshot_committed"
    CONSTRAINT_PROPOSED = "constraint_proposed"
    GRAPH_SYNCED = "graph_synced"


def now() -> datetime:
    return datetime.now(UTC)


def node_id(kind: NodeKind | str, name: str) -> str:
    """Stable, human-readable node id: ``model:fct_orders``, ``column:fct_orders.amount``."""
    return f"{kind}:{name}"


def edge_id(src: str, dst: str, kind: EdgeKind | str) -> str:
    digest = hashlib.sha1(f"{src}|{kind}|{dst}".encode()).hexdigest()[:12]
    return f"e_{digest}"


@dataclass
class Provenance:
    adapter: str
    source: str  # artifact or file the fact was read from
    ref: str | None = None  # commit sha, snapshot id, run id
    observed_at: datetime = field(default_factory=now)


@dataclass
class Node:
    kind: NodeKind
    name: str
    properties: dict[str, Any] = field(default_factory=dict)
    tier: int | None = None
    owner: str | None = None
    provenance: list[Provenance] = field(default_factory=list)

    @property
    def id(self) -> str:
        return node_id(self.kind, self.name)


@dataclass
class Edge:
    src: str
    dst: str
    kind: EdgeKind
    confidence: Confidence = Confidence.PARSED
    properties: dict[str, Any] = field(default_factory=dict)
    provenance: list[Provenance] = field(default_factory=list)

    @property
    def id(self) -> str:
        return edge_id(self.src, self.dst, self.kind)


@dataclass
class Event:
    kind: EventKind
    occurred_at: datetime
    assets: list[str]
    source: str
    source_ref: str | None = None
    actor: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        key = json.dumps(
            [self.kind, self.occurred_at.isoformat(), sorted(self.assets), self.source, self.source_ref],
            default=str,
        )
        return "ev_" + hashlib.sha1(key.encode()).hexdigest()[:12]
