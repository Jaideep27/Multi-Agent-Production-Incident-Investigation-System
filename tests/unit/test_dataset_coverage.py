"""Step 9 gate (part 1): the plan file has the dataset docs/implementation_plan.md §4 promises,
and every incident in it builds, validates and repeats exactly."""

import pytest

from investigator.generator.archetypes import ARCHETYPES
from investigator.generator.build import build_incident
from investigator.generator.dataset import dataset_checksum
from investigator.generator.plan import DEFAULT_PLAN_PATH, load_plan
from investigator.generator.validate import find_problems

PLAN = load_plan(DEFAULT_PLAN_PATH)
ALL = PLAN.incidents
# The original 25 (five domains) and the 12-story evaluation set (INC-*), counted separately.
ENTRIES = []
STORIES = []
for _entry in ALL:
    if _entry.plan_id.startswith("INC-"):
        STORIES.append(_entry)
    else:
        ENTRIES.append(_entry)
DOMAINS = ["crypto_exchange", "payment_gateway", "ott", "ecommerce", "food_delivery"]


def plan_ids(entries):
    ids = []
    for entry in entries:
        ids.append(entry.plan_id)
    return ids


# ------------------------------------------------------------------ coverage (counts in §4)


def test_twenty_five_incidents_five_per_domain():
    assert len(ENTRIES) == 25
    for domain in DOMAINS:
        count = 0
        for entry in ENTRIES:
            if entry.domain == domain:
                count = count + 1
        assert count == 5, domain


def test_twenty_one_identifiable_and_four_inconclusive():
    inconclusive = []
    for entry in ENTRIES:
        if entry.expected_outcome == "inconclusive":
            inconclusive.append(entry.plan_id)
    assert sorted(inconclusive) == ["CX-4", "EC-4", "FD-3", "PG-5"]


def test_eleven_incidents_have_tool_failures():
    with_failures = []
    for entry in ENTRIES:
        if entry.tool_failures:
            with_failures.append(entry.plan_id)
    assert len(with_failures) == 11


def test_every_category_appears_in_two_or_three_incidents():
    counts = {}
    for entry in ENTRIES:
        category = build_incident(entry).truth.scenario.root_category
        counts[category] = counts.get(category, 0) + 1
    assert len(counts) == 10
    for category in counts:
        assert 2 <= counts[category] <= 3, category


def test_every_archetype_is_used():
    used = []
    for entry in ALL:
        if entry.archetype not in used:
            used.append(entry.archetype)
    assert sorted(used) == sorted(ARCHETYPES.keys())
    assert len(used) >= 10


def test_plan_ids_follow_the_domain_prefixes():
    prefixes = {"crypto_exchange": "CX", "payment_gateway": "PG", "ott": "OTT",
                "ecommerce": "EC", "food_delivery": "FD"}
    for entry in ENTRIES:
        assert entry.plan_id.split("-")[0] == prefixes[entry.domain]


def test_inconclusive_entries_and_only_they_describe_what_is_missing():
    for entry in ALL:
        if entry.expected_outcome == "inconclusive":
            assert entry.insufficiency is not None, entry.plan_id
            assert entry.insufficiency.alternatives, entry.plan_id
        else:
            assert entry.insufficiency is None, entry.plan_id


# ------------------------------------------------------------------ every incident


@pytest.mark.parametrize("entry", ALL, ids=plan_ids(ALL))
def test_incident_builds_and_passes_the_validator(entry):
    assert find_problems(build_incident(entry)) == []


@pytest.mark.parametrize("entry", ALL, ids=plan_ids(ALL))
def test_incident_builds_identically_twice(entry):
    assert dataset_checksum(build_incident(entry)) == dataset_checksum(build_incident(entry))


@pytest.mark.parametrize("entry", ALL, ids=plan_ids(ALL))
def test_incident_truth_matches_its_outcome(entry):
    truth = build_incident(entry).truth
    availabilities = []
    for row in truth.decisive:
        availabilities.append(row.availability)
    if entry.expected_outcome == "identifiable":
        assert "retrievable" in availabilities
        assert truth.withheld == []
    else:
        assert "retrievable" not in availabilities
        assert availabilities or truth.withheld
        assert truth.scenario.unidentifiable_reason


def test_incident_ids_are_all_different():
    ids = []
    for entry in ALL:
        ids.append(build_incident(entry).evidence.incident.incident_id)
    assert len(set(ids)) == len(ALL)


def test_pg1_is_unchanged_by_the_new_archetypes():
    # The real Haiku runs and docs refer to this build; scaling up must not alter it.
    for entry in ENTRIES:
        if entry.plan_id == "PG-1":
            checksum = dataset_checksum(build_incident(entry))
    assert checksum == "8edeaab3737974ec5db0b9c62ff25417d4a3884999a475bd814c6a95d7c26db1"


# ------------------------------------------------------------------ the 12-story evaluation set


def test_twelve_stories_on_the_three_story_platforms():
    assert plan_ids(STORIES) == ["INC-%02d" % number for number in range(1, 13)]
    for entry in STORIES:
        assert entry.domain in ["online_retail", "search_platform", "saas_platform"]


def test_stories_have_one_inconclusive_case():
    inconclusive = []
    for entry in STORIES:
        if entry.expected_outcome == "inconclusive":
            inconclusive.append(entry.plan_id)
    assert inconclusive == ["INC-11"]


def test_stories_cover_every_kind_of_trap():
    decoys = []
    noise = []
    failure_modes = []
    for entry in STORIES:
        for decoy in entry.decoys:
            decoys.append(decoy.type)
        for item in entry.noise:
            noise.append(item.type)
        for failure in entry.tool_failures:
            failure_modes.append(failure.mode)
    for kind in ["red_herring_change", "lookalike_history", "downstream_symptom"]:
        assert kind in decoys, kind
    for kind in ["conflicting_sources", "clock_skew"]:
        assert kind in noise, kind
    for mode in ["transient", "partial", "persistent"]:
        assert mode in failure_modes, mode


def test_a_cache_that_evicts_live_keys_also_shows_a_falling_hit_ratio():
    # Regression: an eviction storm with a healthy hit ratio contradicted itself (the Critic
    # rightly refused to accept it in the first eval-v3 run).
    for entry in STORIES:
        if entry.archetype != "session_cache_saturation":
            continue
        dataset = build_incident(entry)
        onset = None
        for log in dataset.evidence.logs:
            if log.service == entry.params["component"] and "evicting" in log.message:
                if onset is None or log.ts < onset:
                    onset = log.ts
        for series in dataset.evidence.metrics:
            if series.service == entry.params["component"] and series.metric_name == "hit_ratio_pct":
                after = []
                for point in series.points:
                    if point.ts > onset:
                        after.append(point.value)
                assert max(after) < 80.0


def test_a_story_stays_solvable_with_the_log_tool_down_for_good():
    found = False
    for entry in STORIES:
        persistent_log_failure = False
        for failure in entry.tool_failures:
            if failure.tool == "query_logs" and failure.mode == "persistent":
                persistent_log_failure = True
        if not persistent_log_failure:
            continue
        found = True
        dataset = build_incident(entry)
        assert entry.expected_outcome == "identifiable"
        for row in dataset.truth.decisive:
            assert not row.evidence_id.startswith("L-"), row.evidence_id
            assert row.availability == "retrievable"
    assert found
