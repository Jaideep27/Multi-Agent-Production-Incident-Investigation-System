"""The evaluation script must pass the chosen pattern and options on to the batch."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_eval.py"


def load_script():
    spec = importlib.util.spec_from_file_location("run_eval_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_with(monkeypatch, tmp_path, argv):
    script = load_script()
    seen = {}

    def fake_batch(args):
        seen["batch"] = args
        return 0

    def fake_report(args):
        seen["report"] = args

    monkeypatch.setattr(script.batch, "main", fake_batch)
    monkeypatch.setattr(script.report, "main", fake_report)
    monkeypatch.setattr(script, "incident_ids", lambda plans: ["inc_00000001"])
    assert script.main(argv) == 0
    return seen


def test_the_pattern_and_options_reach_the_batch(monkeypatch, tmp_path):
    seen = run_with(monkeypatch, tmp_path, ["--label", "eval-v4", "--pattern", "exchange",
                                            "--company-context", "--cache-mode", "off"])
    command = seen["batch"]
    assert command[command.index("--pattern") + 1] == "exchange"
    assert "--company-context" in command
    assert command[command.index("--cache-mode") + 1] == "off"
    assert command[command.index("--label") + 1] == "eval-v4"
    assert seen["report"][seen["report"].index("--label") + 1] == "eval-v4"


def test_without_options_it_runs_as_before(monkeypatch, tmp_path):
    command = run_with(monkeypatch, tmp_path, ["--label", "eval-x"])["batch"]
    assert command[command.index("--pattern") + 1] == "blackboard"
    assert "--company-context" not in command
    assert command[command.index("--cache-mode") + 1] == "record"
