"""Archetype: a config change shrinks a service's connection pool to its database.

Story: the component's pool to the dependency drops from from_value to to_value. Once
traffic needs more connections than the pool has, requests queue for a connection, time
out, and the business metric at the entry service falls. The database itself looks
quieter (fewer connections), which is a hint, not a cause.

Decisive evidence: the change record (its diff shows the shrink), the first pool-timeout
error on the component, and the component's pool-in-use metric pinned at the new limit.
"""

from datetime import timedelta

from pydantic import Field, model_validator

from investigator.generator.baseline import reshape_after
from investigator.generator.draft import DraftChange, DraftLog
from investigator.models.common import NonEmptyText, StrictModel

CATEGORY = "config_change"
POOL_KEY = "max_pool"   # the edge config key that holds the pool size (see topology.py)


class Params(StrictModel):
    component: NonEmptyText
    dependency: NonEmptyText
    setting: NonEmptyText          # the config key as it appears in the change diff
    from_value: int = Field(gt=0)
    to_value: int = Field(gt=0)

    @model_validator(mode="after")
    def check_shrink(self):
        if self.to_value >= self.from_value:
            raise ValueError("to_value must be smaller than from_value for a pool shrink")
        return self


def apply(draft, rng, raw_params):
    params = Params.model_validate(raw_params)
    edge = draft.find_edge(params.component, params.dependency)
    if edge.config.get(POOL_KEY) != params.from_value:
        raise ValueError(
            "topology pool for %s -> %s is %s, plan says %s"
            % (params.component, params.dependency, edge.config.get(POOL_KEY), params.from_value)
        )
    acquire_timeout_ms = edge.config["acquire_timeout_ms"]

    entry = draft.topology["entry_service"]
    business_name, business_unit, business_mean, business_sd, business_label = (
        draft.topology["business_metric"]
    )

    # ---- timeline ----------------------------------------------------------------
    change_ts = draft.window_start + timedelta(minutes=rng.randint(18, 24),
                                               seconds=rng.randint(0, 59))
    onset = change_ts + timedelta(seconds=rng.randint(40, 90))
    reported_at = onset + timedelta(minutes=rng.randint(12, 20))
    draft.trigger_ts = change_ts
    draft.onset_ts = onset
    draft.reported_at = reported_at

    # ---- the change (vague summary: the diff is behind a second call) -------------
    change = DraftChange(
        service=params.component,
        ts=change_ts,
        change_type="config_change",
        version=None,
        author=rng.choice(draft.topology["authors"]),
        summary=params.component + " configuration updated",
        diff={params.setting: {"from": params.from_value, "to": params.to_value}},
    )
    draft.changes.append(change)
    draft.decisive.append([change, "retrievable"])

    # The dependency edge shows the configuration as it is now.
    edge.config[POOL_KEY] = params.to_value

    # ---- metrics on the component ---------------------------------------------------
    in_use = draft.find_metric(params.component, "db_pool_in_use")
    for point in in_use.points:
        if point[0] >= onset:
            point[1] = float(params.to_value)
    draft.decisive.append([in_use, "retrievable"])

    wait = draft.find_metric(params.component, "db_pool_wait_ms")
    reshape_after(wait, onset, rng, acquire_timeout_ms * 0.85, acquire_timeout_ms * 0.05)
    reshape_after(draft.find_metric(params.component, "p99_latency_ms"), onset, rng,
                  acquire_timeout_ms + 150.0, 120.0)

    # ---- errors on the component ----------------------------------------------------
    instances = draft.instances_of(params.component)
    ts = onset
    first = True
    while ts < draft.window_end:
        log = DraftLog(
            service=params.component,
            instance=rng.choice(instances),
            ts=ts,
            level="ERROR",
            message="timed out after %dms waiting for a database connection (pool %d/%d in use)"
            % (acquire_timeout_ms, params.to_value, params.to_value),
            attrs={"pool_max": params.to_value, "waited_ms": acquire_timeout_ms},
        )
        draft.logs.append(log)
        if first:
            draft.decisive.append([log, "retrievable"])
            first = False
        ts = ts + timedelta(seconds=rng.randint(15, 45))

    # ---- symptoms at the entry service ---------------------------------------------
    floor = rng.randint(68, 74)
    reshape_after(draft.find_metric(entry, business_name), onset, rng, float(floor), 0.8)
    reshape_after(draft.find_metric(entry, "error_rate_pct"), onset, rng, 100.0 - floor, 1.0)
    reshape_after(draft.find_metric(entry, "p99_latency_ms"), onset, rng, 6500.0, 600.0)

    upstream_timeout_ms = draft.find_edge(entry, params.component).config["timeout_ms"]
    entry_instances = draft.instances_of(entry)
    ts = onset + timedelta(seconds=rng.randint(5, 20))
    while ts < draft.window_end:
        draft.logs.append(
            DraftLog(
                service=entry,
                instance=rng.choice(entry_instances),
                ts=ts,
                level="WARN",
                message="upstream %s call failed: timeout after %dms route=%s"
                % (params.component, upstream_timeout_ms, rng.choice(draft.topology["routes"])),
                attrs={"status": 504, "request_id": "%08x" % rng.getrandbits(32)},
            )
        )
        ts = ts + timedelta(seconds=rng.randint(20, 60))

    # ---- the database looks quieter ---------------------------------------------------
    component_instances = len(instances)
    reshape_after(draft.find_metric(params.dependency, "active_connections"), onset, rng,
                  float(params.to_value * component_instances + 15), 3.0)

    # ---- incident report and answer key ----------------------------------------------
    draft.summary = "%s dropped to %d%%" % (business_label, floor)
    draft.reporter = rng.choice(draft.topology["reporters"])
    draft.scenario = {
        "root_component": params.component,
        "root_category": CATEGORY,
        "mechanism": (
            "A config change cut the %s connection pool to %s from %d to %d; requests wait "
            "for a free connection, time out, and the %s drops."
            % (params.component, params.dependency, params.from_value, params.to_value,
               business_label)
        ),
        "contributing_factors": [],
    }
    draft.protected_services.append(params.component)
