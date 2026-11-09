"""ClaudeCliBackend: the command line, the subprocess plumbing and output parsing.

A fake `claude` (a small Python script) stands in for the real CLI, so these tests spend no
quota. tests/live/test_claude_live.py makes one real call when IV_LIVE_LLM=1.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from investigator.llm.backend import LlmBackendError
from investigator.llm.claude_cli import ClaudeCliBackend, build_command, parse_cli_output

REPO_ROOT = Path(__file__).resolve().parents[2]

FAKE_CLI = r'''
import json, os, sys, time
prompt = sys.stdin.read()
args = sys.argv[1:]
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
if mode == "sleep":
    with open(os.environ["FAKE_PID_FILE"], "w") as handle:
        handle.write(str(os.getpid()))
    time.sleep(30)
if mode == "crash":
    sys.stderr.write("boom")
    sys.exit(3)
if mode == "garbage":
    print("this is not json")
    sys.exit(0)
reply = {"prompt": prompt, "args": args, "cwd": os.getcwd()}
print(json.dumps({
    "type": "result", "subtype": "success", "is_error": mode == "api_error",
    "result": json.dumps(reply),
    "usage": {"input_tokens": 5, "cache_creation_input_tokens": 100,
              "cache_read_input_tokens": 20, "output_tokens": 7},
}))
'''


@pytest.fixture
def fake_cli(tmp_path):
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLI, encoding="utf-8")
    return [sys.executable, str(script)]


def test_command_disables_tools_customisations_and_session_files():
    command = build_command(["claude"], model="haiku", system_prompt_file="/tmp/sp.txt")
    assert command[0] == "claude"
    assert "-p" in command
    assert command[command.index("--output-format") + 1] == "json"
    assert command[command.index("--model") + 1] == "haiku"
    assert command[command.index("--tools") + 1] == ""
    assert "--safe-mode" in command
    assert "--no-session-persistence" in command
    assert command[command.index("--system-prompt-file") + 1] == "/tmp/sp.txt"


def test_command_carries_no_prompt_text():
    # Prompt text goes through stdin and a file: on Windows `claude` is a .cmd shim, and
    # cmd.exe would mangle quotes, % signs and newlines in arguments.
    command = build_command(["claude"], model="haiku", system_prompt_file="/tmp/sp.txt")
    for argument in command:
        assert "\n" not in argument
        assert '"' not in argument
        assert "%" not in argument


@pytest.mark.asyncio
async def test_system_prompt_reaches_the_cli_as_a_file_and_is_cleaned_up(fake_cli):
    backend = ClaudeCliBackend(executable=fake_cli, timeout_s=30)
    completion = await backend.complete("SYSTEM 100% \"quoted\"\nline two", "p", "haiku")
    args = json.loads(completion.text)["args"]
    path = Path(args[args.index("--system-prompt-file") + 1])
    assert not path.exists()           # removed after the call


def test_parse_counts_all_prompt_tokens_including_cache():
    raw = json.dumps({"is_error": False, "subtype": "success", "result": "hello",
                      "usage": {"input_tokens": 5, "cache_creation_input_tokens": 100,
                                "cache_read_input_tokens": 20, "output_tokens": 7}})
    text, input_tokens, output_tokens = parse_cli_output(raw)
    assert text == "hello"
    assert input_tokens == 125
    assert output_tokens == 7


def test_parse_rejects_an_error_result():
    raw = json.dumps({"is_error": True, "subtype": "error_during_execution", "result": "",
                      "usage": {}})
    with pytest.raises(LlmBackendError, match="error_during_execution"):
        parse_cli_output(raw)


def test_parse_rejects_non_json_output():
    with pytest.raises(LlmBackendError):
        parse_cli_output("Usage: claude ...")


@pytest.mark.asyncio
async def test_prompt_goes_in_on_stdin_and_the_reply_comes_back(fake_cli):
    backend = ClaudeCliBackend(executable=fake_cli, timeout_s=30)
    completion = await backend.complete("system text", "a long prompt\nwith lines", "haiku")
    reply = json.loads(completion.text)
    assert reply["prompt"] == "a long prompt\nwith lines"
    assert "--safe-mode" in reply["args"]
    assert completion.input_tokens == 125
    assert completion.output_tokens == 7
    assert completion.latency_ms >= 0


@pytest.mark.asyncio
async def test_cli_runs_outside_the_repository(fake_cli):
    # Run inside the repo and the CLI would discover CLAUDE.md, which names plan IDs.
    backend = ClaudeCliBackend(executable=fake_cli, timeout_s=30)
    completion = await backend.complete("s", "p", "haiku")
    cwd = Path(json.loads(completion.text)["cwd"]).resolve()
    assert REPO_ROOT not in cwd.parents and cwd != REPO_ROOT
    assert list(cwd.iterdir()) == []
    for folder in [cwd] + list(cwd.parents):
        assert not (folder / "CLAUDE.md").exists()


@pytest.mark.asyncio
async def test_a_crashing_cli_raises_with_its_stderr(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "crash")
    backend = ClaudeCliBackend(executable=fake_cli, timeout_s=30)
    with pytest.raises(LlmBackendError, match="boom"):
        await backend.complete("s", "p", "haiku")


@pytest.mark.asyncio
async def test_unparseable_cli_output_raises(fake_cli, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "garbage")
    backend = ClaudeCliBackend(executable=fake_cli, timeout_s=30)
    with pytest.raises(LlmBackendError):
        await backend.complete("s", "p", "haiku")


def pid_alive(pid):
    if os.name == "nt":
        listing = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                                 capture_output=True, text=True).stdout
        return str(pid) in listing
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.asyncio
async def test_a_slow_cli_is_killed_at_the_timeout(fake_cli, monkeypatch, tmp_path):
    pid_file = tmp_path / "fake.pid"
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "sleep")
    monkeypatch.setenv("FAKE_PID_FILE", str(pid_file))
    backend = ClaudeCliBackend(executable=fake_cli, timeout_s=2)
    with pytest.raises(TimeoutError):
        await backend.complete("s", "p", "haiku")
    pid = int(pid_file.read_text())
    assert not pid_alive(pid)
