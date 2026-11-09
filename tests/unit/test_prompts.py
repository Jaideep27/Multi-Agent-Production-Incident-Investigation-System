"""Prompt wording that agents depend on."""

from investigator.agents import prompts

BRIEF = {
    "summary": "checkout success rate dropped",
    "reporter": "support",
    "reported_at": "2026-10-01T10:40:00+00:00",
    "window_start": "2026-10-01T10:00:00+00:00",
    "window_end": "2026-10-01T11:00:00+00:00",
    "services": [["api", "app"], ["db", "database"]],
}


def test_brief_says_the_symptoms_started_inside_the_data_window():
    # A real run read "data window" as "the incident starts here" and searched only before it.
    text = prompts.render_brief(BRIEF)
    assert "2026-10-01T10:00:00+00:00 to 2026-10-01T11:00:00+00:00" in text
    assert "symptoms began inside this window" in text


def test_dependency_agent_is_told_to_search_the_whole_window_for_changes():
    system = prompts.SYSTEM_PROMPTS["dependency"]
    assert "whole data window" in system


def test_orchestrator_is_told_the_window_contains_the_onset():
    assert "inside the data window" in prompts.SYSTEM_PROMPTS["orchestrator"]


def all_service_names():
    from investigator.generator.topology import TOPOLOGIES
    names = []
    for topology in TOPOLOGIES.values():
        for name, role, count in topology["services"]:
            if name not in names:
                names.append(name)
    return names


def test_no_fixed_prompt_text_names_a_real_service():
    # Guards against fixes that quietly hint at an answer ("look at maps-client ...").
    from investigator.mcp_server import TOOL_DESCRIPTIONS
    texts = list(prompts.SYSTEM_PROMPTS.values()) + list(TOOL_DESCRIPTIONS.values())
    found = []
    for name in all_service_names():
        for text in texts:
            if name in text:
                found.append(name)
    assert found == []


def test_specialists_see_the_calls_already_made():
    calls = [["get_change_history", {"service": "api"}, "empty", 0],
             ["query_logs", {"levels": ["ERROR"]}, "ok", 12]]
    text = prompts.specialist_prompt(BRIEF, "find errors", 1, 3, [], [], "", calls)
    assert "TOOL CALLS ALREADY MADE IN THIS RUN" in text
    assert 'get_change_history {"service":"api"} -> empty (0 items)' in text
    assert "query_logs" in text


def test_reporting_commits_when_direct_evidence_names_the_cause():
    for agent in ["reporting", "investigator"]:
        system = prompts.SYSTEM_PROMPTS[agent]
        assert "names the cause" in system
        assert "exact service name" in system


def test_specialists_are_told_to_start_wide_and_not_repeat():
    for agent in ["observability", "dependency", "investigator"]:
        system = prompts.SYSTEM_PROMPTS[agent]
        assert "whole data window first" in system
        assert "already made" in system


def test_the_single_agent_gets_the_same_advice_on_what_to_keep():
    system = prompts.SYSTEM_PROMPTS["investigator"]
    assert "put what matters in your notes" in system
    assert "could change the conclusion" in system
