"""Decoys: evidence planted to look like the cause. Each is recorded in truth.decoys.

Decoys run after the archetype, because they are placed relative to its trigger time and
reuse its incident summary.
"""

from datetime import timedelta
from typing import Literal

from pydantic import Field

from investigator.generator.baseline import reshape_after
from investigator.generator.draft import DraftChange, DraftLog, DraftPastIncident
from investigator.models.common import NonEmptyText, RootCauseCategory, StrictModel


class RedHerringChangeParams(StrictModel):
    service: NonEmptyText
    change_type: Literal["deploy", "config_change", "scaling", "infra"] = "deploy"
    minutes_before: list[int] = Field(default_factory=lambda: [3, 9], min_length=2, max_length=2)
    # Optional overrides for the change record (for example a docs-only deploy). The random
    # draws stay the same either way, so incidents without them are unchanged.
    summary: str | None = None
    diff: dict | None = None


class DownstreamSymptomParams(StrictModel):
    service: NonEmptyText
    message: NonEmptyText
    level: Literal["WARN", "ERROR"] = "WARN"
    metric: str | None = None          # an existing metric on the service that also bumps
    bad_mean: float = 0.0


class UnrelatedSpikeParams(StrictModel):
    service: NonEmptyText
    metric: NonEmptyText
    bad_mean: float


class LookalikeHistoryParams(StrictModel):
    title: NonEmptyText
    category: RootCauseCategory
    component: NonEmptyText
    resolution: NonEmptyText
    symptoms: list[str] | None = None     # default: the business metric drop and errors


def red_herring_change(draft, rng, raw_params):
    """A deploy of an innocent service a few minutes before the real trigger, with a visible
    restart and a short CPU bump, so it is the first thing a hasty investigator blames."""
    params = RedHerringChangeParams.model_validate(raw_params)
    if params.service not in draft.service_names():
        raise ValueError("decoy service %s is not in the topology" % params.service)

    low, high = params.minutes_before
    deploy_ts = draft.trigger_ts - timedelta(minutes=rng.randint(low, high), seconds=rng.randint(0, 59))
    if params.change_type != "deploy":
        other_change(draft, rng, params, deploy_ts)
        return
    minor = rng.randint(10, 30)
    old_version = "v2.%d.%d" % (minor - 1, rng.randint(0, 9))
    new_version = "v2.%d.0" % minor

    summary = "%s deploy %s" % (params.service, new_version)
    diff = {"image": {"from": params.service + ":" + old_version,
                      "to": params.service + ":" + new_version}}
    if params.summary is not None:
        summary = params.summary
    if params.diff is not None:
        diff = dict(params.diff)
    change = DraftChange(
        service=params.service,
        ts=deploy_ts,
        change_type="deploy",
        version=new_version,
        author=rng.choice(draft.topology["authors"]),
        summary=summary,
        diff=diff,
    )
    draft.changes.append(change)
    draft.decoys.append([change, "red_herring_change"])

    restart_ts = deploy_ts + timedelta(seconds=rng.randint(20, 40))
    for instance in draft.instances_of(params.service):
        # A deploy hours before the window leaves its restart lines outside the stored logs.
        if restart_ts >= draft.window_start:
            draft.logs.append(
                DraftLog(service=params.service, instance=instance, ts=restart_ts, level="INFO",
                         message="starting %s %s" % (params.service, new_version), attrs={})
            )
        restart_ts = restart_ts + timedelta(seconds=rng.randint(30, 90))

    cpu = None
    for metric in draft.metrics:
        if metric.service == params.service and metric.metric_name == "cpu_pct" and metric.instance is None:
            cpu = metric
    if cpu is None:
        return
    bump_end = deploy_ts + timedelta(minutes=8)
    for point in cpu.points:
        if deploy_ts <= point[0] < bump_end:
            point[1] = round(point[1] + rng.uniform(15.0, 25.0), 2)


def other_change(draft, rng, params, ts):
    """A config, scaling or infra change on an innocent service."""
    if params.change_type == "config_change":
        summary = "%s configuration updated" % params.service
        diff = {"log_level": {"from": "info", "to": "debug"}}
    elif params.change_type == "scaling":
        before = rng.randint(2, 4)
        summary = "%s scaled from %d to %d replicas" % (params.service, before, before + 2)
        diff = {"replicas": {"from": before, "to": before + 2}}
    else:
        summary = "%s node pool kernel patch" % params.service
        diff = {"kernel": {"from": "6.1.84", "to": "6.1.90"}}
    if params.summary is not None:
        summary = params.summary
    if params.diff is not None:
        diff = dict(params.diff)
    change = DraftChange(service=params.service, ts=ts, change_type=params.change_type,
                         version=None, author=rng.choice(draft.topology["authors"]),
                         summary=summary, diff=diff)
    draft.changes.append(change)
    draft.decoys.append([change, "red_herring_change"])


def downstream_symptom(draft, rng, raw_params):
    """Errors on a service the real cause hurts, so it looks like a cause itself."""
    params = DownstreamSymptomParams.model_validate(raw_params)
    instances = draft.instances_of(params.service)
    ts = draft.onset_ts + timedelta(seconds=rng.randint(10, 40))
    first = None
    while ts < draft.window_end:
        log = DraftLog(service=params.service, instance=rng.choice(instances), ts=ts,
                       level=params.level, message=params.message, attrs={})
        draft.logs.append(log)
        if first is None:
            first = log
        ts = ts + timedelta(seconds=rng.randint(30, 90))
    if params.metric is not None:
        reshape_after(draft.find_metric(params.service, params.metric), draft.onset_ts, rng,
                      params.bad_mean, params.bad_mean * 0.03)
    draft.decoys.append([first, "downstream_symptom"])


def unrelated_spike(draft, rng, raw_params):
    """A metric spike on another service near the onset (a consequence, or coincidence)."""
    params = UnrelatedSpikeParams.model_validate(raw_params)
    series = draft.find_metric(params.service, params.metric)
    start = draft.trigger_ts - timedelta(minutes=rng.randint(0, 3))
    end = start + timedelta(minutes=rng.randint(6, 12))
    for point in series.points:
        if start <= point[0] < end:
            point[1] = round(params.bad_mean + rng.gauss(0.0, params.bad_mean * 0.03), 2)
    draft.decoys.append([series, "unrelated_spike"])


def lookalike_history(draft, rng, raw_params):
    """A past incident with the same visible symptoms but a different root cause."""
    params = LookalikeHistoryParams.model_validate(raw_params)
    business_label = draft.topology["business_metric"][4]
    symptoms = [business_label + " drop", "payment timeouts", params.component + " errors"]
    if params.symptoms is not None:
        symptoms = list(params.symptoms)
    past = DraftPastIncident(
        occurred_at=draft.window_start - timedelta(days=rng.randint(30, 200),
                                                   minutes=rng.randint(0, 1439)),
        title=params.title,
        category=params.category,
        component=params.component,
        resolution=params.resolution,
        symptoms=symptoms,
    )
    draft.past_incidents.append(past)
    draft.decoys.append([past, "lookalike_history"])


# decoy type -> (params model, apply function)
DECOYS = {
    "red_herring_change": (RedHerringChangeParams, red_herring_change),
    "lookalike_history": (LookalikeHistoryParams, lookalike_history),
    "downstream_symptom": (DownstreamSymptomParams, downstream_symptom),
    "unrelated_spike": (UnrelatedSpikeParams, unrelated_spike),
}
