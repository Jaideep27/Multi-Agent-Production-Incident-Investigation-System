"""Blackboard rules: grounding at write time, references, IDs, task lifecycle, persistence."""

import pytest

from investigator.blackboard.store import (
    Blackboard,
    DuplicateIdError,
    GroundingError,
    TaskStateError,
    UnknownReferenceError,
)
from investigator.models.blackboard import Fact, Hypothesis, Objection, Task
from investigator.models.tools import ToolRequest

SEEN = ["L-0000000a-1", "L-0000000a-2", "C-0000000a-1"]


@pytest.fixture
def board(tmp_path):
    created = Blackboard.create(tmp_path / "run.sqlite")
    created.record_evidence(SEEN)
    yield created
    created.close()


def fact(fact_id="F-0001", evidence_ids=None, kind="fact"):
    if evidence_ids is None:
        evidence_ids = ["L-0000000a-1"]
    return Fact(fact_id=fact_id, kind=kind, statement="pool timeouts on payment-router",
                evidence_ids=evidence_ids, author="observability", round=1)


def hypothesis(hypothesis_id="H-0001", supporting=None, confidence=0.6, round_number=1):
    if supporting is None:
        supporting = ["F-0001"]
    return Hypothesis(hypothesis_id=hypothesis_id, component="payment-router",
                      category="config_change", mechanism="pool shrunk", confidence=confidence,
                      supporting_fact_ids=supporting, author="reasoning", round=round_number)


def task(task_id="T-0001", agent="observability", depends_on=None):
    return Task(task_id=task_id, agent=agent, goal="look at errors",
                depends_on=depends_on or [], round=1)


# ------------------------------------------------------------------ grounding


def test_fact_citing_returned_evidence_is_stored(board):
    board.add_fact(fact())
    stored = board.facts()
    assert len(stored) == 1
    assert stored[0] == fact()


def test_fact_citing_evidence_never_returned_is_refused(board):
    with pytest.raises(GroundingError, match="L-0000000a-9"):
        board.add_fact(fact(evidence_ids=["L-0000000a-1", "L-0000000a-9"]))
    assert board.facts() == []


def test_assumption_may_cite_nothing(board):
    board.add_fact(fact(kind="assumption", evidence_ids=[]))
    assert board.facts()[0].kind == "assumption"


def test_assumption_citing_unseen_evidence_is_still_refused(board):
    with pytest.raises(GroundingError):
        board.add_fact(fact(kind="assumption", evidence_ids=["M-0000000a-5"]))


def test_recording_evidence_twice_keeps_one_copy_in_first_seen_order(board):
    board.record_evidence(["M-0000000a-1", "L-0000000a-1"])
    assert board.seen_evidence() == SEEN + ["M-0000000a-1"]


def test_newly_recorded_evidence_can_be_cited(board):
    with pytest.raises(GroundingError):
        board.add_fact(fact(evidence_ids=["M-0000000a-7"]))
    board.record_evidence(["M-0000000a-7"])
    board.add_fact(fact(evidence_ids=["M-0000000a-7"]))


# ------------------------------------------------------------------ references and ids


def test_duplicate_fact_id_is_refused(board):
    board.add_fact(fact())
    with pytest.raises(DuplicateIdError):
        board.add_fact(fact())


def test_hypothesis_must_cite_existing_facts(board):
    with pytest.raises(UnknownReferenceError, match="F-0001"):
        board.save_hypothesis(hypothesis())
    assert board.hypotheses() == []


def test_hypothesis_contradicting_facts_must_exist_too(board):
    board.add_fact(fact())
    bad = hypothesis().model_copy(update={"contradicting_fact_ids": ["F-0404"]})
    with pytest.raises(UnknownReferenceError, match="F-0404"):
        board.save_hypothesis(bad)


def test_saving_a_hypothesis_again_revises_it_and_keeps_history(board):
    board.add_fact(fact())
    board.save_hypothesis(hypothesis(confidence=0.6, round_number=1))
    board.save_hypothesis(hypothesis(confidence=0.85, round_number=2))
    current = board.hypotheses()
    assert len(current) == 1
    assert current[0].confidence == 0.85
    history = board.hypothesis_history("H-0001")
    confidences = []
    for revision in history:
        confidences.append([revision.round, revision.confidence])
    assert confidences == [[1, 0.6], [2, 0.85]]


def test_objection_must_point_at_an_existing_hypothesis(board):
    objection = Objection(objection_id="O-0001", hypothesis_id="H-0404", reason="no diff seen",
                          blocking=True, round=1)
    with pytest.raises(UnknownReferenceError):
        board.add_objection(objection)


def test_objection_round_trip_keeps_requested_tools(board):
    board.add_fact(fact())
    board.save_hypothesis(hypothesis())
    objection = Objection(
        objection_id="O-0001", hypothesis_id="H-0001", reason="check the diff", blocking=True,
        requested_tools=[ToolRequest(tool="get_change_history", args={"include_diff": True})],
        round=1,
    )
    board.add_objection(objection)
    assert board.objections() == [objection]
    assert board.objections(open_only=True) == [objection]
    board.resolve_objection("O-0001")
    assert board.objections(open_only=True) == []
    assert board.objections()[0].resolved is True


def test_resolving_an_unknown_objection_fails(board):
    with pytest.raises(UnknownReferenceError):
        board.resolve_objection("O-0404")


def test_new_ids_count_up_per_kind(board):
    assert board.new_id("fact") == "F-0001"
    assert board.new_id("fact") == "F-0002"
    assert board.new_id("hypothesis") == "H-0001"
    assert board.new_id("objection") == "O-0001"
    assert board.new_id("task") == "T-0001"
    with pytest.raises(ValueError):
        board.new_id("rumour")


# ------------------------------------------------------------------ tasks


def test_task_dependencies_must_exist(board):
    with pytest.raises(UnknownReferenceError):
        board.add_task(task("T-0002", depends_on=["T-0001"]))


def test_claim_succeeds_once(board):
    board.add_task(task())
    assert board.claim_task("T-0001", "worker-a") is True
    assert board.claim_task("T-0001", "worker-b") is False
    assert board.tasks()[0].status == "running"


def test_only_the_claimer_can_finish_a_task(board):
    board.add_task(task())
    board.claim_task("T-0001", "worker-a")
    with pytest.raises(TaskStateError):
        board.finish_task("T-0001", "worker-b", "done")
    board.finish_task("T-0001", "worker-a", "done")
    assert board.tasks()[0].status == "done"


def test_a_finished_task_cannot_be_finished_again(board):
    board.add_task(task())
    board.claim_task("T-0001", "worker-a")
    board.finish_task("T-0001", "worker-a", "failed")
    with pytest.raises(TaskStateError):
        board.finish_task("T-0001", "worker-a", "done")


def test_finish_status_must_be_done_or_failed(board):
    board.add_task(task())
    board.claim_task("T-0001", "worker-a")
    with pytest.raises(ValueError):
        board.finish_task("T-0001", "worker-a", "pending")


def test_claim_next_waits_for_dependencies(board):
    board.add_task(task("T-0001", agent="observability"))
    board.add_task(task("T-0002", agent="reasoning", depends_on=["T-0001"]))
    assert board.claim_next("reasoning", "worker-r") is None
    claimed = board.claim_next("observability", "worker-o")
    assert claimed.task_id == "T-0001"
    board.finish_task("T-0001", "worker-o", "failed")
    # A failed dependency still unblocks: the agent is told the evidence is missing.
    assert board.claim_next("reasoning", "worker-r").task_id == "T-0002"


def test_claim_next_takes_the_oldest_pending_task_for_that_agent(board):
    board.add_task(task("T-0001", agent="dependency"))
    board.add_task(task("T-0002", agent="observability"))
    board.add_task(task("T-0003", agent="observability"))
    assert board.claim_next("observability", "w").task_id == "T-0002"
    assert board.claim_next("observability", "w").task_id == "T-0003"
    assert board.claim_next("observability", "w") is None


def test_tasks_can_be_filtered_by_status(board):
    board.add_task(task("T-0001"))
    board.add_task(task("T-0002"))
    board.claim_task("T-0001", "w")
    pending = []
    for item in board.tasks(status="pending"):
        pending.append(item.task_id)
    assert pending == ["T-0002"]


# ------------------------------------------------------------------ persistence


def test_contents_survive_reopening(tmp_path):
    path = tmp_path / "run.sqlite"
    first = Blackboard.create(path)
    first.record_evidence(SEEN)
    first.add_fact(fact())
    first.close()

    second = Blackboard.open(path)
    assert second.facts() == [fact()]
    assert second.seen_evidence() == SEEN
    second.close()


def test_create_refuses_to_reuse_an_existing_file(tmp_path):
    path = tmp_path / "run.sqlite"
    Blackboard.create(path).close()
    with pytest.raises(FileExistsError):
        Blackboard.create(path)


def test_open_refuses_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        Blackboard.open(tmp_path / "missing.sqlite")
