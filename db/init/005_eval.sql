-- 005_eval.sql - run logs and scores. The harness writes; the scorer reads.
--
-- scores are computed by the scorer (which reads truth with scorer_ro) and written through
-- harness_rw, so the harness never has to read the answer key.

CREATE TABLE eval.runs (
  run_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  -- No ON DELETE CASCADE on purpose: an incident that has results cannot be rebuilt
  -- by accident and silently wipe them.
  incident_id text NOT NULL REFERENCES evidence.incidents (incident_id),
  system      text NOT NULL CHECK (system IN ('multi_agent', 'single_agent')),
  model       text NOT NULL,
  config      jsonb NOT NULL DEFAULT '{}'::jsonb,
  status      text NOT NULL
              CHECK (status IN ('running', 'completed', 'failed', 'budget_exhausted',
                                'max_rounds')),
  started_at  timestamptz NOT NULL DEFAULT now(),
  ended_at    timestamptz,
  rounds      int NOT NULL DEFAULT 0 CHECK (rounds >= 0),
  final_report jsonb,
  stats       jsonb NOT NULL DEFAULT '{}'::jsonb,   -- collaboration counters (step 10)
  CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE INDEX runs_incident_idx ON eval.runs (incident_id, system);

CREATE TABLE eval.llm_calls (
  call_id       bigserial PRIMARY KEY,
  run_id        uuid NOT NULL REFERENCES eval.runs (run_id) ON DELETE CASCADE,
  agent         text NOT NULL,
  attempt       int  NOT NULL DEFAULT 1 CHECK (attempt >= 1),
  status        text NOT NULL CHECK (status IN ('ok', 'invalid_output', 'timeout', 'error')),
  input_tokens  int,
  output_tokens int,
  latency_ms    int,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX llm_calls_run_idx ON eval.llm_calls (run_id);

CREATE TABLE eval.tool_calls (
  call_id      bigserial PRIMARY KEY,
  run_id       uuid NOT NULL REFERENCES eval.runs (run_id) ON DELETE CASCADE,
  tool         text NOT NULL
               CHECK (tool IN ('query_logs', 'query_metrics', 'get_change_history',
                               'get_service_dependencies', 'search_past_incidents')),
  args         jsonb NOT NULL,
  attempt      int  NOT NULL DEFAULT 1 CHECK (attempt >= 1),
  status       text NOT NULL CHECK (status IN ('ok', 'partial', 'timeout', 'error', 'empty')),
  latency_ms   int,
  evidence_ids text[] NOT NULL DEFAULT '{}',
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX tool_calls_run_idx ON eval.tool_calls (run_id);

CREATE TABLE eval.scores (
  run_id              uuid PRIMARY KEY REFERENCES eval.runs (run_id) ON DELETE CASCADE,
  root_cause_correct  boolean NOT NULL,
  outcome_correct     boolean NOT NULL,
  grounding_cited     int NOT NULL CHECK (grounding_cited >= 0),
  grounding_existing  int NOT NULL CHECK (grounding_existing >= 0),
  grounding_supporting int,
  decisive_recall     double precision CHECK (decisive_recall BETWEEN 0 AND 1),
  decoy_hit           boolean NOT NULL,
  -- planted decoy evidence listed as supporting the top hypothesis (added in step 8)
  decoys_cited_as_support int NOT NULL DEFAULT 0 CHECK (decoys_cited_as_support >= 0),
  confidence          double precision CHECK (confidence BETWEEN 0 AND 1),
  tool_call_count     int NOT NULL,
  llm_call_count      int NOT NULL,
  input_tokens        bigint NOT NULL,
  output_tokens       bigint NOT NULL,
  latency_ms          bigint NOT NULL,
  rounds              int NOT NULL,
  scored_at           timestamptz NOT NULL DEFAULT now(),
  CHECK (grounding_existing <= grounding_cited)
);
