"""SQLite schema for one run's blackboard.

Every table has an INTEGER seq so rows read back in the order they were written. Lists and
nested values are stored as JSON text and turned back into Pydantic models on read.
"""

SCHEMA = """
CREATE TABLE counters (
  name  TEXT PRIMARY KEY,
  value INTEGER NOT NULL
);

-- Evidence IDs that tool results returned to this run: the grounding allow-list.
CREATE TABLE evidence_seen (
  seq         INTEGER PRIMARY KEY,
  evidence_id TEXT NOT NULL UNIQUE
);

CREATE TABLE tasks (
  seq        INTEGER PRIMARY KEY,
  task_id    TEXT NOT NULL UNIQUE,
  agent      TEXT NOT NULL,
  goal       TEXT NOT NULL,
  depends_on TEXT NOT NULL,          -- JSON list of task IDs
  status     TEXT NOT NULL CHECK (status IN ('pending', 'running', 'done', 'failed')),
  round      INTEGER NOT NULL,
  claimed_by TEXT
);

CREATE TABLE facts (
  seq          INTEGER PRIMARY KEY,
  fact_id      TEXT NOT NULL UNIQUE,
  kind         TEXT NOT NULL CHECK (kind IN ('fact', 'assumption')),
  statement    TEXT NOT NULL,
  evidence_ids TEXT NOT NULL,        -- JSON list
  author       TEXT NOT NULL,
  round        INTEGER NOT NULL
);

-- Current version of each hypothesis ...
CREATE TABLE hypotheses (
  seq           INTEGER PRIMARY KEY,
  hypothesis_id TEXT NOT NULL UNIQUE,
  body          TEXT NOT NULL          -- the Hypothesis model as JSON
);

-- ... and every version ever saved, for "did the Critic change the ranking" metrics.
CREATE TABLE hypothesis_revisions (
  seq           INTEGER PRIMARY KEY,
  hypothesis_id TEXT NOT NULL,
  round         INTEGER NOT NULL,
  body          TEXT NOT NULL
);

CREATE TABLE objections (
  seq          INTEGER PRIMARY KEY,
  objection_id TEXT NOT NULL UNIQUE,
  body         TEXT NOT NULL,          -- the Objection model as JSON
  resolved     INTEGER NOT NULL DEFAULT 0
);

-- The agent-to-agent pattern's history, in order: direct messages between agents and the
-- work around them (tasks, findings, tool calls, rankings, verdicts).
CREATE TABLE messages (
  seq          INTEGER PRIMARY KEY,
  sender       TEXT NOT NULL,
  recipient    TEXT NOT NULL,
  intent       TEXT NOT NULL,
  text         TEXT NOT NULL,
  evidence_ids TEXT NOT NULL,          -- JSON list
  round        INTEGER NOT NULL
);
"""
