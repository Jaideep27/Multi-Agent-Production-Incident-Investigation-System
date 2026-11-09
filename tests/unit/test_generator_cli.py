"""`python -m investigator.generator`: build (and optionally load) plan entries."""

from investigator.generator.__main__ import main
from investigator.generator.build import build_incident
from investigator.generator.dataset import dataset_checksum
from investigator.generator.plan import DEFAULT_PLAN_PATH, find_entry, load_plan


def test_dry_run_builds_validates_and_prints_without_a_database(capsys):
    assert main(["--only", "PG-1", "--dry-run"]) == 0
    output = capsys.readouterr().out
    dataset = build_incident(find_entry(load_plan(DEFAULT_PLAN_PATH), "PG-1"))
    assert dataset.evidence.incident.incident_id in output
    assert dataset_checksum(dataset) in output
    assert "PG-1" in output
    assert "valid" in output


def test_unknown_plan_id_fails_cleanly(capsys):
    assert main(["--only", "XX-9", "--dry-run"]) == 2
    assert "XX-9" in capsys.readouterr().err
