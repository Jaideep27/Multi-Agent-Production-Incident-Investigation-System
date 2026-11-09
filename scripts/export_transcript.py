"""Turn a Claude Code session log (JSONL) into a readable Markdown transcript.

    python scripts/export_transcript.py <session.jsonl> [more.jsonl ...] --out transcripts/session_transcript.md

Keeps: the user's messages, the assistant's replies, and every tool call with a shortened
input and result. Drops: internal reasoning blocks, system reminders, and metadata records.
Email addresses, account/organisation IDs, private Tailscale links and the local test passwords
are redacted, because the transcript is published with the code.
"""

import argparse
import json
import re
import sys
from pathlib import Path

INPUT_LIMIT = 600
RESULT_LIMIT = 800

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
TAILSCALE_LINK = re.compile(r"(?:https?://)?[A-Za-z0-9-]+\.[A-Za-z0-9-]+\.ts\.net(?::\d+)?/?")
TEST_PASSWORD = re.compile(r"\b[a-z]+-test-pw\b")
ACCOUNT_IDS = re.compile(r'(\\?"(?:accountUuid|organizationUuid)\\?"\s*:\s*\\?")[0-9a-fA-F-]+')


# Words to blank out, given on the command line so that they are never stored in this file.
EXTRA_TERMS = []


def clean(text):
    text = REMINDER.sub("", text)
    for term in EXTRA_TERMS:
        text = text.replace(term, "[redacted]")
    text = EMAIL.sub("[email redacted]", text)
    text = TAILSCALE_LINK.sub("[private link redacted]", text)
    text = TEST_PASSWORD.sub("[test password redacted]", text)
    text = ACCOUNT_IDS.sub(r"\1[redacted]", text)
    return text.strip()


def shorten(text, limit):
    if len(text) <= limit:
        return text
    return text[:limit] + "\n… [%d more characters]" % (len(text) - limit)


def result_text(content):
    if isinstance(content, str):
        return content
    parts = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, dict) and block.get("type") == "image":
                parts.append("[image]")
    return "\n".join(parts)


def render(paths):
    lines = ["# Coding-assistant transcript", "",
             "Exported from the Claude Code session log(s) with scripts/export_transcript.py.",
             "Tool inputs and results are shortened; internal reasoning is not included.", ""]
    seen = []
    for path in paths:
        for raw in Path(path).read_text(encoding="utf-8").splitlines():
            record = json.loads(raw)
            if record.get("type") not in ("user", "assistant"):
                continue
            # The log can repeat a message after a resume; keep each one once.
            uuid = record.get("uuid")
            if uuid in seen:
                continue
            seen.append(uuid)
            message = record.get("message") or {}
            content = message.get("content")
            role = record["type"]

            if isinstance(content, str):
                text = clean(content)
                if text:
                    lines.append("## %s" % ("User" if role == "user" else "Assistant"))
                    lines.append("")
                    lines.append(text)
                    lines.append("")
                continue
            if not isinstance(content, list):
                continue
            for block in content:
                kind = block.get("type")
                if kind == "text":
                    text = clean(block.get("text", ""))
                    if text:
                        lines.append("## %s" % ("User" if role == "user" else "Assistant"))
                        lines.append("")
                        lines.append(text)
                        lines.append("")
                elif kind == "tool_use":
                    tool_input = clean(json.dumps(block.get("input", {}), ensure_ascii=False))
                    lines.append("**Tool call: %s**" % block.get("name"))
                    lines.append("")
                    lines.append("```")
                    lines.append(shorten(tool_input, INPUT_LIMIT))
                    lines.append("```")
                    lines.append("")
                elif kind == "tool_result":
                    text = clean(result_text(block.get("content")))
                    lines.append("<details><summary>result</summary>")
                    lines.append("")
                    lines.append("```")
                    lines.append(shorten(text, RESULT_LIMIT).replace("```", "'''"))
                    lines.append("```")
                    lines.append("</details>")
                    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Export a session log as Markdown.")
    parser.add_argument("logs", nargs="+")
    parser.add_argument("--out", default="transcripts/session_transcript.md")
    parser.add_argument("--redact", action="append", default=[], help="a word to blank out; repeatable")
    args = parser.parse_args(argv)
    EXTRA_TERMS.extend(args.redact)
    text = render(args.logs)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(text, encoding="utf-8")
    print("wrote %s (%d KB)" % (args.out, len(text.encode("utf-8")) // 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
