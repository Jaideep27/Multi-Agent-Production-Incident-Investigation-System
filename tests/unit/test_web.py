"""The viewer: read-only routes, and the helpers that turn recorded runs into views.

The data store is faked here; the real one needs the demo database.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from investigator.blackboard.store import Blackboard
from investigator.web.app import create_app
from investigator.web.evidence import relative_shift
from investigator.web.run_views import (all_finished, newest_per_system, read_blackboard,
                                        split_into_steps, tool_calls_by_round)
from investigator.web.tool_views import describe_failure_rule, summarise_tool_calls
from investigator.web.rerun import RerunManager


class FakeStore:
    label = "test-label"

    def evaluation(self):
        return {"label": self.label, "incidents": []}

    def incident(self, incident_id, run_label=None):
        if incident_id != "inc_00000001":
            return None
        return {"incident": {"incident_id": incident_id}, "label": run_label or self.label}

    def series_points(self, incident_id, evidence_id):
        if evidence_id != "M-1":
            return None
        return {"evidence_id": evidence_id, "points": [[0, 1.0]]}

    def tool_data(self, incident_id, tool):
        if tool != "query_logs":
            return None
        return {"tool": tool, "rows": [{"evidence_id": "L-1", "message": "hello"}]}

    def conversation(self, run_id):
        return {"history": [{"sender": "critic", "text": "hello"}]}

    def live_progress(self, label):
        return [{"system": "multi_agent", "status": "running", "llm_calls": 3}]


class FakeProcess:
    def __init__(self):
        self.code = None

    def poll(self):
        return self.code


class FakeLauncher:
    def __init__(self):
        self.commands = []
        self.processes = []

    def __call__(self, command, log_path):
        self.commands.append(command)
        process = FakeProcess()
        self.processes.append(process)
        return process


def test_no_rerun_flag_turns_reruns_off_for_a_public_link():
    from investigator.web.__main__ import parse_args, rerun_manager
    assert rerun_manager(parse_args(["--no-rerun"])) is None
    assert isinstance(rerun_manager(parse_args([])), RerunManager)


def make_client(reruns=None):
    return TestClient(create_app(FakeStore(), reruns))


def make_rerun_client(tmp_path):
    launcher = FakeLauncher()
    manager = RerunManager("test-label", log_dir=tmp_path, launcher=launcher)
    return make_client(manager), launcher


# ------------------------------------------------------------------ routes


def test_pages_open_without_a_login():
    client = make_client()
    for path in ["/", "/incident/inc_00000001"]:
        response = client.get(path)
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]


def test_data_endpoints_answer_and_unknown_ids_are_404():
    client = make_client()
    assert client.get("/api/info").json() == {"label": "test-label"}
    assert client.get("/api/evaluation").status_code == 200
    assert client.get("/api/incidents/inc_00000001").status_code == 200
    assert client.get("/api/incidents/inc_99999999").status_code == 404
    assert client.get("/api/incidents/inc_00000001/series/M-1").status_code == 200
    assert client.get("/api/incidents/inc_00000001/series/M-9").status_code == 404


def test_tool_data_is_served_per_tool():
    client = make_client()
    response = client.get("/api/incidents/inc_00000001/tool-data/query_logs")
    assert response.status_code == 200
    assert response.json()["rows"][0]["evidence_id"] == "L-1"
    assert client.get("/api/incidents/inc_00000001/tool-data/drop_tables").status_code == 404


def test_the_viewer_cannot_change_anything():
    client = make_client()
    for path in ["/api/evaluation", "/api/incidents/inc_00000001"]:
        assert client.post(path).status_code == 405
        assert client.delete(path).status_code == 405


# ------------------------------------------------------------------ rerun


def test_rerun_starts_a_worker_under_its_own_label(tmp_path):
    client, launcher = make_rerun_client(tmp_path)
    response = client.post("/api/incidents/inc_00000001/rerun",
                           json={"systems": ["multi_agent"], "mode": "fresh"})
    assert response.status_code == 202
    command = launcher.commands[0]
    assert command[1:3] == ["-m", "investigator.batch"]
    label = command[command.index("--label") + 1]
    assert label.startswith("test-label-rerun-")
    assert command[command.index("--incidents") + 1] == "inc_00000001"
    assert command[command.index("--cache-mode") + 1] == "off"
    status = client.get("/api/rerun").json()
    assert status["state"] == "running"
    assert status["progress"][0]["llm_calls"] == 3


def test_a_rerun_gives_the_agents_the_company_background(tmp_path):
    client, launcher = make_rerun_client(tmp_path)
    client.post("/api/incidents/inc_00000001/rerun", json={"mode": "fresh"})
    assert "--company-context" in launcher.commands[0]


def test_replay_reuses_recorded_answers(tmp_path):
    client, launcher = make_rerun_client(tmp_path)
    client.post("/api/incidents/inc_00000001/rerun", json={"mode": "replay"})
    command = launcher.commands[0]
    assert command[command.index("--cache-mode") + 1] == "record"
    assert "multi_agent" in command and "single_agent" in command


def test_rerun_can_choose_haiku_or_sonnet(tmp_path):
    client, launcher = make_rerun_client(tmp_path)
    client.post("/api/incidents/inc_00000001/rerun", json={"model": "sonnet"})
    command = launcher.commands[0]
    assert command[command.index("--model") + 1] == "sonnet"
    assert client.get("/api/rerun").json()["model"] == "sonnet"
    launcher.processes[0].code = 0
    client.post("/api/incidents/inc_00000001/rerun", json={})
    command = launcher.commands[1]
    assert command[command.index("--model") + 1] == "haiku"
    launcher.processes[1].code = 0
    assert client.post("/api/incidents/inc_00000001/rerun",
                       json={"model": "opus"}).status_code == 400


def test_each_system_shows_its_newest_run():
    # the newest rerun only re-ran the single agent, so the team comes from the run before
    stored = {
        "base-rerun-2": {"single_agent": {"status": "completed"}},
        "base-rerun-1": {"multi_agent": {"status": "completed"},
                         "single_agent": {"status": "completed"}},
        "base": {"multi_agent": {"status": "completed"}, "single_agent": {"status": "completed"}},
    }
    asked = []

    def runs_for_label(label):
        asked.append(label)
        return stored[label]

    shown = newest_per_system(["base-rerun-2", "base-rerun-1", "base"], runs_for_label)
    assert shown["single_agent"]["label"] == "base-rerun-2"
    assert shown["multi_agent"]["label"] == "base-rerun-1"
    assert asked == ["base-rerun-2", "base-rerun-1"]   # stops once both systems are found


def test_only_one_rerun_at_a_time(tmp_path):
    client, launcher = make_rerun_client(tmp_path)
    assert client.post("/api/incidents/inc_00000001/rerun", json={}).status_code == 202
    assert client.post("/api/incidents/inc_00000001/rerun", json={}).status_code == 409
    launcher.processes[0].code = 0
    assert client.get("/api/rerun").json()["state"] == "finished"
    assert client.post("/api/incidents/inc_00000001/rerun", json={}).status_code == 202


def test_rerun_rejects_bad_requests(tmp_path):
    client, launcher = make_rerun_client(tmp_path)
    assert client.post("/api/incidents/not-an-id/rerun", json={}).status_code == 404
    assert client.post("/api/incidents/inc_00000001/rerun", json={"mode": "maybe"}).status_code == 400
    assert client.post("/api/incidents/inc_00000001/rerun",
                       json={"systems": ["everyone"]}).status_code == 400
    assert launcher.commands == []


def test_only_the_official_run_or_its_reruns_can_be_shown():
    client = make_client()
    assert client.get("/api/incidents/inc_00000001?run=test-label").status_code == 200
    assert client.get("/api/incidents/inc_00000001?run=test-label-rerun-20261108-120000").status_code == 200
    assert client.get("/api/incidents/inc_00000001?run=other-label").status_code == 404


# ------------------------------------------------------------------ reading runs


def test_single_agent_tool_calls_belong_to_the_turn_that_asked_for_them():
    llm_calls = [
        {"agent": "investigator", "attempt": 1, "status": "ok", "end_s": 5.0},
        {"agent": "investigator", "attempt": 1, "status": "ok", "end_s": 12.0},
        {"agent": "investigator", "attempt": 1, "status": "ok", "end_s": 20.0},
    ]
    tool_calls = [
        {"tool": "query_logs", "status": "ok", "latency_ms": 40, "end_s": 5.1},
        {"tool": "query_metrics", "status": "timeout", "latency_ms": 100, "end_s": 5.3},
        {"tool": "query_metrics", "status": "ok", "latency_ms": 50, "end_s": 5.6},
        {"tool": "get_change_history", "status": "ok", "latency_ms": 30, "end_s": 12.2},
    ]
    steps = split_into_steps(llm_calls, tool_calls)
    counts = []
    for step in steps:
        counts.append(len(step["tool_calls"]))
    assert counts == [3, 1, 0]


def test_team_tool_calls_are_counted_per_round():
    llm_calls = [
        {"agent": "orchestrator", "attempt": 1, "end_s": 2.0},
        {"agent": "observability", "attempt": 1, "end_s": 6.0},
        {"agent": "orchestrator", "attempt": 2, "end_s": 7.0},   # a repair, not a new round
        {"agent": "orchestrator", "attempt": 1, "end_s": 30.0},
    ]
    tool_calls = [
        {"status": "ok", "end_s": 8.0},
        {"status": "timeout", "end_s": 9.0},
        {"status": "empty", "end_s": 31.0},
    ]
    rounds = tool_calls_by_round(llm_calls, tool_calls)
    assert rounds == [
        {"round": 1, "calls": 2, "failed_attempts": 1, "empty": 0},
        {"round": 2, "calls": 1, "failed_attempts": 0, "empty": 1},
    ]


def test_tool_usage_counts_calls_retries_and_incomplete_results():
    calls = [
        {"attempt": 1, "status": "timeout"},
        {"attempt": 2, "status": "ok"},
        {"attempt": 1, "status": "partial"},
        {"attempt": 1, "status": "empty"},
    ]
    assert summarise_tool_calls(calls) == {"calls": 3, "attempts": 4, "failed": 1,
                                           "partial": 1, "empty": 1}


def test_each_simulated_failure_is_described_in_plain_words():
    assert "first try with a timeout" in describe_failure_rule(
        {"mode": "transient", "fail_first_n": 1, "status": "timeout"})
    assert "about 50%" in describe_failure_rule({"mode": "partial", "keep_fraction": 0.5})
    assert "down for the whole investigation" in describe_failure_rule(
        {"mode": "persistent", "status": "error"})


def test_relative_shift_ranks_a_latency_jump_above_a_flat_series():
    flat = [50.0] * 30
    jump = [5.0] * 15 + [500.0] * 15
    assert relative_shift(flat) == 0.0
    assert relative_shift(jump) > 90
    assert relative_shift([1.0, 2.0]) == 0.0


def test_answer_key_waits_until_every_run_has_finished():
    assert all_finished({"multi_agent": {"status": "completed"},
                         "single_agent": {"status": "budget_exhausted"}})
    assert not all_finished({"multi_agent": {"status": "running"}})


def test_blackboard_reader_matches_the_blackboard_schema(tmp_path):
    path = tmp_path / "blackboard.sqlite"
    board = Blackboard.create(path)
    board.close()
    assert read_blackboard(path) == {"tasks": [], "facts": [], "hypotheses": [],
                                     "objections": []}
    assert read_blackboard(tmp_path / "missing.sqlite") is None


def test_a_rerun_uses_the_agent_to_agent_pattern(tmp_path):
    client, launcher = make_rerun_client(tmp_path)
    client.post("/api/incidents/inc_00000001/rerun", json={"mode": "fresh"})
    command = launcher.commands[0]
    assert command[command.index("--pattern") + 1] == "exchange"


def test_conversation_is_served_only_for_a_real_run_id():
    client = TestClient(create_app(FakeStore()))
    good = "11111111-2222-3333-4444-555555555555"
    response = client.get("/api/runs/%s/conversation" % good)
    assert response.status_code == 200
    assert response.json()["history"][0]["text"] == "hello"
    assert client.get("/api/runs/not-a-run/conversation").status_code == 404
    assert client.get("/api/runs/..%2F..%2Fetc/conversation").status_code == 404


def test_reading_a_conversation_copes_with_runs_that_have_none(tmp_path):
    from investigator.web.run_views import read_conversation
    assert read_conversation(tmp_path / "missing.sqlite") == []
    import sqlite3
    old = tmp_path / "old.sqlite"
    sqlite3.connect(str(old)).close()
    assert read_conversation(old) == []


def test_a_conversation_can_be_read_while_the_run_is_still_writing(tmp_path):
    from investigator.web.run_views import read_conversation
    path = tmp_path / "live.sqlite"
    board = Blackboard.create(path)
    board.add_message("critic", "reasoning", "challenge", "why this cause?", [], 1)
    history = read_conversation(path)
    assert history[0]["text"] == "why this cause?"
    board.add_message("reasoning", "critic", "answer", "because of the diff", [], 1)
    assert len(read_conversation(path)) == 2
    board.close()


def test_the_walk_through_shows_the_messages_an_agent_received():
    from investigator.web.exchanges import extract_inbox
    prompt = ("INCIDENT BRIEF\nsomething\n\nMESSAGES FOR YOU\n"
              "From observability (question): what changed?  evidence: L-1, L-2\n"
              "From critic (challenge): this does\nnot follow\n"
              "Reply to anything that was asked of you.\n\nRank the hypotheses.")
    inbox = extract_inbox(prompt)
    assert inbox[0] == {"sender": "observability", "intent": "question", "text": "what changed?",
                        "evidence_ids": ["L-1", "L-2"]}
    assert inbox[1]["sender"] == "critic"
    assert inbox[1]["text"] == "this does\nnot follow"
    assert inbox[1]["evidence_ids"] == []


def test_a_prompt_with_no_messages_has_an_empty_inbox():
    from investigator.web.exchanges import extract_inbox
    assert extract_inbox("INCIDENT BRIEF\nnothing here") == []


def test_every_section_of_the_page_can_find_its_script():
    page = (Path(__file__).resolve().parents[2] / "src/investigator/web/pages/incident.html").read_text(encoding="utf-8")
    for name in ["format.js", "routing.js", "walkthrough.js", "conversation.js"]:
        assert "/static/incident/" + name in page
    assert page.index("format.js") < page.index("routing.js") < page.index("conversation.js")
    assert 'id="flow-multi"' in page and 'id="flow-single"' in page


def test_key_evidence_counts_what_the_report_cites_like_the_scorer():
    from investigator.web.evidence import key_evidence_of_run
    decisive = [{"evidence_id": "C-1", "text": "the change"}, {"evidence_id": "L-1", "text": "the log"},
                {"evidence_id": "M-1", "text": "the metric"}, {"evidence_id": "D-1", "text": "the edge"}]
    report = {"facts": [{"evidence_ids": ["C-1"]}],
              "hypotheses": [{"supporting_evidence_ids": ["L-1"], "contradicting_evidence_ids": ["M-1"]}]}
    marked = key_evidence_of_run(report, decisive)
    found = []
    for item in marked:
        found.append(item["found"])
    assert found == [True, True, True, False]
    assert marked[0]["text"] == "the change"


def test_key_evidence_copes_with_a_run_with_no_report():
    from investigator.web.evidence import key_evidence_of_run
    assert key_evidence_of_run(None, [{"evidence_id": "C-1", "text": "x"}])[0]["found"] is False


def test_the_case_page_has_no_tools_or_details_sections():
    page = (Path(__file__).resolve().parents[2] / "src/investigator/web/pages/incident.html").read_text(encoding="utf-8")
    assert "How the tools were used" not in page
    assert "More detail" not in page
    assert "tools.js" not in page and "detail.js" not in page


def test_a_report_can_state_why_its_confidence_is_what_it_is():
    from investigator.agents import prompts
    from investigator.models.report import ReportHypothesis
    hypothesis = ReportHypothesis(rank=1, component="api", category="bad_deploy", mechanism="m", confidence=0.7,
                                  confidence_reason="the diff shows the cause directly")
    assert hypothesis.confidence_reason == "the diff shows the cause directly"
    assert ReportHypothesis(rank=1, component="api", category="bad_deploy", mechanism="m", confidence=0.7).confidence_reason == ""
    for agent in ["reporting", "investigator"]:
        assert "confidence_reason" in prompts.SYSTEM_PROMPTS[agent]


def test_the_routing_diagram_is_clickable():
    script = (Path(__file__).resolve().parents[2] / "src/investigator/web/static/incident/routing.js").read_text(encoding="utf-8")
    assert "selectRouteItem" in script
    assert "conversationEntry(" in script
    assert "lane-on" in script
