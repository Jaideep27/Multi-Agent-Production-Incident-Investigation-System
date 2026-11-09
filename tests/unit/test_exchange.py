"""The agent-to-agent pattern: who speaks next, what a message does, and that it always ends."""

import asyncio

import pytest
from pydantic import ValidationError

from investigator.agents import exchange, prompts
from investigator.agents.conversation import Conversation
from investigator.agents.outputs import AgentMessage, SpecialistTurn
from investigator.agents.team import RunContext
from investigator.batch import config_from_args, parse_args
from investigator.blackboard.store import Blackboard
from investigator.llm.client import LlmOutputError
from investigator.runner import RunConfig

NO_MAIL = {"orchestrator": False, "observability": False, "dependency": False, "reasoning": False,
           "critic": False}


def speakers(**changes):
    settings = {"mail": dict(NO_MAIL), "pending_tasks": False, "new_facts": False,
                "has_ranking": True, "reasoning_turns": 1, "unreviewed_ranking": False,
                "approved": False, "reviews": 1, "max_reviews": 3, "stopped": False}
    settings.update(changes)
    return exchange.next_speakers(**settings)


def mail_for(agent):
    mail = dict(NO_MAIL)
    mail[agent] = True
    return mail


def message(to, text="what changed?", intent="question", evidence_ids=None):
    return AgentMessage(to=to, intent=intent, text=text, evidence_ids=evidence_ids or [])


# ------------------------------------------------------------------ who speaks next


def test_with_nothing_left_to_say_the_team_reports():
    assert speakers() == "reporting"


def test_approval_ends_the_conversation_even_with_mail_waiting():
    assert speakers(approved=True, mail=mail_for("reasoning")) == "reporting"


def test_a_spent_budget_ends_the_conversation():
    assert speakers(stopped=True, mail=mail_for("critic"), pending_tasks=True) == "reporting"


def test_specialists_with_mail_or_tasks_go_first():
    assert speakers(mail=mail_for("dependency"), new_facts=True) == "specialists"
    assert speakers(pending_tasks=True, unreviewed_ranking=True) == "specialists"


def test_new_facts_or_a_question_bring_reasoning_back():
    assert speakers(new_facts=True) == "reasoning"
    assert speakers(mail=mail_for("reasoning")) == "reasoning"


def test_a_new_ranking_or_a_reply_brings_the_critic_back():
    assert speakers(unreviewed_ranking=True) == "critic"
    assert speakers(mail=mail_for("critic")) == "critic"


def test_the_critic_stops_after_its_last_review():
    assert speakers(unreviewed_ranking=True, reviews=3) == "reporting"


def test_a_failed_reasoning_turn_is_not_retried_forever():
    assert speakers(has_ranking=False, reasoning_turns=0) == "reasoning"
    assert speakers(has_ranking=False, reasoning_turns=1) == "reporting"


def test_the_orchestrator_can_be_asked_to_replan():
    assert speakers(mail=mail_for("orchestrator"), pending_tasks=True) == "orchestrator"


# ------------------------------------------------------------------ messages


def make_conversation(tmp_path, limit=6):
    board = Blackboard.create(tmp_path / "board.sqlite")
    return Conversation(board, max_messages=limit), board


def test_a_message_reaches_its_recipient_once(tmp_path):
    conversation, _ = make_conversation(tmp_path)
    conversation.post("observability", [message("dependency")], 1)
    assert conversation.has_mail("dependency")
    delivered = conversation.take("dependency")
    assert delivered[0].sender == "observability"
    assert conversation.take("dependency") == []
    assert not conversation.has_mail("dependency")


def test_every_message_is_kept_on_the_blackboard(tmp_path):
    conversation, board = make_conversation(tmp_path)
    conversation.post("critic", [message("reasoning", "why not the deploy?", "challenge")], 2)
    saved = board.messages()
    assert saved[0]["sender"] == "critic"
    assert saved[0]["recipient"] == "reasoning"
    assert saved[0]["intent"] == "challenge"
    assert saved[0]["round"] == 2


def test_the_message_limit_holds(tmp_path):
    conversation, board = make_conversation(tmp_path, limit=2)
    conversation.post("critic", [message("reasoning"), message("dependency"), message("observability")], 1)
    assert conversation.sent == 2
    assert conversation.dropped == 1
    assert not conversation.has_mail("observability")
    assert len(board.messages()) == 2


def test_an_agent_cannot_message_itself(tmp_path):
    conversation, _ = make_conversation(tmp_path)
    conversation.post("critic", [message("critic")], 1)
    assert conversation.sent == 0
    assert not conversation.has_mail("critic")


def test_a_message_needs_text():
    with pytest.raises(ValidationError):
        AgentMessage(to="critic", intent="question", text="")


# ------------------------------------------------------------------ the prompts


def test_the_blackboard_pattern_stays_the_default():
    assert RunConfig().pattern == "blackboard"
    plain = parse_args(["--label", "x", "--incidents", "inc_00000001"])
    assert config_from_args(plain).pattern == "blackboard"
    chosen = parse_args(["--label", "x", "--incidents", "inc_00000001", "--pattern", "exchange"])
    assert config_from_args(chosen).pattern == "exchange"


def test_only_the_exchange_prompts_offer_messages():
    assert "messages" not in prompts.SYSTEM_PROMPTS["critic"]
    assert "messages" in prompts.EXCHANGE_SYSTEM_PROMPTS["critic"]
    assert "AgentMessage" in prompts.EXCHANGE_SYSTEM_PROMPTS["critic"]


def test_all_six_agents_are_in_the_exchange():
    for agent in ["orchestrator", "observability", "dependency", "reasoning", "critic", "reporting"]:
        assert ("You are the %s agent" % agent.capitalize()) in prompts.EXCHANGE_SYSTEM_PROMPTS[agent]


def test_the_two_patterns_share_the_same_guidance_on_tools():
    for prompt_set in [prompts.SYSTEM_PROMPTS, prompts.EXCHANGE_SYSTEM_PROMPTS]:
        for agent in ["observability", "dependency"]:
            assert prompts.TOOL_JUDGEMENT in prompt_set[agent]


def test_a_prompt_without_mail_has_no_messages_section():
    brief = {"services": [["api", "app"]], "reported_at": "t0", "reporter": "oncall",
             "summary": "errors", "window_start": "t-1", "window_end": "t1"}
    assert "MESSAGES FOR YOU" not in prompts.reasoning_prompt(brief, [], [], [], [])


# ------------------------------------------------------------------ a whole conversation


class FakeGateway:
    logical_calls = 0

    class limits:
        max_tool_calls = 40

    def calls_made(self):
        return []

    async def execute(self, requests):
        return []


class ScriptedLlm:
    """Answers each agent from its own list and records every prompt it was sent."""

    def __init__(self, script):
        self.script = script
        self.calls = []

    async def structured(self, agent, system, prompt, output_model, check=None):
        self.calls.append([agent, prompt])
        if not self.script.get(agent):
            raise LlmOutputError("no scripted answer", attempts=1)
        output = output_model.model_validate(self.script[agent].pop(0))
        if check is not None:
            assert check(output) == []
        return output

    def prompts_of(self, agent):
        found = []
        for name, prompt in self.calls:
            if name == agent:
                found.append(prompt)
        return found

    def order(self):
        names = []
        for name, _ in self.calls:
            names.append(name)
        return names


def hypothesis(category, confidence):
    return {"component": "api", "category": category, "mechanism": "m", "confidence": confidence}


def run_script(tmp_path, script):
    board = Blackboard.create(tmp_path / "run.sqlite")
    brief = {"services": [["api", "app"]], "reported_at": "t0", "reporter": "oncall",
             "summary": "errors", "window_start": "t-1", "window_end": "t1"}
    llm = ScriptedLlm(script)
    ctx = RunContext(brief=brief, llm=llm, gateway=FakeGateway(), board=board, max_rounds=3,
                     max_specialist_steps=3)
    exchange.configure(ctx)
    rounds = asyncio.run(exchange.run_exchange(ctx))
    return ctx, llm, rounds


def base_script():
    ranking = {"hypotheses": [hypothesis("bad_deploy", 0.6), hypothesis("config_change", 0.2)]}
    return {
        "orchestrator": [{"tasks": [{"agent": "dependency", "goal": "changes"},
                                    {"agent": "observability", "goal": "errors"}]}],
        "dependency": [{"done": True}],
        "observability": [{"done": True}],
        "reasoning": [ranking],
        "critic": [{"approved": True}],
    }


def test_with_no_messages_the_team_goes_straight_through(tmp_path):
    ctx, llm, rounds = run_script(tmp_path, base_script())
    assert llm.order() == ["orchestrator", "dependency", "observability", "reasoning", "critic",
                           "reporting"]
    assert rounds == 1
    assert ctx.status == "completed"
    assert ctx.board.messages() == []


def test_a_question_wakes_a_finished_colleague_who_can_answer(tmp_path):
    script = base_script()
    script["observability"] = [{"done": True, "messages": [
        {"to": "dependency", "intent": "question", "text": "what changed just before the errors?"}]}]
    script["dependency"].append({"done": True, "messages": [
        {"to": "reasoning", "intent": "answer", "text": "one change, a minute before"}]})
    ctx, llm, _ = run_script(tmp_path, script)
    assert llm.order().count("dependency") == 2
    assert "what changed just before the errors?" in llm.prompts_of("dependency")[1]
    assert "one change, a minute before" in llm.prompts_of("reasoning")[0]
    assert ctx.stats["messages_sent"] == 2


def test_a_challenge_makes_reasoning_answer_and_the_critic_look_again(tmp_path):
    script = base_script()
    revised = {"hypotheses": [hypothesis("config_change", 0.7), hypothesis("bad_deploy", 0.2)]}
    script["reasoning"].append(revised)
    script["critic"] = [
        {"approved": False, "messages": [{"to": "reasoning", "intent": "challenge",
                                          "text": "the release came after the errors"}]},
        {"approved": True},
    ]
    ctx, llm, rounds = run_script(tmp_path, script)
    assert llm.order() == ["orchestrator", "dependency", "observability", "reasoning", "critic",
                           "reasoning", "critic", "reporting"]
    assert "the release came after the errors" in llm.prompts_of("reasoning")[1]
    assert rounds == 2
    assert ctx.stats["top_hypothesis_changes"] == 1
    assert ctx.status == "completed"


def test_two_agents_cannot_argue_forever(tmp_path):
    script = base_script()
    ranking = script["reasoning"][0]
    objection = {"approved": False, "messages": [{"to": "reasoning", "intent": "challenge",
                                                  "text": "not convinced"}]}
    script["reasoning"] = []
    script["critic"] = []
    for _ in range(50):
        script["reasoning"].append(dict(ranking, messages=[
            {"to": "critic", "intent": "answer", "text": "here is why"}]))
        script["critic"].append(objection)
    ctx, llm, rounds = run_script(tmp_path, script)
    assert llm.order().count("critic") == 3
    assert llm.order()[-1] == "reporting"
    assert rounds == 3
    assert ctx.status in ["completed", "max_rounds"]


def test_a_message_cannot_cite_evidence_the_run_never_saw(tmp_path):
    board = Blackboard.create(tmp_path / "run.sqlite")
    ctx = RunContext(brief={"services": []}, llm=None, gateway=None, board=board, max_rounds=3,
                     max_specialist_steps=3)
    exchange.configure(ctx)
    from investigator.agents.team import message_problems
    output = SpecialistTurn(messages=[{"to": "critic", "intent": "finding", "text": "see this",
                                       "evidence_ids": ["L-1a2b3c4d-000042"]}])
    problems = message_problems(ctx, "observability", output)
    assert len(problems) == 1
    assert "L-1a2b3c4d-000042" in problems[0]
    board.record_evidence(["L-1a2b3c4d-000042"])
    assert message_problems(ctx, "observability", output) == []


def test_a_specialist_keeps_the_question_on_every_step_of_its_reply(tmp_path):
    script = base_script()
    script["observability"] = [{"done": True, "messages": [
        {"to": "dependency", "intent": "question", "text": "who changed it?"}]}]
    script["dependency"].append({"done": False, "tool_requests": [{"tool": "get_service_dependencies", "args": {}}]})
    script["dependency"].append({"done": True})
    _, llm, _ = run_script(tmp_path, script)
    replies = llm.prompts_of("dependency")[1:]
    assert len(replies) == 2
    for prompt in replies:
        assert "who changed it?" in prompt


def test_a_specialist_keeps_earlier_mail_during_a_longer_task(tmp_path):
    script = base_script()
    script["orchestrator"] = [{"tasks": [{"agent": "observability", "goal": "errors"},
                                         {"agent": "dependency", "goal": "changes"}]}]
    script["observability"] = [{"done": True, "messages": [
        {"to": "dependency", "intent": "finding", "text": "errors begin at one service"}]}]
    script["dependency"] = [
        {"done": False, "tool_requests": [{"tool": "get_service_dependencies", "args": {}}]},
        {"done": True},
    ]
    _, llm, _ = run_script(tmp_path, script)
    prompts_seen = llm.prompts_of("dependency")
    assert len(prompts_seen) == 2
    assert "errors begin at one service" in prompts_seen[0]
    assert "errors begin at one service" in prompts_seen[1]


# ------------------------------------------------------------------ keeping the talk useful


def test_specialists_cannot_use_up_the_whole_message_budget(tmp_path):
    conversation, board = make_conversation(tmp_path)
    conversation.post("observability", [message("dependency"), message("reasoning")], 1)
    conversation.post("dependency", [message("critic"), message("reasoning")], 1)
    assert conversation.sent == 3
    assert conversation.dropped == 1
    conversation.post("critic", [message("reasoning", "why this cause?", "challenge")], 1)
    assert conversation.sent == 4


def test_an_agent_cannot_ask_again_before_a_reply(tmp_path):
    conversation, _ = make_conversation(tmp_path)
    conversation.post("observability", [message("dependency", "first")], 1)
    conversation.post("observability", [message("dependency", "first, in other words")], 1)
    assert conversation.sent == 1
    conversation.post("dependency", [message("observability", "here you go", "answer")], 1)
    conversation.post("observability", [message("dependency", "a follow-up")], 1)
    assert conversation.sent == 2
    assert conversation.replies == 1


def test_the_history_holds_the_work_and_messages_only_the_direct_talk(tmp_path):
    conversation, board = make_conversation(tmp_path)
    conversation.log("orchestrator", "observability", "task", "find the errors", 1)
    conversation.post("observability", [message("dependency")], 1)
    assert len(board.messages()) == 1
    kinds = []
    for entry in board.history():
        kinds.append(entry["intent"])
    assert kinds == ["task", "question"]


def test_the_history_shows_a_whole_run_in_order(tmp_path):
    script = base_script()
    script["dependency"] = [{"done": True, "facts": [
        {"kind": "assumption", "statement": "a change looks likely"}]}]
    ctx, _, _ = run_script(tmp_path, script)
    kinds = []
    for entry in ctx.board.history():
        if entry["intent"] not in kinds:
            kinds.append(entry["intent"])
    assert kinds == ["task", "facts", "ranking", "verdict", "report"]


def test_a_fact_without_evidence_becomes_an_assumption():
    turn = SpecialistTurn(facts=[{"kind": "fact", "statement": "looks related"}])
    assert turn.facts[0].kind == "assumption"


def test_causes_out_of_order_are_sorted_not_rejected():
    from investigator.agents.outputs import ReasoningOutput
    output = ReasoningOutput.model_validate({"hypotheses": [hypothesis("bad_deploy", 0.2),
                                                            hypothesis("config_change", 0.7)]})
    assert output.hypotheses[0].confidence == 0.7


def test_an_overlong_message_is_trimmed_not_rejected():
    long_message = AgentMessage(to="critic", intent="finding", text="x" * 2000)
    assert len(long_message.text) == 600


def test_agents_are_told_the_information_may_be_wrong():
    for agent in ["observability", "dependency", "reasoning", "critic"]:
        assert "may be incomplete, contradictory or misleading" in prompts.SYSTEM_PROMPTS[agent]


def test_the_brief_can_list_what_data_exists():
    brief = {"services": [["api", "app"]], "reported_at": "t0", "reporter": "oncall",
             "summary": "errors", "window_start": "t-1", "window_end": "t1",
             "metrics": [["api", ["cpu_pct", "error_rate_pct"]]]}
    text = prompts.render_brief(brief)
    assert "METRICS YOU CAN QUERY" in text
    assert "- api: cpu_pct, error_rate_pct" in text


def test_no_exchange_prompt_names_a_real_service():
    from investigator.generator.topology import TOPOLOGIES
    for topology in TOPOLOGIES.values():
        for name, _, _ in topology["services"]:
            for text in prompts.EXCHANGE_SYSTEM_PROMPTS.values():
                assert name not in text


def test_a_reply_gets_through_when_the_budgets_are_spent(tmp_path):
    conversation, board = make_conversation(tmp_path, limit=1)
    conversation.post("reasoning", [message("observability", "can you check this?")], 1)
    assert conversation.sent == 1
    conversation.post("observability", [message("reasoning", "checked, nothing there", "answer")], 1)
    assert conversation.replies == 1
    assert conversation.dropped == 0
    assert conversation.has_mail("reasoning")
    assert len(board.messages()) == 2


def test_a_reply_cannot_start_a_free_back_and_forth(tmp_path):
    conversation, _ = make_conversation(tmp_path, limit=1)
    conversation.post("critic", [message("reasoning", "why?", "challenge")], 1)
    conversation.post("reasoning", [message("critic", "because", "answer")], 1)
    conversation.post("critic", [message("reasoning", "and then?", "challenge")], 1)
    assert conversation.replies == 1
    assert conversation.dropped == 1
    assert len(conversation.take("reasoning")) == 1


def test_a_message_nobody_asked_for_is_not_a_reply(tmp_path):
    conversation, _ = make_conversation(tmp_path, limit=1)
    conversation.post("critic", [message("reasoning")], 1)
    conversation.post("observability", [message("dependency", "unprompted", "answer")], 1)
    assert conversation.replies == 0
    assert conversation.dropped == 1


def test_replies_show_in_the_run_statistics(tmp_path):
    script = base_script()
    script["reasoning"] = [dict(script["reasoning"][0], messages=[
        {"to": "dependency", "intent": "question", "text": "can you check the diff?"}]),
        script["reasoning"][0]]
    script["dependency"].append({"done": True, "messages": [
        {"to": "reasoning", "intent": "answer", "text": "the diff adds one rule"}]})
    ctx, llm, _ = run_script(tmp_path, script)
    assert ctx.stats["replies_sent"] == 1
    assert "the diff adds one rule" in llm.prompts_of("reasoning")[1]
