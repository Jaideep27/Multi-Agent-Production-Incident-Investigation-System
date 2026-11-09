"""The incident plan file (configs/incident_plan.yaml) and its validation."""

import copy
import re
import typing
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from investigator.generator.plan import DEFAULT_PLAN_PATH, IncidentPlan, find_entry, load_plan
from investigator.models.common import DomainName

SQL_DIR = Path(__file__).resolve().parents[2] / "db" / "init"


def raw_plan():
    with open(DEFAULT_PLAN_PATH, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def raw_pg1():
    """The PG-1 entry as plain data, so a test can break one field of a valid entry."""
    plan = raw_plan()
    for entry in plan["incidents"]:
        if entry["plan_id"] == "PG-1":
            return copy.deepcopy(entry)
    raise AssertionError("PG-1 missing from the plan file")


def plan_with(entries):
    return {"incidents": entries}


def test_domains_match_the_sql_check():
    sql = (SQL_DIR / "003_evidence.sql").read_text(encoding="utf-8")
    table = sql.split("CREATE TABLE evidence.incidents")[1].split(";")[0]
    domain_block = table.split("domain IN")[1].split("reported_at")[0]
    from_sql = re.findall(r"'([a-z_]+)'", domain_block)
    assert sorted(from_sql) == sorted(typing.get_args(DomainName))


def test_the_plan_file_loads_and_contains_pg1():
    plan = load_plan(DEFAULT_PLAN_PATH)
    entry = find_entry(plan, "PG-1")
    assert entry.domain == "payment_gateway"
    assert entry.expected_outcome == "identifiable"


def test_find_entry_rejects_an_unknown_plan_id():
    plan = load_plan(DEFAULT_PLAN_PATH)
    with pytest.raises(KeyError):
        find_entry(plan, "XX-99")


def test_a_valid_entry_is_accepted():
    # The baseline for every "rejects" test below: the unmodified entry must pass.
    IncidentPlan.model_validate(plan_with([raw_pg1()]))


def test_plan_rejects_duplicate_plan_ids():
    first = raw_pg1()
    second = raw_pg1()
    second["seed"] = first["seed"] + 1
    with pytest.raises(ValidationError, match="duplicate plan_id"):
        IncidentPlan.model_validate(plan_with([first, second]))


def test_plan_rejects_duplicate_seeds():
    first = raw_pg1()
    second = raw_pg1()
    second["plan_id"] = "PG-9"
    with pytest.raises(ValidationError, match="duplicate seed"):
        IncidentPlan.model_validate(plan_with([first, second]))


def test_entry_rejects_an_unknown_archetype():
    entry = raw_pg1()
    entry["archetype"] = "no_such_archetype"
    with pytest.raises(ValidationError, match="unknown archetype"):
        IncidentPlan.model_validate(plan_with([entry]))


def test_entry_rejects_an_unknown_domain():
    entry = raw_pg1()
    entry["domain"] = "banking"
    with pytest.raises(ValidationError):
        IncidentPlan.model_validate(plan_with([entry]))


def test_entry_rejects_a_badly_formed_plan_id():
    entry = raw_pg1()
    entry["plan_id"] = "pg1"
    with pytest.raises(ValidationError):
        IncidentPlan.model_validate(plan_with([entry]))


def test_entry_rejects_an_unknown_field():
    entry = raw_pg1()
    entry["root_cause"] = "anything"
    with pytest.raises(ValidationError):
        IncidentPlan.model_validate(plan_with([entry]))


def test_archetype_params_are_checked():
    # A pool "shrink" that grows the pool is not this archetype.
    entry = raw_pg1()
    entry["params"]["to_value"] = entry["params"]["from_value"] + 10
    with pytest.raises(ValidationError, match="to_value"):
        IncidentPlan.model_validate(plan_with([entry]))


def test_archetype_params_reject_unknown_fields():
    entry = raw_pg1()
    entry["params"]["surprise"] = 1
    with pytest.raises(ValidationError):
        IncidentPlan.model_validate(plan_with([entry]))


def test_tool_failure_rejects_an_unknown_mode():
    entry = raw_pg1()
    entry["tool_failures"][0]["mode"] = "sometimes"
    with pytest.raises(ValidationError):
        IncidentPlan.model_validate(plan_with([entry]))


def test_noise_rejects_an_unknown_type():
    entry = raw_pg1()
    entry["noise"].append({"type": "solar_flare"})
    with pytest.raises(ValidationError):
        IncidentPlan.model_validate(plan_with([entry]))


def test_decoy_rejects_an_unknown_type():
    entry = raw_pg1()
    entry["decoys"].append({"type": "smoke_and_mirrors", "params": {}})
    with pytest.raises(ValidationError):
        IncidentPlan.model_validate(plan_with([entry]))
