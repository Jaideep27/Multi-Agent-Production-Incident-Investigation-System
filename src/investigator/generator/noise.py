"""Noise: realistic imperfections that are not decoys. Each is recorded in truth.noise_applied.

Noise must never destroy the evidence an identifiable incident needs, so it skips the
services the archetype protected and any service that owns a decisive log. The validator
checks the result again.
"""

from datetime import timedelta

from pydantic import Field

from investigator.generator.draft import DraftLog, DraftMetric
from investigator.models.common import NonEmptyText, StrictModel

BENIGN_WARNINGS = [
    "GC pause of {ms}ms",
    "slow DNS lookup for metrics-collector took {ms}ms",
    "retrying metadata refresh (attempt 2)",
    "connection reset by peer, reconnecting",
    "config reload skipped: no changes detected",
]

GAP_ATTEMPTS = 10


def services_noise_may_touch(draft):
    """Services with logs that are neither protected nor the owner of a decisive log."""
    off_limits = list(draft.protected_services)
    for item, availability in draft.decisive:
        if isinstance(item, DraftLog) and item.service not in off_limits:
            off_limits.append(item.service)

    names = []
    for log in draft.logs:
        if log.service in off_limits:
            continue
        if log.service not in names:
            names.append(log.service)
    names.sort()
    return names


def benign_warnings(draft, rng, params):
    """Scatter harmless WARN lines over unrelated services, so 'any WARN' is not a signal."""
    candidates = services_noise_may_touch(draft)
    count = rng.randint(6, 12)
    window_seconds = int((draft.window_end - draft.window_start).total_seconds())
    for number in range(count):
        service = rng.choice(candidates)
        template = rng.choice(BENIGN_WARNINGS)
        draft.logs.append(
            DraftLog(
                service=service,
                instance=rng.choice(draft.instances_of(service)),
                ts=draft.window_start + timedelta(seconds=rng.randint(0, window_seconds - 1)),
                level="WARN",
                message=template.format(ms=rng.randint(120, 900)),
                attrs={},
            )
        )
    draft.noise.append(["benign_warnings", {"count": count}])


def log_gap(draft, rng, params):
    """Drop every log line of one service for a few minutes (a shipping outage)."""
    candidates = services_noise_may_touch(draft)
    for attempt in range(GAP_ATTEMPTS):
        service = rng.choice(candidates)
        length = rng.randint(4, 8)
        window_minutes = int((draft.window_end - draft.window_start).total_seconds() // 60)
        start = draft.window_start + timedelta(minutes=rng.randint(10, window_minutes - length - 2))
        end = start + timedelta(minutes=length)

        kept = []
        removed = 0
        for log in draft.logs:
            if log.service == service and start <= log.ts < end:
                removed = removed + 1
            else:
                kept.append(log)

        if removed > 0:
            draft.logs = kept
            draft.noise.append(
                ["log_gap", {"service": service, "start": start.isoformat(),
                             "end": end.isoformat(), "removed": removed}]
            )
            return
    raise ValueError("log_gap found no window with logs to remove after %d tries" % GAP_ATTEMPTS)


class NoParams(StrictModel):
    pass


class ClockSkewParams(StrictModel):
    service: NonEmptyText
    seconds: int = Field(ge=-300, le=300)


class ConflictingSourcesParams(StrictModel):
    service: NonEmptyText
    metric: NonEmptyText
    factor: float = Field(default=0.6, gt=0)


def clock_skew(draft, rng, raw_params):
    """One service's clock runs off: its log timestamps shift (lines pushed out of the window
    are lost). Unlike a gap this may touch the root service; the evidence stays, the timeline
    just gets harder to read."""
    params = ClockSkewParams.model_validate(raw_params)
    shift = timedelta(seconds=params.seconds)
    kept = []
    shifted = 0
    for log in draft.logs:
        if log.service == params.service:
            log.ts = log.ts + shift
            shifted = shifted + 1
            if not draft.window_start <= log.ts <= draft.window_end:
                continue
        kept.append(log)
    draft.logs = kept
    draft.noise.append(["clock_skew", {"service": params.service, "seconds": params.seconds,
                                       "shifted": shifted}])


def conflicting_sources(draft, rng, raw_params):
    """A second monitoring source reports the same metric with different values."""
    params = ConflictingSourcesParams.model_validate(raw_params)
    original = draft.find_metric(params.service, params.metric)
    points = []
    for ts, value in original.points:
        points.append([ts, round(value * params.factor + rng.gauss(0.0, 1.0), 2)])
    draft.metrics.append(DraftMetric(service=original.service, instance=original.instance,
                                     metric_name=original.metric_name, unit=original.unit,
                                     source="cloud_monitor", interval_s=original.interval_s,
                                     points=points))
    draft.noise.append(["conflicting_sources", {"service": params.service, "metric": params.metric,
                                                "factor": params.factor}])


NOISE = {
    "benign_warnings": benign_warnings,
    "log_gap": log_gap,
    "clock_skew": clock_skew,
    "conflicting_sources": conflicting_sources,
}

NOISE_PARAMS = {
    "benign_warnings": NoParams,
    "log_gap": NoParams,
    "clock_skew": ClockSkewParams,
    "conflicting_sources": ConflictingSourcesParams,
}
