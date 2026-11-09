"""What each agent was asked and what it answered (for the walk-through and the prompts page)."""

import json
import re
from pathlib import Path

from investigator.agents.prompts import SYSTEM_PROMPTS
from investigator.llm.client import extract_json

AGENT_IN_SYSTEM_PROMPT = re.compile(r"You are the (\w+) agent", re.IGNORECASE)
PROMPT_SHOWN = 6000          # characters of each prompt sent to the page
INBOX_TITLE = "MESSAGES FOR YOU\n"
INBOX_END = "\nReply to anything that was asked of you."
EVIDENCE_MARK = "  evidence: "
ONE_MESSAGE_START = re.compile(r"\n(?=From \w+ \(\w+\): )")
ONE_MESSAGE = re.compile(r"From (\w+) \((\w+)\): (.*)", re.DOTALL)


def agent_of(system_prompt):
    match = AGENT_IN_SYSTEM_PROMPT.search(system_prompt or "")
    if match is None:
        return "unknown"
    return match.group(1).lower()


def recover_from_cache(cache_dir, system, summary, started, ended):
    """Cache files written during [started, ended] for this system's agents and incident."""
    cache_dir = Path(cache_dir)
    if not cache_dir.exists():
        return []
    found = []
    for path in cache_dir.glob("*.json"):
        written = path.stat().st_mtime
        if written < started - 2 or written > ended + 2:
            continue
        stored = json.loads(path.read_text(encoding="utf-8"))
        request = stored.get("request", {})
        agent = agent_of(request.get("system"))
        if (system == "single_agent") != (agent == "investigator"):
            continue
        if summary not in request.get("prompt", ""):
            continue
        found.append([written, {"time": written, "agent": agent, "attempt": None, "status": None,
                                "prompt": request.get("prompt", ""),
                                "reply": stored.get("completion", {}).get("text", ""),
                                "problem": None}])
    found.sort(key=lambda pair: pair[0])
    entries = []
    for pair in found:
        entries.append(pair[1])
    return entries


def extract_inbox(prompt):
    """The messages from colleagues inside a prompt, for the walk-through.

    Prompts are shortened before they reach the page and this section comes near the end, so
    it is read from the full prompt here.
    """
    if INBOX_TITLE not in prompt:
        return []
    section = prompt.split(INBOX_TITLE, 1)[1].split(INBOX_END, 1)[0]
    inbox = []
    for part in ONE_MESSAGE_START.split(section):
        match = ONE_MESSAGE.match(part)
        if match is None:
            continue
        text = match.group(3).strip()
        evidence_ids = []
        if EVIDENCE_MARK in text:
            text, listed = text.rsplit(EVIDENCE_MARK, 1)
            evidence_ids = listed.split(", ")
        inbox.append({"sender": match.group(1), "intent": match.group(2), "text": text,
                      "evidence_ids": evidence_ids})
    return inbox


def shape_exchanges(entries):
    """Parse each reply (JSON, possibly wrapped in prose) and shorten long prompts."""
    shaped = []
    for entry in entries:
        parsed = None
        if entry.get("reply"):
            try:
                parsed = extract_json(entry["reply"])
            except ValueError:
                parsed = None
        prompt = entry.get("prompt") or ""
        shaped.append({"agent": entry.get("agent"), "attempt": entry.get("attempt"),
                       "status": entry.get("status"), "problem": entry.get("problem"),
                       "prompt": prompt[:PROMPT_SHOWN], "prompt_length": len(prompt),
                       "inbox": extract_inbox(prompt),
                       "reply": entry.get("reply"), "parsed": parsed})
    return shaped


def agent_prompts():
    """Each agent's fixed instructions and the JSON schema its answer must follow."""
    prompts = []
    for agent in ["orchestrator", "observability", "dependency", "reasoning", "critic",
                  "reporting", "investigator"]:
        text = SYSTEM_PROMPTS[agent]
        instructions = text
        schema = ""
        if "\nSchema:\n" in text:
            instructions, schema = text.split("\nSchema:\n", 1)
        prompts.append({"agent": agent, "instructions": instructions.strip(),
                        "schema": schema.strip()})
    return prompts
