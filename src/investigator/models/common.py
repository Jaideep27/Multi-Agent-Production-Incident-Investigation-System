"""Shared vocabulary for all models.

The value lists here repeat CHECK constraints in db/init/*.sql. A test compares them with the
SQL so the two cannot drift apart.
"""

from datetime import timezone
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints


class StrictModel(BaseModel):
    """Base for every boundary model.

    Unknown fields are an error rather than silently dropped: an LLM that invents a field
    should get its output rejected and repaired, not half-accepted.
    """

    model_config = ConfigDict(extra="forbid")


RootCauseCategory = Literal[
    "bad_deploy",
    "config_change",
    "resource_exhaustion",
    "db_contention",
    "cache_failure",
    "lb_imbalance",
    "dependency_failure",
    "rate_limiting",
    "capacity_scaling",
    "cert_expiry",
]

ToolName = Literal[
    "query_logs",
    "query_metrics",
    "get_change_history",
    "get_service_dependencies",
    "search_past_incidents",
]

ToolStatus = Literal["ok", "partial", "timeout", "error", "empty"]

DomainName = Literal[
    "crypto_exchange",
    "payment_gateway",
    "ott",
    "ecommerce",
    "food_delivery",
    # three more platforms for the story-driven evaluation set
    "online_retail",
    "search_platform",
    "saas_platform",
]

AgentName = Literal[
    "orchestrator",
    "observability",
    "dependency",
    "reasoning",
    "critic",
    "reporting",
]

# Evidence kind -> ID prefix. Mirrors the evidence_index CHECK in 003_evidence.sql (a test
# compares them).
EVIDENCE_PREFIXES = {
    "log": "L",
    "metric": "M",
    "change": "C",
    "past_incident": "P",
    "dependency": "D",
}

# A prefix from EVIDENCE_PREFIXES, then the incident hash, then a number: L-7f3a1c2d-000412.
EvidenceId = Annotated[str, StringConstraints(pattern=r"^[LMCPD]-[0-9a-f]{8}-[0-9]+$")]

Probability = Annotated[float, Field(ge=0.0, le=1.0)]

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

NonNegativeInt = Annotated[int, Field(ge=0)]


def to_utc(value):
    """Store every time in UTC, so equal instants written with different offsets compare
    (and fingerprint) the same."""
    return value.astimezone(timezone.utc)


# Naive datetimes are rejected: a time without a zone is ambiguous in incident data.
UtcDatetime = Annotated[AwareDatetime, AfterValidator(to_utc)]


def find_duplicates(values):
    """Return the values that appear more than once, in first-seen order."""
    seen = []
    duplicates = []
    for value in values:
        if value in seen:
            if value not in duplicates:
                duplicates.append(value)
        else:
            seen.append(value)
    return duplicates
