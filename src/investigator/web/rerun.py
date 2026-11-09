"""Re-running one test case from the viewer.

The viewer itself never runs agents: it starts `python -m investigator.batch` as a separate
process (the worker), which uses the harness and agent roles from the environment, and then
reads the new runs from the database like any other run.

* One rerun at a time.
* Each rerun gets its own label (<label>-rerun-<time>), so the official evaluation results
  are never replaced.
* mode "replay" reuses recorded model answers where the prompt is the same (normally all of
  them, so no quota is spent); mode "fresh" calls the model for every step.
* model "haiku" (the evaluation's model) or "sonnet". Recorded answers are kept per model,
  so the first Sonnet run of a case calls the model even in replay mode.
* Reruns use the agent-to-agent pattern, and the page can follow the conversation live.
* Reruns also give the agents the general background of the company (never the answer). That
  changes the prompts, so the first rerun of a case with a background calls the model even in
  replay mode, and a rerun is not a like-for-like comparison with the official result.
"""

import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CACHE_MODES = {"replay": "record", "fresh": "off"}
SYSTEMS = ["multi_agent", "single_agent"]
MODELS = ["haiku", "sonnet"]


def default_launcher(command, log_path):
    log_file = open(log_path, "w", encoding="utf-8")
    return subprocess.Popen(command, stdout=log_file, stderr=subprocess.STDOUT)


class RerunManager:
    def __init__(self, base_label, log_dir="runs/reruns", launcher=default_launcher,
                 clock=time.time):
        self.base_label = base_label
        self.log_dir = Path(log_dir)
        self.launcher = launcher
        self.clock = clock
        self.job = None

    def busy(self):
        return self.job is not None and self.job["process"].poll() is None

    def start(self, incident_id, systems, mode, model="haiku"):
        """Start a rerun; returns (job summary, error message)."""
        if mode not in CACHE_MODES:
            return None, "mode must be replay or fresh"
        if model not in MODELS:
            return None, "model must be haiku or sonnet"
        if not systems:
            return None, "choose at least one system"
        for system in systems:
            if system not in SYSTEMS:
                return None, "unknown system " + str(system)
        if self.busy():
            return None, "another rerun is still running"

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        label = "%s-rerun-%s" % (self.base_label, stamp)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / (label + ".log")
        command = [sys.executable, "-m", "investigator.batch", "--label", label,
                   "--incidents", incident_id, "--systems"] + list(systems) + [
                   "--cache-mode", CACHE_MODES[mode], "--model", model, "--company-context",
                   "--pattern", "exchange"]
        process = self.launcher(command, log_path)
        self.job = {"incident_id": incident_id, "systems": list(systems), "mode": mode,
                    "model": model, "label": label, "log_path": log_path, "started": self.clock(),
                    "process": process}
        return self.summary(), None

    def summary(self):
        if self.job is None:
            return {"state": "idle"}
        code = self.job["process"].poll()
        state = "running" if code is None else ("finished" if code == 0 else "failed")
        tail = ""
        if state == "failed" and self.job["log_path"].exists():
            lines = self.job["log_path"].read_text(encoding="utf-8", errors="replace").splitlines()
            tail = "\n".join(lines[-8:])
        return {"state": state, "incident_id": self.job["incident_id"],
                "systems": self.job["systems"], "mode": self.job["mode"],
                "model": self.job["model"], "label": self.job["label"],
                "elapsed_s": round(self.clock() - self.job["started"]), "log_tail": tail}
