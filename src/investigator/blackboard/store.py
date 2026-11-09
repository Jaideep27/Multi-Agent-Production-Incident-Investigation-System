"""Blackboard: one SQLite file per run, shared by every agent of that run.

Concurrency: each worker opens its own Blackboard (its own connection). The file uses WAL so
readers do not block the writer, and every write runs in BEGIN IMMEDIATE, which takes the
write lock up front: a read-check-write sequence (claim a task, check grounding, mint an ID)
cannot interleave with another writer. A worker that finds the lock taken waits up to
BUSY_TIMEOUT_S.

Write-time rules (the models only check shape):
- grounding: every evidence ID a fact or assumption cites must have been returned to this run
  (record_evidence is fed from the gateway's results);
- references: hypotheses cite existing facts, objections existing hypotheses, tasks existing
  tasks;
- tasks: a claim succeeds once; only the claimer may finish; finished is final.
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from investigator.blackboard.schema import SCHEMA
from investigator.models.blackboard import Fact, Hypothesis, Objection, Task

BUSY_TIMEOUT_S = 30.0
ID_PREFIXES = {"task": "T", "fact": "F", "hypothesis": "H", "objection": "O"}
FINISHED = ["done", "failed"]
DIRECT_INTENTS = ["finding", "question", "challenge", "answer"]


class BlackboardError(Exception):
    pass


class GroundingError(BlackboardError):
    """A fact cites evidence this run never received."""


class UnknownReferenceError(BlackboardError):
    pass


class DuplicateIdError(BlackboardError):
    pass


class TaskStateError(BlackboardError):
    pass


def connect(path):
    connection = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_S, isolation_level=None)
    connection.execute("PRAGMA busy_timeout = %d" % int(BUSY_TIMEOUT_S * 1000))
    return connection


class Blackboard:
    def __init__(self, connection):
        self.connection = connection

    # ------------------------------------------------------------------ open / close

    @classmethod
    def create(cls, path):
        path = Path(path)
        if path.exists():
            raise FileExistsError("blackboard already exists: %s" % path)
        connection = connect(path)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)
        return cls(connection)

    @classmethod
    def open(cls, path):
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError("no blackboard at %s" % path)
        return cls(connect(path))

    def close(self):
        self.connection.close()

    @contextmanager
    def write(self):
        """One write transaction holding the write lock from the start."""
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
        self.connection.execute("COMMIT")

    def query(self, sql, params=()):
        return self.connection.execute(sql, params).fetchall()

    # ------------------------------------------------------------------ ids and evidence

    def new_id(self, kind):
        if kind not in ID_PREFIXES:
            raise ValueError("unknown id kind: %s" % kind)
        with self.write() as connection:
            connection.execute("INSERT OR IGNORE INTO counters (name, value) VALUES (?, 0)",
                               (kind,))
            connection.execute("UPDATE counters SET value = value + 1 WHERE name = ?", (kind,))
            value = connection.execute("SELECT value FROM counters WHERE name = ?",
                                       (kind,)).fetchone()[0]
        return "%s-%04d" % (ID_PREFIXES[kind], value)

    def record_evidence(self, evidence_ids):
        with self.write() as connection:
            for evidence_id in evidence_ids:
                connection.execute(
                    "INSERT OR IGNORE INTO evidence_seen (evidence_id) VALUES (?)", (evidence_id,)
                )

    def seen_evidence(self):
        ids = []
        for row in self.query("SELECT evidence_id FROM evidence_seen ORDER BY seq"):
            ids.append(row[0])
        return ids

    # ------------------------------------------------------------------ facts

    def add_fact(self, fact):
        with self.write() as connection:
            missing = []
            for evidence_id in fact.evidence_ids:
                row = connection.execute("SELECT 1 FROM evidence_seen WHERE evidence_id = ?",
                                         (evidence_id,)).fetchone()
                if row is None:
                    missing.append(evidence_id)
            if missing:
                raise GroundingError("%s cites evidence this run never received: %s"
                                     % (fact.fact_id, ", ".join(missing)))
            try:
                connection.execute(
                    "INSERT INTO facts (fact_id, kind, statement, evidence_ids, author, round) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (fact.fact_id, fact.kind, fact.statement, json.dumps(fact.evidence_ids),
                     fact.author, fact.round),
                )
            except sqlite3.IntegrityError:
                raise DuplicateIdError("fact %s already exists" % fact.fact_id)

    def facts(self):
        found = []
        rows = self.query("SELECT fact_id, kind, statement, evidence_ids, author, round "
                          "FROM facts ORDER BY seq")
        for fact_id, kind, statement, evidence_ids, author, round_number in rows:
            found.append(Fact(fact_id=fact_id, kind=kind, statement=statement,
                              evidence_ids=json.loads(evidence_ids), author=author,
                              round=round_number))
        return found

    # ------------------------------------------------------------------ hypotheses

    def save_hypothesis(self, hypothesis):
        """Insert a new hypothesis or replace the current version; every version is kept."""
        body = hypothesis.model_dump_json()
        with self.write() as connection:
            cited = list(hypothesis.supporting_fact_ids) + list(hypothesis.contradicting_fact_ids)
            missing = []
            for fact_id in cited:
                row = connection.execute("SELECT 1 FROM facts WHERE fact_id = ?",
                                         (fact_id,)).fetchone()
                if row is None:
                    missing.append(fact_id)
            if missing:
                raise UnknownReferenceError("%s cites unknown facts: %s"
                                            % (hypothesis.hypothesis_id, ", ".join(missing)))

            existing = connection.execute("SELECT 1 FROM hypotheses WHERE hypothesis_id = ?",
                                          (hypothesis.hypothesis_id,)).fetchone()
            if existing is None:
                connection.execute("INSERT INTO hypotheses (hypothesis_id, body) VALUES (?, ?)",
                                   (hypothesis.hypothesis_id, body))
            else:
                connection.execute("UPDATE hypotheses SET body = ? WHERE hypothesis_id = ?",
                                   (body, hypothesis.hypothesis_id))
            connection.execute(
                "INSERT INTO hypothesis_revisions (hypothesis_id, round, body) VALUES (?, ?, ?)",
                (hypothesis.hypothesis_id, hypothesis.round, body),
            )

    def hypotheses(self):
        found = []
        for row in self.query("SELECT body FROM hypotheses ORDER BY seq"):
            found.append(Hypothesis.model_validate_json(row[0]))
        return found

    def hypothesis_history(self, hypothesis_id):
        found = []
        rows = self.query("SELECT body FROM hypothesis_revisions WHERE hypothesis_id = ? "
                          "ORDER BY seq", (hypothesis_id,))
        for row in rows:
            found.append(Hypothesis.model_validate_json(row[0]))
        return found

    # ------------------------------------------------------------------ objections

    def add_objection(self, objection):
        with self.write() as connection:
            if objection.hypothesis_id is not None:
                row = connection.execute("SELECT 1 FROM hypotheses WHERE hypothesis_id = ?",
                                         (objection.hypothesis_id,)).fetchone()
                if row is None:
                    raise UnknownReferenceError("objection %s is about unknown hypothesis %s"
                                                % (objection.objection_id, objection.hypothesis_id))
            try:
                connection.execute(
                    "INSERT INTO objections (objection_id, body, resolved) VALUES (?, ?, ?)",
                    (objection.objection_id, objection.model_dump_json(), int(objection.resolved)),
                )
            except sqlite3.IntegrityError:
                raise DuplicateIdError("objection %s already exists" % objection.objection_id)

    def resolve_objection(self, objection_id):
        with self.write() as connection:
            cursor = connection.execute(
                "UPDATE objections SET resolved = 1 WHERE objection_id = ?", (objection_id,)
            )
            if cursor.rowcount != 1:
                raise UnknownReferenceError("no objection %s" % objection_id)

    def objections(self, open_only=False):
        sql = "SELECT body, resolved FROM objections"
        if open_only:
            sql = sql + " WHERE resolved = 0"
        sql = sql + " ORDER BY seq"
        found = []
        for body, resolved in self.query(sql):
            objection = Objection.model_validate_json(body)
            objection.resolved = bool(resolved)
            found.append(objection)
        return found

    # ------------------------------------------------------------------ messages

    def add_message(self, sender, recipient, intent, text, evidence_ids, round_number):
        with self.write() as connection:
            connection.execute(
                "INSERT INTO messages (sender, recipient, intent, text, evidence_ids, round) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sender, recipient, intent, text, json.dumps(evidence_ids), round_number),
            )

    def history(self):
        """The whole conversation in order: direct messages and the work around them."""
        found = []
        rows = self.query("SELECT sender, recipient, intent, text, evidence_ids, round "
                          "FROM messages ORDER BY seq")
        for sender, recipient, intent, text, evidence_ids, round_number in rows:
            found.append({"sender": sender, "recipient": recipient, "intent": intent, "text": text,
                          "evidence_ids": json.loads(evidence_ids), "round": round_number})
        return found

    def messages(self):
        """Only what one agent said directly to another."""
        found = []
        for entry in self.history():
            if entry["intent"] in DIRECT_INTENTS:
                found.append(entry)
        return found

    # ------------------------------------------------------------------ tasks

    def add_task(self, task):
        with self.write() as connection:
            for dependency in task.depends_on:
                row = connection.execute("SELECT 1 FROM tasks WHERE task_id = ?",
                                         (dependency,)).fetchone()
                if row is None:
                    raise UnknownReferenceError("task %s depends on unknown task %s"
                                                % (task.task_id, dependency))
            try:
                connection.execute(
                    "INSERT INTO tasks (task_id, agent, goal, depends_on, status, round) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (task.task_id, task.agent, task.goal, json.dumps(task.depends_on),
                     task.status, task.round),
                )
            except sqlite3.IntegrityError:
                raise DuplicateIdError("task %s already exists" % task.task_id)

    def claim_task(self, task_id, worker):
        """True for exactly one caller: the one whose UPDATE changed the row."""
        with self.write() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET status = 'running', claimed_by = ? "
                "WHERE task_id = ? AND status = 'pending'",
                (worker, task_id),
            )
            return cursor.rowcount == 1

    def claim_next(self, agent, worker):
        """Claim the oldest pending task for this agent whose dependencies have all finished
        (done or failed). Returns the Task, or None when nothing is ready."""
        with self.write() as connection:
            candidates = connection.execute(
                "SELECT task_id, depends_on FROM tasks WHERE agent = ? AND status = 'pending' "
                "ORDER BY seq",
                (agent,),
            ).fetchall()
            for task_id, depends_on in candidates:
                ready = True
                for dependency in json.loads(depends_on):
                    status = connection.execute("SELECT status FROM tasks WHERE task_id = ?",
                                                (dependency,)).fetchone()[0]
                    if status not in FINISHED:
                        ready = False
                        break
                if ready:
                    connection.execute(
                        "UPDATE tasks SET status = 'running', claimed_by = ? WHERE task_id = ?",
                        (worker, task_id),
                    )
                    return self.read_task(connection, task_id)
        return None

    def finish_task(self, task_id, worker, status):
        if status not in FINISHED:
            raise ValueError("a task finishes as done or failed, not %s" % status)
        with self.write() as connection:
            row = connection.execute("SELECT status, claimed_by FROM tasks WHERE task_id = ?",
                                     (task_id,)).fetchone()
            if row is None:
                raise UnknownReferenceError("no task %s" % task_id)
            current, claimed_by = row
            if current != "running" or claimed_by != worker:
                raise TaskStateError("task %s is %s (claimed by %s); %s cannot finish it"
                                     % (task_id, current, claimed_by, worker))
            connection.execute("UPDATE tasks SET status = ? WHERE task_id = ?", (status, task_id))

    def read_task(self, connection, task_id):
        row = connection.execute(
            "SELECT task_id, agent, goal, depends_on, status, round FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return Task(task_id=row[0], agent=row[1], goal=row[2], depends_on=json.loads(row[3]),
                    status=row[4], round=row[5])

    def tasks(self, status=None):
        sql = "SELECT task_id FROM tasks"
        params = ()
        if status is not None:
            sql = sql + " WHERE status = ?"
            params = (status,)
        sql = sql + " ORDER BY seq"
        found = []
        for row in self.query(sql, params):
            found.append(self.read_task(self.connection, row[0]))
        return found
