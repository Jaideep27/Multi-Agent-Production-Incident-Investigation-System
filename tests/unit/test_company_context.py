"""The company background that reruns give the agents: general, and never the answer."""

from investigator.agents import prompts
from investigator.batch import config_from_args, parse_args
from investigator.company_context import (DEFAULT_CONTEXT_PATH, add_background,
                                          load_company_contexts, render_background)
from investigator.generator.plan import DEFAULT_PLAN_PATH, load_plan
from investigator.runner import RunConfig

# Words that would describe a fault. The background describes a healthy system only.
FAULT_WORDS = ["slow", "stuck", "fail", "broke", "outage", "error", "crash", "root cause", "flag",
               "regex", "expired", "timeout", "overload"]

BRIEF = {
    "summary": "checkout success rate dropped",
    "reporter": "support",
    "reported_at": "2026-10-01T10:40:00+00:00",
    "window_start": "2026-10-01T10:00:00+00:00",
    "window_end": "2026-10-01T11:00:00+00:00",
    "services": [["api", "app"], ["db", "database"]],
}
COMPANIES = {"shop": {"name": "Acme", "what_it_does": "Acme sells things.",
                      "how_it_works": "api calls db.",
                      "services": [{"name": "api", "plain": "Takes orders."}]}}


def test_official_runs_do_not_get_the_background():
    assert RunConfig().company_context is False
    assert add_background(dict(BRIEF), "shop", False, COMPANIES) == BRIEF


def test_a_rerun_gets_the_background_of_its_company():
    brief = add_background(dict(BRIEF), "shop", True, COMPANIES)
    assert "Acme sells things." in brief["background"]
    assert "api: Takes orders." in brief["background"]


def test_a_company_without_a_background_leaves_the_brief_alone():
    assert add_background(dict(BRIEF), "elsewhere", True, COMPANIES) == BRIEF


def test_the_prompt_is_unchanged_without_a_background():
    # Official prompts must stay byte-identical: their recorded answers are keyed on them.
    assert "BACKGROUND" not in prompts.render_brief(BRIEF)


def test_every_agent_sees_the_background_through_the_brief():
    brief = add_background(dict(BRIEF), "shop", True, COMPANIES)
    text = prompts.render_brief(brief)
    assert "COMPANY BACKGROUND" in text
    assert "Acme sells things." in text


def test_the_background_describes_only_a_healthy_system():
    companies = load_company_contexts()
    assert companies
    for domain in companies:
        text = render_background(companies[domain]).lower()
        for word in FAULT_WORDS:
            assert word not in text, "%s background says %r" % (domain, word)


def test_the_background_carries_no_detail_of_any_incident():
    companies = load_company_contexts()
    plan = load_plan(DEFAULT_PLAN_PATH)
    for entry in plan.incidents:
        if entry.domain not in companies:
            continue
        text = render_background(companies[entry.domain]).lower()
        service_names = []
        for service in companies[entry.domain]["services"]:
            service_names.append(service["name"])
        for value in entry.params.values():
            if isinstance(value, str) and value not in service_names:
                assert value.lower() not in text, "%s leaks %r" % (entry.plan_id, value)


def test_the_background_file_names_no_plan_id_or_archetype():
    text = DEFAULT_CONTEXT_PATH.read_text(encoding="utf-8")
    plan = load_plan(DEFAULT_PLAN_PATH)
    for entry in plan.incidents:
        assert entry.plan_id not in text
        assert entry.archetype not in text


def test_batch_turns_the_background_on_only_when_asked():
    plain = parse_args(["--label", "x", "--incidents", "inc_00000001"])
    assert config_from_args(plain).company_context is False
    with_context = parse_args(["--label", "x", "--incidents", "inc_00000001",
                               "--company-context"])
    assert config_from_args(with_context).company_context is True
