"""The agent-to-agent pattern: who speaks next depends on what was said.

The blackboard pattern (graph.py) always runs plan -> investigate -> reason -> critique. Here
the conversation decides: a message wakes its recipient, new facts bring Reasoning back, and a
new ranking or a reply brings the Critic back. The agents themselves are the same functions as
in team.py; only the order differs, and they can address each other.

Python still decides when it stops. next_speakers() is a pure function so the rules have
direct tests: every turn needs a message, a task, new facts or a new ranking; messages are
capped; Critic reviews are capped; and everything spends the same tool and LLM budgets as the
blackboard pattern.
"""

from investigator.agents import prompts, team
from investigator.agents.conversation import Conversation
from investigator.models.blackboard import Task

SPECIALISTS = ["observability", "dependency"]
MAX_MESSAGES = 6       # per run; a message can wake an idle agent, which costs a model call
MAX_TURNS = 30         # a backstop; the rules below end the conversation much sooner


def configure(ctx):
    """Switch a run context to the agent-to-agent prompts, output shapes and inboxes."""
    ctx.system_prompts = prompts.EXCHANGE_SYSTEM_PROMPTS
    ctx.output_models = prompts.TALKING_OUTPUTS
    ctx.conversation = Conversation(ctx.board, max_messages=MAX_MESSAGES)


def next_speakers(mail, pending_tasks, new_facts, has_ranking, reasoning_turns, unreviewed_ranking,
                  approved, reviews, max_reviews, stopped):
    """Who speaks next: "orchestrator", "specialists", "reasoning", "critic" or "reporting"."""
    if stopped or approved:
        return "reporting"
    if mail["orchestrator"]:
        return "orchestrator"
    if pending_tasks or mail["observability"] or mail["dependency"]:
        return "specialists"
    if mail["reasoning"] or new_facts or (not has_ranking and reasoning_turns == 0):
        return "reasoning"
    if (mail["critic"] or unreviewed_ranking) and reviews < max_reviews:
        return "critic"
    return "reporting"


def add_reply_tasks(ctx, round_number):
    """A specialist with mail gets a short task to deal with it."""
    for agent in SPECIALISTS:
        if ctx.conversation.has_mail(agent):
            ctx.board.add_task(Task(task_id=ctx.board.new_id("task"), agent=agent,
                                    goal=team.REPLY_GOAL, round=round_number))
            ctx.count("specialists_woken")


async def run_exchange(ctx):
    """Runs the conversation and the report; returns the number of Critic reviews."""
    conversation = ctx.conversation
    await team.plan_round(ctx, 1)

    reviews = 0
    reasoning_turns = 0
    rankings = 0
    rankings_reviewed = 0
    facts_at_last_ranking = 0
    for _ in range(MAX_TURNS):
        mail = {}
        for agent in conversation.inboxes:
            mail[agent] = conversation.has_mail(agent)
        speaker = next_speakers(
            mail=mail,
            pending_tasks=len(ctx.board.tasks(status="pending")) > 0,
            new_facts=len(ctx.board.facts()) > facts_at_last_ranking,
            has_ranking=len(ctx.ranking) > 0,
            reasoning_turns=reasoning_turns,
            unreviewed_ranking=rankings > rankings_reviewed,
            approved=ctx.approved,
            reviews=reviews,
            max_reviews=ctx.max_rounds,
            stopped=ctx.stop_reason is not None,
        )
        round_number = reviews + 1
        if speaker == "reporting":
            break
        if speaker == "orchestrator":
            await team.plan_round(ctx, round_number)
        elif speaker == "specialists":
            add_reply_tasks(ctx, round_number)
            await team.investigate(ctx, round_number)
        elif speaker == "reasoning":
            await team.reason(ctx, round_number)
            reasoning_turns += 1
            facts_at_last_ranking = len(ctx.board.facts())
            if ctx.ranking:
                rankings += 1
        else:
            await team.critique(ctx, round_number)
            reviews += 1
            rankings_reviewed = rankings

    ctx.count("messages_sent", conversation.sent)
    ctx.count("replies_sent", conversation.replies)
    if conversation.dropped:
        ctx.count("messages_dropped", conversation.dropped)
    _, status = team.next_step(reviews, ctx.max_rounds, team.has_open_blocking(ctx),
                               ctx.stop_reason, approved=ctx.approved)
    if status == "running":
        status = "max_rounds"
    ctx.status = status
    await team.write_report(ctx)
    return reviews
