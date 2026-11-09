"""Tool inputs, tool requests and the common tool result envelope.

Hard rule enforced here: no tool takes an incident ID. The MCP server is bound to one
incident when it starts, and every args model forbids unknown fields, so an agent cannot
point a tool at another incident.
"""

from typing import Any, Literal

from pydantic import Field, model_validator

from investigator.models.common import (
    EvidenceId,
    NonEmptyText,
    RootCauseCategory,
    StrictModel,
    ToolName,
    ToolStatus,
    UtcDatetime,
    find_duplicates,
)
from investigator.stable_hash import call_fingerprint

LogLevel = Literal["DEBUG", "INFO", "WARN", "ERROR", "FATAL"]
ChangeType = Literal["deploy", "config_change", "scaling", "infra", "feature_flag"]


def check_time_range(start, end):
    if start is not None and end is not None:
        if start >= end:
            raise ValueError("start must be before end")


# --------------------------------------------------------------------- tool inputs


class QueryLogsArgs(StrictModel):
    service: str | None = None
    levels: list[LogLevel] = Field(default_factory=list)  # empty means every level
    contains: str | None = None
    start: UtcDatetime | None = None
    end: UtcDatetime | None = None
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = None

    @model_validator(mode="after")
    def check_range(self):
        check_time_range(self.start, self.end)
        return self


class QueryMetricsArgs(StrictModel):
    service: str | None = None
    metric_name: str | None = None
    instance: str | None = None  # None means every series, fleet-wide and per instance
    start: UtcDatetime | None = None
    end: UtcDatetime | None = None
    limit: int = Field(default=20, ge=1, le=100)  # number of series

    @model_validator(mode="after")
    def check_range(self):
        check_time_range(self.start, self.end)
        return self


class GetChangeHistoryArgs(StrictModel):
    service: str | None = None
    change_type: ChangeType | None = None
    change_ids: list[EvidenceId] = Field(default_factory=list)
    include_diff: bool = False  # diffs are behind this flag so an agent has to ask
    start: UtcDatetime | None = None
    end: UtcDatetime | None = None
    limit: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def check_range(self):
        check_time_range(self.start, self.end)
        return self


class GetServiceDependenciesArgs(StrictModel):
    service: str | None = None  # None means the whole graph
    direction: Literal["upstream", "downstream", "both"] = "both"


class SearchPastIncidentsArgs(StrictModel):
    query: NonEmptyText
    category: RootCauseCategory | None = None
    limit: int = Field(default=5, ge=1, le=20)


ARGS_MODELS = {
    "query_logs": QueryLogsArgs,
    "query_metrics": QueryMetricsArgs,
    "get_change_history": GetChangeHistoryArgs,
    "get_service_dependencies": GetServiceDependenciesArgs,
    "search_past_incidents": SearchPastIncidentsArgs,
}


# -------------------------------------------------------------------- tool request


class ToolRequest(StrictModel):
    """A tool call an agent asks for. Agents return these; the gateway runs them.

    args are validated against the tool's schema and then replaced by the normalised form
    (defaults filled in, times in UTC). That way two requests for the same query get the same
    fingerprint however the agent wrote them.
    """

    tool: ToolName
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""  # free text for logs; not part of the fingerprint

    @model_validator(mode="after")
    def normalise_args(self):
        args_model = ARGS_MODELS[self.tool]
        parsed = args_model.model_validate(self.args)
        self.args = parsed.model_dump(mode="json")
        return self

    def fingerprint(self):
        return call_fingerprint(self.tool, self.args)


# --------------------------------------------------------------------- tool result


class ToolItem(StrictModel):
    evidence_id: EvidenceId
    data: dict[str, Any] = Field(default_factory=dict)


class ToolResult(StrictModel):
    """The envelope every tool returns.

    The status rules below make each status mean one thing, so the gateway and the agents
    never have to guess (for example "ok" with no items is "empty", not "ok").
    """

    call_id: NonEmptyText
    tool: ToolName
    status: ToolStatus
    items: list[ToolItem] = Field(default_factory=list)
    truncated: bool = False
    next_cursor: str | None = None
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None

    @model_validator(mode="after")
    def check_status_rules(self):
        failed = self.status == "timeout" or self.status == "error"

        if failed:
            if not self.error:
                raise ValueError("a %s result needs an error message" % self.status)
            if len(self.items) > 0:
                raise ValueError("a %s result cannot carry items" % self.status)
        else:
            if self.error is not None:
                raise ValueError("only timeout or error results carry an error message")

        if self.status == "ok" and len(self.items) == 0:
            raise ValueError("an ok result needs items; use status 'empty'")
        if self.status == "empty" and len(self.items) > 0:
            raise ValueError("an empty result cannot carry items")
        if self.status == "partial" and len(self.warnings) == 0:
            raise ValueError("a partial result must say in warnings what is missing")

        if self.next_cursor is not None and not self.truncated:
            raise ValueError("next_cursor is only allowed on a truncated result")

        evidence_ids = []
        for item in self.items:
            evidence_ids.append(item.evidence_id)
        duplicates = find_duplicates(evidence_ids)
        if len(duplicates) > 0:
            raise ValueError("duplicate evidence ids in one result: %s" % duplicates)

        return self
