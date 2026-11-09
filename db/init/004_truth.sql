-- 004_truth.sql - the hidden answer key. Only scorer_ro (read) and generator_rw (write)
-- can touch this schema. agent_ro has no USAGE on it, so any access is "permission denied".
--
-- An inconclusive incident keeps its REAL root cause here. What makes it inconclusive is
-- that the decisive evidence is unreachable or withheld in the visible data.

CREATE TABLE truth.scenarios (
  incident_id       text PRIMARY KEY
                    REFERENCES evidence.incidents (incident_id) ON DELETE CASCADE,
  plan_id           text NOT NULL UNIQUE,        -- human-readable id such as PG-1
  archetype         text NOT NULL,
  seed              bigint NOT NULL,
  generator_version text NOT NULL,
  expected_outcome  text NOT NULL CHECK (expected_outcome IN ('identifiable', 'inconclusive')),
  solvable_after_noise boolean NOT NULL,
  unidentifiable_reason text,
  root_component    text NOT NULL,
  root_category     evidence.root_cause_category NOT NULL,
  mechanism         text NOT NULL,
  contributing_factors jsonb NOT NULL DEFAULT '[]'::jsonb
                    CHECK (jsonb_typeof(contributing_factors) = 'array'),
  CHECK ((expected_outcome = 'identifiable') = solvable_after_noise),
  CHECK ((expected_outcome = 'inconclusive') = (unidentifiable_reason IS NOT NULL))
);

-- Evidence that settles the question. 'unreachable' means it is loaded, but a persistent
-- failure rule blocks every attempt to fetch it.
CREATE TABLE truth.decisive_evidence (
  incident_id  text NOT NULL REFERENCES truth.scenarios (incident_id) ON DELETE CASCADE,
  evidence_id  text NOT NULL,
  availability text NOT NULL CHECK (availability IN ('retrievable', 'unreachable')),
  PRIMARY KEY (incident_id, evidence_id),
  FOREIGN KEY (incident_id, evidence_id)
    REFERENCES evidence.evidence_index (incident_id, evidence_id) ON DELETE CASCADE
);

-- Evidence planted to mislead.
CREATE TABLE truth.decoys (
  incident_id text NOT NULL REFERENCES truth.scenarios (incident_id) ON DELETE CASCADE,
  evidence_id text NOT NULL,
  decoy_type  text NOT NULL
              CHECK (decoy_type IN ('red_herring_change', 'lookalike_history',
                                    'downstream_symptom', 'unrelated_spike',
                                    'averaged_metric')),
  PRIMARY KEY (incident_id, evidence_id),
  FOREIGN KEY (incident_id, evidence_id)
    REFERENCES evidence.evidence_index (incident_id, evidence_id) ON DELETE CASCADE
);

-- Decisive evidence that was never loaded at all, so there is no row to reference.
CREATE TABLE truth.withheld_evidence (
  withheld_id bigserial PRIMARY KEY,
  incident_id text NOT NULL REFERENCES truth.scenarios (incident_id) ON DELETE CASCADE,
  kind        text NOT NULL
              CHECK (kind IN ('log', 'metric', 'change', 'past_incident', 'dependency')),
  description text NOT NULL,
  would_have_shown jsonb
);

-- Plausible wrong answers that the visible evidence supports.
CREATE TABLE truth.alternative_explanations (
  incident_id   text NOT NULL REFERENCES truth.scenarios (incident_id) ON DELETE CASCADE,
  category      evidence.root_cause_category NOT NULL,
  component     text NOT NULL,
  why_plausible text NOT NULL,
  PRIMARY KEY (incident_id, category, component)
);

CREATE TABLE truth.noise_applied (
  noise_id    bigserial PRIMARY KEY,
  incident_id text NOT NULL REFERENCES truth.scenarios (incident_id) ON DELETE CASCADE,
  noise_type  text NOT NULL,
  params      jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- Cross-table rules that a single CHECK cannot express. The generator's validator and the
-- test suite both require this view to be empty.
CREATE VIEW truth.invariant_violations AS
  SELECT s.incident_id,
         'identifiable_without_retrievable_decisive_evidence'::text AS violation
    FROM truth.scenarios s
   WHERE s.expected_outcome = 'identifiable'
     AND NOT EXISTS (SELECT 1 FROM truth.decisive_evidence d
                      WHERE d.incident_id = s.incident_id
                        AND d.availability = 'retrievable')
  UNION ALL
  SELECT s.incident_id, 'identifiable_with_unreachable_decisive_evidence'
    FROM truth.scenarios s
   WHERE s.expected_outcome = 'identifiable'
     AND EXISTS (SELECT 1 FROM truth.decisive_evidence d
                  WHERE d.incident_id = s.incident_id
                    AND d.availability = 'unreachable')
  UNION ALL
  SELECT s.incident_id, 'identifiable_with_withheld_evidence'
    FROM truth.scenarios s
   WHERE s.expected_outcome = 'identifiable'
     AND EXISTS (SELECT 1 FROM truth.withheld_evidence w
                  WHERE w.incident_id = s.incident_id)
  UNION ALL
  SELECT s.incident_id, 'inconclusive_with_retrievable_decisive_evidence'
    FROM truth.scenarios s
   WHERE s.expected_outcome = 'inconclusive'
     AND EXISTS (SELECT 1 FROM truth.decisive_evidence d
                  WHERE d.incident_id = s.incident_id
                    AND d.availability = 'retrievable')
  UNION ALL
  SELECT s.incident_id, 'inconclusive_without_unreachable_or_withheld_evidence'
    FROM truth.scenarios s
   WHERE s.expected_outcome = 'inconclusive'
     AND NOT EXISTS (SELECT 1 FROM truth.decisive_evidence d
                      WHERE d.incident_id = s.incident_id
                        AND d.availability = 'unreachable')
     AND NOT EXISTS (SELECT 1 FROM truth.withheld_evidence w
                      WHERE w.incident_id = s.incident_id)
  UNION ALL
  SELECT s.incident_id, 'inconclusive_without_alternative_explanation'
    FROM truth.scenarios s
   WHERE s.expected_outcome = 'inconclusive'
     AND NOT EXISTS (SELECT 1 FROM truth.alternative_explanations a
                      WHERE a.incident_id = s.incident_id)
  UNION ALL
  SELECT d.incident_id, 'evidence_is_both_decisive_and_decoy'
    FROM truth.decisive_evidence d
    JOIN truth.decoys y
      ON y.incident_id = d.incident_id AND y.evidence_id = d.evidence_id;
