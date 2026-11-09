"""ClaudeCliBackend: one `claude -p` subprocess per completion.

Flags (checked against `claude --help`, CLI 2.1.319, and two real calls on 2026-11-07):
  -p --output-format json   one JSON result object on stdout
  --model <alias>           e.g. haiku (resolved to claude-haiku-5-5)
  --tools ""                no built-in tools: agents cannot read files or run commands
  --safe-mode               no CLAUDE.md, hooks, skills, plugins or MCP servers. Without it the
                            CLI would load this repo's CLAUDE.md, which names plan IDs.
  --no-session-persistence  do not save a session per call
  --system-prompt-file      the system prompt, from a temp file
(--bare would isolate even more, but it only accepts an API key, not a Pro-plan login.)

No prompt text is ever a command-line argument: the user prompt goes in on stdin and the
system prompt through a file. On Windows `claude` is an npm .cmd shim run by cmd.exe, which
mangles quotes, % signs and newlines in arguments.

The process runs in an empty temporary directory, outside the repository.
"""

import asyncio
import json
import os
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path

from investigator.llm.backend import Completion, LlmBackend, LlmBackendError


def build_command(executable, model, system_prompt_file):
    command = list(executable)
    command.extend([
        "-p",
        "--output-format", "json",
        "--model", model,
        "--tools", "",
        "--safe-mode",
        "--no-session-persistence",
        "--system-prompt-file", str(system_prompt_file),
    ])
    return command


def parse_cli_output(raw):
    """(text, input_tokens, output_tokens) from the CLI's JSON result."""
    try:
        data = json.loads(raw)
    except ValueError:
        raise LlmBackendError("claude printed something that is not JSON: %r" % raw[:300])
    if not isinstance(data, dict):
        raise LlmBackendError("claude printed JSON that is not an object")

    subtype = data.get("subtype")
    if data.get("is_error") or subtype != "success":
        raise LlmBackendError("claude reported %s: %s" % (subtype, str(data.get("result"))[:300]))

    usage = data.get("usage") or {}
    input_tokens = 0
    for key in ["input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"]:
        input_tokens = input_tokens + int(usage.get(key) or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    return str(data.get("result") or ""), input_tokens, output_tokens


def default_executable():
    found = shutil.which("claude")
    if found is None:
        raise LlmBackendError("the claude CLI is not on PATH")
    return [found]


async def kill_process_tree(process):
    """Kill the CLI and anything it started (the .cmd shim starts node as a child)."""
    if process.returncode is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       capture_output=True, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    await process.wait()


class ClaudeCliBackend(LlmBackend):
    def __init__(self, executable=None, timeout_s=120.0):
        if executable is None:
            executable = default_executable()
        self.executable = list(executable)
        self.timeout_s = timeout_s
        self.work_dir = tempfile.mkdtemp(prefix="iv-llm-cwd-")       # stays empty
        self.prompt_dir = tempfile.mkdtemp(prefix="iv-llm-prompts-")

    async def complete(self, system, prompt, model):
        handle, system_path = tempfile.mkstemp(suffix=".txt", dir=self.prompt_dir)
        with os.fdopen(handle, "w", encoding="utf-8") as system_file:
            system_file.write(system)

        command = build_command(self.executable, model, system_path)
        extra = {}
        if os.name != "nt":
            extra["start_new_session"] = True       # own process group, for kill_process_tree
        started = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.work_dir,
                **extra,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(prompt.encode("utf-8")), self.timeout_s
                )
            except TimeoutError:
                await kill_process_tree(process)
                raise TimeoutError("claude did not finish within %.0fs" % self.timeout_s)
        finally:
            Path(system_path).unlink(missing_ok=True)

        latency_ms = int((time.monotonic() - started) * 1000)
        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="replace")[-500:]
            raise LlmBackendError("claude exited with %d: %s" % (process.returncode, message))

        text, input_tokens, output_tokens = parse_cli_output(stdout.decode("utf-8"))
        return Completion(text=text, input_tokens=input_tokens, output_tokens=output_tokens,
                          latency_ms=latency_ms, model=model)
