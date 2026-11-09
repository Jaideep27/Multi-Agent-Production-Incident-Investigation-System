"""Deterministic failure injection for the tools.

Rules come from evidence.tool_behavior, one per tool. A decision depends only on
(seed, tool, normalised args, attempt), never on call order or on other calls, so the same
investigation fails the same way every time and in any order. The seed is the incident ID.

Modes
  transient      the first fail_first_n attempts of each distinct call fail, later ones pass
  persistent     every attempt fails (makes evidence unreachable)
  probabilistic  an attempt fails when its draw is below p (a retry draws again)
  partial        the call succeeds but drops items; each item is kept when its draw is below
                 keep_fraction. The draw includes the args, so a different query can still
                 surface an item that this one dropped.

`when` limits a rule to calls whose normalised args contain the given values, for example
{"include_diff": true} to fail only the diff lookups.
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import Field

from investigator.models.common import Probability, StrictModel
from investigator.models.tools import ARGS_MODELS
from investigator.stable_hash import unit_draw

FAILING_MODES = ["transient", "persistent", "probabilistic"]


class FailureRule(StrictModel):
    mode: Literal["transient", "persistent", "partial", "probabilistic"]
    status: Literal["timeout", "error"] = "timeout"
    fail_first_n: int = Field(default=1, ge=1, le=10)
    p: Probability = 0.0
    keep_fraction: Probability = 0.5
    when: dict = Field(default_factory=dict)


@dataclass
class Decision:
    action: str                      # "pass", "fail" or "partial"
    status: str | None = None        # for "fail": "timeout" or "error"
    keep_fraction: float | None = None


def parse_rules(raw_rules):
    """[[tool, rules dict], ...] -> {tool: FailureRule}. Raises ValueError on bad rules."""
    parsed = {}
    for tool, raw in raw_rules:
        if tool not in ARGS_MODELS:
            raise ValueError("failure rule for unknown tool " + str(tool))
        if tool in parsed:
            raise ValueError("two failure rules for " + tool)
        rule = FailureRule.model_validate(raw)
        known_fields = ARGS_MODELS[tool].model_fields
        for key in rule.when:
            if key not in known_fields:
                raise ValueError("rule for %s matches on %s, which the tool does not have"
                                 % (tool, key))
        parsed[tool] = rule
    return parsed


def rule_applies(rule, args):
    for key in rule.when:
        if args.get(key) != rule.when[key]:
            return False
    return True


class FailureInjector:
    def __init__(self, seed, rules):
        self.seed = seed
        self.rules = parse_rules(rules)

    def decide(self, tool, args, attempt):
        """args must be the normalised args (ToolRequest.args); attempt counts from 1."""
        if tool not in self.rules:
            return Decision(action="pass")
        rule = self.rules[tool]
        if not rule_applies(rule, args):
            return Decision(action="pass")

        if rule.mode == "transient":
            if attempt <= rule.fail_first_n:
                return Decision(action="fail", status=rule.status)
            return Decision(action="pass")

        if rule.mode == "persistent":
            return Decision(action="fail", status=rule.status)

        if rule.mode == "probabilistic":
            draw = unit_draw(self.seed, tool, args, attempt)
            if draw < rule.p:
                return Decision(action="fail", status=rule.status)
            return Decision(action="pass")

        return Decision(action="partial", keep_fraction=rule.keep_fraction)

    def keep_ids(self, tool, args, evidence_ids, keep_fraction):
        """The evidence IDs a partial result keeps, in the order given."""
        kept = []
        for evidence_id in evidence_ids:
            draw = unit_draw(self.seed, tool, args, "keep:" + evidence_id)
            if draw < keep_fraction:
                kept.append(evidence_id)
        return kept
