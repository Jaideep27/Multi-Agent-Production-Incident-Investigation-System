"""The deliverable documents exist, and the technical note respects its 500-word limit."""

import re
from pathlib import Path

DOCS = Path(__file__).resolve().parents[2] / "docs"


def word_count(text):
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'./_-]*", text))


def test_technical_note_is_at_most_500_words():
    text = (DOCS / "technical_note.md").read_text(encoding="utf-8")
    assert 200 <= word_count(text) <= 500


def test_architecture_and_limitations_documents_exist():
    for name in ["architecture.md", "limitations.md"]:
        assert (DOCS / name).read_text(encoding="utf-8").strip()
