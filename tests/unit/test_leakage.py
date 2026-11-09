"""Leakage: nothing an agent can see may carry the answer key.

Two sides:
* static: source code outside the generator (agents, prompts, tools, scorer, as they are
  added) never names a plan ID, an archetype, or the plan file itself;
* data: generated evidence never contains answer-key text (the validator's leak check;
  tests/integration/test_generator_load.py repeats it on what agent_ro actually reads).
"""

from pathlib import Path

from investigator.generator.build import build_incident
from investigator.generator.plan import DEFAULT_PLAN_PATH, find_entry, load_plan
from investigator.generator.validate import find_leaks

SRC_DIR = Path(__file__).resolve().parents[2] / "src" / "investigator"
GENERATOR_DIR = SRC_DIR / "generator"
SCANNED_SUFFIXES = [".py", ".md", ".txt", ".j2", ".yaml", ".yml", ".json"]


def forbidden_names():
    """Plan IDs and archetype names from the plan file, plus the file's own name."""
    plan = load_plan(DEFAULT_PLAN_PATH)
    names = ["incident_plan"]
    for entry in plan.incidents:
        if entry.plan_id not in names:
            names.append(entry.plan_id)
        if entry.archetype not in names:
            names.append(entry.archetype)
    return names


def find_mentions(directory, names, skip_directory):
    """Return 'path: name' for every scanned file under directory that contains a name."""
    found = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix not in SCANNED_SUFFIXES:
            continue
        if skip_directory is not None and skip_directory in path.parents:
            continue
        text = path.read_text(encoding="utf-8")
        for name in names:
            if name in text:
                found.append("%s: %s" % (path, name))
    return found


def test_forbidden_names_include_pg1_and_its_archetype():
    names = forbidden_names()
    assert "PG-1" in names
    assert find_entry(load_plan(DEFAULT_PLAN_PATH), "PG-1").archetype in names


def test_no_source_outside_the_generator_names_a_plan_id_or_archetype():
    assert find_mentions(SRC_DIR, forbidden_names(), GENERATOR_DIR) == []


def test_scanner_finds_a_planted_name(tmp_path):
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "prompt.md").write_text("Look closely at PG-1.", encoding="utf-8")
    (tmp_path / "clean.py").write_text("x = 1\n", encoding="utf-8")
    found = find_mentions(tmp_path, ["PG-1"], None)
    assert len(found) == 1
    assert "prompt.md" in found[0]


def test_scanner_skips_the_generator_directory(tmp_path):
    generator = tmp_path / "generator"
    generator.mkdir()
    (generator / "archetypes.py").write_text("NAME = 'PG-1'\n", encoding="utf-8")
    assert find_mentions(tmp_path, ["PG-1"], generator) == []


def test_generated_evidence_has_no_answer_key_text():
    dataset = build_incident(find_entry(load_plan(DEFAULT_PLAN_PATH), "PG-1"))
    assert find_leaks(dataset) == []
