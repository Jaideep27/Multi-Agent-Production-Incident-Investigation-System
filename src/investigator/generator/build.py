"""Build one incident from its plan entry, entirely in memory (no database).

Every random choice comes from one random.Random(entry.seed), made in a fixed order, so the
same entry always gives the same dataset in any process (tests/unit/test_generator_build.py
checks this across PYTHONHASHSEED values).
"""

import random
from datetime import datetime, timedelta, timezone

from investigator.generator import GENERATOR_VERSION
from investigator.generator.archetypes import ARCHETYPES
from investigator.generator.baseline import add_baseline
from investigator.generator.dataset import ScenarioRow
from investigator.generator.decoys import DECOYS
from investigator.generator.draft import Draft
from investigator.generator.finalize import finalize
from investigator.generator.noise import NOISE
from investigator.generator.topology import topology_for
from investigator.stable_hash import canonical_json, sha256_hex

EPOCH = datetime(2026, 10, 1, tzinfo=timezone.utc)
WINDOW_MINUTES = 60


def incident_id_for(entry):
    """Opaque and stable: the plan ID is hashed, never shown."""
    digest = sha256_hex(canonical_json(["incident", entry.plan_id, entry.seed, GENERATOR_VERSION]))
    return "inc_" + digest[:8]


def pick_window_start(rng):
    return EPOCH + timedelta(
        days=rng.randint(0, 29),
        hours=rng.randint(8, 21),
        minutes=rng.choice([0, 15, 30, 45]),
    )


def add_tool_failures(draft, tool_failures):
    for spec in tool_failures:
        for tool_name, rules in draft.tool_rules:
            if tool_name == spec.tool:
                raise ValueError("two failure rules for tool " + spec.tool)
        rules = {"mode": spec.mode}
        for key in spec.params:
            rules[key] = spec.params[key]
        draft.tool_rules.append([spec.tool, rules])


def make_scenario(entry, draft):
    identifiable = entry.expected_outcome == "identifiable"
    reason = draft.scenario.get("unidentifiable_reason")
    if not identifiable and reason is None:
        # Only story-based archetypes apply an insufficiency spec.
        raise ValueError(entry.plan_id + ": this archetype cannot build an inconclusive incident")
    return ScenarioRow(
        plan_id=entry.plan_id,
        archetype=entry.archetype,
        seed=entry.seed,
        generator_version=GENERATOR_VERSION,
        expected_outcome=entry.expected_outcome,
        solvable_after_noise=identifiable,
        unidentifiable_reason=reason,
        root_component=draft.scenario["root_component"],
        root_category=draft.scenario["root_category"],
        mechanism=draft.scenario["mechanism"],
        contributing_factors=draft.scenario["contributing_factors"],
    )


def build_incident(entry):
    rng = random.Random(entry.seed)
    window_start = pick_window_start(rng)
    draft = Draft(
        domain=entry.domain,
        topology=topology_for(entry.domain),
        window_start=window_start,
        window_end=window_start + timedelta(minutes=WINDOW_MINUTES),
    )

    add_baseline(draft, rng)
    if entry.insufficiency is not None:
        draft.insufficiency = entry.insufficiency.model_dump(mode="json")

    archetype = ARCHETYPES[entry.archetype]
    archetype.apply(draft, rng, entry.params)

    for decoy in entry.decoys:
        params_model, apply_decoy = DECOYS[decoy.type]
        apply_decoy(draft, rng, decoy.params)

    for noise in entry.noise:
        apply_noise = NOISE[noise.type]
        apply_noise(draft, rng, noise.params)

    add_tool_failures(draft, entry.tool_failures)

    scenario = make_scenario(entry, draft)
    return finalize(draft, incident_id_for(entry), scenario)
