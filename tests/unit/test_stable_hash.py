"""Tests for investigator.stable_hash.

Everything that must come out the same on every run (failure injection, de-duplication,
cache keys, generated IDs) goes through this module, so these tests pin its exact output.
"""

import math
import os
import subprocess
import sys

import pytest

from investigator.stable_hash import (
    call_fingerprint,
    canonical_json,
    sha256_hex,
    unit_draw,
    unit_from_digest,
)


# ------------------------------------------------------------------ canonical_json


def test_canonical_json_ignores_key_order():
    first = canonical_json({"b": 1, "a": 2})
    second = canonical_json({"a": 2, "b": 1})
    assert first == second
    assert first == '{"a":2,"b":1}'


def test_canonical_json_ignores_key_order_in_nested_objects():
    first = canonical_json({"outer": {"y": [1, 2], "x": None}})
    second = canonical_json({"outer": {"x": None, "y": [1, 2]}})
    assert first == second


def test_canonical_json_keeps_list_order():
    assert canonical_json([1, 2]) != canonical_json([2, 1])


def test_canonical_json_keeps_non_ascii_text_as_is():
    assert canonical_json({"name": "café"}) == '{"name":"café"}'


def test_canonical_json_rejects_sets():
    # Set iteration order is not stable across processes, so sets are refused outright.
    with pytest.raises(TypeError):
        canonical_json({"services": {"api", "db"}})


def test_canonical_json_rejects_nan():
    with pytest.raises(ValueError):
        canonical_json({"value": math.nan})


# ---------------------------------------------------------------------- sha256_hex


def test_sha256_hex_matches_the_published_test_vector():
    expected = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert sha256_hex("abc") == expected


# ---------------------------------------------------------------- call_fingerprint


def test_fingerprint_golden_value():
    # Pins the exact format. If this changes, every cache key and de-duplication key changes.
    fingerprint = call_fingerprint("query_logs", {"service": "api", "limit": 50})
    assert fingerprint == "9207eccfcd6469762da09a1ece152519be3170dab7910ec1ccf688a1bcc05b6d"


def test_fingerprint_ignores_argument_order():
    first = call_fingerprint("query_logs", {"service": "api", "limit": 50})
    second = call_fingerprint("query_logs", {"limit": 50, "service": "api"})
    assert first == second


def test_fingerprint_changes_with_the_tool():
    first = call_fingerprint("query_logs", {"service": "api"})
    second = call_fingerprint("query_metrics", {"service": "api"})
    assert first != second


def test_fingerprint_changes_with_an_argument_value():
    first = call_fingerprint("query_logs", {"service": "api"})
    second = call_fingerprint("query_logs", {"service": "db"})
    assert first != second


def test_fingerprint_has_no_separator_collisions():
    # A naive "tool|args" join could make these two equal.
    first = call_fingerprint("a|b", {"x": 1})
    second = call_fingerprint("a", {"b|x": 1})
    assert first != second


# ----------------------------------------------------------------------- unit_draw


def test_unit_draw_golden_value():
    value = unit_draw(42, "query_logs", {"service": "api"}, 1)
    assert value == 0.0946671007076636


def test_unit_draw_is_repeatable():
    first = unit_draw(7, "query_metrics", {"service": "api"}, 2)
    second = unit_draw(7, "query_metrics", {"service": "api"}, 2)
    assert first == second


def test_unit_draw_ignores_argument_order():
    first = unit_draw(7, "query_logs", {"a": 1, "b": 2}, 1)
    second = unit_draw(7, "query_logs", {"b": 2, "a": 1}, 1)
    assert first == second


def test_unit_draw_changes_with_seed_and_with_attempt():
    base = unit_draw(7, "query_logs", {"service": "api"}, 1)
    other_seed = unit_draw(8, "query_logs", {"service": "api"}, 1)
    other_attempt = unit_draw(7, "query_logs", {"service": "api"}, 2)
    assert base != other_seed
    assert base != other_attempt


def test_largest_possible_digest_still_maps_below_one():
    # 2**64 - 1 divided by 2**64 rounds to exactly 1.0 as a float; the mapping must not.
    assert unit_from_digest(b"\xff" * 32) < 1.0
    assert unit_from_digest(b"\x00" * 32) == 0.0


def test_unit_draw_stays_in_the_unit_interval_and_is_roughly_uniform():
    values = []
    for attempt in range(1, 2001):
        value = unit_draw(1, "query_logs", {"service": "api"}, attempt)
        values.append(value)

    for value in values:
        assert 0.0 <= value < 1.0

    # Deterministic inputs, so this is not flaky; it catches a badly skewed mapping.
    mean = sum(values) / len(values)
    below_one_tenth = 0
    for value in values:
        if value < 0.1:
            below_one_tenth = below_one_tenth + 1
    assert 0.47 < mean < 0.53
    assert 160 < below_one_tenth < 240


# ----------------------------------------------------------- stable across processes


def test_results_do_not_depend_on_pythonhashseed():
    # Python's built-in hash() changes per process; ours must not. Run in two fresh
    # interpreters with different hash seeds and compare.
    code = (
        "from investigator.stable_hash import call_fingerprint, unit_draw\n"
        "print(call_fingerprint('query_logs', {'service': 'api', 'level': ['WARN', 'ERROR']}))\n"
        "print(repr(unit_draw(3, 'query_logs', {'service': 'api'}, 1)))\n"
    )
    outputs = []
    for hash_seed in ["1", "2"]:
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = hash_seed
        result = subprocess.run(
            [sys.executable, "-c", code], env=env, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        outputs.append(result.stdout)

    assert outputs[0] == outputs[1]
