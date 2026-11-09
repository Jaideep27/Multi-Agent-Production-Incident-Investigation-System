"""One generated incident as Pydantic models: what the generator writes and read.py reads back.

Services are referred to by name, never by the database's serial service_id, so a dataset
read back from Postgres compares equal to the one that was built (the round-trip test) and
its checksum does not depend on load order.

Canonical order (build and read both produce it): services by name, evidence lists by
evidence_id, metric points by time, tool rules by tool name, decisive and decoy rows by
evidence_id, alternatives by (category, component), withheld and noise rows in the order
they were applied.
"""

from typing import Literal

from pydantic import Field

from investigator.models.common import (
    DomainName,
    EvidenceId,
    NonEmptyText,
    RootCauseCategory,
    StrictModel,
    ToolName,
    UtcDatetime,
)
from investigator.stable_hash import canonical_json, sha256_hex

ServiceRole = Literal[
    "load_balancer", "app", "worker", "cache", "database", "queue", "external_api", "cdn"
]
LogLevel = Literal["DEBUG", "INFO", "WARN", "ERROR", "FATAL"]
MetricSource = Literal["prometheus", "cloud_monitor", "apm"]
ChangeType = Literal["deploy", "config_change", "scaling", "infra", "feature_flag"]
DecoyType = Literal[
    "red_herring_change", "lookalike_history", "downstream_symptom", "unrelated_spike",
    "averaged_metric",
]
EvidenceKind = Literal["log", "metric", "change", "past_incident", "dependency"]


# ----------------------------------------------------------------------- evidence


class IncidentRow(StrictModel):
    incident_id: str = Field(pattern=r"^inc_[0-9a-f]{8}$")
    domain: DomainName
    reported_at: UtcDatetime
    window_start: UtcDatetime
    window_end: UtcDatetime
    summary: NonEmptyText
    reporter: NonEmptyText


class ServiceRow(StrictModel):
    name: NonEmptyText
    role: ServiceRole
    instance_count: int = Field(ge=1)


class DependencyRow(StrictModel):
    evidence_id: EvidenceId
    caller: str
    callee: str
    config: dict


class LogRow(StrictModel):
    evidence_id: EvidenceId
    service: str
    instance: str | None
    ts: UtcDatetime
    level: LogLevel
    message: NonEmptyText
    attrs: dict


class MetricPoint(StrictModel):
    ts: UtcDatetime
    value: float


class MetricSeriesRow(StrictModel):
    evidence_id: EvidenceId
    service: str
    instance: str | None
    metric_name: NonEmptyText
    unit: NonEmptyText
    source: MetricSource
    interval_s: int = Field(gt=0)
    points: list[MetricPoint]


class ChangeRow(StrictModel):
    evidence_id: EvidenceId
    service: str
    ts: UtcDatetime
    change_type: ChangeType
    version: str | None
    author: str | None
    summary: NonEmptyText
    diff: dict | None   # stored in change_details; None means no detail row


class PastIncidentRow(StrictModel):
    evidence_id: EvidenceId
    occurred_at: UtcDatetime
    title: NonEmptyText
    root_cause_category: RootCauseCategory
    root_cause_component: str | None
    resolution: str | None
    symptoms: list[str]


class ToolBehaviorRow(StrictModel):
    tool_name: ToolName
    rules: dict


class EvidenceSet(StrictModel):
    incident: IncidentRow
    services: list[ServiceRow]
    dependencies: list[DependencyRow]
    logs: list[LogRow]
    metrics: list[MetricSeriesRow]
    changes: list[ChangeRow]
    past_incidents: list[PastIncidentRow]
    tool_behavior: list[ToolBehaviorRow]


# -------------------------------------------------------------------------- truth


class ScenarioRow(StrictModel):
    plan_id: str
    archetype: str
    seed: int
    generator_version: str
    expected_outcome: Literal["identifiable", "inconclusive"]
    solvable_after_noise: bool
    unidentifiable_reason: str | None
    root_component: str
    root_category: RootCauseCategory
    mechanism: NonEmptyText
    contributing_factors: list


class DecisiveRow(StrictModel):
    evidence_id: EvidenceId
    availability: Literal["retrievable", "unreachable"]


class DecoyRow(StrictModel):
    evidence_id: EvidenceId
    decoy_type: DecoyType


class WithheldRow(StrictModel):
    kind: EvidenceKind
    description: NonEmptyText
    would_have_shown: dict | None


class AlternativeRow(StrictModel):
    category: RootCauseCategory
    component: str
    why_plausible: NonEmptyText


class NoiseRow(StrictModel):
    noise_type: NonEmptyText
    params: dict


class TruthSet(StrictModel):
    scenario: ScenarioRow
    decisive: list[DecisiveRow]
    decoys: list[DecoyRow]
    withheld: list[WithheldRow]
    alternatives: list[AlternativeRow]
    noise: list[NoiseRow]


class IncidentDataset(StrictModel):
    evidence: EvidenceSet
    truth: TruthSet


def dataset_checksum(dataset):
    """sha256 of the canonical JSON form; equal datasets give equal checksums in any process."""
    return sha256_hex(canonical_json(dataset.model_dump(mode="json")))
