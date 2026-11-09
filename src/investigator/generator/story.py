"""The shared storyline that archetypes fill in: trigger -> onset -> signature evidence on the
root service -> errors climbing the call path -> a business symptom at the entry service.

An archetype describes its incident as a story dict and calls tell(draft, rng, story):

  root, category, mechanism   the answer key (truth.scenarios)
  summary                     incident report text; may use {label} and {floor}
  floor                       [lo, hi] for the entry service's business metric, or None
  change                      optional trigger change {change_type, summary, diff, version,
                              service}; decisive
  edge_updates                [[caller, callee, key, value]] (current config shown on edges)
  metrics                     [{name, unit, bad_mean, bad_sd, healthy_mean, healthy_sd,
                              service, per_instance}]; the first is decisive
  logs                        [[level, message, attrs]] repeated on the root (or log_service)
                              from the onset; the first line is decisive
  log_service                 where the signature logs live (default: root)
  logs_decisive               False: the logs support the story but are not decisive evidence
  change_lead_minutes         the change lands this long before the onset (default: just before)
  caller_message, caller_status   what services on the path to the root log
  contributing_factors        list of strings

Inconclusive incidents (draft.insufficiency set from the plan entry):
  withhold      parts never generated ("change", "logs", "metric"); recorded in
                truth.withheld_evidence
  unreachable   parts generated but marked unreachable (a persistent tool rule in the plan
                entry blocks them; the validator checks the rule exists)
  any other decisive part is left out of truth.decisive_evidence: it is ambiguous on its own.
"""

from datetime import timedelta

from investigator.generator.baseline import new_metric, reshape_after
from investigator.generator.draft import DraftChange, DraftLog


def path_between(draft, start, target):
    """Services on a call path start -> ... -> target (breadth-first over edges), or None."""
    if start == target:
        return [start]
    paths = [[start]]
    visited = [start]
    while paths:
        next_paths = []
        for path in paths:
            last = path[-1]
            for edge in draft.edges:
                if edge.caller != last or edge.callee in visited:
                    continue
                extended = path + [edge.callee]
                if edge.callee == target:
                    return extended
                visited.append(edge.callee)
                next_paths.append(extended)
        paths = next_paths
    return None


def find_or_create_metric(draft, rng, service, spec):
    for metric in draft.metrics:
        if metric.service == service and metric.metric_name == spec["name"] and metric.instance is None:
            return metric
    return new_metric(draft, rng, service, spec["name"], spec["unit"],
                      spec.get("healthy_mean", 0.0), spec.get("healthy_sd", 0.0), "prometheus")


def add_metric_story(draft, rng, service, spec, onset):
    """Shift the metric after onset. per_instance: one hot instance, the rest stay healthy,
    and the fleet-wide series barely moves (the average hides the hot node)."""
    if not spec.get("per_instance"):
        series = find_or_create_metric(draft, rng, service, spec)
        reshape_after(series, onset, rng, spec["bad_mean"], spec.get("bad_sd", 1.0))
        return series

    instances = draft.instances_of(service)
    hot_name = instances[0]
    hot = None
    for instance in instances:
        series = new_metric(draft, rng, service, spec["name"], spec["unit"],
                            spec.get("healthy_mean", 0.0), spec.get("healthy_sd", 0.0), "prometheus")
        series.instance = instance
        if instance == hot_name:
            reshape_after(series, onset, rng, spec["bad_mean"], spec.get("bad_sd", 1.0))
            hot = series
    fleet = find_or_create_metric(draft, rng, service, spec)
    share = (spec["bad_mean"] - spec.get("healthy_mean", 0.0)) / len(instances)
    reshape_after(fleet, onset, rng, spec.get("healthy_mean", 0.0) + share, spec.get("healthy_sd", 1.0))
    return hot


def withheld_row(kind, description, would_have_shown):
    return {"kind": kind, "description": description, "would_have_shown": would_have_shown}


def tell(draft, rng, story):
    root = story["root"]
    draft.service_role(root)      # raises for a service the topology does not have
    insufficiency = draft.insufficiency or {}
    withhold = insufficiency.get("withhold", [])
    unreachable = insufficiency.get("unreachable", [])
    inconclusive = bool(insufficiency)
    entry = draft.topology["entry_service"]
    business_name, business_unit, business_mean, business_sd, business_label = (
        draft.topology["business_metric"]
    )

    # ---- timeline -----------------------------------------------------------------
    trigger_ts = draft.window_start + timedelta(minutes=rng.randint(18, 24),
                                                seconds=rng.randint(0, 59))
    onset = trigger_ts + timedelta(seconds=rng.randint(40, 90))
    draft.trigger_ts = trigger_ts
    draft.onset_ts = onset
    draft.reported_at = onset + timedelta(minutes=rng.randint(12, 20))

    decisive = []     # [part, item]

    # ---- trigger change -----------------------------------------------------------
    change_spec = story.get("change")
    if change_spec is not None:
        # A slow-burning change (for example a migration) can land long before the symptoms;
        # it may then predate the stored data window.
        change_ts = trigger_ts
        if story.get("change_lead_minutes") is not None:
            change_ts = onset - timedelta(minutes=story["change_lead_minutes"])
        change = DraftChange(
            service=change_spec.get("service", root), ts=change_ts,
            change_type=change_spec["change_type"], version=change_spec.get("version"),
            author=rng.choice(draft.topology["authors"]), summary=change_spec["summary"],
            diff=change_spec["diff"],
        )
        if "change" in withhold:
            draft.withheld.append(withheld_row("change", change_spec["summary"], change_spec["diff"]))
        else:
            draft.changes.append(change)
            decisive.append(["change", change])

    for caller, callee, key, value in story.get("edge_updates", []):
        draft.find_edge(caller, callee).config[key] = value

    # ---- metrics --------------------------------------------------------------------
    first = True
    for spec in story.get("metrics", []):
        service = spec.get("service", root)
        if first and "metric" in withhold:
            draft.withheld.append(withheld_row("metric", "%s on %s" % (spec["name"], service),
                                               {"bad_mean": spec["bad_mean"]}))
            first = False
            continue
        series = add_metric_story(draft, rng, service, spec, onset)
        if first:
            decisive.append(["metric", series])
            first = False

    # ---- signature logs ------------------------------------------------------------
    log_service = story.get("log_service", root)
    log_specs = story.get("logs", [])
    if log_specs and "logs" in withhold:
        level, message, attrs = log_specs[0]
        draft.withheld.append(withheld_row("log", "%s logs on %s" % (level, log_service),
                                           {"message": message}))
    elif log_specs:
        instances = draft.instances_of(log_service)
        ts = onset
        index = 0
        while ts < draft.window_end:
            level, message, attrs = log_specs[index % len(log_specs)]
            log = DraftLog(service=log_service, instance=rng.choice(instances), ts=ts,
                           level=level, message=message, attrs=dict(attrs))
            draft.logs.append(log)
            # logs_decisive=False: the story must be solvable without logs (for example when
            # the log tool is down for the whole incident).
            if index == 0 and story.get("logs_decisive", True):
                decisive.append(["logs", log])
            index = index + 1
            ts = ts + timedelta(seconds=rng.randint(15, 45))
    draft.protected_services.append(root)
    if log_service not in draft.protected_services:
        draft.protected_services.append(log_service)

    # ---- errors climbing the call path ------------------------------------------------
    path = path_between(draft, entry, log_service)
    if path is not None and len(path) > 1:
        caller_template = story.get("caller_message",
                                    "upstream {callee} call failed: timeout after {timeout}ms route={route}")
        for position in range(len(path) - 2, -1, -1):
            caller = path[position]
            callee = path[position + 1]
            if draft.service_role(caller) not in ["app", "load_balancer"]:
                continue
            timeout = draft.find_edge(caller, callee).config.get("timeout_ms", 5000)
            caller_instances = draft.instances_of(caller)
            ts = onset + timedelta(seconds=rng.randint(5, 20))
            while ts < draft.window_end:
                message = caller_template.format(callee=callee, timeout=timeout,
                                                 route=rng.choice(draft.topology["routes"]))
                draft.logs.append(DraftLog(
                    service=caller, instance=rng.choice(caller_instances), ts=ts, level="WARN",
                    message=message,
                    attrs={"status": story.get("caller_status", 504),
                           "request_id": "%08x" % rng.getrandbits(32)},
                ))
                ts = ts + timedelta(seconds=rng.randint(20, 60))

    # ---- business symptom -------------------------------------------------------------
    floor = None
    if story.get("floor") is not None:
        low, high = story["floor"]
        floor = rng.randint(low, high)
        reshape_after(draft.find_metric(entry, business_name), onset, rng, float(floor), 0.8)
        reshape_after(draft.find_metric(entry, "error_rate_pct"), onset, rng, 100.0 - floor, 1.0)

    # ---- report and answer key ---------------------------------------------------------
    draft.summary = story["summary"].format(label=business_label, floor=floor)
    draft.reporter = rng.choice(draft.topology["reporters"])
    draft.scenario = {
        "root_component": root,
        "root_category": story["category"],
        "mechanism": story["mechanism"],
        "contributing_factors": list(story.get("contributing_factors", [])),
        "unidentifiable_reason": insufficiency.get("reason"),
    }

    for part, item in decisive:
        if not inconclusive:
            draft.decisive.append([item, "retrievable"])
        elif part in unreachable:
            draft.decisive.append([item, "unreachable"])
        # otherwise ambiguous on its own: not decisive for an inconclusive incident

    for alternative in insufficiency.get("alternatives", []):
        draft.alternatives.append(dict(alternative))
