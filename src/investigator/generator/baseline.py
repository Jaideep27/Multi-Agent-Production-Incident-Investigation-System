"""Healthy background: what every service looks like when nothing is wrong.

Every service gets ordinary INFO logs and role-appropriate metrics, every caller of a pool
edge gets pool metrics, and the domain's unrelated history becomes past incidents. The
archetype then bends some of this out of shape; whatever it does not touch stays healthy and
gives the agents something to rule out.
"""

from datetime import timedelta

from investigator.generator.draft import DraftEdge, DraftLog, DraftMetric, DraftPastIncident

METRIC_INTERVAL_S = 60

# role -> [(metric name, unit, healthy mean, standard deviation)]
ROLE_METRICS = {
    "load_balancer": [("requests_per_s", "req/s", 850.0, 40.0)],
    "app": [
        ("cpu_pct", "percent", 32.0, 3.0),
        ("p99_latency_ms", "ms", 180.0, 15.0),
        ("error_rate_pct", "percent", 0.3, 0.1),
    ],
    "worker": [("cpu_pct", "percent", 25.0, 3.0), ("queue_lag", "messages", 40.0, 10.0)],
    "database": [
        ("cpu_pct", "percent", 41.0, 3.0),
        ("active_connections", "connections", 140.0, 6.0),
    ],
    "cache": [("hit_ratio_pct", "percent", 97.5, 0.4), ("cpu_pct", "percent", 18.0, 2.0)],
    "queue": [("consumer_lag", "messages", 120.0, 25.0)],
    "external_api": [],
    "cdn": [],
}

# role -> INFO message templates
ROLE_LOG_TEMPLATES = {
    "load_balancer": ["health check passed for {count} backends"],
    "app": ["request completed route={route} status=200 duration_ms={duration}"],
    "worker": ["processed batch size={count} duration_ms={duration}"],
    "database": ["checkpoint complete: wrote {count} buffers"],
    "cache": ["background save completed keys={count}"],
    "queue": ["partition leaders balanced partitions={count}"],
    "external_api": [],
    "cdn": [],
}


def healthy_value(rng, mean, sd, unit):
    """One noisy reading, never negative, percentages capped at 100, rounded to 2 places."""
    value = rng.gauss(mean, sd)
    if value < 0:
        value = 0.0
    if unit == "percent" and value > 100:
        value = 100.0
    return round(value, 2)


def reshape_after(metric, start_ts, rng, mean, sd):
    """Replace every point at or after start_ts with readings around a new mean."""
    for point in metric.points:
        if point[0] >= start_ts:
            point[1] = healthy_value(rng, mean, sd, metric.unit)


def minute_timestamps(draft):
    stamps = []
    ts = draft.window_start
    while ts < draft.window_end:
        stamps.append(ts)
        ts = ts + timedelta(seconds=METRIC_INTERVAL_S)
    return stamps


def new_metric(draft, rng, service, name, unit, mean, sd, source):
    points = []
    for ts in minute_timestamps(draft):
        points.append([ts, healthy_value(rng, mean, sd, unit)])
    metric = DraftMetric(
        service=service,
        instance=None,
        metric_name=name,
        unit=unit,
        source=source,
        interval_s=METRIC_INTERVAL_S,
        points=points,
    )
    draft.metrics.append(metric)
    return metric


def add_services_and_edges(draft):
    for name, role, count in draft.topology["services"]:
        draft.services.append([name, role, count])
    for caller, callee, config in draft.topology["edges"]:
        draft.edges.append(DraftEdge(caller=caller, callee=callee, config=dict(config)))


def add_metrics(draft, rng):
    for name, role, count in draft.services:
        for metric_name, unit, mean, sd in ROLE_METRICS[role]:
            new_metric(draft, rng, name, metric_name, unit, mean, sd, "prometheus")

    for edge in draft.edges:
        if "max_pool" in edge.config:
            pool = edge.config["max_pool"]
            new_metric(draft, rng, edge.caller, "db_pool_in_use", "connections",
                       pool * 0.4, pool * 0.05, "prometheus")
            new_metric(draft, rng, edge.caller, "db_pool_wait_ms", "ms", 2.0, 0.5, "prometheus")

    entry = draft.topology["entry_service"]
    name, unit, mean, sd, label = draft.topology["business_metric"]
    new_metric(draft, rng, entry, name, unit, mean, sd, "apm")


def add_logs(draft, rng):
    routes = draft.topology["routes"]
    for name, role, count in draft.services:
        templates = ROLE_LOG_TEMPLATES[role]
        if not templates:
            continue
        instances = draft.instances_of(name)
        ts = draft.window_start + timedelta(seconds=rng.randint(0, 60))
        while ts < draft.window_end:
            template = rng.choice(templates)
            message = template.format(
                route=rng.choice(routes),
                duration=rng.randint(40, 260),
                count=rng.randint(3, 400),
            )
            attrs = {}
            if role == "app":
                attrs = {"status": 200, "request_id": "%08x" % rng.getrandbits(32)}
            draft.logs.append(
                DraftLog(service=name, instance=rng.choice(instances), ts=ts, level="INFO",
                         message=message, attrs=attrs)
            )
            ts = ts + timedelta(seconds=rng.randint(90, 200))


def add_history(draft, rng):
    for item in draft.topology["history"]:
        occurred_at = draft.window_start - timedelta(days=rng.randint(20, 400),
                                                     minutes=rng.randint(0, 1439))
        draft.past_incidents.append(
            DraftPastIncident(
                occurred_at=occurred_at,
                title=item["title"],
                category=item["category"],
                component=item["component"],
                resolution=item["resolution"],
                symptoms=list(item["symptoms"]),
            )
        )


def add_baseline(draft, rng):
    add_services_and_edges(draft)
    add_metrics(draft, rng)
    add_logs(draft, rng)
    add_history(draft, rng)
