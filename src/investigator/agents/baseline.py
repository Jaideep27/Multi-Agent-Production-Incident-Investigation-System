"""The single-agent baseline: one agent, every tool, the same gateway, model and limits.

It differs from the team only in organisation: no division of labour, no blackboard, no
critic. It keeps its own notes (grounded like the team's facts) and sees only its two most
recent tool-result batches, the usual way a lone agent manages a long investigation.
"""

from dataclasses import dataclass, field

from investigator.agents import prompts
from investigator.agents.checks import component_problems
from investigator.agents.outputs import BaselineStep
from investigator.agents.team import describe_facts, describe_tool_work, label_partial
from investigator.llm.client import LlmBudgetExhausted, LlmOutputError
from investigator.models.report import FinalReport, ReportFact
from investigator.models.tools import ARGS_MODELS

BASELINE_TOOLS = list(ARGS_MODELS.keys())
RECENT_BATCHES = 2


@dataclass
class BaselineContext:
    brief: dict
    llm: object
    gateway: object
    max_steps: int
    notes: list = field(default_factory=list)      # [kind, statement, evidence_ids]
    remarks: list = field(default_factory=list)    # limits reached, failures
    stop_reason: str | None = None
    status: str = "running"
    report: FinalReport | None = None
    steps_used: int = 0
    stats: dict = field(default_factory=dict)
    history: object = None                         # step-by-step record for the viewer

    def stop(self, reason, remark):
        if self.stop_reason is None:
            self.stop_reason = reason
            self.remarks.append(remark)


def log_step(ctx, recipient, kind, text, step_number):
    if ctx.history is not None and text:
        ctx.history.log("investigator", recipient, kind, text, step_number)


def cited_in_step(step):
    ids = []
    for note in step.notes:
        ids.extend(note.evidence_ids)
    report = step.final_report
    if report is not None:
        for fact in report.facts:
            ids.extend(fact.evidence_ids)
        for hypothesis in report.hypotheses:
            ids.extend(hypothesis.supporting_evidence_ids)
            ids.extend(hypothesis.contradicting_evidence_ids)
        for action in report.recommended_actions:
            ids.extend(action.evidence_ids)
    return ids


def step_check(ctx, last):
    def check(step):
        problems = []
        seen = ctx.gateway.seen_evidence_ids()
        for evidence_id in cited_in_step(step):
            if evidence_id not in seen:
                problems.append("evidence %s was never returned to you; cite only IDs from "
                                "your tool results" % evidence_id)
        if last and step.final_report is None:
            problems.append("this is your last step: return final_report")
        if step.final_report is not None:
            components = []
            for hypothesis in step.final_report.hypotheses:
                components.append(hypothesis.component)
            problems.extend(component_problems(components, ctx.brief["services"]))
        return problems

    return check


def fallback_report(ctx, reason):
    facts = []
    assumptions = []
    for kind, statement, evidence_ids in ctx.notes:
        if kind == "fact":
            facts.append(ReportFact(statement=statement, evidence_ids=evidence_ids))
        else:
            assumptions.append(statement)
    unresolved = [reason]
    for remark in ctx.remarks:
        if remark not in unresolved:
            unresolved.append(remark)
    return FinalReport(outcome="inconclusive",
                       summary="Assembled from the investigator's notes because no validated "
                               "report was produced.",
                       facts=facts, assumptions=assumptions, unresolved=unresolved)


async def run_baseline(ctx):
    recent = []
    for step_number in range(1, ctx.max_steps + 1):
        ctx.steps_used = step_number
        last = step_number == ctx.max_steps or ctx.stop_reason is not None
        prompt = prompts.investigator_prompt(ctx.brief, step_number, ctx.max_steps, ctx.notes,
                                             recent, last, ctx.remarks, ctx.gateway.calls_made())
        try:
            step = await ctx.llm.structured("investigator", prompts.SYSTEM_PROMPTS["investigator"],
                                            prompt, BaselineStep, check=step_check(ctx, last))
        except LlmBudgetExhausted:
            ctx.stop("llm_budget", "the LLM-call budget ran out before a report was written")
            break
        except LlmOutputError:
            ctx.remarks.append("step %d: the investigator gave no valid output" % step_number)
            ctx.stats["agent_failures"] = ctx.stats.get("agent_failures", 0) + 1
            break

        for note in step.notes:
            ctx.notes.append([note.kind, note.statement, list(note.evidence_ids)])
        log_step(ctx, "notes", "facts", describe_facts(step.notes), step_number)
        if step.final_report is not None:
            ctx.report = step.final_report
            break
        if last or not step.tool_requests:
            continue

        outcomes = await ctx.gateway.execute(step.tool_requests)
        log_step(ctx, "tools", "tools", describe_tool_work(outcomes), step_number)
        for outcome in outcomes:
            if outcome.source == "budget_exhausted":
                ctx.stop("tool_budget", "the tool-call budget ran out; some requested checks were not run")
            if outcome.source == "deadline_exceeded":
                ctx.stop("deadline", "the time limit was reached; some requested checks were not run")
        recent.append(prompts.render_outcomes(outcomes))
        recent = recent[-RECENT_BATCHES:]

    if ctx.stop_reason is not None:
        ctx.status = "budget_exhausted"
    elif ctx.report is None and ctx.steps_used == ctx.max_steps:
        ctx.status = "max_rounds"
    else:
        ctx.status = "completed"

    if ctx.report is None:
        ctx.report = fallback_report(ctx, "the investigator did not produce a valid report")
        ctx.stats["fallback_reports"] = 1
    ctx.report = label_partial(ctx.report, ctx)
    log_step(ctx, "report", "report",
             "%s: %s" % (ctx.report.outcome.replace("_", " "), ctx.report.summary), ctx.steps_used)
