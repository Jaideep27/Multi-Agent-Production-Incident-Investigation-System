-- 003_evidence.sql - everything an investigation is allowed to see.
--
-- Conventions
--   * Every table has an incident_id column (row-level security in 006 depends on it).
--   * Every evidence item has an evidence_id that is unique across the database:
--       L-... log line, M-... metric series, C-... change record, P-... past incident,
--       D-... service dependency edge (so topology and config facts can be cited too).
--     The generator embeds the incident hash in the id, e.g. L-7f3a1c2d-000412.
--   * evidence_index lists every evidence item once. Child tables and the truth schema
--     reference it with composite keys, so a row can never point at another incident.
--   * JSONB is used only for free-form attributes.

CREATE DOMAIN evidence.root_cause_category AS text
  CHECK (VALUE IN (
    'bad_deploy', 'config_change', 'resource_exhaustion', 'db_contention',
    'cache_failure', 'lb_imbalance', 'dependency_failure', 'rate_limiting',
    'capacity_scaling', 'cert_expiry'
  ));

-- ---------------------------------------------------------------- incidents

CREATE TABLE evidence.incidents (
  incident_id  text PRIMARY KEY
               CHECK (incident_id ~ '^inc_[0-9a-f]{8}$'),
  domain       text NOT NULL
               CHECK (domain IN ('crypto_exchange', 'payment_gateway', 'ott',
                                 'ecommerce', 'food_delivery', 'online_retail',
                                 'search_platform', 'saas_platform')),
  reported_at  timestamptz NOT NULL,
  window_start timestamptz NOT NULL,
  window_end   timestamptz NOT NULL,
  summary      text NOT NULL,
  reporter     text NOT NULL,
  CHECK (window_start < window_end)
);

-- ----------------------------------------------------------------- services

CREATE TABLE evidence.services (
  service_id     bigserial PRIMARY KEY,
  incident_id    text NOT NULL
                 REFERENCES evidence.incidents (incident_id) ON DELETE CASCADE,
  name           text NOT NULL,
  role           text NOT NULL
                 CHECK (role IN ('load_balancer', 'app', 'worker', 'cache', 'database',
                                 'queue', 'external_api', 'cdn')),
  instance_count int NOT NULL DEFAULT 1 CHECK (instance_count > 0),
  UNIQUE (incident_id, name),
  UNIQUE (incident_id, service_id)          -- target of composite foreign keys
);

-- ----------------------------------------------------------- evidence index

CREATE TABLE evidence.evidence_index (
  evidence_id text PRIMARY KEY,
  incident_id text NOT NULL
              REFERENCES evidence.incidents (incident_id) ON DELETE CASCADE,
  kind        text NOT NULL
              CHECK (kind IN ('log', 'metric', 'change', 'past_incident', 'dependency')),
  CHECK (
       (kind = 'log'           AND evidence_id LIKE 'L-%')
    OR (kind = 'metric'        AND evidence_id LIKE 'M-%')
    OR (kind = 'change'        AND evidence_id LIKE 'C-%')
    OR (kind = 'past_incident' AND evidence_id LIKE 'P-%')
    OR (kind = 'dependency'    AND evidence_id LIKE 'D-%')
  ),
  UNIQUE (incident_id, evidence_id),
  UNIQUE (incident_id, evidence_id, kind)
);

-- ------------------------------------------------------ service dependencies

-- Each edge is an evidence item, so a fact such as "payment-router calls postgres with a
-- pool of 20" can cite it and be checked for grounding like any log line.
CREATE TABLE evidence.service_dependencies (
  evidence_id text   NOT NULL UNIQUE,
  incident_id text   NOT NULL,
  kind        text   NOT NULL DEFAULT 'dependency' CHECK (kind = 'dependency'),
  caller_id   bigint NOT NULL,
  callee_id   bigint NOT NULL,
  config      jsonb  NOT NULL DEFAULT '{}'::jsonb,   -- timeout_ms, retries, pool size, ...
  PRIMARY KEY (incident_id, caller_id, callee_id),
  CHECK (caller_id <> callee_id),
  FOREIGN KEY (incident_id, caller_id)
    REFERENCES evidence.services (incident_id, service_id) ON DELETE CASCADE,
  FOREIGN KEY (incident_id, callee_id)
    REFERENCES evidence.services (incident_id, service_id) ON DELETE CASCADE,
  FOREIGN KEY (incident_id, evidence_id, kind)
    REFERENCES evidence.evidence_index (incident_id, evidence_id, kind) ON DELETE CASCADE
);

-- --------------------------------------------------------------------- logs

CREATE TABLE evidence.log_entries (
  evidence_id text PRIMARY KEY,
  incident_id text   NOT NULL,
  kind        text   NOT NULL DEFAULT 'log' CHECK (kind = 'log'),
  service_id  bigint NOT NULL,
  instance    text,
  ts          timestamptz NOT NULL,          -- as recorded; may be clock-skewed
  level       text NOT NULL CHECK (level IN ('DEBUG', 'INFO', 'WARN', 'ERROR', 'FATAL')),
  message     text NOT NULL,
  attrs       jsonb NOT NULL DEFAULT '{}'::jsonb,   -- instance, request id, status code, ...
  msg_tsv     tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, message)) STORED,
  FOREIGN KEY (incident_id, service_id)
    REFERENCES evidence.services (incident_id, service_id) ON DELETE CASCADE,
  FOREIGN KEY (incident_id, evidence_id, kind)
    REFERENCES evidence.evidence_index (incident_id, evidence_id, kind) ON DELETE CASCADE
);

-- keyset pagination and per-service time-range scans
CREATE INDEX log_entries_service_time_idx
  ON evidence.log_entries (incident_id, service_id, ts, evidence_id);
-- "show me the errors" is the common first query
CREATE INDEX log_entries_problem_time_idx
  ON evidence.log_entries (incident_id, ts)
  WHERE level IN ('WARN', 'ERROR', 'FATAL');
CREATE INDEX log_entries_message_idx ON evidence.log_entries USING gin (msg_tsv);
CREATE INDEX log_entries_attrs_idx   ON evidence.log_entries USING gin (attrs jsonb_path_ops);

-- ------------------------------------------------------------------ metrics

CREATE TABLE evidence.metric_series (
  evidence_id text PRIMARY KEY,
  incident_id text   NOT NULL,
  kind        text   NOT NULL DEFAULT 'metric' CHECK (kind = 'metric'),
  service_id  bigint NOT NULL,
  instance    text,                           -- NULL means a fleet-wide aggregate
  metric_name text   NOT NULL,
  unit        text   NOT NULL,
  source      text   NOT NULL CHECK (source IN ('prometheus', 'cloud_monitor', 'apm')),
  interval_s  int    NOT NULL CHECK (interval_s > 0),
  UNIQUE (incident_id, evidence_id),          -- target for metric_points
  UNIQUE NULLS NOT DISTINCT (incident_id, service_id, instance, metric_name, source),
  FOREIGN KEY (incident_id, service_id)
    REFERENCES evidence.services (incident_id, service_id) ON DELETE CASCADE,
  FOREIGN KEY (incident_id, evidence_id, kind)
    REFERENCES evidence.evidence_index (incident_id, evidence_id, kind) ON DELETE CASCADE
);

CREATE TABLE evidence.metric_points (
  incident_id text NOT NULL,                  -- repeated so the row-level policy needs no join
  series_id   text NOT NULL,
  ts          timestamptz NOT NULL,
  value       double precision NOT NULL,
  PRIMARY KEY (series_id, ts),
  FOREIGN KEY (incident_id, series_id)
    REFERENCES evidence.metric_series (incident_id, evidence_id) ON DELETE CASCADE
);

-- ------------------------------------------------------------------ changes

CREATE TABLE evidence.changes (
  evidence_id text PRIMARY KEY,
  incident_id text   NOT NULL,
  kind        text   NOT NULL DEFAULT 'change' CHECK (kind = 'change'),
  service_id  bigint NOT NULL,
  ts          timestamptz NOT NULL,
  change_type text NOT NULL
              CHECK (change_type IN ('deploy', 'config_change', 'scaling', 'infra',
                                     'feature_flag')),
  version     text,
  author      text,
  summary     text NOT NULL,
  UNIQUE (incident_id, evidence_id),          -- target for change_details
  FOREIGN KEY (incident_id, service_id)
    REFERENCES evidence.services (incident_id, service_id) ON DELETE CASCADE,
  FOREIGN KEY (incident_id, evidence_id, kind)
    REFERENCES evidence.evidence_index (incident_id, evidence_id, kind) ON DELETE CASCADE
);

CREATE INDEX changes_time_idx ON evidence.changes (incident_id, ts);

-- The actual diff sits behind a second call, so an agent has to ask for it.
CREATE TABLE evidence.change_details (
  evidence_id text PRIMARY KEY,
  incident_id text  NOT NULL,
  diff        jsonb NOT NULL,
  FOREIGN KEY (incident_id, evidence_id)
    REFERENCES evidence.changes (incident_id, evidence_id) ON DELETE CASCADE
);

-- ------------------------------------------------------------ past incidents

CREATE TABLE evidence.past_incidents (
  evidence_id         text PRIMARY KEY,
  incident_id         text NOT NULL,          -- the incident this history is shown for
  kind                text NOT NULL DEFAULT 'past_incident' CHECK (kind = 'past_incident'),
  occurred_at         timestamptz NOT NULL,
  title               text NOT NULL,
  root_cause_category evidence.root_cause_category NOT NULL,
  root_cause_component text,
  resolution          text,
  symptoms            jsonb NOT NULL CHECK (jsonb_typeof(symptoms) = 'array'),
  FOREIGN KEY (incident_id, evidence_id, kind)
    REFERENCES evidence.evidence_index (incident_id, evidence_id, kind) ON DELETE CASCADE
);

CREATE INDEX past_incidents_symptoms_idx ON evidence.past_incidents USING gin (symptoms);

-- ------------------------------------------------------------ tool behaviour

-- Describes WHEN a tool misbehaves (timeouts, truncation), never WHAT it returns.
CREATE TABLE evidence.tool_behavior (
  incident_id text NOT NULL
              REFERENCES evidence.incidents (incident_id) ON DELETE CASCADE,
  tool_name   text NOT NULL
              CHECK (tool_name IN ('query_logs', 'query_metrics', 'get_change_history',
                                   'get_service_dependencies', 'search_past_incidents')),
  rules       jsonb NOT NULL,
  PRIMARY KEY (incident_id, tool_name)
);
