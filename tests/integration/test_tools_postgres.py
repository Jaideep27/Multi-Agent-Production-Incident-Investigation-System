"""The five tools against Postgres (agent_ro, row-level security), on the generated PG-1.

Expected values come from the built dataset, so the tests check the tools against the data
that was loaded rather than against numbers copied into the test.
"""

import psycopg
import pytest
import pytest_asyncio

from investigator import db
from investigator.generator.build import build_incident
from investigator.generator.load import delete_plan, load_incident
from investigator.generator.plan import DEFAULT_PLAN_PATH, find_entry, load_plan
from investigator.tools.failures import FailureInjector
from investigator.tools.postgres_backend import PostgresBackend, open_agent_pool
from investigator.tools.service import ToolService

pytestmark = [pytest.mark.db, pytest.mark.asyncio]


@pytest.fixture(scope="module")
def twin(pg1):
    """A second incident from the same archetype (different seed), to prove isolation."""
    entry = find_entry(load_plan(DEFAULT_PLAN_PATH), "PG-1")
    other = entry.model_copy(update={"seed": entry.seed + 77, "plan_id": "PG-98"})
    dataset = build_incident(other)
    connection = psycopg.connect(db.conninfo_for("generator_rw"))
    load_incident(connection, dataset)
    yield dataset
    delete_plan(connection, "PG-98")
    connection.close()


@pytest_asyncio.fixture
async def pool(pg_database):
    agent_pool = await open_agent_pool(min_size=1, max_size=4)
    yield agent_pool
    await agent_pool.close()


def service_for(pool, incident_id):
    backend = PostgresBackend(pool, incident_id)
    return ToolService(backend=backend, injector=FailureInjector(seed=incident_id, rules=[]))


def ids_of(result):
    ids = []
    for item in result.items:
        ids.append(item.evidence_id)
    return ids


def all_ids(dataset):
    evidence = dataset.evidence
    ids = []
    for group in [evidence.dependencies, evidence.logs, evidence.metrics, evidence.changes,
                  evidence.past_incidents]:
        for item in group:
            ids.append(item.evidence_id)
    return ids


# ------------------------------------------------------------------ query_logs


async def test_query_logs_filters_by_service_and_level(pg1, pool):
    service = service_for(pool, pg1.evidence.incident.incident_id)
    result = await service.call("query_logs", {"service": "payment-router", "levels": ["ERROR"],
                                               "limit": 200})
    expected = []
    for log in pg1.evidence.logs:
        if log.service == "payment-router" and log.level == "ERROR":
            expected.append(log.evidence_id)
    assert result.status == "ok"
    assert ids_of(result) == expected
    first = result.items[0].data
    assert first["service"] == "payment-router"
    assert first["level"] == "ERROR"
    assert "message" in first
    assert first["ts"].endswith("Z")


async def test_query_logs_pages_through_everything_with_a_cursor(pg1, pool):
    service = service_for(pool, pg1.evidence.incident.incident_id)
    collected = []
    args = {"levels": ["WARN", "ERROR"], "limit": 10}
    pages = 0
    while True:
        result = await service.call("query_logs", args)
        pages = pages + 1
        collected.extend(ids_of(result))
        if not result.truncated:
            assert result.next_cursor is None
            break
        assert len(result.items) == 10
        args = {"levels": ["WARN", "ERROR"], "limit": 10, "cursor": result.next_cursor}

    expected = []
    for log in pg1.evidence.logs:
        if log.level == "WARN" or log.level == "ERROR":
            expected.append(log.evidence_id)
    assert collected == expected
    assert pages > 5


async def test_query_logs_contains_is_a_case_insensitive_substring(pg1, pool):
    service = service_for(pool, pg1.evidence.incident.incident_id)
    result = await service.call("query_logs", {"contains": "WAITING FOR A DATABASE", "limit": 200})
    assert result.status == "ok"
    for item in result.items:
        assert "waiting for a database" in item.data["message"]


async def test_query_logs_treats_wildcards_and_quotes_literally(pg1, pool):
    service = service_for(pool, pg1.evidence.incident.incident_id)
    for text in ["%", "'; DROP TABLE evidence.log_entries; --", "\\"]:
        result = await service.call("query_logs", {"contains": text})
        assert result.status == "empty", text
    still_there = await service.call("query_logs", {"limit": 1})
    assert still_there.status == "ok"


async def test_query_logs_time_range_is_start_inclusive_end_exclusive(pg1, pool):
    logs = pg1.evidence.logs
    start = logs[10].ts
    end = logs[40].ts
    service = service_for(pool, pg1.evidence.incident.incident_id)
    result = await service.call("query_logs", {"start": start.isoformat(), "end": end.isoformat(),
                                               "limit": 200})
    expected = []
    for log in logs:
        if start <= log.ts < end:
            expected.append(log.evidence_id)
    assert ids_of(result) == expected


async def test_unknown_service_is_empty_with_a_helpful_warning(pg1, pool):
    service = service_for(pool, pg1.evidence.incident.incident_id)
    result = await service.call("query_logs", {"service": "payments-router"})
    assert result.status == "empty"
    assert "payment-router" in result.warnings[0]


async def test_invalid_cursor_is_an_error(pg1, pool):
    service = service_for(pool, pg1.evidence.incident.incident_id)
    result = await service.call("query_logs", {"cursor": "not-a-cursor"})
    assert result.status == "error"
    assert "cursor" in result.error


# ------------------------------------------------------------------ query_metrics


def dataset_metric(dataset, service_name, metric_name):
    for metric in dataset.evidence.metrics:
        if metric.service == service_name and metric.metric_name == metric_name:
            return metric
    raise AssertionError("no such metric")


async def test_query_metrics_returns_points_and_stats(pg1, pool):
    service = service_for(pool, pg1.evidence.incident.incident_id)
    result = await service.call("query_metrics", {"service": "payment-router",
                                                  "metric_name": "db_pool_in_use"})
    expected = dataset_metric(pg1, "payment-router", "db_pool_in_use")
    assert ids_of(result) == [expected.evidence_id]
    data = result.items[0].data
    values = []
    for ts, value in data["points"]:
        values.append(value)
    expected_values = []
    for point in expected.points:
        expected_values.append(point.value)
    assert values == expected_values
    assert data["stats"]["max"] == max(expected_values)
    assert data["unit"] == "connections"


async def test_query_metrics_time_range_narrows_the_points(pg1, pool):
    expected = dataset_metric(pg1, "payment-api", "upi_success_rate_pct")
    start = expected.points[10].ts
    end = expected.points[20].ts
    service = service_for(pool, pg1.evidence.incident.incident_id)
    result = await service.call("query_metrics", {"service": "payment-api",
                                                  "metric_name": "upi_success_rate_pct",
                                                  "start": start.isoformat(),
                                                  "end": end.isoformat()})
    assert len(result.items[0].data["points"]) == 10


async def test_unknown_metric_name_is_empty_with_the_real_names(pg1, pool):
    service = service_for(pool, pg1.evidence.incident.incident_id)
    result = await service.call("query_metrics", {"service": "payment-api",
                                                  "metric_name": "error_rate"})
    assert result.status == "empty"
    expected = []
    for metric in pg1.evidence.metrics:
        if metric.service == "payment-api":
            expected.append(metric.metric_name)
    warning = result.warnings[0]
    assert "error_rate" in warning
    for name in expected:
        assert name in warning


async def test_unknown_metric_name_without_a_service_lists_all_names(pg1, pool):
    service = service_for(pool, pg1.evidence.incident.incident_id)
    result = await service.call("query_metrics", {"metric_name": "nonexistent_metric"})
    assert result.status == "empty"
    assert "db_pool_in_use" in result.warnings[0]


async def test_query_metrics_limit_truncates(pg1, pool):
    service = service_for(pool, pg1.evidence.incident.incident_id)
    result = await service.call("query_metrics", {"limit": 3})
    assert len(result.items) == 3
    assert result.truncated is True


# ------------------------------------------------------------------ get_change_history


async def test_change_history_hides_the_diff_unless_asked(pg1, pool):
    service = service_for(pool, pg1.evidence.incident.incident_id)
    plain = await service.call("get_change_history", {})
    detailed = await service.call("get_change_history", {"include_diff": True})

    expected_ids = []
    expected_diffs = {}
    for change in pg1.evidence.changes:
        expected_ids.append(change.evidence_id)
        expected_diffs[change.evidence_id] = change.diff
    assert ids_of(plain) == expected_ids
    for item in plain.items:
        assert "diff" not in item.data
    for item in detailed.items:
        assert item.data["diff"] == expected_diffs[item.evidence_id]


async def test_change_history_filters_by_ids_and_type(pg1, pool):
    service = service_for(pool, pg1.evidence.incident.incident_id)
    deploys = await service.call("get_change_history", {"change_type": "deploy"})
    for item in deploys.items:
        assert item.data["change_type"] == "deploy"
    one_id = pg1.evidence.changes[-1].evidence_id
    by_id = await service.call("get_change_history", {"change_ids": [one_id]})
    assert ids_of(by_id) == [one_id]


# ------------------------------------------------------------------ get_service_dependencies


async def test_dependencies_by_direction(pg1, pool):
    service = service_for(pool, pg1.evidence.incident.incident_id)
    whole = await service.call("get_service_dependencies", {})
    downstream = await service.call("get_service_dependencies",
                                    {"service": "payment-router", "direction": "downstream"})
    upstream = await service.call("get_service_dependencies",
                                  {"service": "payment-router", "direction": "upstream"})
    both = await service.call("get_service_dependencies", {"service": "payment-router"})

    assert len(whole.items) == len(pg1.evidence.dependencies)
    callees = []
    callers = []
    for edge in pg1.evidence.dependencies:
        if edge.caller == "payment-router":
            callees.append(edge.callee)
        if edge.callee == "payment-router":
            callers.append(edge.caller)

    found_callees = []
    for item in downstream.items:
        assert item.data["caller"] == "payment-router"
        found_callees.append(item.data["callee"])
    found_callers = []
    for item in upstream.items:
        assert item.data["callee"] == "payment-router"
        found_callers.append(item.data["caller"])
    assert sorted(found_callees) == sorted(callees)
    assert sorted(found_callers) == sorted(callers)
    assert len(both.items) == len(callees) + len(callers)


async def test_dependency_items_carry_their_config(pg1, pool):
    service = service_for(pool, pg1.evidence.incident.incident_id)
    result = await service.call("get_service_dependencies",
                                {"service": "payment-router", "direction": "downstream"})
    configs = {}
    for item in result.items:
        assert item.evidence_id.startswith("D-")
        configs[item.data["callee"]] = item.data["config"]
    assert configs["postgres-payments"]["max_pool"] == 20


# ------------------------------------------------------------------ search_past_incidents


async def test_search_ranks_the_best_match_first(pg1, pool):
    service = service_for(pool, pg1.evidence.incident.incident_id)
    result = await service.call("search_past_incidents", {"query": "UPI success rate drop"})
    assert result.status == "ok"
    assert result.items[0].data["title"].startswith("UPI failures")
    scores = []
    for item in result.items:
        scores.append(item.data["match_score"])
    assert scores == sorted(scores, reverse=True)


async def test_search_filters_by_category(pg1, pool):
    service = service_for(pool, pg1.evidence.incident.incident_id)
    result = await service.call("search_past_incidents",
                                {"query": "errors delays evictions", "category": "cache_failure"})
    for item in result.items:
        assert item.data["root_cause_category"] == "cache_failure"


async def test_search_with_no_matching_words_is_empty(pg1, pool):
    service = service_for(pool, pg1.evidence.incident.incident_id)
    result = await service.call("search_past_incidents", {"query": "zebra quantum"})
    assert result.status == "empty"


# ------------------------------------------------------------------ isolation


async def test_every_returned_id_belongs_to_the_bound_incident(pg1, twin, pool):
    service = service_for(pool, pg1.evidence.incident.incident_id)
    own_ids = all_ids(pg1)
    calls = [
        ["query_logs", {"limit": 200}],
        ["query_metrics", {"limit": 100}],
        ["get_change_history", {"limit": 200}],
        ["get_service_dependencies", {}],
        ["search_past_incidents", {"query": "errors drop timeouts delays evictions"}],
    ]
    returned = []
    for tool, args in calls:
        result = await service.call(tool, args)
        assert result.status == "ok", tool
        returned.extend(ids_of(result))
    for evidence_id in returned:
        assert evidence_id in own_ids


async def test_two_incidents_never_mix(pg1, twin, pool):
    first = service_for(pool, pg1.evidence.incident.incident_id)
    second = service_for(pool, twin.evidence.incident.incident_id)
    first_result = await first.call("get_service_dependencies", {})
    second_result = await second.call("get_service_dependencies", {})
    first_hash = pg1.evidence.incident.incident_id[len("inc_"):]
    second_hash = twin.evidence.incident.incident_id[len("inc_"):]
    for evidence_id in ids_of(first_result):
        assert evidence_id.split("-")[1] == first_hash
    for evidence_id in ids_of(second_result):
        assert evidence_id.split("-")[1] == second_hash


async def test_backend_reads_the_incidents_failure_rules(pg1, pool):
    backend = PostgresBackend(pool, pg1.evidence.incident.incident_id)
    rules = await backend.failure_rules()
    assert rules == [["query_logs", {"mode": "transient", "fail_first_n": 1, "status": "timeout"}]]


async def test_backend_refuses_an_incident_that_does_not_exist(pool):
    backend = PostgresBackend(pool, "inc_0badf00d")
    with pytest.raises(LookupError):
        await backend.check_incident_exists()
