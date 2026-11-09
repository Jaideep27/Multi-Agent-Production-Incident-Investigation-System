"""Step 5 gate: the blackboard under concurrent writers and competing claimers.

Each worker is a thread with its own connection to the same SQLite file, which is how agents
running in parallel share one run's blackboard.
"""

import threading

from investigator.blackboard.store import Blackboard
from investigator.models.blackboard import Fact, Task

WORKERS = 8
WRITES_PER_WORKER = 200
EVIDENCE = ["L-0000000a-1", "M-0000000a-1"]


def run_threads(target, count):
    errors = []
    threads = []

    def guarded(number):
        try:
            target(number)
        except Exception as error:     # surface thread errors in the test thread
            errors.append(repr(error))

    for number in range(count):
        threads.append(threading.Thread(target=guarded, args=(number,)))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    return errors


def test_eight_workers_write_two_hundred_facts_each(tmp_path):
    path = tmp_path / "run.sqlite"
    setup = Blackboard.create(path)
    setup.record_evidence(EVIDENCE)
    setup.close()

    start = threading.Barrier(WORKERS)

    def writer(number):
        board = Blackboard.open(path)
        try:
            start.wait()
            for write in range(WRITES_PER_WORKER):
                fact_id = board.new_id("fact")
                board.add_fact(Fact(fact_id=fact_id, kind="fact",
                                    statement="worker %d write %d" % (number, write),
                                    evidence_ids=[EVIDENCE[write % 2]],
                                    author="observability", round=1))
        finally:
            board.close()

    errors = run_threads(writer, WORKERS)
    assert errors == []

    check = Blackboard.open(path)
    facts = check.facts()
    check.close()
    assert len(facts) == WORKERS * WRITES_PER_WORKER
    ids = []
    statements = []
    for item in facts:
        ids.append(item.fact_id)
        statements.append(item.statement)
    assert len(set(ids)) == 1600
    assert len(set(statements)) == 1600


def test_two_workers_claiming_one_task_yield_one_winner(tmp_path):
    path = tmp_path / "run.sqlite"
    setup = Blackboard.create(path)
    rounds = 50
    for number in range(1, rounds + 1):
        setup.add_task(Task(task_id="T-%04d" % number, agent="observability", goal="look",
                            round=1))
    setup.close()

    for number in range(1, rounds + 1):
        task_id = "T-%04d" % number
        start = threading.Barrier(2)
        wins = []

        def claimer(worker):
            board = Blackboard.open(path)
            try:
                start.wait()
                if board.claim_task(task_id, "worker-%d" % worker):
                    wins.append(worker)
            finally:
                board.close()

        errors = run_threads(claimer, 2)
        assert errors == []
        assert len(wins) == 1, task_id


def test_eight_workers_drain_a_queue_without_double_claims(tmp_path):
    path = tmp_path / "run.sqlite"
    setup = Blackboard.create(path)
    total = 120
    for number in range(1, total + 1):
        setup.add_task(Task(task_id="T-%04d" % number, agent="observability", goal="look",
                            round=1))
    setup.close()

    claimed = []
    lock = threading.Lock()
    start = threading.Barrier(WORKERS)

    def drainer(worker):
        board = Blackboard.open(path)
        try:
            start.wait()
            while True:
                item = board.claim_next("observability", "worker-%d" % worker)
                if item is None:
                    break
                with lock:
                    claimed.append(item.task_id)
                board.finish_task(item.task_id, "worker-%d" % worker, "done")
        finally:
            board.close()

    errors = run_threads(drainer, WORKERS)
    assert errors == []
    assert len(claimed) == total
    assert len(set(claimed)) == total

    check = Blackboard.open(path)
    statuses = []
    for item in check.tasks():
        statuses.append(item.status)
    check.close()
    assert statuses == ["done"] * total
