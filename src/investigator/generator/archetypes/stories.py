"""Archetypes for the story-driven evaluation set: multi-step production stories with a trap.

Each fills the shared storyline (generator/story.py) like the other archetypes. Service names
come from the plan entry, never from here, so the same story could run on another topology
with matching services.
"""

from investigator.generator.story import tell
from investigator.models.common import NonEmptyText, StrictModel


# ------------------------------------------------------------------ cache memory saturation


class CacheSaturationParams(StrictModel):
    component: NonEmptyText          # the cache that fills up
    client: NonEmptyText             # the service whose users lose their sessions
    surge_factor: float = 4.0


def session_cache_saturation(draft, rng, raw_params):
    params = CacheSaturationParams.model_validate(raw_params)
    normal = 850.0
    tell(draft, rng, {
        "root": params.component,
        "category": "resource_exhaustion",
        "mechanism": ("A traffic spike of about %.0fx filled %s to its memory limit; it began "
                      "evicting live session keys, so %s users were logged out."
                      % (params.surge_factor, params.component, params.client)),
        "summary": "Users are being logged out across the site during a traffic spike; "
                   "{label} at {floor}%",
        "floor": [80, 90],
        "metrics": [
            {"name": "memory_used_pct", "unit": "percent", "healthy_mean": 61.0, "healthy_sd": 2.0,
             "bad_mean": 99.6, "bad_sd": 0.2},
            {"name": "evicted_keys_per_s", "unit": "keys/s", "healthy_mean": 0.0, "healthy_sd": 0.0,
             "bad_mean": 5200.0, "bad_sd": 400.0},
            # Evicting live keys must show up as misses, or the evidence contradicts itself.
            {"name": "hit_ratio_pct", "unit": "percent", "healthy_mean": 97.5, "healthy_sd": 0.4,
             "bad_mean": 61.0, "bad_sd": 3.0},
            {"name": "requests_per_s", "unit": "req/s", "service": "edge-lb",
             "healthy_mean": normal, "healthy_sd": 40.0,
             "bad_mean": normal * params.surge_factor, "bad_sd": 150.0},
        ],
        "logs": [["WARN", "used_memory reached maxmemory (4gb); evicting keys with allkeys-lru "
                          "(%d evicted in the last second)" % rng.randint(4000, 6500), {}]],
        "caller_message": "session lookup in {callee} missed; user redirected to /login route={route}",
        "caller_status": 302,
    })


# ------------------------------------------------------------------ feature flag regression


class FeatureFlagParams(StrictModel):
    component: NonEmptyText          # where the flag was turned on
    flag: NonEmptyText
    entry_latency_service: NonEmptyText   # the user-facing service whose p99 explodes
    lookalike_service: str | None = None  # a service history blames; it stays healthy now


def feature_flag_regression(draft, rng, raw_params):
    params = FeatureFlagParams.model_validate(raw_params)
    metrics = [
        {"name": "cpu_pct", "unit": "percent", "healthy_mean": 30.0, "healthy_sd": 3.0,
         "bad_mean": 99.0, "bad_sd": 0.5},
        {"name": "p99_latency_ms", "unit": "ms", "service": params.entry_latency_service,
         "healthy_mean": 180.0, "healthy_sd": 15.0, "bad_mean": 8000.0, "bad_sd": 400.0},
    ]
    if params.lookalike_service is not None:
        # The service a past incident blamed looks fine today: heap well below the limit.
        metrics.append({"name": "jvm_heap_used_pct", "unit": "percent",
                        "service": params.lookalike_service, "healthy_mean": 42.0,
                        "healthy_sd": 2.0, "bad_mean": 42.0, "bad_sd": 2.0})
    tell(draft, rng, {
        "root": params.component,
        "category": "config_change",
        "mechanism": ("Turning on the %s feature flag in %s added a regular expression with "
                      "catastrophic backtracking; parsing pinned its CPU and search p99 latency "
                      "rose to about 8 seconds." % (params.flag, params.component)),
        "summary": "Search is very slow (p99 around 8 s); {label} at {floor}%",
        "floor": [85, 93],
        "change": {"change_type": "feature_flag", "version": None,
                   "summary": "%s feature flag %s enabled" % (params.component, params.flag),
                   "diff": {"flags." + params.flag: {"from": False, "to": True},
                            "synonym_pattern": {"from": None, "to": "^(\\w+\\s?)*$"}}},
        "metrics": metrics,
        "logs": [["WARN", "regex evaluation took %dms for one query (pattern synonym_pattern); "
                          "worker thread pinned" % rng.randint(2100, 4800), {}],
                 ["ERROR", "query parse timed out after 5000ms; returning 504", {"status": 504}]],
        "caller_message": "upstream {callee} timed out after {timeout}ms route={route}",
    })


# ------------------------------------------------------------------ swallowed error


class SwallowedErrorParams(StrictModel):
    component: NonEmptyText          # the service that hides its errors behind HTTP 200
    caller: NonEmptyText             # the service that renders the empty result
    version: NonEmptyText
    previous_version: NonEmptyText


def swallowed_error(draft, rng, raw_params):
    params = SwallowedErrorParams.model_validate(raw_params)
    tell(draft, rng, {
        "root": params.component,
        "category": "bad_deploy",
        "mechanism": ("Release %s of %s catches stock lookup failures and returns HTTP 200 with "
                      "an errors list and no items; status-code dashboards stay green while "
                      "about 40%% of product pages render empty."
                      % (params.version, params.component)),
        "summary": ("Customers report that about 40% of product pages render empty, while the "
                    "dashboards show 99.9% HTTP 200"),
        "floor": None,
        "change": {"change_type": "deploy", "version": params.version,
                   "summary": "%s deploy %s" % (params.component, params.version),
                   "diff": {"image": {"from": "%s:%s" % (params.component, params.previous_version),
                                      "to": "%s:%s" % (params.component, params.version)},
                            "stock_lookup_errors": {"from": "returned as gRPC UNAVAILABLE",
                                                    "to": "caught; respond OK with errors[]"}}},
        "change_lead_minutes": 25,
        "metrics": [{"name": "empty_responses_pct", "unit": "percent", "service": params.caller,
                     "healthy_mean": 0.4, "healthy_sd": 0.2, "bad_mean": 40.0, "bad_sd": 2.0}],
        "logs": [["ERROR", "stock lookup failed: deadline exceeded on warehouse shard %d; "
                           "returning OK with errors[] and no items" % rng.randint(3, 9),
                  {"grpc_status": "OK"}]],
        "caller_message": "upstream {callee} returned 200 with errors[]; rendering the page "
                          "without items route={route}",
        "caller_status": 200,
    })


# ------------------------------------------------------------------ rate limiter retry storm


class RetryStormParams(StrictModel):
    component: NonEmptyText          # the service whose rate limiter was misconfigured
    clients: list[NonEmptyText]      # callers that retry aggressively
    limit_from: int = 500
    limit_to: int = 50


def rate_limiter_retry_storm(draft, rng, raw_params):
    params = RetryStormParams.model_validate(raw_params)
    logs = []
    for client in params.clients:
        logs.append(["WARN", "rate limit exceeded for client %s (%d rps per client); responding 429"
                     % (client, params.limit_to), {"status": 429}])
    tell(draft, rng, {
        "root": params.component,
        "category": "config_change",
        "mechanism": ("A config change cut the per-client rate limit of %s from %d to %d rps; "
                      "its callers' aggressive retries turned the 429s into a retry storm and "
                      "mutual timeouts." % (params.component, params.limit_from, params.limit_to)),
        "summary": "Logins and profile pages fail intermittently; {label} at {floor}%",
        "floor": [78, 88],
        "change": {"change_type": "config_change", "version": None,
                   "summary": "%s configuration updated" % params.component,
                   "diff": {"rate_limit.per_client_rps": {"from": params.limit_from,
                                                          "to": params.limit_to}}},
        "metrics": [
            {"name": "rate_limited_requests_per_s", "unit": "req/s", "healthy_mean": 0.5,
             "healthy_sd": 0.3, "bad_mean": 1800.0, "bad_sd": 120.0},
            {"name": "requests_per_s", "unit": "req/s", "healthy_mean": 400.0,
             "healthy_sd": 20.0, "bad_mean": 2600.0, "bad_sd": 150.0},
        ],
        "logs": logs,
        "caller_message": "upstream {callee} returned 429; retrying (attempt 3/3) route={route}",
        "caller_status": 429,
    })


# ------------------------------------------------------------------ schema migration backlog


class MigrationParams(StrictModel):
    component: NonEmptyText          # the service whose release ran the migration
    worker: NonEmptyText             # the legacy consumer that fails
    queue: NonEmptyText
    version: NonEmptyText
    lead_minutes: int = 45


def schema_migration_backlog(draft, rng, raw_params):
    params = MigrationParams.model_validate(raw_params)
    tell(draft, rng, {
        "root": params.component,
        "category": "bad_deploy",
        "mechanism": ("Release %s of %s ran an online migration adding a NOT NULL column without "
                      "a default and started publishing v3 events; the legacy v2 %s cannot "
                      "deserialise them or insert rows, so messages pile up in the dead-letter "
                      "queue and invoices stall about %d minutes later."
                      % (params.version, params.component, params.worker, params.lead_minutes)),
        "summary": "Invoices have not been generated for a while and the billing queue keeps growing",
        "floor": None,
        "change": {"change_type": "deploy", "version": params.version,
                   "summary": "%s deploy %s with schema migration" % (params.component, params.version),
                   "diff": {"migration": "ALTER TABLE invoices ADD COLUMN tax_region text NOT NULL",
                            "event_schema": {"from": "v2", "to": "v3"}}},
        "change_lead_minutes": params.lead_minutes,
        "metrics": [
            {"name": "consumer_lag", "unit": "messages", "service": params.queue,
             "healthy_mean": 120.0, "healthy_sd": 25.0, "bad_mean": 48000.0, "bad_sd": 3000.0},
            {"name": "dead_letter_messages", "unit": "messages", "service": params.queue,
             "healthy_mean": 0.0, "healthy_sd": 0.0, "bad_mean": 15000.0, "bad_sd": 1000.0},
        ],
        "log_service": params.worker,
        "logs": [["ERROR", "failed to deserialize InvoiceCreated: unknown field 'tax_region' "
                           "(consumer schema v2); message sent to the dead-letter queue", {}],
                 ["ERROR", "insert into invoices failed: null value in column \"tax_region\" "
                           "violates not-null constraint", {}]],
    })


# ------------------------------------------------------------------ disk I/O saturation


class IoSaturationParams(StrictModel):
    component: NonEmptyText


def io_saturation(draft, rng, raw_params):
    params = IoSaturationParams.model_validate(raw_params)
    tell(draft, rng, {
        "root": params.component,
        "category": "resource_exhaustion",
        "mechanism": ("The local disk of %s reached its IOPS limit; the write-ahead buffer "
                      "filled and the service started dropping about 80%% of incoming events."
                      % params.component),
        "summary": "About 80% of analytics events are being dropped",
        "floor": None,
        "metrics": [
            {"name": "disk_io_util_pct", "unit": "percent", "healthy_mean": 35.0,
             "healthy_sd": 4.0, "bad_mean": 100.0, "bad_sd": 0.1},
            {"name": "dropped_events_pct", "unit": "percent", "healthy_mean": 0.1,
             "healthy_sd": 0.05, "bad_mean": 80.0, "bad_sd": 3.0},
            {"name": "disk_queue_depth", "unit": "requests", "healthy_mean": 2.0,
             "healthy_sd": 0.5, "bad_mean": 140.0, "bad_sd": 10.0},
        ],
        # The log store is down for this incident, so the story must be solvable from
        # metrics alone: the logs are there but not decisive.
        "logs_decisive": False,
        "logs": [["ERROR", "write-ahead buffer full; dropping batch of %d events"
                  % rng.randint(400, 900), {}]],
    })


# ------------------------------------------------------------------ crash with two changes


class CrashParams(StrictModel):
    component: NonEmptyText
    driver_from: NonEmptyText
    driver_to: NonEmptyText


def crash_with_two_changes(draft, rng, raw_params):
    """The real trigger is the GPU driver bump; an OS image update lands in the same minute
    (added by the plan as a second change), and the crash logs and metrics are gone."""
    params = CrashParams.model_validate(raw_params)
    tell(draft, rng, {
        "root": params.component,
        "category": "bad_deploy",
        "mechanism": ("The GPU driver upgrade %s -> %s on %s faults under the transcoding "
                      "load; the process crashed and auto-restarted." % (params.driver_from,
                                                                         params.driver_to,
                                                                         params.component)),
        "summary": "%s crashed and restarted; transcoding jobs failed during the crash" % params.component,
        "floor": None,
        "change": {"change_type": "infra", "version": None,
                   "summary": "%s GPU driver upgrade" % params.component,
                   "diff": {"gpu_driver": {"from": params.driver_from, "to": params.driver_to}}},
        "metrics": [
            {"name": "gpu_xid_errors_per_min", "unit": "errors/min", "healthy_mean": 0.0,
             "healthy_sd": 0.0, "bad_mean": 12.0, "bad_sd": 2.0},
            {"name": "container_restarts", "unit": "restarts", "healthy_mean": 0.0,
             "healthy_sd": 0.0, "bad_mean": 1.0, "bad_sd": 0.0},
        ],
        "logs": [["ERROR", "NVRM: Xid 79, GPU has fallen off the bus; transcoder exiting", {}]],
    })


# ------------------------------------------------------------------ missing client timeout


class MissingTimeoutParams(StrictModel):
    component: NonEmptyText          # our service without a client timeout
    vendor: NonEmptyText             # the external API that slows down


def missing_client_timeout(draft, rng, raw_params):
    params = MissingTimeoutParams.model_validate(raw_params)
    tell(draft, rng, {
        "root": params.component,
        "category": "dependency_failure",
        "mechanism": ("%s slowed from about 100ms to 12s; %s calls it with no client timeout, "
                      "so worker threads blocked, the thread pool ran out, health checks failed "
                      "and Kubernetes restarted the pods in a loop." % (params.vendor, params.component)),
        "summary": ("Order fulfilment is failing and " + params.component
                    + " pods keep restarting; {label} at {floor}%"),
        "floor": [84, 92],
        "metrics": [
            {"name": "busy_threads_pct", "unit": "percent", "healthy_mean": 22.0,
             "healthy_sd": 3.0, "bad_mean": 100.0, "bad_sd": 0.1},
            {"name": "upstream_latency_ms", "unit": "ms", "healthy_mean": 100.0,
             "healthy_sd": 10.0, "bad_mean": 12000.0, "bad_sd": 800.0},
            {"name": "container_restarts", "unit": "restarts", "healthy_mean": 0.0,
             "healthy_sd": 0.0, "bad_mean": 6.0, "bad_sd": 1.0},
        ],
        "logs": [["WARN", "call to %s still in progress after %ds (no client timeout configured)"
                  % (params.vendor, rng.randint(60, 180)), {}],
                 ["WARN", "thread pool exhausted: 200/200 busy; requests queued", {}],
                 ["ERROR", "liveness probe failed: GET /health timed out after 1s; container "
                           "will be restarted", {}]],
    })
