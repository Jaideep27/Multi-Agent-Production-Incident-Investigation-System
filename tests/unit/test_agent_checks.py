"""Hypothesis components must be exact service names from the incident brief."""

from investigator.agents.checks import component_problems

SERVICES = [["maps-client", "app"], ["order-service", "app"]]


def test_exact_names_pass():
    assert component_problems(["maps-client", "order-service"], SERVICES) == []


def test_a_sentence_instead_of_a_name_is_a_problem_that_lists_the_real_names():
    problems = component_problems(["maps-client (origin); dispatch path"], SERVICES)
    assert len(problems) == 1
    assert "maps-client, order-service" in problems[0]


def test_case_and_spacing_must_match_too():
    assert len(component_problems([" Maps-Client"], SERVICES)) == 1
