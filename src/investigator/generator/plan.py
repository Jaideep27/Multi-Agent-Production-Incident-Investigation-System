"""Read and validate configs/incident_plan.yaml.

The plan is answer-key data (it names root causes), so only the generator reads it.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, model_validator

from investigator.generator.archetypes import ARCHETYPES
from investigator.generator.decoys import DECOYS
from investigator.generator.noise import NOISE_PARAMS
from investigator.models.common import (
    DomainName,
    NonEmptyText,
    NonNegativeInt,
    RootCauseCategory,
    StrictModel,
    ToolName,
    find_duplicates,
)

DEFAULT_PLAN_PATH = Path(__file__).resolve().parents[3] / "configs" / "incident_plan.yaml"

FailureMode = Literal["transient", "persistent", "partial", "probabilistic"]
DecoyType = Literal["red_herring_change", "lookalike_history", "downstream_symptom", "unrelated_spike"]
NoiseType = Literal["benign_warnings", "log_gap", "clock_skew", "conflicting_sources"]
EvidencePart = Literal["change", "logs", "metric"]


class ToolFailureSpec(StrictModel):
    tool: ToolName
    mode: FailureMode
    # Meaning is defined by the failure injector (step 3); stored as given.
    params: dict = Field(default_factory=dict)


class DecoySpec(StrictModel):
    type: DecoyType
    params: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_params(self):
        params_model, apply_function = DECOYS[self.type]
        params_model.model_validate(self.params)
        return self


class NoiseSpec(StrictModel):
    type: NoiseType
    params: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_params(self):
        NOISE_PARAMS[self.type].model_validate(self.params)
        return self


class AlternativeSpec(StrictModel):
    category: RootCauseCategory
    component: NonEmptyText
    why_plausible: NonEmptyText


class InsufficiencySpec(StrictModel):
    """Why an inconclusive incident cannot be solved from its data (truth keeps the real cause)."""

    reason: NonEmptyText
    withhold: list[EvidencePart] = Field(default_factory=list)     # never generated
    unreachable: list[EvidencePart] = Field(default_factory=list)  # generated, blocked by a rule
    alternatives: list[AlternativeSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def check_parts(self):
        if not self.withhold and not self.unreachable:
            raise ValueError("an inconclusive incident must withhold or block some evidence")
        for part in self.withhold:
            if part in self.unreachable:
                raise ValueError("%s cannot be both withheld and unreachable" % part)
        return self


class PlanEntry(StrictModel):
    plan_id: str = Field(pattern=r"^[A-Z]{2,3}-[0-9]+$")
    domain: DomainName
    archetype: str
    seed: NonNegativeInt
    expected_outcome: Literal["identifiable", "inconclusive"]
    params: dict
    decoys: list[DecoySpec] = Field(default_factory=list)
    noise: list[NoiseSpec] = Field(default_factory=list)
    tool_failures: list[ToolFailureSpec] = Field(default_factory=list)
    insufficiency: InsufficiencySpec | None = None

    @model_validator(mode="after")
    def check_outcome(self):
        if (self.expected_outcome == "inconclusive") != (self.insufficiency is not None):
            raise ValueError("insufficiency is required for inconclusive incidents and only for them")
        return self

    @model_validator(mode="after")
    def check_archetype_and_params(self):
        if self.archetype not in ARCHETYPES:
            raise ValueError("unknown archetype: " + self.archetype)
        # Each archetype owns a Pydantic model for its parameters; this raises if they are wrong.
        archetype = ARCHETYPES[self.archetype]
        archetype.Params.model_validate(self.params)
        return self


class IncidentPlan(StrictModel):
    incidents: list[PlanEntry]

    @model_validator(mode="after")
    def check_unique(self):
        plan_ids = []
        seeds = []
        for entry in self.incidents:
            plan_ids.append(entry.plan_id)
            seeds.append(entry.seed)
        duplicate_ids = find_duplicates(plan_ids)
        if duplicate_ids:
            raise ValueError("duplicate plan_id: " + ", ".join(duplicate_ids))
        duplicate_seeds = find_duplicates(seeds)
        if duplicate_seeds:
            text = []
            for seed in duplicate_seeds:
                text.append(str(seed))
            raise ValueError("duplicate seed: " + ", ".join(text))
        return self


def load_plan(path):
    with open(path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return IncidentPlan.model_validate(raw)


def find_entry(plan, plan_id):
    for entry in plan.incidents:
        if entry.plan_id == plan_id:
            return entry
    raise KeyError("no plan entry " + plan_id)
