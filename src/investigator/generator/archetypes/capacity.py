"""Archetypes where demand outgrows a limit: traffic surges, saturated resources, throttling."""

from typing import Literal

from pydantic import Field

from investigator.generator.story import tell
from investigator.models.common import NonEmptyText, StrictModel


# ------------------------------------------------------------------ traffic_surge


class TrafficSurgeParams(StrictModel):
    component: NonEmptyText
    surge_factor: float = Field(default=3.5, gt=1)
    max_replicas: int = Field(default=4, ge=1)


def traffic_surge(draft, rng, raw_params):
    params = TrafficSurgeParams.model_validate(raw_params)
    queue = rng.choice([256, 512, 1024])
    tell(draft, rng, {
        "root": params.component,
        "category": "capacity_scaling",
        "mechanism": "Traffic rose about %.1fx; %s stayed at its maximum of %d replicas, saturated "
                     "its CPU and request queue, and shed load." % (params.surge_factor,
                                                                    params.component,
                                                                    params.max_replicas),
        "summary": "{label} dropped to {floor}% during a busy period",
        "floor": [70, 82],
        "metrics": [
            {"name": "cpu_pct", "unit": "percent", "bad_mean": 97.0, "bad_sd": 1.5},
            {"name": "requests_per_s", "unit": "req/s", "service": "edge-lb",
             "bad_mean": 850.0 * params.surge_factor, "bad_sd": 60.0},
        ],
        "logs": [
            ["WARN", "request queue full, shedding load (queue %d/%d)" % (queue, queue),
             {"status": 503}],
            ["ERROR", "autoscaler: desired replicas %d exceeds max %d; staying at %d"
             % (params.max_replicas * 3, params.max_replicas, params.max_replicas), {}],
        ],
        "caller_message": "upstream {callee} call failed: 503 overloaded route={route}",
        "caller_status": 503,
    })


# ------------------------------------------------------------------ resource_saturation


class ResourceSaturationParams(StrictModel):
    component: NonEmptyText
    mode: Literal["memory_oom", "cpu_throttle", "connection_limit"]
    limit: str = "512Mi"                  # memory or CPU limit, as shown in messages
    max_connections: int = Field(default=200, ge=1)
    scaled_service: str | None = None     # connection_limit: the fleet that grew
    replicas_from: int = 4
    replicas_to: int = 8


def resource_saturation(draft, rng, raw_params):
    params = ResourceSaturationParams.model_validate(raw_params)
    story = {
        "root": params.component,
        "category": "resource_exhaustion",
        "summary": "{label} dropped to {floor}%",
        "floor": [72, 86],
    }
    if params.mode == "memory_oom":
        story["mechanism"] = ("%s hit its memory limit (%s) under load; containers were "
                              "OOM-killed and restarted, failing in-flight requests."
                              % (params.component, params.limit))
        story["metrics"] = [{"name": "memory_usage_pct", "unit": "percent", "healthy_mean": 61.0,
                             "healthy_sd": 3.0, "bad_mean": 99.0, "bad_sd": 0.6}]
        story["logs"] = [
            ["ERROR", "container killed: OOMKilled (memory limit %s)" % params.limit, {"exit_code": 137}],
            ["WARN", "heap usage 97% of container limit", {}],
        ]
    elif params.mode == "cpu_throttle":
        story["mechanism"] = ("%s containers hit their CPU limit (%s) and were throttled in most "
                              "scheduling periods, so requests queued although node CPU looked "
                              "low." % (params.component, params.limit))
        story["metrics"] = [{"name": "cpu_throttled_pct", "unit": "percent", "healthy_mean": 2.0,
                             "healthy_sd": 0.8, "bad_mean": 64.0, "bad_sd": 4.0}]
        story["logs"] = [
            ["WARN", "CPU throttled in 63%% of scheduling periods (limit %s)" % params.limit, {}],
            ["WARN", "request handling delayed 2400ms waiting for CPU", {}],
        ]
    else:
        story["mechanism"] = ("%s scaled from %d to %d replicas; their connection pools together "
                              "exceeded max_connections=%d on %s, which refused new connections."
                              % (params.scaled_service, params.replicas_from, params.replicas_to,
                                 params.max_connections, params.component))
        story["change"] = {
            "service": params.scaled_service, "change_type": "scaling", "version": None,
            "summary": "%s scaled from %d to %d replicas" % (params.scaled_service,
                                                            params.replicas_from, params.replicas_to),
            "diff": {"replicas": {"from": params.replicas_from, "to": params.replicas_to}},
        }
        story["metrics"] = [{"name": "active_connections", "unit": "connections",
                             "bad_mean": float(params.max_connections), "bad_sd": 0.0}]
        story["logs"] = [
            ["FATAL", "sorry, too many clients already (max_connections=%d)" % params.max_connections, {}],
        ]
    tell(draft, rng, story)


# ------------------------------------------------------------------ rate_limit_throttling


class RateLimitParams(StrictModel):
    component: NonEmptyText
    mode: Literal["quota", "per_ip", "callbacks"]
    upstream: str | None = None           # who enforces the limit (quota, callbacks)
    log_service: str | None = None        # where the evidence is logged, if not the component
    limit: int = Field(default=100, ge=1)


def rate_limit_throttling(draft, rng, raw_params):
    params = RateLimitParams.model_validate(raw_params)
    story = {
        "root": params.component,
        "category": "rate_limiting",
        "summary": "{label} dropped to {floor}%",
        "floor": [74, 88],
        "caller_status": 429,
        "metrics": [{"name": "throttled_requests_per_s", "unit": "req/s", "healthy_mean": 0.2,
                     "healthy_sd": 0.1, "bad_mean": 85.0, "bad_sd": 6.0,
                     "service": params.log_service or params.component}],
    }
    if params.log_service:
        story["log_service"] = params.log_service
    if params.mode == "quota":
        story["mechanism"] = ("%s exhausted its request quota at %s (%d requests a day); every "
                              "further call was rejected with 429."
                              % (params.component, params.upstream, params.limit))
        story["logs"] = [["ERROR", "%s responded 429: daily quota of %d requests exceeded"
                          % (params.upstream, params.limit), {"status": 429}]]
        story["caller_message"] = "upstream {callee} returned stale data after 429s route={route}"
    elif params.mode == "per_ip":
        address = "203.0.113.%d" % rng.randint(2, 250)
        story["mechanism"] = ("%s limits each client IP to %d requests/s; many customers behind "
                              "one corporate NAT address shared that limit and were rejected."
                              % (params.component, params.limit))
        story["logs"] = [["WARN", "429 rate limit exceeded for client %s (limit %d rps per IP)"
                          % (address, params.limit), {"client_ip": address, "status": 429}]]
    else:
        story["mechanism"] = ("%s throttled payment callbacks (429), so orders never left the "
                              "pending-payment state." % params.component)
        story["logs"] = [["ERROR", "callback from %s rejected: 429 too many requests"
                          % params.component, {"status": 429}]]
    tell(draft, rng, story)
