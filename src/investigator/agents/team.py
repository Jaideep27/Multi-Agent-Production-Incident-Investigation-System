"""The six agents' work for one round, plus the deterministic rules around them.

Each agent is one LlmClient.structured() call (the specialists loop over a few steps). Python,
not the model, mints IDs, writes the blackboard, runs tools through the gateway, enforces
which tools an agent may use, and decides when the investigation stops.

When an agent fails (no valid output after 3 attempts) the run carries on: its task is marked
failed and a note tells later agents what evidence is missing. If the Reporting agent fails, a
Python fallback builds an inconclusive report from the blackboard.
"""

import asyncio
from dataclasses import dataclass, field

from investigator.agents import prompts
from investigator.agents.checks import component_problems
from investigator.llm.client import LlmBudgetExhausted, LlmOutputError
from investigator.models.blackboard import Fact, Hypothesis, Objection, Task
from investigator.models.report import FinalReport, ReportFact, ReportHypothesis

DEFAULT_TASKS = [
    ["observability", "Find when the symptoms start, which service shows errors first, and "
                      "what the error messages say."],
    ["dependency", "Check changes made shortly before the symptoms (with diffs) and the "
                   "configuration of the affected services' dependencies."],
]

RECENT_RESULT_BATCHES = 1    # a specialist keeps what matters as facts; older results are not re-sent
REPLY_GOAL = ("A colleague needs something from you: see the messages below. Check what you need "
              "to, record what you find, and reply to them.")
REPLY_STEPS = 2              # one step to look, one to record and answer


def default_system_prompts():
    return prompts.SYSTEM_PROMPTS


def default_output_models():
    return prompts.PLAIN_OUTPUTS


@dataclass
class RunContext:
    brief: dict
    llm: object               # LlmClient
    gateway: object           # Gateway
    board: object             # Blackboard
    max_rounds: int
    max_specialist_steps: int
    notes: list = field(default_factory=list)
    ranking: list = field(default_factory=list)          # hypothesis IDs, best first
    hypothesis_ids: dict = field(default_factory=dict)   # "component/category" -> H-id
    critic_requests: list = field(default_factory=list)  # ToolRequests for next round
    approved: bool = False                               # the Critic's latest verdict
    blocked_once: list = field(default_factory=list)     # hypothesis IDs already blocked
    # Specialists may not spend the last tool calls: they are kept for the Critic's follow-ups.
    # The runner sets it to a fifth of the tool budget.
    tool_reserve: int = 0
    stop_reason: str | None = None                       # "tool_budget", "llm_budget", "deadline"
    status: str = "running"
    report: FinalReport | None = None
    stats: dict = field(default_factory=dict)
    # The agent-to-agent pattern replaces these three (exchange.configure).
    system_prompts: dict = field(default_factory=default_system_prompts)
    output_models: dict = field(default_factory=default_output_models)
    conversation: object = None

    def count(self, name, amount=1):
        self.stats[name] = self.stats.get(name, 0) + amount

    def stop(self, reason, note):
        if self.stop_reason is None:
            self.stop_reason = reason
            self.notes.append(note)


# ------------------------------------------------------------------ helpers


def record_outcomes(ctx, outcomes):
    """Feed returned evidence to the blackboard and notice budget or deadline refusals."""
    ids = []
    for outcome in outcomes:
        for evidence_id in outcome.result_evidence_ids():
            ids.append(evidence_id)
        if outcome.source == "budget_exhausted":
            ctx.stop("tool_budget", "the tool-call budget ran out; some requested checks were not run")
        if outcome.source == "deadline_exceeded":
            ctx.stop("deadline", "the time limit was reached; some requested checks were not run")
    if ids:
        ctx.board.record_evidence(ids)


def affordable(requests, max_calls, made, reserve):
    """The requests a specialist may still make without eating into the reserved calls."""
    left = max_calls - reserve - made
    if left <= 0:
        return []
    return list(requests[:left])


def ranked_hypotheses(ctx):
    current = {}
    for hypothesis in ctx.board.hypotheses():
        current[hypothesis.hypothesis_id] = hypothesis
    ranked = []
    for hypothesis_id in ctx.ranking:
        ranked.append(current[hypothesis_id])
    return ranked


async def ask(ctx, agent, prompt, output_model, check=None):
    """One structured call; returns None (and notes why) if the agent failed or the budget is
    spent."""
    try:
        return await ctx.llm.structured(agent, ctx.system_prompts[agent], prompt,
                                        output_model, check=check)
    except LlmBudgetExhausted:
        ctx.stop("llm_budget", "the LLM-call budget ran out before the %s agent could finish" % agent)
    except LlmOutputError:
        ctx.count("agent_failures")
    return None


def take_mail(ctx, agent):
    if ctx.conversation is None:
        return []
    return ctx.conversation.take(agent)


def share(ctx, agent, output, round_number):
    """Deliver what an agent said to its colleagues."""
    if ctx.conversation is not None:
        ctx.conversation.post(agent, output.messages, round_number)


def log_event(ctx, sender, recipient, kind, text, round_number):
    """Add a line to the conversation history; only the agent-to-agent pattern keeps one."""
    if ctx.conversation is not None and text:
        ctx.conversation.log(sender, recipient, kind, text, round_number)


def describe_tool_work(outcomes):
    lines = []
    for outcome in outcomes:
        result = outcome.result
        state = "not run (%s)" % outcome.source
        if result is not None:
            state = "%s, %d items" % (result.status, len(result.items))
        lines.append("%s %s -> %s" % (outcome.request.tool, prompts.compact(outcome.request.args),
                                      state))
    return "\n".join(lines)


def describe_facts(proposed_facts):
    lines = []
    for proposed in proposed_facts:
        lines.append("[%s] %s" % (proposed.kind, proposed.statement))
    return "\n".join(lines)


def describe_ranking(hypotheses):
    lines = []
    rank = 1
    for proposed in hypotheses:
        lines.append("%d. %s / %s (confidence %.2f): %s"
                     % (rank, proposed.component, proposed.category, proposed.confidence,
                        proposed.mechanism))
        rank = rank + 1
    return "\n".join(lines)


def describe_verdict(output):
    lines = ["Approved the top answer." if output.approved else "Not approved yet."]
    for objection in output.objections:
        kind = "Blocking" if objection.blocking else "Minor"
        lines.append("%s objection: %s" % (kind, objection.reason))
    return "\n".join(lines)


def message_problems(ctx, agent, output):
    """Messages follow the same grounding rule as facts: only evidence this run has seen."""
    if ctx.conversation is None:
        return []
    problems = []
    seen = ctx.board.seen_evidence()
    for message in output.messages:
        if message.to == agent:
            problems.append("do not send a message to yourself")
        for evidence_id in message.evidence_ids:
            if evidence_id not in seen:
                problems.append("your message cites evidence %s, which was never returned to "
                                "this run" % evidence_id)
    return problems


# ------------------------------------------------------------------ orchestrator


async def plan_round(ctx, round_number):
    tasks = []
    if ctx.stop_reason is None:
        prompt = prompts.orchestrator_prompt(ctx.brief, round_number, ctx.max_rounds,
                                             ctx.board.facts(), ranked_hypotheses(ctx),
                                             ctx.board.objections(open_only=True), ctx.notes,
                                             inbox=take_mail(ctx, "orchestrator"))

        def check(proposed):
            return message_problems(ctx, "orchestrator", proposed)

        output = await ask(ctx, "orchestrator", prompt, ctx.output_models["orchestrator"],
                           check=check)
        if output is not None:
            share(ctx, "orchestrator", output, round_number)
            for planned in output.tasks:
                tasks.append([planned.agent, planned.goal, planned.after])
        elif ctx.stop_reason is None:
            ctx.notes.append("round %d: the orchestrator failed; default tasks were used" % round_number)
            for agent, goal in DEFAULT_TASKS:
                tasks.append([agent, goal, None])

    created = []
    for agent, goal, after in tasks:
        depends_on = []
        if after is not None:
            depends_on = [created[after - 1]]
        task_id = ctx.board.new_id("task")
        ctx.board.add_task(Task(task_id=task_id, agent=agent, goal=goal, depends_on=depends_on,
                                round=round_number))
        created.append(task_id)
        log_event(ctx, "orchestrator", agent, "task", goal, round_number)


# ------------------------------------------------------------------ specialists


def specialist_check(ctx, agent):
    allowed = prompts.AGENT_TOOLS[agent]

    def check(output):
        problems = []
        for request in output.tool_requests:
            if request.tool not in allowed:
                problems.append("%s is not one of your tools (%s)" % (request.tool, ", ".join(allowed)))
        seen = ctx.board.seen_evidence()
        for proposed in output.facts:
            for evidence_id in proposed.evidence_ids:
                if evidence_id not in seen:
                    problems.append("evidence %s was never returned to you; cite only IDs from "
                                    "your tool results" % evidence_id)
        problems.extend(message_problems(ctx, agent, output))
        return problems

    return check


async def run_specialist(ctx, task, worker, critic_results, round_number):
    agent = task.agent
    goal = task.goal
    if task.depends_on:
        goal = goal + (" (This task builds on task %s, which has finished: start from its facts "
                       "on the blackboard.)" % ", ".join(task.depends_on))
    earlier_results = []
    max_steps = ctx.max_specialist_steps
    if task.goal == REPLY_GOAL:
        max_steps = REPLY_STEPS
    mail = []
    for step in range(1, max_steps + 1):
        # Mail arriving on the last step stays in the inbox: this step cannot act on it.
        if step < max_steps:
            mail.extend(take_mail(ctx, agent))
        prompt = prompts.specialist_prompt(ctx.brief, goal, step, max_steps,
                                           ctx.board.facts(), earlier_results[-RECENT_RESULT_BATCHES:],
                                           critic_results, ctx.gateway.calls_made(), inbox=mail)
        output = await ask(ctx, agent, prompt, ctx.output_models[agent],
                           check=specialist_check(ctx, agent))
        if output is None:
            ctx.board.finish_task(task.task_id, worker, "failed")
            if ctx.stop_reason is None:
                ctx.notes.append("round %d: the %s agent failed its task (%s); that evidence is "
                                 "missing" % (round_number, agent, task.goal))
            return

        for proposed in output.facts:
            ctx.board.add_fact(Fact(fact_id=ctx.board.new_id("fact"), kind=proposed.kind,
                                    statement=proposed.statement,
                                    evidence_ids=proposed.evidence_ids, author=agent,
                                    round=round_number))
        log_event(ctx, agent, "blackboard", "facts", describe_facts(output.facts), round_number)
        share(ctx, agent, output, round_number)

        last_step = step == max_steps
        if output.done or len(output.tool_requests) == 0 or last_step or ctx.stop_reason:
            break
        requests = affordable(output.tool_requests, ctx.gateway.limits.max_tool_calls,
                              ctx.gateway.logical_calls, ctx.tool_reserve)
        if len(requests) < len(output.tool_requests):
            ctx.count("reserve_trimmed", len(output.tool_requests) - len(requests))
        if not requests:
            note = ("the last %d tool calls are reserved for the Critic's follow-up checks"
                    % ctx.tool_reserve)
            if note not in ctx.notes:
                ctx.notes.append(note)
            break
        outcomes = await ctx.gateway.execute(requests)
        record_outcomes(ctx, outcomes)
        log_event(ctx, agent, "tools", "tools", describe_tool_work(outcomes), round_number)
        earlier_results.append(prompts.render_outcomes(outcomes))

    ctx.board.finish_task(task.task_id, worker, "done")


async def investigate(ctx, round_number):
    critic_results = ""
    if ctx.critic_requests and ctx.stop_reason is None:
        requests = ctx.critic_requests
        ctx.critic_requests = []
        outcomes = await ctx.gateway.execute(requests)
        record_outcomes(ctx, outcomes)
        ctx.count("critic_requested_tool_calls", len(requests))
        log_event(ctx, "critic", "tools", "tools", describe_tool_work(outcomes), round_number)
        critic_results = prompts.render_outcomes(outcomes)

    # Waves: every task whose dependencies have finished runs now, in parallel; tasks waiting
    # for one of them run in the next wave and start from its facts.
    wave = 0
    while True:
        finished = []
        for task in ctx.board.tasks():
            if task.status in ["done", "failed"]:
                finished.append(task.task_id)
        runs = []
        for task in ctx.board.tasks(status="pending"):
            ready = True
            for dependency in task.depends_on:
                if dependency not in finished:
                    ready = False
            if not ready:
                continue
            worker = "%s-r%d-w%d" % (task.agent, round_number, wave)
            if ctx.board.claim_task(task.task_id, worker):
                if task.depends_on:
                    ctx.count("dependent_tasks")
                runs.append(run_specialist(ctx, task, worker, critic_results, round_number))
        if not runs:
            break
        await asyncio.gather(*runs)
        wave = wave + 1


# ------------------------------------------------------------------ reasoning


def reasoning_check(ctx):
    def check(output):
        known = []
        for fact in ctx.board.facts():
            known.append(fact.fact_id)
        components = []
        for proposed in output.hypotheses:
            components.append(proposed.component)
        problems = component_problems(components, ctx.brief["services"])
        for proposed in output.hypotheses:
            for fact_id in proposed.supporting_fact_ids + proposed.contradicting_fact_ids:
                if fact_id not in known:
                    problems.append("fact %s does not exist; use fact IDs from the FACTS list" % fact_id)
            for fact_id in proposed.supporting_fact_ids:
                if fact_id in proposed.contradicting_fact_ids:
                    problems.append("fact %s cannot both support and contradict" % fact_id)
        problems.extend(message_problems(ctx, "reasoning", output))
        return problems

    return check


async def reason(ctx, round_number):
    if ctx.stop_reason is not None:
        return
    prompt = prompts.reasoning_prompt(ctx.brief, ctx.board.facts(), ranked_hypotheses(ctx),
                                      ctx.board.objections(open_only=True), ctx.notes,
                                      inbox=take_mail(ctx, "reasoning"))
    output = await ask(ctx, "reasoning", prompt, ctx.output_models["reasoning"],
                       check=reasoning_check(ctx))
    if output is None:
        if ctx.stop_reason is None:
            ctx.notes.append("round %d: the reasoning agent failed; the previous ranking was kept"
                             % round_number)
        return

    ranking = []
    for proposed in output.hypotheses:
        key = proposed.component + "/" + proposed.category
        if key not in ctx.hypothesis_ids:
            ctx.hypothesis_ids[key] = ctx.board.new_id("hypothesis")
        hypothesis_id = ctx.hypothesis_ids[key]
        ctx.board.save_hypothesis(Hypothesis(
            hypothesis_id=hypothesis_id, component=proposed.component,
            category=proposed.category, mechanism=proposed.mechanism,
            confidence=proposed.confidence, supporting_fact_ids=proposed.supporting_fact_ids,
            contradicting_fact_ids=proposed.contradicting_fact_ids, author="reasoning",
            round=round_number,
        ))
        ranking.append(hypothesis_id)
    if ctx.ranking and ranking and ctx.ranking[0] != ranking[0]:
        ctx.count("top_hypothesis_changes")
    ctx.ranking = ranking
    log_event(ctx, "reasoning", "blackboard", "ranking", describe_ranking(output.hypotheses),
              round_number)
    share(ctx, "reasoning", output, round_number)
    for assumption in output.assumptions:
        if assumption.strip():
            ctx.board.add_fact(Fact(fact_id=ctx.board.new_id("fact"), kind="assumption",
                                    statement=assumption, evidence_ids=[], author="reasoning",
                                    round=round_number))


# ------------------------------------------------------------------ critic


def critic_check(ctx, open_ids):
    def check(output):
        problems = []
        for objection in output.objections:
            if objection.hypothesis_id is not None and objection.hypothesis_id not in ctx.ranking:
                problems.append("hypothesis %s is not in the ranking" % objection.hypothesis_id)
        for objection_id in output.resolved_objection_ids:
            if objection_id not in open_ids:
                problems.append("objection %s is not open" % objection_id)
        problems.extend(message_problems(ctx, "critic", output))
        return problems

    return check


async def critique(ctx, round_number):
    if ctx.stop_reason is not None or not ctx.ranking:
        return
    open_objections = ctx.board.objections(open_only=True)
    open_ids = []
    for objection in open_objections:
        open_ids.append(objection.objection_id)
    prompt = prompts.critic_prompt(ctx.brief, ctx.board.facts(), ranked_hypotheses(ctx),
                                   open_objections, round_number, ctx.max_rounds,
                                   inbox=take_mail(ctx, "critic"))
    output = await ask(ctx, "critic", prompt, ctx.output_models["critic"],
                       check=critic_check(ctx, open_ids))
    if output is None:
        if ctx.stop_reason is None:
            ctx.notes.append("round %d: the critic failed; the ranking was not reviewed" % round_number)
        return

    for objection_id in output.resolved_objection_ids:
        ctx.board.resolve_objection(objection_id)
    ctx.approved = output.approved
    log_event(ctx, "critic", "team", "verdict", describe_verdict(output), round_number)
    share(ctx, "critic", output, round_number)
    if output.approved:
        ctx.count("critic_approvals")
    for proposed in output.objections:
        blocking = proposed.blocking
        target = proposed.hypothesis_id or "(investigation)"
        # One blocking objection per hypothesis per run: a repeat would keep the loop going
        # without new evidence, so it is kept as a non-blocking remark.
        if blocking and target in ctx.blocked_once:
            blocking = False
            ctx.count("blocking_downgraded")
        elif blocking:
            ctx.blocked_once.append(target)
        objection_id = ctx.board.new_id("objection")
        ctx.board.add_objection(Objection(
            objection_id=objection_id, hypothesis_id=proposed.hypothesis_id,
            reason=proposed.reason, blocking=blocking, requested_tools=proposed.requested_tools,
            alternative=proposed.alternative, follow_up_agent=proposed.follow_up_agent,
            round=round_number,
        ))
        ctx.count("objections")
        if blocking:
            ctx.count("blocking_objections")
            for request in proposed.requested_tools:
                ctx.critic_requests.append(request)
            add_follow_up_task(ctx, objection_id, proposed, round_number + 1)


def add_follow_up_task(ctx, objection_id, proposed, round_number):
    """Route a blocking objection to the specialist the Critic named, for the next round."""
    goal = ("Settle the Critic's objection %s: %s Competing explanation: %s. Run the check "
            "that tells the two apart and record what it shows, for or against."
            % (objection_id, proposed.reason.rstrip(".") + ".", proposed.alternative))
    ctx.board.add_task(Task(task_id=ctx.board.new_id("task"), agent=proposed.follow_up_agent,
                            goal=goal, round=round_number))
    ctx.count("follow_up_tasks")


def next_step(round_number, max_rounds, open_blocking, stop_reason, approved=False):
    """(route, status) after a critique. Pure, so the termination rules have direct tests."""
    if stop_reason is not None:
        return "report", "budget_exhausted"
    if approved or not open_blocking:
        return "report", "completed"
    if round_number >= max_rounds:
        return "report", "max_rounds"
    return "plan", "running"


def has_open_blocking(ctx):
    for objection in ctx.board.objections(open_only=True):
        if objection.blocking:
            return True
    return False


# ------------------------------------------------------------------ reporting


def report_check(ctx):
    def check(report):
        seen = ctx.board.seen_evidence()
        cited = []
        for fact in report.facts:
            cited.extend(fact.evidence_ids)
        for hypothesis in report.hypotheses:
            cited.extend(hypothesis.supporting_evidence_ids)
            cited.extend(hypothesis.contradicting_evidence_ids)
        for action in report.recommended_actions:
            cited.extend(action.evidence_ids)
        components = []
        for hypothesis in report.hypotheses:
            components.append(hypothesis.component)
        problems = component_problems(components, ctx.brief["services"])
        for evidence_id in cited:
            if evidence_id not in seen:
                problems.append("evidence %s was never returned by a tool in this run" % evidence_id)
        return problems

    return check


def evidence_of_facts(ctx, fact_ids):
    by_id = {}
    for fact in ctx.board.facts():
        by_id[fact.fact_id] = fact
    ids = []
    for fact_id in fact_ids:
        if fact_id in by_id:
            for evidence_id in by_id[fact_id].evidence_ids:
                if evidence_id not in ids:
                    ids.append(evidence_id)
    return ids


def fallback_report(ctx, reason):
    """A valid inconclusive report built from the blackboard, without the LLM."""
    hypotheses = []
    rank = 1
    previous_confidence = 1.0
    for hypothesis in ranked_hypotheses(ctx):
        supporting = evidence_of_facts(ctx, hypothesis.supporting_fact_ids)
        contradicting = []
        for evidence_id in evidence_of_facts(ctx, hypothesis.contradicting_fact_ids):
            if evidence_id not in supporting:
                contradicting.append(evidence_id)
        confidence = min(hypothesis.confidence, previous_confidence)
        previous_confidence = confidence
        hypotheses.append(ReportHypothesis(
            rank=rank, component=hypothesis.component, category=hypothesis.category,
            mechanism=hypothesis.mechanism, confidence=confidence,
            supporting_evidence_ids=supporting, contradicting_evidence_ids=contradicting,
        ))
        rank = rank + 1

    facts = []
    assumptions = []
    for fact in ctx.board.facts():
        if fact.kind == "fact":
            facts.append(ReportFact(statement=fact.statement, evidence_ids=fact.evidence_ids))
        else:
            assumptions.append(fact.statement)

    unresolved = [reason]
    for note in ctx.notes:
        if note not in unresolved:
            unresolved.append(note)
    return FinalReport(
        outcome="inconclusive",
        summary="Automatically assembled from the blackboard because no validated report was "
                "produced. The ranking below is the reasoning agent's last one.",
        hypotheses=hypotheses, facts=facts, assumptions=assumptions, unresolved=unresolved,
    )


def label_partial(report, ctx):
    """When the run stopped early, the report says so in unresolved."""
    if ctx.stop_reason is None:
        return report
    unresolved = list(report.unresolved)
    label = "investigation stopped early (%s)" % ctx.stop_reason
    if label not in unresolved:
        unresolved.append(label)
    return report.model_copy(update={"unresolved": unresolved})


async def write_report(ctx):
    report = None
    if ctx.stop_reason != "llm_budget":
        prompt = prompts.reporting_prompt(ctx.brief, ctx.board.facts(), ranked_hypotheses(ctx),
                                          ctx.board.objections(open_only=True), ctx.notes,
                                          ctx.board.seen_evidence(), approved=ctx.approved)
        report = await ask(ctx, "reporting", prompt, FinalReport, check=report_check(ctx))
    if report is None:
        report = fallback_report(ctx, "the reporting agent did not produce a valid report")
        ctx.count("fallback_reports")
    ctx.report = label_partial(report, ctx)
    log_event(ctx, "reporting", "team", "report",
              "%s: %s" % (ctx.report.outcome.replace("_", " "), ctx.report.summary), 0)
