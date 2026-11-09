"""Seeded incident generator: archetype x domain topology + decoys + noise.

Everything here is part of the answer-key side of the system. Code outside this package must
not import the plan file or name plan IDs and archetypes (see tests/unit/test_leakage.py).

Pipeline (build.py): plan entry -> draft (mutable working set) -> finalize (evidence IDs,
Pydantic dataset) -> validate -> load into Postgres as generator_rw.
"""

# Part of every incident ID and stored in truth.scenarios; bump it when the output changes.
GENERATOR_VERSION = "0.2.0"
