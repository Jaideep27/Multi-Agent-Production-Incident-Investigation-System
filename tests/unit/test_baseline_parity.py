"""Step 8 gate: the single-agent baseline gets exactly the team's tools, schemas and limits."""

from investigator.agents import baseline, prompts
from investigator.models.tools import ARGS_MODELS
from investigator.runner import RunConfig


def test_baseline_has_exactly_the_five_tools():
    assert sorted(baseline.BASELINE_TOOLS) == sorted(ARGS_MODELS.keys())


def test_baseline_tools_equal_everything_the_team_can_call():
    team = []
    for agent in prompts.AGENT_TOOLS:
        for tool in prompts.AGENT_TOOLS[agent]:
            if tool not in team:
                team.append(tool)
    assert sorted(team) == sorted(baseline.BASELINE_TOOLS)


def test_baseline_prompt_describes_every_tool_the_same_way():
    system = prompts.SYSTEM_PROMPTS["investigator"]
    for line in prompts.tool_guide(sorted(ARGS_MODELS.keys())).splitlines():
        assert line in system


def test_both_systems_take_their_limits_from_one_config():
    config = RunConfig()
    # One RunConfig feeds both systems; nothing in it is per-system.
    assert "gateway" in RunConfig.model_fields
    assert "llm" in RunConfig.model_fields
    assert config.gateway.max_tool_calls == 40
    assert config.llm.max_llm_calls == 60
    for name in RunConfig.model_fields:
        assert "multi" not in name and "single" not in name
