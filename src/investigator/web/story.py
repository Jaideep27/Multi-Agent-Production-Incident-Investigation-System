"""The plain-language story of a test case, for someone reading the viewer for the first time.

Two sources, on purpose. The telling (the company, what a clue means, why a trap is wrong) is
written by hand in configs/case_stories.yaml. Every time, number and log line comes from the
database, so the story cannot disagree with the data. The file contains the answers: only the
viewer reads it, and only once the answer key may be shown.
"""

from pathlib import Path

import yaml

from investigator.web.rows import epoch_ms, rows_as_dicts

DEFAULT_STORIES_PATH = Path(__file__).resolve().parents[3] / "configs" / "case_stories.yaml"


def load_stories(path=DEFAULT_STORIES_PATH):
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


# ------------------------------------------------------------------ numbers

def median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def before_and_during(points, onset_ms):
    """The usual level up to the onset, and the level over the last third of the data.

    The last third, not "everything after the onset", because some faults take a while to
    show after the change that caused them.
    """
    before_values = []
    for timestamp, value in points:
        if timestamp <= onset_ms:
            before_values.append(value)
    if not before_values:
        before_values.append(points[0][1])
    during_values = []
    for timestamp, value in points[len(points) * 2 // 3:]:
        during_values.append(value)
    return median(before_values), median(during_values)


def first_move(points, onset_ms, before, during):
    """When a metric first got more than halfway from its usual level to its new one."""
    halfway = abs(during - before) / 2
    for timestamp, value in points:
        if timestamp > onset_ms and abs(value - before) > halfway:
            return timestamp
    return None


def read_points(connection, incident_id, series_id):
    points = []
    for row in connection.execute(
            "SELECT ts, value FROM evidence.metric_points WHERE incident_id = %s "
            "AND series_id = %s ORDER BY ts", (incident_id, series_id)):
        points.append((epoch_ms(row[0]), row[1]))
    return points


def measure(connection, incident_id, series, onset_ms):
    """One metric's before, during and the moment it moved."""
    points = read_points(connection, incident_id, series["evidence_id"])
    before, during = before_and_during(points, onset_ms)
    return {"service": series["service"], "metric": series["metric_name"],
            "unit": series["unit"], "before": before, "during": during,
            "moved_at": first_move(points, onset_ms, before, during)}


def find_series(connection, incident_id, service, metric_name):
    """A metric named in the stories file. A name that does not exist is a writing mistake."""
    rows = rows_as_dicts(connection.execute(
        """
        SELECT m.evidence_id, s.name AS service, m.metric_name, m.unit
          FROM evidence.metric_series m JOIN evidence.services s ON s.service_id = m.service_id
         WHERE m.incident_id = %s AND s.name = %s AND m.metric_name = %s
         ORDER BY m.instance NULLS FIRST LIMIT 1
        """, (incident_id, service, metric_name)))
    if not rows:
        raise ValueError("the story names a metric that does not exist: %s %s"
                         % (service, metric_name))
    return rows[0]


def measure_listed(connection, incident_id, listed, onset_ms):
    """The metrics a story lists, each with its plain-language label and note."""
    measured = []
    for item in listed:
        series = find_series(connection, incident_id, item["service"], item["metric"])
        entry = measure(connection, incident_id, series, onset_ms)
        entry["label"] = item["label"]
        entry["short"] = item.get("short")
        entry["note"] = item.get("note")
        measured.append(entry)
    return measured


# ------------------------------------------------------------------ the clues

def find_onset(connection, incident, truth):
    """When the trouble began, as a moment inside the data window.

    The earliest decisive change or log line. A change from before the window does not count:
    it may have sat harmlessly for a long time, and there is no data from before it to compare
    with. Without decisive evidence, the root service's first error, then the first change.
    """
    earliest = None
    for evidence_id in truth["decisive_evidence"]:
        row = None
        if evidence_id.startswith("C-"):
            row = connection.execute(
                "SELECT ts FROM evidence.changes WHERE evidence_id = %s",
                (evidence_id,)).fetchone()
        elif evidence_id.startswith("L-"):
            row = connection.execute(
                "SELECT ts FROM evidence.log_entries WHERE evidence_id = %s",
                (evidence_id,)).fetchone()
        if row is None:
            continue
        found = epoch_ms(row[0])
        if found >= incident["window_start"] and (earliest is None or found < earliest):
            earliest = found
    if earliest is not None:
        return earliest
    first_error = first_error_of(connection, incident["incident_id"], truth["root_component"])
    if first_error is not None:
        return first_error
    row = connection.execute("SELECT min(ts) FROM evidence.changes WHERE incident_id = %s",
                             (incident["incident_id"],)).fetchone()
    if row is not None and row[0] is not None:
        return max(epoch_ms(row[0]), incident["window_start"])
    return incident["reported_at"]


def diff_rows(stored):
    """A change's diff as before/after rows. Some entries are a plain description instead."""
    rows = []
    for field in sorted(stored):
        value = stored[field]
        if isinstance(value, dict):
            rows.append({"field": field, "before": value.get("from"), "after": value.get("to")})
        else:
            rows.append({"field": field, "before": None, "after": value})
    return rows


def change_clue(connection, evidence_id):
    row = rows_as_dicts(connection.execute(
        """
        SELECT c.ts, s.name AS service, c.change_type, c.version, c.author, c.summary, d.diff
          FROM evidence.changes c JOIN evidence.services s ON s.service_id = c.service_id
          LEFT JOIN evidence.change_details d ON d.evidence_id = c.evidence_id
         WHERE c.evidence_id = %s
        """, (evidence_id,)))[0]
    diff = diff_rows(row["diff"] or {})
    return {"kind": "change", "ts": epoch_ms(row["ts"]), "service": row["service"],
            "change_type": row["change_type"], "version": row["version"],
            "author": row["author"], "summary": row["summary"], "diff": diff}


def log_clue(connection, incident_id, evidence_id):
    row = rows_as_dicts(connection.execute(
        """
        SELECT l.ts, l.level, l.message, l.service_id, s.name AS service
          FROM evidence.log_entries l JOIN evidence.services s ON s.service_id = l.service_id
         WHERE l.evidence_id = %s
        """, (evidence_id,)))[0]
    repeats = connection.execute(
        "SELECT count(*) FROM evidence.log_entries WHERE incident_id = %s AND service_id = %s "
        "AND message = %s", (incident_id, row["service_id"], row["message"])).fetchone()[0]
    return {"kind": "log", "ts": epoch_ms(row["ts"]), "service": row["service"],
            "level": row["level"], "message": row["message"], "repeats": repeats}


def metric_clue(connection, incident_id, evidence_id, onset_ms):
    series = rows_as_dicts(connection.execute(
        """
        SELECT m.evidence_id, s.name AS service, m.metric_name, m.unit
          FROM evidence.metric_series m JOIN evidence.services s ON s.service_id = m.service_id
         WHERE m.evidence_id = %s
        """, (evidence_id,)))[0]
    clue = measure(connection, incident_id, series, onset_ms)
    clue["kind"] = "metric"
    return clue


def read_clues(connection, incident_id, truth, written, onset_ms):
    """The decisive evidence, shown in full, each with what it means in plain words."""
    clues = []
    for evidence_id in truth["decisive_evidence"]:
        if evidence_id.startswith("C-"):
            clue = change_clue(connection, evidence_id)
        elif evidence_id.startswith("L-"):
            clue = log_clue(connection, incident_id, evidence_id)
        elif evidence_id.startswith("M-"):
            clue = metric_clue(connection, incident_id, evidence_id, onset_ms)
        else:
            continue
        clue["evidence_id"] = evidence_id
        clue["title"] = written["clues"][clue["kind"]]["title"]
        clue["meaning"] = written["clues"][clue["kind"]]["meaning"]
        clues.append(clue)
    return clues


# ------------------------------------------------------------------ the traps

def past_incident_details(connection, evidence_id):
    rows = rows_as_dicts(connection.execute(
        "SELECT occurred_at, title, root_cause_category, root_cause_component, resolution, "
        "symptoms FROM evidence.past_incidents WHERE evidence_id = %s", (evidence_id,)))
    row = rows[0]
    row["occurred_at"] = epoch_ms(row["occurred_at"])
    return row


def read_misleading(connection, incident_id, truth, written, onset_ms):
    """The misleading information: why it is tempting, why it is wrong, and the real numbers."""
    misleading = []
    for item in written["misleading"]:
        entry = {"title": item["title"], "why_tempting": item["why_tempting"],
                 "why_wrong": item["why_wrong"],
                 "checks": measure_listed(connection, incident_id, item.get("checks", []),
                                          onset_ms),
                 "past_incident": None, "text": None}
        for decoy in truth["decoys"]:
            if decoy["decoy_type"] != item.get("decoy_type"):
                continue
            entry["text"] = decoy["text"] if entry["text"] is None else entry["text"] + " · " + decoy["text"]
            if decoy["evidence_id"].startswith("P-"):
                entry["past_incident"] = past_incident_details(connection, decoy["evidence_id"])
        misleading.append(entry)
    return misleading


# ------------------------------------------------------------------ the timeline

def first_error_of(connection, incident_id, service):
    row = connection.execute(
        """
        SELECT min(l.ts) FROM evidence.log_entries l
          JOIN evidence.services s ON s.service_id = l.service_id
         WHERE l.incident_id = %s AND s.name = %s AND l.level = 'ERROR'
        """, (incident_id, service)).fetchone()
    return epoch_ms(row[0])


def build_timeline(connection, incident, truth, written, clues, impact):
    """Real timestamps from the data, each with one plain sentence from the story."""
    labels = written["timeline"]
    events = []
    logged_at = []
    for clue in clues:
        if clue["kind"] == "change":
            events.append({"ts": clue["ts"], "text": labels["change"]})
        elif clue["kind"] == "log":
            events.append({"ts": clue["ts"], "text": labels["first_sign"]})
            logged_at.append(clue["ts"])
    # A misleading change belongs on the timeline too: being close in time is what misleads.
    for decoy in truth["decoys"]:
        if decoy["evidence_id"].startswith("C-") and "decoy_change" in labels:
            row = connection.execute("SELECT ts FROM evidence.changes WHERE evidence_id = %s",
                                     (decoy["evidence_id"],)).fetchone()
            events.append({"ts": epoch_ms(row[0]), "text": labels["decoy_change"]})
    # Changes that are neither the proof nor a planted trap, when the story has a line for them.
    if "other_change" in labels:
        known = list(truth["decisive_evidence"])
        for decoy in truth["decoys"]:
            known.append(decoy["evidence_id"])
        for evidence_id, ts in connection.execute(
                "SELECT evidence_id, ts FROM evidence.changes WHERE incident_id = %s ORDER BY ts",
                (incident["incident_id"],)):
            if evidence_id not in known:
                events.append({"ts": epoch_ms(ts), "text": labels["other_change"]})
    if impact[0]["moved_at"] is not None:
        events.append({"ts": impact[0]["moved_at"], "text": labels["users_hit"]})
    first_error = first_error_of(connection, incident["incident_id"], truth["root_component"])
    # When the first error is the log line already listed, it would only repeat it.
    if first_error is not None and first_error not in logged_at:
        events.append({"ts": first_error, "text": labels["first_error"]})
    events.append({"ts": incident["reported_at"], "text": labels["reported"]})
    events.append({"ts": incident["window_end"], "text": labels["data_ends"]})

    def event_time(event):
        return event["ts"]

    events.sort(key=event_time)
    return events


# ------------------------------------------------------------------ the data behind a case

# Every table holding this case's data, and how an agent can reach it (None = it cannot).
DATA_TABLES = [
    {"table": "evidence.incidents", "reached": "Given in the brief",
     "holds": "The report: the one-line problem, who reported it, the time window"},
    {"table": "evidence.services", "reached": "Given in the brief",
     "holds": "The services, their types and how many copies run"},
    {"table": "evidence.service_dependencies", "reached": "get_service_dependencies",
     "holds": "Who calls whom, with timeouts and retries"},
    {"table": "evidence.log_entries", "reached": "query_logs", "holds": "Log lines"},
    {"table": "evidence.metric_series", "reached": "query_metrics",
     "holds": "Which metrics exist for each service"},
    {"table": "evidence.metric_points", "reached": "query_metrics",
     "holds": "The readings of those metrics, one per minute"},
    {"table": "evidence.changes", "reached": "get_change_history",
     "holds": "Deploys, config changes and feature switches"},
    {"table": "evidence.change_details", "reached": "get_change_history",
     "holds": "What exactly each change altered"},
    {"table": "evidence.past_incidents", "reached": "search_past_incidents",
     "holds": "Earlier outages"},
    {"table": "evidence.tool_behavior", "reached": "Applied by the tool server",
     "holds": "Which tools are set to fail in this case"},
    {"table": "truth.scenarios", "reached": None, "holds": "The answer: root cause and category"},
    {"table": "truth.decisive_evidence", "reached": None,
     "holds": "Which evidence proves the answer"},
    {"table": "truth.decoys", "reached": None, "holds": "Which evidence is misleading"},
    {"table": "truth.noise_applied", "reached": None, "holds": "Harmless noise added on purpose"},
    {"table": "truth.alternative_explanations", "reached": None,
     "holds": "Other causes that would also fit"},
    {"table": "truth.withheld_evidence", "reached": None,
     "holds": "Evidence removed on purpose"},
]


def read_inventory(connection, incident_id):
    """How many rows each table holds for this case. Table names come from the list above."""
    inventory = []
    for entry in DATA_TABLES:
        row = connection.execute("SELECT count(*) FROM %s WHERE incident_id = %%s" % entry["table"],
                                 (incident_id,)).fetchone()
        inventory.append({"table": entry["table"], "holds": entry["holds"],
                          "reached": entry["reached"], "rows": row[0]})
    return inventory


# ------------------------------------------------------------------ the whole story

def company_with_services(company, incident):
    copies = {}
    roles = {}
    for service in incident["services"]:
        copies[service["name"]] = service["instance_count"]
        roles[service["name"]] = service["role"]
    services = []
    for service in company["services"]:
        services.append({"name": service["name"], "plain": service["plain"],
                         "role": roles[service["name"]], "copies": copies[service["name"]]})
    return {"name": company["name"], "what_it_does": company["what_it_does"],
            "how_it_works": company["how_it_works"], "services": services}


def read_missing_evidence(connection, incident_id):
    """Evidence removed on purpose, and what it would have shown."""
    missing = []
    for description, shown in connection.execute(
            "SELECT description, would_have_shown FROM truth.withheld_evidence "
            "WHERE incident_id = %s ORDER BY withheld_id", (incident_id,)):
        message = None
        if isinstance(shown, dict):
            message = shown.get("message")
        missing.append({"description": description, "message": message})
    return missing


def build_story(connection, incident, truth, stories, companies):
    """The full story of one case, or None if it has none or the answer must stay hidden."""
    if truth is None:
        return None
    written = stories["stories"].get(incident["plan_id"])
    if written is None:
        return None
    incident_id = incident["incident_id"]
    onset_ms = find_onset(connection, incident, truth)
    impact = measure_listed(connection, incident_id, written["impact"], onset_ms)
    clues = read_clues(connection, incident_id, truth, written, onset_ms)
    return {
        "company": company_with_services(companies[incident["domain"]], incident),
        "summary": written["summary"],
        "what_went_wrong": written["what_went_wrong"],
        "impact": impact,
        "misleading": read_misleading(connection, incident_id, truth, written, onset_ms),
        "clues": clues,
        "clues_note": written["clues"].get("note"),
        "missing_evidence": read_missing_evidence(connection, incident_id),
        "answer": written["answer"],
        "timeline": build_timeline(connection, incident, truth, written, clues, impact),
        "fixes": written["fixes"],
        "glossary": written["glossary"],
        "inventory": read_inventory(connection, incident_id),
    }
