"""Hashing that gives the same answer in every process, on every machine.

Why this module exists: Python's built-in hash() is salted per process (PYTHONHASHSEED), so
anything derived from it (which call fails, which calls are duplicates, cache keys, IDs)
would change from run to run. Everything that must be reproducible goes through here, and a
test bans the built-in hash() from src/.

Values are first turned into canonical JSON (sorted keys, no spaces), so two dicts with the
same content always hash the same, whatever order their keys were inserted in. Several parts
are hashed as one JSON array rather than joined with a separator, so a "|" inside a value
cannot make two different inputs collide.
"""

import hashlib
import json


def canonical_json(value):
    """Serialise value to one fixed JSON string.

    Sets are refused (json raises TypeError) because their order is not stable across
    processes. NaN and infinity are refused because they are not valid JSON.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def sha256_hex(text):
    """SHA-256 of a UTF-8 string, as 64 lowercase hex characters."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def call_fingerprint(tool, args):
    """Identity of a tool call: used for de-duplication and as a cache key.

    args should already be normalised (see models.tools.ToolRequest), so that writing a
    default value or leaving it out gives the same fingerprint.
    """
    text = canonical_json([tool, args])
    return sha256_hex(text)


def unit_draw(seed, tool, args, attempt):
    """A number in [0, 1) fixed by (seed, tool, args, attempt).

    The failure injector compares this with a rule's probability. Because it depends only on
    these inputs, the same call fails the same way on every run, no matter in which order or
    in parallel with what other calls it runs.
    """
    text = canonical_json([seed, tool, args, attempt])
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return unit_from_digest(digest)


def unit_from_digest(digest):
    """Map a hash digest to a float in [0, 1).

    Only the top 53 bits are used: a float holds 53 bits exactly, and dividing a full 64-bit
    value by 2**64 can round up to 1.0.
    """
    first_eight = int.from_bytes(digest[:8], "big")
    top_53_bits = first_eight >> 11
    return top_53_bits / 2**53
