"""The exported transcript is published, so private links and test passwords must not reach it."""

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "export_transcript.py"


def load_exporter():
    spec = importlib.util.spec_from_file_location("export_transcript", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_private_tailscale_link_is_removed():
    cleaned = load_exporter().clean("open https://my-host.example-tail.ts.net:10000/ to see it")
    assert "ts.net" not in cleaned
    assert "my-host" not in cleaned


def test_local_test_passwords_are_removed():
    cleaned = load_exporter().clean('$env:AGENT_RO_PASSWORD="agent-test-pw"')
    assert "agent-test-pw" not in cleaned


def test_words_given_on_the_command_line_are_removed():
    exporter = load_exporter()
    exporter.EXTRA_TERMS.append("private-word")
    assert "private-word" not in exporter.clean("a private-word here")


def test_ordinary_text_is_kept():
    assert load_exporter().clean("the test passed on port 8000") == "the test passed on port 8000"
