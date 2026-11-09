"""The plain-language case stories shown by the viewer."""

from datetime import datetime, timezone
from pathlib import Path

from investigator.company_context import load_company_contexts
from investigator.web.story import (DATA_TABLES, before_and_during, build_story, build_timeline,
                                    diff_rows, find_onset, first_move, load_stories)

SRC_DIR = Path(__file__).resolve().parents[2] / "src" / "investigator"
WEB_DIR = SRC_DIR / "web"
STORY_PARTS = ["summary", "what_went_wrong", "impact", "misleading", "clues", "answer", "timeline",
               "fixes", "glossary"]


def points_every_minute(values):
    points = []
    for index in range(len(values)):
        points.append((index * 60000, values[index]))
    return points


def test_before_is_the_usual_level_and_during_is_the_late_level():
    points = points_every_minute([10, 10, 10, 10, 90, 90, 90, 90, 90])
    before, during = before_and_during(points, 3 * 60000)
    assert before == 10
    assert during == 90


def test_the_moment_a_metric_moved_is_the_first_reading_past_halfway():
    points = points_every_minute([10, 10, 10, 10, 90, 90, 90])
    assert first_move(points, 3 * 60000, 10, 90) == 4 * 60000


def test_a_metric_that_never_moved_has_no_moment():
    points = points_every_minute([10, 10, 10, 10, 10])
    assert first_move(points, 2 * 60000, 10, 90) is None


def test_the_story_stays_hidden_until_the_answer_key_may_be_shown():
    # No answer key yet (a run is unfinished): the story contains the answer, so none is given.
    stories = {"stories": {"X-1": {}}}
    incident = {"plan_id": "X-1", "domain": "somewhere"}
    assert build_story(None, incident, None, stories, {}) is None


def test_a_case_without_a_written_story_keeps_the_old_page():
    incident = {"plan_id": "X-1", "domain": "somewhere"}
    assert build_story(None, incident, {"decisive_evidence": []}, {"stories": {}}, {}) is None


def test_every_written_story_has_all_its_parts_and_a_company():
    stories = load_stories()
    companies = load_company_contexts()
    assert stories["stories"]
    for plan_id in stories["stories"]:
        story = stories["stories"][plan_id]
        for part in STORY_PARTS:
            assert story.get(part), "%s has no %s" % (plan_id, part)
        assert story["domain"] in companies


def test_the_story_says_nothing_about_how_the_systems_scored():
    # The story is the problem statement; results come in their own section afterwards.
    words = ["multi-agent", "multi_agent", "single agent", "single_agent", "who solved"]
    text = (WEB_DIR / "static" / "incident" / "story.js").read_text(encoding="utf-8").lower()
    file_text = (Path(__file__).resolve().parents[2] / "configs" / "case_stories.yaml")
    text = text + file_text.read_text(encoding="utf-8").lower()
    for word in words:
        assert word not in text, word


def test_the_metric_charts_move_into_the_full_story():
    # The page removes this section by its id once both charts have moved.
    page = (WEB_DIR / "pages" / "incident.html").read_text(encoding="utf-8")
    script = (WEB_DIR / "static" / "incident" / "story.js").read_text(encoding="utf-8")
    assert 'id="metrics-section"' in page
    assert 'getElementById("metrics-section").remove()' in script
    assert 'takeChart("metric-users"' in script
    assert 'takeChart("metric-key"' in script


def test_every_answer_key_table_is_marked_hidden_from_the_agents():
    truth_tables = 0
    for entry in DATA_TABLES:
        if entry["table"].startswith("truth."):
            truth_tables += 1
            assert entry["reached"] is None, entry["table"]
        else:
            assert entry["reached"], entry["table"]
    assert truth_tables > 0


def test_only_the_viewer_reads_the_stories():
    # The stories contain the answers, so nothing an agent runs may load them.
    readers = []
    for path in sorted(SRC_DIR.rglob("*.py")):
        if WEB_DIR in path.parents:
            continue
        if "case_stories" in path.read_text(encoding="utf-8"):
            readers.append(str(path))
    assert readers == []


class FakeTimelineConnection:
    """Answers the two timeline queries with fixed times."""

    def __init__(self, first_error_ms, change_ms):
        self.first_error_ms = first_error_ms
        self.change_ms = change_ms

    def execute(self, sql, params):
        moment = self.first_error_ms if "min(l.ts)" in sql else self.change_ms
        value = None if moment is None else datetime.fromtimestamp(moment / 1000, tz=timezone.utc)
        if "ORDER BY ts" in sql:
            return [("C-OTHER", value)]
        return FakeRow((value,))


class FakeRow:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


TIMELINE_LABELS = {"first_sign": "first sign", "first_error": "first error", "users_hit": "users hit",
                   "reported": "reported", "data_ends": "ends", "decoy_change": "misleading change",
                   "change": "the change"}


def timeline_texts(connection, clues, decoys):
    incident = {"incident_id": "inc_1", "reported_at": 500000, "window_end": 900000}
    truth = {"root_component": "api", "decoys": decoys, "decisive_evidence": []}
    impact = [{"moved_at": 300000}]
    events = build_timeline(connection, incident, truth, {"timeline": TIMELINE_LABELS}, clues, impact)
    texts = []
    for event in events:
        texts.append(event["text"])
    return texts


def test_the_first_error_is_not_listed_twice():
    clues = [{"kind": "log", "ts": 200000}]
    assert "first error" not in timeline_texts(FakeTimelineConnection(200000, None), clues, [])


def test_a_later_first_error_gets_its_own_line():
    clues = [{"kind": "log", "ts": 200000}]
    assert "first error" in timeline_texts(FakeTimelineConnection(400000, None), clues, [])


def test_a_misleading_change_appears_on_the_timeline_in_order():
    clues = [{"kind": "log", "ts": 200000}]
    decoys = [{"evidence_id": "C-1", "decoy_type": "red_herring_change"}]
    texts = timeline_texts(FakeTimelineConnection(None, 100000), clues, decoys)
    assert texts[0] == "misleading change"


def test_the_first_four_cases_have_stories_on_a_described_company():
    stories = load_stories()
    companies = load_company_contexts()
    for plan_id in ["INC-01", "INC-02", "INC-03", "INC-04"]:
        assert plan_id in stories["stories"]
        assert stories["stories"][plan_id]["domain"] in companies


def onset_of(first_error_ms, change_ms, decisive):
    incident = {"incident_id": "inc_1", "window_start": 60000, "reported_at": 500000}
    truth = {"root_component": "api", "decisive_evidence": decisive}
    return find_onset(FakeTimelineConnection(first_error_ms, change_ms), incident, truth)


def test_a_change_from_before_the_data_window_does_not_set_the_onset():
    # the change is at 10s and the window opens at 60s, so the first error is used instead
    assert onset_of(200000, 10000, ["C-1"]) == 200000


def test_a_decisive_change_inside_the_window_sets_the_onset():
    assert onset_of(200000, 100000, ["C-1"]) == 100000


def test_with_no_decisive_evidence_the_first_change_sets_the_onset():
    assert onset_of(None, 100000, []) == 100000


def test_a_metric_with_nothing_before_the_onset_still_has_a_baseline():
    before, during = before_and_during(points_every_minute([5, 5, 9, 9, 9, 9]), -1)
    assert before == 5
    assert during == 9


def test_another_change_gets_its_own_timeline_line_when_the_story_has_one():
    labels = dict(TIMELINE_LABELS, other_change="another change")
    incident = {"incident_id": "inc_1", "reported_at": 500000, "window_end": 900000}
    truth = {"root_component": "api", "decoys": [], "decisive_evidence": []}
    impact = [{"moved_at": None}]
    events = build_timeline(FakeTimelineConnection(None, 100000), incident, truth, {"timeline": labels}, [], impact)
    texts = []
    for event in events:
        texts.append(event["text"])
    assert "another change" in texts


def test_all_twelve_cases_have_a_story_on_a_described_company():
    stories = load_stories()
    companies = load_company_contexts()
    for number in range(1, 13):
        plan_id = "INC-%02d" % number
        assert plan_id in stories["stories"], plan_id
        assert stories["stories"][plan_id]["domain"] in companies


def test_a_diff_can_hold_a_plain_description():
    rows = diff_rows({"migration": "ALTER TABLE invoices", "schema": {"from": "v2", "to": "v3"}})
    assert rows[0] == {"field": "migration", "before": None, "after": "ALTER TABLE invoices"}
    assert rows[1] == {"field": "schema", "before": "v2", "after": "v3"}
