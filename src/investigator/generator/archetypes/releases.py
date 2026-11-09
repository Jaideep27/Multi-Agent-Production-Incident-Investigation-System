"""Archetypes caused by shipping something: a bad release, a retry-policy regression."""

from typing import Literal

from investigator.generator.story import tell
from investigator.models.common import NonEmptyText, StrictModel


class BadReleaseParams(StrictModel):
    component: NonEmptyText
    mode: Literal["query_overload", "memory_leak", "consumer_bug", "input_exception"]
    version: NonEmptyText
    previous_version: NonEmptyText
    database: str | None = None          # query_overload: the database it overloads


def bad_release(draft, rng, raw_params):
    params = BadReleaseParams.model_validate(raw_params)
    image_diff = {"image": {"from": "%s:%s" % (params.component, params.previous_version),
                            "to": "%s:%s" % (params.component, params.version)}}
    story = {
        "root": params.component,
        "category": "bad_deploy",
        "summary": "{label} dropped to {floor}% after a release",
        "floor": [72, 86],
        "change": {"change_type": "deploy", "version": params.version,
                   "summary": "%s deploy %s" % (params.component, params.version)},
    }
    if params.mode == "query_overload":
        image_diff["profile_lookup"] = {"from": "cached", "to": "joins watch_history per request"}
        story["mechanism"] = ("Release %s of %s replaced a cached lookup with a per-request join; "
                              "the new query pattern overloaded %s."
                              % (params.version, params.component, params.database))
        story["metrics"] = [{"name": "cpu_pct", "unit": "percent", "service": params.database,
                             "bad_mean": 96.0, "bad_sd": 1.5},
                            {"name": "p99_latency_ms", "unit": "ms", "bad_mean": 3600.0, "bad_sd": 200.0}]
        story["logs"] = [["WARN", "slow query 3400ms: SELECT p.* FROM profiles p JOIN watch_history w "
                                  "ON w.profile_id = p.id WHERE p.account_id = $1", {}]]
        story["summary"] = "Profile switching errors after a release; {label} at {floor}%"
    elif params.mode == "memory_leak":
        image_diff["session_cache"] = {"from": "bounded LRU", "to": "unbounded map"}
        story["mechanism"] = ("Release %s of %s keeps every session in an unbounded map; memory "
                              "grows until the container is OOM-killed, roughly every 40 minutes."
                              % (params.version, params.component))
        story["metrics"] = [{"name": "memory_usage_pct", "unit": "percent", "healthy_mean": 55.0,
                             "healthy_sd": 3.0, "bad_mean": 93.0, "bad_sd": 3.0}]
        story["logs"] = [["ERROR", "container killed: OOMKilled; restart count %d" % rng.randint(3, 6),
                          {"exit_code": 137}],
                         ["WARN", "heap grows ~12MB/min since %s" % params.version, {}]]
    elif params.mode == "input_exception":
        image_diff["promo_code_resolver"] = {"from": "skips an empty promo code",
                                             "to": "reads promoCode.trim() unconditionally"}
        story["mechanism"] = ("Release %s of %s dereferences the promo code without a null check; "
                              "every checkout without a promo code throws and returns HTTP 500."
                              % (params.version, params.component))
        story["metrics"] = [{"name": "error_rate_pct", "unit": "percent", "healthy_mean": 0.3,
                             "healthy_sd": 0.1, "bad_mean": 34.0, "bad_sd": 2.5}]
        story["logs"] = [["ERROR", "NullPointerException: cannot invoke String.trim() because "
                                   "promoCode is null at PromoCodeResolver.apply(PromoCodeResolver.java:42)",
                          {"status": 500}]]
        story["summary"] = "Checkout requests fail with HTTP 500; {label} at {floor}%"
        story["floor"] = [68, 80]
        story["caller_message"] = "upstream {callee} returned HTTP 500 route={route}"
        story["caller_status"] = 502
    else:
        image_diff["assignment_handler"] = {"from": "v1 schema", "to": "expects rider_zone field"}
        story["mechanism"] = ("Release %s of %s expects a field older messages lack; the "
                              "assignment handler fails and skips them, so orders are never "
                              "assigned." % (params.version, params.component))
        story["metrics"] = [{"name": "queue_lag", "unit": "messages", "bad_mean": 9000.0, "bad_sd": 600.0}]
        story["logs"] = [["ERROR", "assignment handler failed: KeyError 'rider_zone' (message skipped)", {}]]
        story["summary"] = "Orders are not being assigned to riders"
        story["floor"] = None
    story["change"]["diff"] = image_diff
    tell(draft, rng, story)


class RetryPolicyParams(StrictModel):
    component: NonEmptyText
    target: NonEmptyText               # who the retries hammer (text only)


def retry_policy_regression(draft, rng, raw_params):
    params = RetryPolicyParams.model_validate(raw_params)
    tell(draft, rng, {
        "root": params.component,
        "category": "config_change",
        "mechanism": ("A config change removed the retry backoff in %s; when %s slowed down, "
                      "failed deliveries were retried immediately and the queue grew without "
                      "bound." % (params.component, params.target)),
        "summary": "Merchant webhooks are arriving late and the delivery queue keeps growing",
        "floor": None,
        "change": {"change_type": "config_change", "version": None,
                   "summary": "%s configuration updated" % params.component,
                   "diff": {"retry.backoff": {"from": "exponential 1s..5m", "to": "none"}}},
        "metrics": [{"name": "queue_lag", "unit": "messages", "bad_mean": 42000.0, "bad_sd": 3000.0},
                    {"name": "outbound_requests_per_s", "unit": "req/s", "healthy_mean": 40.0,
                     "healthy_sd": 4.0, "bad_mean": 900.0, "bad_sd": 60.0}],
        "logs": [["WARN", "retrying delivery to %s immediately (attempt %d, no backoff)"
                  % (params.target, rng.randint(9, 20)), {}],
                 ["ERROR", "delivery queue lag %d messages" % rng.randint(30000, 50000), {}]],
        "contributing_factors": ["%s responded slowly, which started the retries" % params.target],
    })
