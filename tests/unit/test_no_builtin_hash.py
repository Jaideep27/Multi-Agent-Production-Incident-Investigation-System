"""Python's built-in hash() is randomized per process (PYTHONHASHSEED), so anything built on
it would differ between runs. This test bans it from src/.

It parses the code instead of grepping text, so `hashlib.sha256(`, `obj.hash(`, `__hash__`,
comments and strings are not false alarms, while aliasing (`h = hash`) is still caught.
"""

import ast
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[2] / "src"


def find_builtin_hash_uses(directory):
    """Return "file:line" for every reference to the bare name `hash` under directory."""
    found = []
    for path in sorted(directory.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "hash":
                found.append("%s:%d" % (path, node.lineno))
    return found


def test_src_directory_exists_and_has_python_files():
    # Guards against the scan silently passing because it looked in the wrong place.
    python_files = list(SRC_DIR.rglob("*.py"))
    assert len(python_files) > 0


def test_src_does_not_use_builtin_hash():
    found = find_builtin_hash_uses(SRC_DIR)
    assert found == [], "built-in hash() is banned; use investigator.stable_hash: %s" % found


def test_scanner_catches_a_call_and_an_alias(tmp_path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("x = hash('a')\nh = hash\n", encoding="utf-8")
    found = find_builtin_hash_uses(tmp_path)
    assert len(found) == 2


def test_scanner_ignores_lookalikes(tmp_path):
    ok_file = tmp_path / "ok.py"
    ok_file.write_text(
        "import hashlib\n"
        "digest = hashlib.sha256(b'a').hexdigest()\n"
        "class Thing:\n"
        "    def __hash__(self):\n"
        "        return 1\n"
        "    def hash(self):\n"
        "        return 2\n"
        "value = Thing().hash()\n"
        "# hash('in a comment')\n"
        "text = \"hash('in a string')\"\n",
        encoding="utf-8",
    )
    found = find_builtin_hash_uses(tmp_path)
    assert found == []
