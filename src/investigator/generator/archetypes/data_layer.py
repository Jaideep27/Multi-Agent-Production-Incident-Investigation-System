"""Archetypes in caches and databases: cache degradation and lock contention."""

from typing import Literal

from investigator.generator.story import tell
from investigator.models.common import NonEmptyText, StrictModel


class CacheDegradationParams(StrictModel):
    component: NonEmptyText
    mode: Literal["restart", "eviction_policy", "aligned_ttl"]
    ttl_s: int = 600


def cache_degradation(draft, rng, raw_params):
    params = CacheDegradationParams.model_validate(raw_params)
    story = {
        "root": params.component,
        "category": "cache_failure",
        "summary": "{label} dropped to {floor}%",
        "floor": [76, 88],
        "metrics": [{"name": "hit_ratio_pct", "unit": "percent", "bad_mean": 14.0, "bad_sd": 3.0}],
        "caller_message": "cache {callee} miss; database fallback took 2400ms route={route}",
    }
    if params.mode == "restart":
        story["mechanism"] = ("%s restarted with an empty dataset; every lookup missed and the "
                              "load stampeded onto the database behind it." % params.component)
        story["logs"] = [
            ["WARN", "server restarted; dataset empty (0 keys loaded)", {}],
            ["WARN", "keyspace misses spiking; hit ratio below 20%", {}],
        ]
    elif params.mode == "eviction_policy":
        story["mechanism"] = ("A config change switched %s from noeviction to allkeys-lru; "
                              "idempotency keys were evicted, so retries were treated as new "
                              "requests." % params.component)
        story["change"] = {"change_type": "config_change", "version": None,
                           "summary": "%s configuration updated" % params.component,
                           "diff": {"maxmemory-policy": {"from": "noeviction", "to": "allkeys-lru"}}}
        story["logs"] = [["WARN", "evicted %d keys in the last minute (policy allkeys-lru)"
                          % rng.randint(12000, 30000), {}]]
        story["metrics"] = [{"name": "evicted_keys_per_s", "unit": "keys/s", "healthy_mean": 0.0,
                             "healthy_sd": 0.0, "bad_mean": 310.0, "bad_sd": 25.0},
                            {"name": "hit_ratio_pct", "unit": "percent", "bad_mean": 61.0,
                             "bad_sd": 4.0}]
        story["summary"] = "Duplicate payment attempts reported; {label} at {floor}%"
    else:
        story["mechanism"] = ("A config change gave every %s key the same %ds TTL; keys expire "
                              "together, and each mass expiry sends a burst of misses to the "
                              "database." % (params.component, params.ttl_s))
        story["change"] = {"change_type": "config_change", "version": None,
                           "summary": "%s configuration updated" % params.component,
                           "diff": {"default_ttl_s": {"from": "jittered 300-900", "to": params.ttl_s}}}
        story["logs"] = [["WARN", "%d keys expired within 2s" % rng.randint(30000, 50000), {}]]
    tell(draft, rng, story)


class LockContentionParams(StrictModel):
    component: NonEmptyText
    table: NonEmptyText
    cause: Literal["hot_row", "long_transaction"]


def lock_contention(draft, rng, raw_params):
    params = LockContentionParams.model_validate(raw_params)
    if params.cause == "hot_row":
        mechanism = ("Many concurrent checkouts updated the same %s row on %s; they queued on "
                     "its row lock until the lock wait timed out." % (params.table, params.component))
        first_log = "lock wait timeout exceeded on %s (waited 5000ms)" % params.table
        second_log = "%d sessions waiting for the same row lock on %s" % (rng.randint(40, 90), params.table)
    else:
        pid = rng.randint(2000, 9000)
        mechanism = ("A long-running transaction (pid %d) held row locks on %s in %s; writes "
                     "queued behind it and timed out." % (pid, params.table, params.component))
        first_log = "process %d holds locks on %s for 840s; blocking %d writers" % (
            pid, params.table, rng.randint(30, 60))
        second_log = "lock wait timeout exceeded on %s (waited 5000ms)" % params.table
    tell(draft, rng, {
        "root": params.component,
        "category": "db_contention",
        "mechanism": mechanism,
        "summary": "{label} dropped to {floor}%",
        "floor": [70, 84],
        "metrics": [{"name": "lock_waits_per_s", "unit": "waits/s", "healthy_mean": 0.5,
                     "healthy_sd": 0.3, "bad_mean": 140.0, "bad_sd": 12.0}],
        "logs": [["WARN", first_log, {}], ["ERROR", second_log, {}]],
        "caller_message": "upstream {callee} call failed: lock wait timeout route={route}",
    })
