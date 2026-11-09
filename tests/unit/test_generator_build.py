"""Building PG-1 in memory: determinism, shape, and the planted story.

Tests may name PG-1 and its parameters; the "no hardcoded answers" rule is about agent
prompts, tools and the scorer, which a leakage test checks separately.
"""

import os
import re
import subprocess
import sys

from investigator.generator.build import build_incident
from investigator.generator.dataset import dataset_checksum
from investigator.generator.plan import DEFAULT_PLAN_PATH, find_entry, load_plan
from investigator.generator.validate import find_problems


def pg1_entry():
    return find_entry(load_plan(DEFAULT_PLAN_PATH), "PG-1")


def evidence_by_id(dataset):
    found = {}
    evidence = dataset.evidence
    for item in evidence.dependencies:
        found[item.evidence_id] = item
    for item in evidence.logs:
        found[item.evidence_id] = item
    for item in evidence.metrics:
        found[item.evidence_id] = item
    for item in evidence.changes:
        found[item.evidence_id] = item
    for item in evidence.past_incidents:
        found[item.evidence_id] = item
    return found


def decisive_items(dataset):
    lookup = evidence_by_id(dataset)
    items = []
    for row in dataset.truth.decisive:
        items.append(lookup[row.evidence_id])
    return items


# ------------------------------------------------------------------ determinism


def test_building_twice_gives_the_same_checksum():
    first = build_incident(pg1_entry())
    second = build_incident(pg1_entry())
    assert dataset_checksum(first) == dataset_checksum(second)
    assert first == second


def test_build_does_not_depend_on_the_python_hash_seed():
    script = (
        "from investigator.generator.build import build_incident\n"
        "from investigator.generator.dataset import dataset_checksum\n"
        "from investigator.generator.plan import DEFAULT_PLAN_PATH, find_entry, load_plan\n"
        "print(dataset_checksum(build_incident(find_entry(load_plan(DEFAULT_PLAN_PATH), 'PG-1'))))\n"
    )
    checksums = []
    for hash_seed in ["1", "2"]:
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = hash_seed
        result = subprocess.run(
            [sys.executable, "-c", script], env=env, capture_output=True, text=True, check=True
        )
        checksums.append(result.stdout.strip())

    expected = dataset_checksum(build_incident(pg1_entry()))
    assert checksums == [expected, expected]


def test_a_different_seed_gives_a_different_dataset():
    entry = pg1_entry()
    other = entry.model_copy(update={"seed": entry.seed + 1})
    first = build_incident(entry)
    second = build_incident(other)
    assert dataset_checksum(first) != dataset_checksum(second)
    assert first.evidence.incident.incident_id != second.evidence.incident.incident_id


# ------------------------------------------------------------------ identifiers


def test_incident_id_is_opaque():
    dataset = build_incident(pg1_entry())
    incident_id = dataset.evidence.incident.incident_id
    assert re.fullmatch(r"inc_[0-9a-f]{8}", incident_id)
    assert "pg" not in incident_id.lower().replace("inc_", "")


def test_every_evidence_id_embeds_the_incident_hash():
    dataset = build_incident(pg1_entry())
    incident_hash = dataset.evidence.incident.incident_id[len("inc_"):]
    ids = list(evidence_by_id(dataset).keys())
    assert len(ids) > 100
    for evidence_id in ids:
        assert evidence_id.split("-")[1] == incident_hash


def test_evidence_lists_are_sorted_by_id():
    evidence = build_incident(pg1_entry()).evidence
    lists = [
        evidence.dependencies,
        evidence.logs,
        evidence.metrics,
        evidence.changes,
        evidence.past_incidents,
    ]
    for items in lists:
        ids = []
        for item in items:
            ids.append(item.evidence_id)
        assert ids == sorted(ids)


def test_log_ids_follow_time_order():
    # Numbering by time means the position of an ID says nothing about the answer.
    logs = build_incident(pg1_entry()).evidence.logs
    for earlier, later in zip(logs, logs[1:]):
        assert earlier.ts <= later.ts


# ------------------------------------------------------------------ validity


def test_built_pg1_passes_the_validator():
    assert find_problems(build_incident(pg1_entry())) == []


# ------------------------------------------------------------------ the planted story


def test_truth_records_the_planned_root_cause():
    entry = pg1_entry()
    scenario = build_incident(entry).truth.scenario
    assert scenario.plan_id == "PG-1"
    assert scenario.archetype == entry.archetype
    assert scenario.seed == entry.seed
    assert scenario.expected_outcome == "identifiable"
    assert scenario.solvable_after_noise is True
    assert scenario.unidentifiable_reason is None
    assert scenario.root_component == entry.params["component"]
    assert scenario.root_category == "config_change"


def test_decisive_evidence_covers_change_log_and_metric():
    dataset = build_incident(pg1_entry())
    kinds = []
    for row in dataset.truth.decisive:
        assert row.availability == "retrievable"
        kinds.append(row.evidence_id[0])
    assert sorted(kinds) == ["C", "L", "M"]


def test_decisive_change_diff_shows_the_pool_shrink():
    entry = pg1_entry()
    dataset = build_incident(entry)
    changes = []
    for item in decisive_items(dataset):
        if item.evidence_id.startswith("C-"):
            changes.append(item)
    assert len(changes) == 1
    change = changes[0]
    assert change.service == entry.params["component"]
    assert change.change_type == "config_change"
    assert change.diff == {
        entry.params["setting"]: {"from": entry.params["from_value"], "to": entry.params["to_value"]}
    }
    # The summary is vague on purpose: an agent has to ask for the diff.
    assert str(entry.params["to_value"]) not in change.summary


def test_dependency_edge_shows_the_current_pool_size():
    entry = pg1_entry()
    dataset = build_incident(entry)
    edges = []
    for edge in dataset.evidence.dependencies:
        if edge.caller == entry.params["component"] and edge.callee == entry.params["dependency"]:
            edges.append(edge)
    assert len(edges) == 1
    assert edges[0].config["max_pool"] == entry.params["to_value"]


def test_symptoms_start_after_the_change():
    entry = pg1_entry()
    dataset = build_incident(entry)
    change_ts = None
    first_error_ts = None
    for item in decisive_items(dataset):
        if item.evidence_id.startswith("C-"):
            change_ts = item.ts
        if item.evidence_id.startswith("L-"):
            first_error_ts = item.ts
    assert change_ts < first_error_ts
    assert first_error_ts < dataset.evidence.incident.reported_at


def test_decoy_deploy_lands_shortly_before_the_real_change():
    dataset = build_incident(pg1_entry())
    lookup = evidence_by_id(dataset)
    decoy_change = None
    for row in dataset.truth.decoys:
        if row.decoy_type == "red_herring_change":
            decoy_change = lookup[row.evidence_id]
    real_change = None
    for item in decisive_items(dataset):
        if item.evidence_id.startswith("C-"):
            real_change = item
    assert decoy_change.change_type == "deploy"
    gap_seconds = (real_change.ts - decoy_change.ts).total_seconds()
    assert 0 < gap_seconds <= 15 * 60


def test_lookalike_past_incident_points_somewhere_else():
    entry = pg1_entry()
    dataset = build_incident(entry)
    lookup = evidence_by_id(dataset)
    lookalikes = []
    for row in dataset.truth.decoys:
        if row.decoy_type == "lookalike_history":
            lookalikes.append(lookup[row.evidence_id])
    assert len(lookalikes) == 1
    assert lookalikes[0].root_cause_component != entry.params["component"]


def test_business_metric_drops_after_the_change():
    dataset = build_incident(pg1_entry())
    summary = dataset.evidence.incident.summary
    floor = int(re.search(r"(\d+)%", summary).group(1))
    assert 60 <= floor <= 80


def test_log_gap_never_hides_decisive_evidence():
    entry = pg1_entry()
    dataset = build_incident(entry)
    gaps = []
    for row in dataset.truth.noise:
        if row.noise_type == "log_gap":
            gaps.append(row.params)
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap["service"] != entry.params["component"]
    assert gap["removed"] > 0

    decisive_ids = []
    for row in dataset.truth.decisive:
        decisive_ids.append(row.evidence_id)
    log_ids = []
    for log in dataset.evidence.logs:
        log_ids.append(log.evidence_id)
    for evidence_id in decisive_ids:
        if evidence_id.startswith("L-"):
            assert evidence_id in log_ids


def test_benign_warnings_are_recorded_as_noise():
    dataset = build_incident(pg1_entry())
    types = []
    for row in dataset.truth.noise:
        types.append(row.noise_type)
    assert sorted(types) == ["benign_warnings", "log_gap"]


def test_first_log_query_is_set_to_time_out():
    dataset = build_incident(pg1_entry())
    rules = {}
    for row in dataset.evidence.tool_behavior:
        rules[row.tool_name] = row.rules
    assert rules == {"query_logs": {"mode": "transient", "fail_first_n": 1, "status": "timeout"}}
