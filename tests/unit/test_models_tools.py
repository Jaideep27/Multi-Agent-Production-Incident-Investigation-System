"""Tests for the shared vocabulary and the tool boundary models (investigator.models.common,
investigator.models.tools)."""

import re
import typing
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from investigator.models.common import EVIDENCE_PREFIXES, EvidenceId, RootCauseCategory, ToolName
from investigator.models.tools import ARGS_MODELS, ToolRequest, ToolResult
from investigator.stable_hash import call_fingerprint

SQL_DIR = Path(__file__).resolve().parents[2] / "db" / "init"


def quoted_words(text):
    return re.findall(r"'([a-z_]+)'", text)


# ------------------------------------------------------- vocabulary matches the SQL


def test_categories_match_the_sql_domain():
    sql = (SQL_DIR / "003_evidence.sql").read_text(encoding="utf-8")
    block = sql.split("CREATE DOMAIN evidence.root_cause_category")[1].split(";")[0]
    assert sorted(quoted_words(block)) == sorted(typing.get_args(RootCauseCategory))


def test_tool_names_match_the_sql_check():
    sql = (SQL_DIR / "005_eval.sql").read_text(encoding="utf-8")
    block = sql.split("CREATE TABLE eval.tool_calls")[1].split("args")[0]
    assert sorted(quoted_words(block)) == sorted(typing.get_args(ToolName))


def test_every_tool_has_an_args_model():
    assert sorted(ARGS_MODELS.keys()) == sorted(typing.get_args(ToolName))


# ------------------------------------------------------------------- evidence ids


def test_evidence_prefixes_match_the_sql_check():
    # The evidence_index CHECK pairs each kind with a prefix: (kind = 'log' AND evidence_id LIKE 'L-%')
    sql = (SQL_DIR / "003_evidence.sql").read_text(encoding="utf-8")
    block = sql.split("CREATE TABLE evidence.evidence_index")[1].split(";")[0]
    pairs = re.findall(r"kind = '([a-z_]+)'\s+AND evidence_id LIKE '([A-Z])-%'", block)
    from_sql = {}
    for kind, prefix in pairs:
        from_sql[kind] = prefix
    assert from_sql == EVIDENCE_PREFIXES


def test_evidence_id_accepts_every_known_prefix():
    for kind in EVIDENCE_PREFIXES:
        value = EVIDENCE_PREFIXES[kind] + "-aaaaaaaa-1"
        assert TypeAdapter(EvidenceId).validate_python(value) == value


@pytest.mark.parametrize(
    "value", ["L-aaaaaaaa-1", "M-7f3a1c2d-000412", "C-0123abcd-9", "P-ffffffff-10", "D-aaaaaaaa-2"]
)
def test_evidence_id_accepts_the_generator_format(value):
    assert TypeAdapter(EvidenceId).validate_python(value) == value


@pytest.mark.parametrize(
    "value",
    ["X-aaaaaaaa-1", "L-AAAAAAAA-1", "L-aaaa-1", "L-aaaaaaaa-", "L-aaaaaaaa-1 ", "inc_aaaaaaaa", ""],
)
def test_evidence_id_rejects_other_strings(value):
    with pytest.raises(ValidationError):
        TypeAdapter(EvidenceId).validate_python(value)


# -------------------------------------------------------------------- tool inputs


# The smallest valid args for each tool. The rejection tests below add one bad field to
# these, so a rejection can only come from that field (not from a missing required one).
MINIMAL_ARGS = {
    "query_logs": {},
    "query_metrics": {},
    "get_change_history": {},
    "get_service_dependencies": {},
    "search_past_incidents": {"query": "timeouts"},
}


def args_with(tool, key, value):
    args = dict(MINIMAL_ARGS[tool])
    args[key] = value
    return args


@pytest.mark.parametrize("tool", sorted(ARGS_MODELS.keys()))
def test_minimal_args_are_valid(tool):
    ARGS_MODELS[tool].model_validate(MINIMAL_ARGS[tool])


@pytest.mark.parametrize("tool", sorted(ARGS_MODELS.keys()))
def test_no_tool_accepts_an_incident_id(tool):
    # Hard rule: the incident is bound by the server, never passed as a tool argument.
    with pytest.raises(ValidationError):
        ARGS_MODELS[tool].model_validate(args_with(tool, "incident_id", "inc_aaaaaaaa"))


@pytest.mark.parametrize("tool", sorted(ARGS_MODELS.keys()))
def test_no_tool_accepts_unknown_arguments(tool):
    with pytest.raises(ValidationError):
        ARGS_MODELS[tool].model_validate(args_with(tool, "sql", "select 1"))


@pytest.mark.parametrize("limit", [0, 201])
def test_query_logs_limit_is_bounded(limit):
    with pytest.raises(ValidationError):
        ARGS_MODELS["query_logs"].model_validate({"limit": limit})


def test_times_must_carry_a_timezone():
    with pytest.raises(ValidationError):
        ARGS_MODELS["query_logs"].model_validate({"start": "2026-11-01T10:00:00"})


def test_start_must_be_before_end():
    with pytest.raises(ValidationError):
        ARGS_MODELS["query_logs"].model_validate(
            {"start": "2026-11-01T11:00:00Z", "end": "2026-11-01T10:00:00Z"}
        )


def test_past_incident_search_needs_a_query():
    with pytest.raises(ValidationError):
        ARGS_MODELS["search_past_incidents"].model_validate({"query": "   "})


# ------------------------------------------------------------------- tool requests


def test_tool_request_rejects_an_unknown_tool():
    with pytest.raises(ValidationError):
        ToolRequest.model_validate({"tool": "run_sql", "args": {}})


def test_tool_request_checks_args_against_the_tool_schema():
    with pytest.raises(ValidationError):
        ToolRequest.model_validate({"tool": "query_logs", "args": {"limit": "lots"}})


def test_tool_request_rejects_an_incident_id_in_args():
    with pytest.raises(ValidationError):
        ToolRequest.model_validate(
            {"tool": "query_logs", "args": {"incident_id": "inc_aaaaaaaa"}}
        )


def test_fingerprint_is_the_same_whether_defaults_are_written_or_omitted():
    short = ToolRequest.model_validate({"tool": "query_logs", "args": {"service": "api"}})
    full = ToolRequest.model_validate(
        {"tool": "query_logs", "args": {"service": "api", "limit": 50, "levels": []}}
    )
    assert short.fingerprint() == full.fingerprint()


def test_fingerprint_is_the_same_for_equal_times_in_different_offsets():
    utc = ToolRequest.model_validate(
        {"tool": "query_logs", "args": {"start": "2026-11-01T10:00:00Z"}}
    )
    india = ToolRequest.model_validate(
        {"tool": "query_logs", "args": {"start": "2026-11-01T15:30:00+05:30"}}
    )
    assert utc.fingerprint() == india.fingerprint()


def test_fingerprint_ignores_the_reason_text():
    first = ToolRequest.model_validate(
        {"tool": "query_metrics", "args": {"service": "api"}, "reason": "check CPU"}
    )
    second = ToolRequest.model_validate(
        {"tool": "query_metrics", "args": {"service": "api"}, "reason": "look at latency"}
    )
    assert first.fingerprint() == second.fingerprint()


def test_fingerprint_differs_for_different_queries():
    first = ToolRequest.model_validate({"tool": "query_logs", "args": {"service": "api"}})
    second = ToolRequest.model_validate({"tool": "query_logs", "args": {"service": "db"}})
    assert first.fingerprint() != second.fingerprint()


def test_fingerprint_is_built_from_the_normalised_args():
    request = ToolRequest.model_validate(
        {"tool": "get_service_dependencies", "args": {"service": "api"}}
    )
    assert request.args == {"service": "api", "direction": "both"}
    assert request.fingerprint() == call_fingerprint("get_service_dependencies", request.args)


# -------------------------------------------------------------------- tool results


def result(**fields):
    base = {"call_id": "call-1", "tool": "query_logs"}
    for key in fields:
        base[key] = fields[key]
    return ToolResult.model_validate(base)


def item(evidence_id):
    return {"evidence_id": evidence_id, "data": {"message": "x"}}


def test_ok_result_with_items_is_valid():
    parsed = result(status="ok", items=[item("L-aaaaaaaa-1"), item("L-aaaaaaaa-2")])
    assert len(parsed.items) == 2


def test_ok_result_must_have_items():
    with pytest.raises(ValidationError):
        result(status="ok", items=[])


def test_empty_result_must_have_no_items():
    with pytest.raises(ValidationError):
        result(status="empty", items=[item("L-aaaaaaaa-1")])


@pytest.mark.parametrize("status", ["timeout", "error"])
def test_failed_result_needs_an_error_message_and_no_items(status):
    with pytest.raises(ValidationError):
        result(status=status)
    with pytest.raises(ValidationError):
        result(status=status, error="boom", items=[item("L-aaaaaaaa-1")])
    assert result(status=status, error="boom").error == "boom"


@pytest.mark.parametrize("status", ["ok", "empty", "partial"])
def test_non_failed_result_has_no_error_message(status):
    items = [item("L-aaaaaaaa-1")]
    if status == "empty":
        items = []
    with pytest.raises(ValidationError):
        result(status=status, items=items, warnings=["w"], error="boom")


def test_partial_result_must_say_why():
    with pytest.raises(ValidationError):
        result(status="partial", items=[item("L-aaaaaaaa-1")])
    parsed = result(status="partial", items=[item("L-aaaaaaaa-1")], warnings=["lines dropped"])
    assert parsed.warnings == ["lines dropped"]


def test_result_rejects_duplicate_evidence_ids():
    with pytest.raises(ValidationError):
        result(status="ok", items=[item("L-aaaaaaaa-1"), item("L-aaaaaaaa-1")])


def test_next_cursor_requires_truncated():
    with pytest.raises(ValidationError):
        result(status="ok", items=[item("L-aaaaaaaa-1")], next_cursor="abc")
    parsed = result(status="ok", items=[item("L-aaaaaaaa-1")], next_cursor="abc", truncated=True)
    assert parsed.truncated is True


def test_result_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        result(status="ok", items=[item("L-aaaaaaaa-1")], answer="the root cause")
