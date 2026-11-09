"""The five tools as shown on a case page: what each does, its simulated failure, its use."""

from investigator.web.evidence import describe_evidence

TOOL_INFO = [
    ["query_logs", "Logs", "Searches log lines by service, level, text and time."],
    ["query_metrics", "Metrics", "Reads a metric's time series (for example latency or error rate)."],
    ["get_change_history", "Deployments and changes",
     "Lists deploys, config changes, feature flags and scaling; shows a change's diff on request."],
    ["get_service_dependencies", "Service dependencies",
     "Shows who calls whom, with each call's timeout, retries and pool size."],
    ["search_past_incidents", "Past incidents", "Finds earlier incidents with similar symptoms."],
]


def describe_failure_rule(rules):
    """The simulated failure of one tool in this case, in one sentence."""
    if isinstance(rules, list):
        rules = rules[0] if rules else {}
    mode = rules.get("mode")
    status = rules.get("status", "error")
    status = ("an " if status[:1] in "aeiou" else "a ") + status
    # the sentences below say "with %s", for example "with a timeout" or "with an error"
    if mode == "transient":
        count = rules.get("fail_first_n", 1)
        tries = "first try" if count == 1 else "first %d tries" % count
        return ("Each new query fails on its %s with %s; the gateway retries it automatically."
                % (tries, status))
    if mode == "partial":
        share = int(round(float(rules.get("keep_fraction", 0.5)) * 100))
        return "Returns only about %d%% of the results (an incomplete answer, flagged as partial)." % share
    if mode == "persistent":
        return "Always fails with %s: the tool is down for the whole investigation." % status
    if mode == "probabilistic":
        return "Fails at random some of the time."
    return "Fails in an unusual way (%s)." % mode


def summarise_tool_calls(calls):
    logical = 0
    failed = 0
    partial = 0
    empty = 0
    for call in calls:
        if call["attempt"] == 1:
            logical = logical + 1
        if call["status"] in ("timeout", "error"):
            failed = failed + 1
        elif call["status"] == "partial":
            partial = partial + 1
        elif call["status"] == "empty":
            empty = empty + 1
    return {"calls": logical, "attempts": len(calls), "failed": failed, "partial": partial,
            "empty": empty}


def tool_overview(connection, runs, conditions):
    """Per tool: what it does, this case's simulated failure, how each system used it, one
    real input/output example and, if it failed, what happened next."""
    rules_by_tool = {}
    for row in (conditions or {}).get("tool_failures", []):
        rules_by_tool[row["tool_name"]] = row["rules"]
    overview = []
    for tool, name, what in TOOL_INFO:
        entry = {"tool": tool, "name": name, "what": what, "failure": None, "usage": {},
                 "example": None, "failure_example": None}
        if tool in rules_by_tool:
            entry["failure"] = describe_failure_rule(rules_by_tool[tool])
        for system in ["multi_agent", "single_agent"]:
            run = runs.get(system)
            if run is None:
                continue
            calls = []
            for call in run["tool_calls"]:
                if call["tool"] == tool:
                    calls.append(call)
            entry["usage"][system] = summarise_tool_calls(calls)
            for index in range(len(calls)):
                call = calls[index]
                if entry["example"] is None and call["status"] in ("ok", "partial") and call["sample_ids"]:
                    sample = []
                    for evidence_id in call["sample_ids"]:
                        sample.append(describe_evidence(connection, evidence_id))
                    entry["example"] = {"system": system, "args": call["args"],
                                        "status": call["status"], "items": call["evidence_count"],
                                        "sample": sample}
                if entry["failure_example"] is None and call["status"] in ("timeout", "error", "partial"):
                    then = None
                    for later in calls[index + 1:]:
                        if later["args"] == call["args"] and later["attempt"] == call["attempt"] + 1:
                            then = later["status"]
                            break
                    entry["failure_example"] = {"system": system, "attempt": call["attempt"],
                                                "status": call["status"], "then": then}
        overview.append(entry)
    return overview
