"""A scripted 'model' for end-to-end runs without quota.

It answers each agent from the prompt alone (it parses the IDs it was shown), so it knows
nothing about the answer key. Options make one agent misbehave, to test recovery.
"""

import json
import re

AGENT_PATTERN = re.compile(r"You are the (\w+) agent")


def first_ids(text, prefix, limit):
    found = []
    for match in re.finditer(r"\b%s-[0-9a-f]{8}-[0-9]+\b" % prefix, text):
        if match.group(0) not in found:
            found.append(match.group(0))
        if len(found) == limit:
            break
    return found


def section(text, title):
    if title not in text:
        return ""
    after = text.split(title, 1)[1]
    return after.split("\n\n", 1)[0]


class StubBrain:
    def __init__(self, broken_agent=None, critic_always_objects=False, invent_evidence_once=None,
                 vague_component_once=None, chain_tasks=False):
        self.chain_tasks = chain_tasks      # orchestrator makes the dependency task wait
        self.order = []                     # agents in the order they were called
        self.broken_agent = broken_agent
        self.critic_always_objects = critic_always_objects
        # "reporting" or "investigator": the first report names its component as a sentence
        self.vague_component_once = vague_component_once
        self.vagued = False
        # "observability" or "reporting": that agent's first citing answer uses a made-up ID
        self.invent_evidence_once = invent_evidence_once
        self.invented = False
        self.calls_by_agent = {}

    def component(self, agent, prompt):
        name = section(prompt, "Services: ").split(",")[0].split(" (")[0].strip()
        if agent == self.vague_component_once and not self.vagued:
            self.vagued = True
            return name + " (the place where it starts)"
        return name

    def maybe_invent(self, agent, ids):
        if agent == self.invent_evidence_once and not self.invented and ids:
            self.invented = True
            return ["L-0badf00d-000001"] + ids[1:]
        return ids

    def __call__(self, system, prompt, model, number):
        agent = AGENT_PATTERN.search(system).group(1).lower()
        self.calls_by_agent[agent] = self.calls_by_agent.get(agent, 0) + 1
        self.order.append(agent)
        if agent == self.broken_agent:
            return "I cannot answer in JSON today."
        handler = getattr(self, "answer_" + agent)
        return json.dumps(handler(prompt))

    def answer_orchestrator(self, prompt):
        dependency_task = {"agent": "dependency", "goal": "check recent changes"}
        if self.chain_tasks:
            dependency_task["after"] = 1
        return {"tasks": [{"agent": "observability", "goal": "find the first errors"}, dependency_task]}

    def specialist(self, prompt, requests, prefix, agent):
        results = prompt.split("YOUR LATEST TOOL RESULTS", 1)[1]
        if "(none: request the tools you need)" in results:
            return {"tool_requests": requests, "facts": [], "done": False}
        ids = self.maybe_invent(agent, first_ids(results, prefix, 2))
        if not ids:
            return {"tool_requests": [], "facts": [], "done": True}
        return {"tool_requests": [], "done": True, "facts": [
            {"kind": "fact", "statement": "the results show %s" % ", ".join(ids),
             "evidence_ids": ids},
        ]}

    def answer_observability(self, prompt):
        return self.specialist(prompt, [
            {"tool": "query_logs", "args": {"levels": ["ERROR"], "limit": 10}},
            {"tool": "query_metrics", "args": {"limit": 5}},
        ], "L", "observability")

    def answer_dependency(self, prompt):
        return self.specialist(prompt, [
            {"tool": "get_change_history", "args": {"include_diff": True}},
            {"tool": "get_service_dependencies", "args": {}},
        ], "C", "dependency")

    def answer_reasoning(self, prompt):
        facts = re.findall(r"^(F-\d{4}) \[fact", prompt, flags=re.MULTILINE)
        services = section(prompt, "Services: ").split(",")
        component = services[0].split(" (")[0].strip()
        return {"hypotheses": [
            {"component": component, "category": "config_change",
             "mechanism": "a recent change broke it", "confidence": 0.7,
             "supporting_fact_ids": facts[:2], "contradicting_fact_ids": []},
            {"component": component, "category": "resource_exhaustion",
             "mechanism": "it ran out of capacity", "confidence": 0.2},
        ], "assumptions": ["traffic was normal before the symptoms"]}

    def answer_critic(self, prompt):
        hypotheses = re.findall(r"^\d+\. (H-\d{4})", prompt, flags=re.MULTILINE)
        open_section = prompt.split("YOUR OPEN OBJECTIONS FROM EARLIER ROUNDS", 1)[1]
        open_ids = re.findall(r"^(O-\d{4})", open_section, flags=re.MULTILINE)
        if self.critic_always_objects or not open_ids:
            return {"approved": False, "objections": [{
                "hypothesis_id": hypotheses[0],
                "reason": "the diff of the change was not checked",
                "blocking": True,
                "alternative": "the change is a coincidence",
                "follow_up_agent": "dependency",
                "requested_tools": [{"tool": "get_change_history",
                                     "args": {"include_diff": True, "change_type": "config_change"}}],
            }], "resolved_objection_ids": []}
        return {"approved": True, "objections": [], "resolved_objection_ids": open_ids}

    def answer_investigator(self, prompt):
        """Single agent: step 1 asks for three tools, step 2 notes what it saw and reports."""
        results = prompt.split("YOUR RECENT TOOL RESULTS", 1)[1]
        if "(none: request the tools you need)" in results:
            return {"tool_requests": [
                {"tool": "query_logs", "args": {"levels": ["ERROR"], "limit": 10}},
                {"tool": "get_change_history", "args": {"include_diff": True}},
                {"tool": "get_service_dependencies", "args": {}},
            ]}
        ids = first_ids(results, "L", 1) + first_ids(results, "C", 1) + first_ids(results, "D", 1)
        cited = self.maybe_invent("investigator", ids[:2])
        return {
            "notes": [{"kind": "fact", "statement": "errors follow a change", "evidence_ids": cited}],
            "final_report": {
                "outcome": "root_cause_identified",
                "summary": "A recent change is the most likely cause.",
                "hypotheses": [{"rank": 1, "component": self.component("investigator", prompt),
                                "category": "config_change",
                                "mechanism": "a recent change broke it", "confidence": 0.7,
                                "supporting_evidence_ids": cited}],
                "facts": [{"statement": "errors follow the change", "evidence_ids": cited}],
                "recommended_actions": [{"action": "roll back the change",
                                         "priority": "immediate", "rationale": "it precedes the errors"}],
            },
        }

    def answer_reporting(self, prompt):
        allowed = section(prompt, "EVIDENCE IDS YOU MAY CITE\n").split(", ")
        cited = self.maybe_invent("reporting", allowed[:2])
        return {
            "outcome": "root_cause_identified",
            "summary": "A recent change is the most likely cause.",
            "hypotheses": [{"rank": 1, "component": self.component("reporting", prompt),
                            "category": "config_change",
                            "mechanism": "a recent change broke it", "confidence": 0.7,
                            "supporting_evidence_ids": cited}],
            "facts": [{"statement": "errors follow the change", "evidence_ids": cited}],
            "recommended_actions": [{"action": "roll back the change", "priority": "immediate",
                                     "rationale": "it precedes the errors",
                                     "evidence_ids": cited[:1]}],
        }
