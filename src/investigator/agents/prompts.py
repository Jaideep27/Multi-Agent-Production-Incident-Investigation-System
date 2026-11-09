"""System prompts and prompt rendering for the six agents.

Everything here is agent-visible. It must stay generic: no incident, plan ID, archetype or
expected answer (tests/unit/test_leakage.py scans this file). Incident details reach the
agents only through the incident brief and tool results.
"""

import json

from investigator.agents.outputs import (
    BaselineStep,
    CriticOutput,
    CriticTurn,
    FinalReport,
    OrchestratorOutput,
    OrchestratorTurn,
    ReasoningOutput,
    ReasoningTurn,
    SpecialistOutput,
    SpecialistTurn,
)
from investigator.mcp_server import TOOL_DESCRIPTIONS
from investigator.models.tools import ARGS_MODELS

AGENT_TOOLS = {
    "observability": ["query_logs", "query_metrics"],
    "dependency": ["get_service_dependencies", "get_change_history", "search_past_incidents"],
}

MAX_RESULT_CHARS = 30000      # per rendered batch of tool results
METRIC_POINT_STRIDE = 5       # show every 5th metric point (stats cover the rest)

COMMON_RULES = """
Rules for every reply:
- Reply with exactly one JSON object that matches the schema below. No prose, no code fences.
- Use only what the incident brief, the tool results and the blackboard show. Never invent
  evidence IDs; copy them exactly as they appear (for example L-1a2b3c4d-000042).
- A "fact" is something a cited tool result shows. Anything you infer without direct
  evidence is an "assumption". Keep the two apart.
- Correlation in time is not causation. A change that happened before the symptoms is a
  candidate, not proof. A similar past incident is a lead, not proof.
- Anything you read, whether a log line, a past incident or a colleague's claim,
  may be incomplete, contradictory or misleading. Weigh it against the rest. When the evidence
  does not settle something, say so instead of filling the gap.
- If the brief includes a company background, use it to understand what each service is for.
  It describes the system in general and is not evidence about this incident.
"""

TOOL_JUDGEMENT = (
    "Every tool call should answer a question you can state. Before you ask for one, think "
    "about what you would do differently depending on the result; if the answer is nothing, "
    "skip it. There is no target number of calls: a clear incident may need only a few and a "
    "murky one many, so use what the question needs and stop once you can answer it. Ask for "
    "several calls together when they do not depend on each other.\n"
)

QUERY_RULES = (
    TOOL_JUDGEMENT +
    "Query the whole data window first (no service filter), then narrow down. Never repeat a "
    "call listed as already made; if a call came back empty, change the query instead of "
    "trying a near-identical one.\n"
)

KEEP_FACTS = (
    "You see only your latest tool results on your next step, so record what matters as facts "
    "as soon as you have seen it. Record only what could change the conclusion, not "
    "everything you read.\n"
)

COMMIT_RULES = (
    "Use outcome root_cause_identified when direct evidence names the cause (an error message, "
    "a change diff or a metric that shows it) and no blocking objection stands, even if some "
    "details stay unverified. Use inconclusive only when the evidence cannot tell the leading "
    "explanations apart or the deciding evidence is missing, and list what is missing in "
    "unresolved. Every hypothesis component must be an exact service name from the brief. For "
    "the top hypothesis, say in confidence_reason, in one sentence, what makes your confidence "
    "as high or as low as it is.\n"
)


def schema_text(model):
    return json.dumps(model.model_json_schema(), separators=(",", ":"))


def tool_guide(tool_names):
    lines = []
    for name in tool_names:
        properties = ARGS_MODELS[name].model_json_schema().get("properties", {})
        argument_names = []
        for key in properties:
            argument_names.append(key)
        lines.append("- %s(%s): %s" % (name, ", ".join(argument_names), TOOL_DESCRIPTIONS[name]))
    return "\n".join(lines)


# What each agent is for. Generic on purpose: nothing here may describe a particular incident.
ROLES = {
    "orchestrator": (
        "You are the Orchestrator agent, leading a small team that is investigating a "
        "production incident.\n"
        "Read the brief and what the team has found so far, then decide what is most worth "
        "finding out next. Give the Observability agent (logs and metrics) and the Dependency "
        "agent (service topology, change history, past incidents) one goal each, or two only "
        "if they are truly independent, written as the questions you want answered. Aim them at whatever would separate the "
        "likely explanations, and do not ask for work already on the blackboard. In later "
        "rounds, go after the gaps the Critic raised. The symptoms began inside the data "
        "window given in the brief, so point the agents at that whole window (and shortly "
        "before it), not only at the time before it.\n"
        "Tasks run in parallel unless you set after=<n> (the 1-based position of an earlier "
        "task in your list). Use it when a task really needs another task's findings first; "
        "the waiting task then starts from those facts.\n"
    ),
    "observability": (
        "You are the Observability agent. You read logs and metrics the way an experienced "
        "on-call engineer does: when did this start, where did it start, and what do the "
        "errors actually say?\n"
        "Each step you may request tool calls (they run in parallel and you see the results "
        "next step) and record facts from results you have already seen. Compare a metric "
        "before and after the symptoms start. A service that is only waiting on another one "
        "shows errors too, so look for the earliest and most specific signal.\n"
        "Your tools:\n" + tool_guide(AGENT_TOOLS["observability"]) + "\n"
        + QUERY_RULES + KEEP_FACTS +
        "Set done=true when you have what your goal needs.\n"
    ),
    "dependency": (
        "You are the Dependency agent. You work out how the services depend on each other, "
        "what changed, and whether something like this has happened before.\n"
        "Each step you may request tool calls (they run in parallel and you see the results "
        "next step) and record facts from results you have already seen. Search the whole "
        "data window (and the hours before it) for changes, since the symptoms began inside "
        "it. Read the diff (include_diff=true) before you call a change relevant, and ask "
        "whether that change could produce these symptoms at all. Check the client "
        "configuration on dependency edges.\n"
        "Your tools:\n" + tool_guide(AGENT_TOOLS["dependency"]) + "\n"
        + QUERY_RULES + KEEP_FACTS +
        "Set done=true when you have what your goal needs.\n"
    ),
    "reasoning": (
        "You are the Reasoning agent. Your job is to work out which explanation really fits. "
        "From the facts on the blackboard, produce two to four competing root-cause "
        "hypotheses, best first. Each names the component where the problem starts (not where "
        "the symptom shows), a category, the causal mechanism, a confidence between 0 and 1, "
        "and the fact IDs that support or contradict it. Prefer the hypothesis that explains "
        "the timeline and every symptom, and be honest about what counts against your "
        "favourite. Take the Critic's open objections seriously. If the evidence cannot "
        "separate two explanations, keep both and lower the confidence. Each component must "
        "be an exact service name from the brief.\n"
    ),
    "critic": (
        "You are the Critic agent. Your job is to try to break the team's conclusion before "
        "anyone relies on it, and to give a verdict on the top-ranked hypothesis.\n"
        "Set approved=true when direct evidence supports it (an error message, a change "
        "diff or a metric that shows the mechanism) and the facts contradict or leave "
        "unsupported the other hypotheses, even if minor details stay unverified. Look for "
        "the usual traps: a change that is close in time but whose diff cannot cause the "
        "symptom, a past incident with similar symptoms but a different cause, a service that "
        "only shows errors because something upstream failed, timestamps that disagree.\n"
        "Otherwise set approved=false and write a blocking objection: what is unproven, the "
        "competing explanation (alternative), the specialist who should settle it "
        "(follow_up_agent: observability for logs and metrics, dependency for changes, "
        "topology and past incidents) and the tool calls that tell the two apart "
        "(requested_tools). Each hypothesis can be blocked only once, so make the check "
        "decisive. Smaller concerns go in non-blocking objections. Resolve earlier "
        "objections that the new facts answer (resolved_objection_ids).\n"
        "Tools you may request:\n" + tool_guide(list(ARGS_MODELS.keys())) + "\n"
    ),
    "reporting": (
        "You are the Reporting agent. Write the final incident report from the blackboard, so "
        "that an engineer who was not involved can act on it. "
        + COMMIT_RULES +
        "If the Critic approved the top hypothesis, use root_cause_identified unless the "
        "deciding evidence is missing. "
        "Rank hypotheses 1..n with "
        "non-increasing confidence. Cite evidence IDs (not fact IDs) for every fact, "
        "hypothesis and action. Recommended actions should fix the cause, not only the "
        "symptom.\n"
    ),
    # The single-agent baseline: one agent with every tool, the same rules and the same
    # report format as the team, and no division of labour or critic.
    "investigator": (
        "You are the Investigator agent, working alone on a production incident.\n"
        "Each step you may request tool calls (they run in parallel and you see the results "
        "next step), add notes (facts citing evidence IDs, or assumptions), or finish by "
        "returning final_report. Find when the symptoms start and where errors appear first, "
        "search the whole data window for changes and look at their diffs, check dependency "
        "configuration, and compare metrics before and after the onset. "
        + COMMIT_RULES +
        "Rank hypotheses 1..n with non-increasing confidence, naming the component where the "
        "problem starts.\n"
        "Your tools:\n" + tool_guide(sorted(ARGS_MODELS.keys())) + "\n"
        + QUERY_RULES +
        "You see only your most recent tool results, so put what matters in your notes as soon "
        "as you have seen it. Note only what could change the conclusion, not everything you "
        "read.\n"
    ),
}

# Added to the roles in the agent-to-agent pattern. It describes when talking is worth it and
# leaves what to say, and to whom, to the agent.
TALKING_RULES = (
    "\nYou are not working alone: you can speak directly to a colleague through messages. "
    "They are observability (logs and metrics), dependency (changes, topology, past "
    "incidents), reasoning (weighing the explanations), critic (testing the conclusion) and "
    "orchestrator (the plan).\n"
    "Speak up when it would change what someone does next: you found something only they can "
    "follow up, you need something only they can get, or you think they have something wrong. "
    "Say what you saw, what you make of it and exactly what you need back, and cite evidence "
    "IDs when you have them. If someone asked you something, answer it plainly, even when the "
    "answer is \"I checked and found nothing\" or \"you are right\".\n"
    "Everyone already reads the blackboard, so do not repeat it, report progress, agree for "
    "the sake of it or say thanks. If you have nothing that would change anyone's next move, "
    "send nothing. Every message interrupts a colleague and spends the team's limited "
    "budget.\n"
)

TALKING_ROLES = {
    "orchestrator": (
        "Your tasks already reach the specialists, so use messages only for something a task "
        "cannot carry.\n"
    ),
    "observability": (
        "When your results point at something in a colleague's area, tell them while they are "
        "still working: it can save the whole team a search.\n"
    ),
    "dependency": (
        "When your results point at something in a colleague's area, tell them while they are "
        "still working: it can save the whole team a search.\n"
    ),
    "reasoning": (
        "If two explanations are close and one specific check would separate them, ask the "
        "specialist who can run it instead of guessing. If the Critic challenges you, either "
        "change the ranking or answer with the fact that settles the point.\n"
    ),
    "critic": (
        "If the ranking does not follow from the facts, challenge reasoning directly and name "
        "the fact it has overlooked. If a piece of evidence is missing, ask the specialist "
        "who can get it. Keep a blocking objection for when the whole answer depends on it.\n"
    ),
}

PLAIN_OUTPUTS = {
    "orchestrator": OrchestratorOutput,
    "observability": SpecialistOutput,
    "dependency": SpecialistOutput,
    "reasoning": ReasoningOutput,
    "critic": CriticOutput,
    "reporting": FinalReport,
    "investigator": BaselineStep,
}

TALKING_OUTPUTS = {
    "orchestrator": OrchestratorTurn,
    "observability": SpecialistTurn,
    "dependency": SpecialistTurn,
    "reasoning": ReasoningTurn,
    "critic": CriticTurn,
    "reporting": FinalReport,
}


def compose_prompt(agent, output_model, talking):
    text = ROLES[agent]
    if talking and agent in TALKING_ROLES:
        text = text + TALKING_RULES + TALKING_ROLES[agent]
    return text + COMMON_RULES + "\nSchema:\n" + schema_text(output_model)


def build_prompts(outputs, talking):
    built = {}
    for agent in outputs:
        built[agent] = compose_prompt(agent, outputs[agent], talking)
    return built


SYSTEM_PROMPTS = build_prompts(PLAIN_OUTPUTS, False)
EXCHANGE_SYSTEM_PROMPTS = build_prompts(TALKING_OUTPUTS, True)


# ------------------------------------------------------------------ rendering


def render_brief(brief):
    services = []
    for name, role in brief["services"]:
        services.append("%s (%s)" % (name, role))
    text = (
        "INCIDENT BRIEF\n"
        "Reported: %s by %s\n"
        "Symptom: %s\n"
        "Data window: %s to %s (the stored evidence covers this period; the symptoms began "
        "inside this window, so search all of it)\n"
        "Services: %s\n"
        % (brief["reported_at"], brief["reporter"], brief["summary"], brief["window_start"],
           brief["window_end"], ", ".join(services))
    )
    if brief.get("metrics"):
        lines = ["\nMETRICS YOU CAN QUERY (names only; they say nothing about what happened)"]
        for service, names in brief["metrics"]:
            lines.append("- %s: %s" % (service, ", ".join(names)))
        text = text + "\n".join(lines) + "\n"
    if brief.get("background"):
        text = text + ("\nCOMPANY BACKGROUND (how this system normally works; it says nothing "
                       "about this incident)\n%s\n" % brief["background"])
    return text


def compact(value):
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def render_item(item):
    data = dict(item.data)
    if "points" in data:
        sampled = []
        index = 0
        for point in data["points"]:
            if index % METRIC_POINT_STRIDE == 0:
                sampled.append(point)
            index = index + 1
        data["points"] = sampled
        data["points_note"] = "every %dth point shown; stats cover all" % METRIC_POINT_STRIDE
    return "  %s %s" % (item.evidence_id, compact(data))


def render_outcomes(outcomes):
    """Tool results as text: one header per call, one line per item."""
    lines = []
    for outcome in outcomes:
        request = outcome.request
        header = "CALL %s %s -> " % (request.tool, compact(request.args))
        result = outcome.result
        if result is None:
            lines.append(header + "not run (%s)" % outcome.source)
            continue
        status = result.status
        if result.error:
            status = status + ": " + result.error
        if result.truncated:
            status = status + " (truncated"
            if result.next_cursor:
                status = status + ", next_cursor=" + result.next_cursor
            status = status + ")"
        for warning in result.warnings:
            status = status + " [warning: " + warning + "]"
        lines.append(header + status)
        for item in result.items:
            lines.append(render_item(item))
    text = "\n".join(lines)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + "\n[results cut at %d characters; narrow your queries]" % MAX_RESULT_CHARS
    return text


def render_facts(facts):
    if not facts:
        return "(none yet)"
    lines = []
    for fact in facts:
        lines.append("%s [%s, %s, round %d] %s  evidence: %s"
                     % (fact.fact_id, fact.kind, fact.author, fact.round, fact.statement,
                        ", ".join(fact.evidence_ids) or "-"))
    return "\n".join(lines)


def render_hypotheses(hypotheses):
    if not hypotheses:
        return "(none yet)"
    lines = []
    rank = 1
    for hypothesis in hypotheses:
        lines.append("%d. %s %s/%s confidence %.2f: %s  supported by %s; contradicted by %s"
                     % (rank, hypothesis.hypothesis_id, hypothesis.component, hypothesis.category,
                        hypothesis.confidence, hypothesis.mechanism,
                        ", ".join(hypothesis.supporting_fact_ids) or "-",
                        ", ".join(hypothesis.contradicting_fact_ids) or "-"))
        rank = rank + 1
    return "\n".join(lines)


def render_objections(objections):
    if not objections:
        return "(none open)"
    lines = []
    for objection in objections:
        kind = "BLOCKING" if objection.blocking else "minor"
        line = "%s [%s] about %s: %s" % (objection.objection_id, kind,
                                         objection.hypothesis_id or "the investigation",
                                         objection.reason)
        if objection.alternative:
            line = line + " (competing explanation: %s)" % objection.alternative
        lines.append(line)
    return "\n".join(lines)


def render_notes(notes):
    if not notes:
        return "(none)"
    lines = []
    for note in notes:
        lines.append("- " + note)
    return "\n".join(lines)


def render_messages(messages):
    lines = []
    for message in messages:
        line = "From %s (%s): %s" % (message.sender, message.intent, message.text)
        if message.evidence_ids:
            line = line + "  evidence: " + ", ".join(message.evidence_ids)
        lines.append(line)
    return "\n".join(lines)


def inbox_section(messages):
    """Empty without mail, so prompts in the blackboard pattern are not affected."""
    if not messages:
        return ""
    return ("\n\nMESSAGES FOR YOU\n" + render_messages(messages)
            + "\nReply to anything that was asked of you.")


def orchestrator_prompt(brief, round_number, max_rounds, facts, hypotheses, objections, notes,
                        inbox=None):
    return (
        render_brief(brief)
        + "\nROUND %d of at most %d\n" % (round_number, max_rounds)
        + "\nFACTS SO FAR\n" + render_facts(facts)
        + "\n\nCURRENT HYPOTHESES\n" + render_hypotheses(hypotheses)
        + "\n\nOPEN OBJECTIONS\n" + render_objections(objections)
        + "\n\nNOTES\n" + render_notes(notes)
        + inbox_section(inbox)
        + "\n\nPlan this round's tasks."
    )


MAX_CALLS_SHOWN = 40


def render_calls_made(calls_made):
    """One line per tool call already made in this run (newest last, at most 40)."""
    if not calls_made:
        return "(none yet)"
    lines = []
    for tool, args, status, count in calls_made[-MAX_CALLS_SHOWN:]:
        lines.append("%s %s -> %s (%d items)" % (tool, compact(args), status, count))
    return "\n".join(lines)


def specialist_prompt(brief, goal, step, max_steps, facts, earlier_results, critic_results,
                      calls_made=None, inbox=None):
    last = step == max_steps
    text = (
        render_brief(brief)
        + "\nYOUR GOAL: " + goal
        + "\nSTEP %d of %d.%s\n" % (step, max_steps,
                                    " This is your last step: request no tools, record facts and set done=true."
                                    if last else "")
        + "\nFACTS ALREADY ON THE BLACKBOARD\n" + render_facts(facts)
        + "\n\nTOOL CALLS ALREADY MADE IN THIS RUN (by any agent)\n" + render_calls_made(calls_made)
    )
    if critic_results:
        text = text + "\n\nRESULTS OF CHECKS THE CRITIC ASKED FOR\n" + critic_results
    if earlier_results:
        text = text + "\n\nYOUR LATEST TOOL RESULTS\n" + "\n".join(earlier_results)
    else:
        text = text + "\n\nYOUR LATEST TOOL RESULTS\n(none: request the tools you need)"
    return text + inbox_section(inbox)


def reasoning_prompt(brief, facts, hypotheses, objections, notes, inbox=None):
    return (
        render_brief(brief)
        + "\nFACTS\n" + render_facts(facts)
        + "\n\nPREVIOUS HYPOTHESES\n" + render_hypotheses(hypotheses)
        + "\n\nOPEN OBJECTIONS FROM THE CRITIC\n" + render_objections(objections)
        + "\n\nNOTES (failed work, missing evidence)\n" + render_notes(notes)
        + inbox_section(inbox)
        + "\n\nRank the hypotheses. Cite fact IDs (F-...) in supporting_fact_ids and "
          "contradicting_fact_ids."
    )


def critic_prompt(brief, facts, hypotheses, objections, round_number, max_rounds, inbox=None):
    return (
        render_brief(brief)
        + "\nROUND %d of at most %d\n" % (round_number, max_rounds)
        + "\nFACTS\n" + render_facts(facts)
        + "\n\nRANKED HYPOTHESES\n" + render_hypotheses(hypotheses)
        + "\n\nYOUR OPEN OBJECTIONS FROM EARLIER ROUNDS\n" + render_objections(objections)
        + inbox_section(inbox)
        + "\n\nReview the ranking. Use hypothesis IDs (H-...) and objection IDs (O-...) "
          "exactly as shown."
    )


def render_notes_list(notes):
    if not notes:
        return "(none yet)"
    lines = []
    for kind, statement, evidence_ids in notes:
        lines.append("[%s] %s  evidence: %s" % (kind, statement, ", ".join(evidence_ids) or "-"))
    return "\n".join(lines)


def investigator_prompt(brief, step, max_steps, notes, recent_results, last, remarks,
                        calls_made=None):
    text = render_brief(brief) + "\nSTEP %d of %d.\n" % (step, max_steps)
    text = text + "\nTOOL CALLS ALREADY MADE IN THIS RUN\n" + render_calls_made(calls_made) + "\n"
    if last:
        text = text + ("LAST STEP: request no tools; return final_report now, citing only "
                       "evidence IDs from your notes and results.\n")
    text = text + "\nYOUR NOTES SO FAR\n" + render_notes_list(notes)
    if remarks:
        text = text + "\n\nREMARKS\n" + render_notes(remarks)
    if recent_results:
        text = text + "\n\nYOUR RECENT TOOL RESULTS (older ones live on only in your notes)\n"
        text = text + "\n".join(recent_results)
    else:
        text = text + "\n\nYOUR RECENT TOOL RESULTS\n(none: request the tools you need)"
    return text


def reporting_prompt(brief, facts, hypotheses, objections, notes, evidence_ids, approved=False):
    verdict = "approved the top hypothesis" if approved else "did not approve the top hypothesis"
    return (
        render_brief(brief)
        + "\nFACTS\n" + render_facts(facts)
        + "\n\nRANKED HYPOTHESES\n" + render_hypotheses(hypotheses)
        + "\n\nCRITIC VERDICT\nThe Critic " + verdict + "."
        + "\n\nOPEN OBJECTIONS\n" + render_objections(objections)
        + "\n\nNOTES (failed work, missing evidence, limits reached)\n" + render_notes(notes)
        + "\n\nEVIDENCE IDS YOU MAY CITE\n" + ", ".join(evidence_ids)
        + "\n\nWrite the final report."
    )
