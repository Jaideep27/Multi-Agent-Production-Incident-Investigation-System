# Implementation plan (Phase 1)

Status: design approved, steps 0-9 done, step 10 next. This file is the single source of truth for scope,
decisions and gates. Counts in this file are re-derived from `configs/incident_plan.yaml` by a
test once that file exists; do not edit them by hand.

## 1. Scope

Build a multi-agent system that investigates simulated production incidents, plus a single-agent
baseline that uses the same model, tools and budgets, and an evaluation comparing the two on 25
synthetic incidents (5 domains x 5 incidents).

Out of scope for Phase 1: live monitoring (Prometheus/Grafana), which is Phase 2.

## 2. Architecture

```
 generator (seeded): archetype x domain topology + noise layer
                         | loads (generator_rw)
                         v
   +-------------------- PostgreSQL 16 ---------------------+
   | evidence.*  agent_ro: row-level security, one incident |
   | truth.*     scorer_ro only                             |
   | eval.*      harness_rw writes, scorer_ro reads         |
   +-------^---------------------------------^--------------+
           | SQL as agent_ro                 | read at scoring time
   +-------+--------------------+      +-----+------+
   | MCP server (one per run)   |      | scorer     |
   | 5 tools, Pydantic I/O,     |      +------------+
   | failure injection,         |
   | EvidenceBackend interface  |
   +-------^--------------------+
           | MCP over stdio
   +-------+--------------------+
   | ToolGateway (MCP client)   |  validate, timeout, retry, de-duplicate,
   +-------^--------------------+  budget, log
           | tool_requests / results
   +-------+---------------------------------------------------+
   | LangGraph runner                                          |
   | Orchestrator -> [Observability || Dependency] -> Reasoning|
   |      ^                                              |     |
   |      +---- new tasks <---- Critic <-----------------+     |
   |                              | no blocking objections     |
   |                              v                            |
   |                          Reporting -> FinalReport         |
   | each agent = `claude -p` (built-in tools off, JSON out)   |
   | SQLite blackboard: tasks, facts, hypotheses, objections   |
   +-----------------------------------------------------------+
```

### Decisions that shape everything else

- Agents never call MCP themselves. They return `tool_requests` as JSON and Python executes
  them through the gateway: one place for logging, budgets, failure handling and parallelism,
  and the baseline uses the identical path. `claude -p` runs with its built-in tools disabled.
- The MCP server is bound to one incident at start-up (`INCIDENT_ID` env var). The incident is
  never a tool argument, and the server sets `app.incident_id` per transaction.
- Roles: `generator_rw` loads data, `agent_ro` is used by the MCP server, `scorer_ro` reads the
  answer key, `harness_rw` writes run logs.
- The graph loop is deterministic Python. The LLM plans and reasons; code enforces limits and
  termination.

## 3. Database security model

- Schemas: `evidence` (agent-visible), `truth` (answer key), `eval` (run logs and scores).
- `PUBLIC` has no privileges on any of them.
- Default privileges plus explicit grants after table creation (a test compares the full
  role x table privilege matrix against the catalog).
- Row-level security is enabled on every `evidence` table by a catalog-driven loop, so a table
  cannot be missed. Policies exist for `agent_ro` (bound incident only), `scorer_ro` (all rows)
  and `generator_rw` (all rows). An unbound session sees nothing.
- The binding is transaction-local (`set_config(..., true)`) so pooled connections cannot leak it.
- Evidence kinds and ID prefixes: log `L-`, metric `M-`, change `C-`, past incident `P-`,
  service dependency edge `D-` (decided 2026-11-07, after step 1: dependency edges are
  evidence so topology and config facts can be cited and scored for grounding).
- Composite foreign keys keep every child row inside its own incident. `truth` rows reference
  `evidence.evidence_index`, so an answer key can never point at evidence that does not exist.
- Threat model: RLS is defence in depth against tool bugs (a forgotten WHERE). The hard
  boundary is that the MCP server exposes no raw SQL. An `agent_ro` session that could run
  arbitrary SQL could rebind itself; the upgrade path is a per-run login role.

## 4. Dataset plan (25 incidents)

Built from ~10 archetype generators x 5 domain topologies. Plan IDs below are for humans; the
database uses opaque `inc_<hash>` IDs, and the mapping lives only in `truth.scenarios`.

Root cause is shown as `component -> category`.

| ID | Everyday symptom | Root cause | Decoy / noise | Tool failure | Outcome |
|---|---|---|---|---|---|
| CX-1 | Order placement slows during a BTC rally | order-api -> capacity_scaling | price-feed deploy 10 min earlier; matching-engine clock skew | none | identifiable |
| CX-2 | Withdrawals stuck pending | wallet-service -> resource_exhaustion (memory limit, OOM) | noisy blockchain-node warnings | metrics timeout once | identifiable |
| CX-3 | Order book stale, ledger DB load jumps | redis-orderbook -> cache_failure (restart, stampede) | ledger slow queries (symptom); log gap | none | identifiable |
| CX-4 | Trade confirmations slow, ledger writes time out | postgres-ledger -> db_contention (long transaction holds locks) | matching-engine deploy; two DB CPU sources disagree | lock logs withheld; change-detail call persistently fails | inconclusive |
| CX-5 | Quotes go stale, retries pile up | price-feed-client -> dependency_failure (vendor feed erroring, retry amplification) | matching-engine CPU high (consequence) | partial logs (transient) | identifiable |
| PG-1 | UPI success rate drops to 71% | payment-router -> config_change (DB pool 100 -> 20) | notification deploy; look-alike past incident; log gap | first log query times out | identifiable |
| PG-2 | Some checkouts time out, fleet average looks fine | lb -> lb_imbalance (backend weight skew) | bank-adapter latency; averaged metric hides hot node | none | identifiable |
| PG-3 | Duplicate payment attempts | redis-idempotency -> cache_failure (eviction policy changed) | Postgres slow queries (downstream); webhook deploy | none | identifiable |
| PG-4 | Merchant webhooks late, queue grows | webhook-worker -> config_change (backoff removed, retry storm) | merchant endpoint slowness (trigger, contributing factor) | change history partial | identifiable |
| PG-5 | Intermittent TLS failures with one bank | bank-adapter -> cert_expiry (mTLS cert) | looks like a bank outage; look-alike past incident | change history persistently times out | inconclusive |
| OTT-1 | Video start failures at a premiere | playback-service -> capacity_scaling | catalog-api deploy earlier that day | none | identifiable |
| OTT-2 | Home page slows every ~10 minutes | redis-catalog -> cache_failure (aligned TTLs) | postgres-meta CPU (symptom); two hit-ratio sources disagree | none | identifiable |
| OTT-3 | Buffering for one region | edge-lb -> lb_imbalance (routed to distant origin) | DRM provider latency blip | logs timeout | identifiable |
| OTT-4 | "License request failed" | license-service -> dependency_failure (third-party DRM) | playback deploy hours earlier | none | identifiable |
| OTT-5 | Profile switching errors after a release | auth-service -> bad_deploy (new query pattern overloads DB) | CDN config change; auth logs skewed 90 s ahead | none | identifiable (hard) |
| EC-1 | Flash sale: checkout 502s | postgres-orders -> db_contention (hot inventory row lock) | payment-ext latency | none | identifiable |
| EC-2 | Cart page slow, node CPU looks low | cart-service -> resource_exhaustion (CPU throttling) | Redis latency; node CPU vs container throttling disagree | none | identifiable |
| EC-3 | Errors rise slowly, restarts every ~40 min | checkout-service -> bad_deploy (memory leak, OOM) | autoscaling event; unrelated infra change | deployments partial | identifiable |
| EC-4 | Orders stuck "pending payment" | payment-ext -> rate_limiting (callbacks throttled) | queue worker looks suspicious | callback logs withheld; logs and dependency tool persistently fail | inconclusive |
| EC-5 | Sale spike: 503s while instances healthy | lb -> rate_limiting (per-IP limit, corporate NAT) | bot traffic spike; cart deploy | none | identifiable |
| FD-1 | Lunch rush: order placement slow | postgres-orders -> resource_exhaustion (pool x replicas > max_connections) | maps-api latency | none | identifiable |
| FD-2 | ETAs wildly wrong | dispatch maps client -> rate_limiting (quota) | dispatch deploy; log gap | none | identifiable |
| FD-3 | Orders not assigned to riders | dispatch consumer -> bad_deploy (unidentifiable from data) | two lag sources disagree | consumer logs withheld; log queries persistently time out; metrics partial | inconclusive |
| FD-4 | Riders see stale assignments after restart | lb -> lb_imbalance (sticky sessions) | Redis geo-cache warnings | none | identifiable |
| FD-5 | Push notifications delayed ~20 min | notification-service -> cert_expiry (push provider cert) | notification deploy; queue depth (symptom) | dependency tool timeout | identifiable |

Coverage (verified by test): 25 incidents, 5 per domain, 21 identifiable and 4 inconclusive,
11 with tool failures, every category in 2-3 incidents:

| Category | Incidents |
|---|---|
| capacity_scaling | CX-1, OTT-1 |
| resource_exhaustion | CX-2, EC-2, FD-1 |
| cache_failure | CX-3, PG-3, OTT-2 |
| db_contention | CX-4, EC-1 |
| rate_limiting | EC-4, EC-5, FD-2 |
| config_change | PG-1, PG-4 |
| lb_imbalance | PG-2, OTT-3, FD-4 |
| cert_expiry | PG-5, FD-5 |
| bad_deploy | OTT-5, EC-3, FD-3 |
| dependency_failure | OTT-4, CX-5 |

### Inconclusive incidents keep their real ground truth

`truth.scenarios` always holds the true component, category and mechanism. Insufficiency is
modelled separately:

- decisive evidence is either `retrievable` or `unreachable` (loaded, but a persistent failure
  rule blocks it), or recorded in `truth.withheld_evidence` (never loaded);
- `truth.alternative_explanations` lists the plausible wrong answers the visible data supports;
- identifiable incidents may only use transient or partial failures, so they stay solvable;
- the view `truth.invariant_violations` must be empty (checked by tests and by the generator).

This check is structural. It proves the planted ambiguity exists, not that no clever agent could
infer the cause; that limit goes in `docs/limitations.md`.

### Dataset as built (step 9)

- All 25 entries are in `configs/incident_plan.yaml`. 11 archetypes (PG-1's
  `pool_size_regression` plus `retry_policy_regression`, `bad_release`, `traffic_surge`,
  `resource_saturation`, `rate_limit_throttling`, `cache_degradation`, `lock_contention`,
  `lb_misbalance`, `upstream_vendor_failure`, `certificate_expiry`; several have modes) on a
  shared storyline engine (`generator/story.py`). PG-1's data is unchanged (checksum test).
- Decoys: `red_herring_change` (deploy/config/scaling/infra, any distance before the
  trigger), `lookalike_history`, `downstream_symptom`, `unrelated_spike`. Noise:
  `benign_warnings`, `log_gap`, `clock_skew`, `conflicting_sources`.
- Inconclusive entries carry an `insufficiency` spec: parts never generated (`withhold`),
  parts blocked by a persistent tool rule (`unreachable`; the validator now requires that
  rule), the real reason, and alternative explanations. Other decisive parts of an
  inconclusive incident are left out of `truth.decisive_evidence` (ambiguous on their own).
  FD-3's release record stays visible, so a bold agent could still guess it (see limits).
- Gate: `tests/unit/test_dataset_coverage.py` re-derives the counts above (25, 5 per domain,
  21/4, 11 with tool failures, each category 2-3) and builds, validates and double-builds
  every entry; `tests/integration/test_solvability_audit.py` loads all 25 and probes each
  decisive item through the real tools with the incident's own failure rules (3 attempts):
  every retrievable item comes back, no unreachable item does, withheld logs are absent.

### Generator as built (step 2)

- `configs/incident_plan.yaml` is answer-key data (plan ID, archetype, parameters, decoys,
  noise, tool-failure rules per entry). Only `src/investigator/generator/` reads it; a leakage
  test fails if any other source file names a plan ID, an archetype or the file itself.
- Pipeline: plan entry -> draft (baseline traffic, archetype, decoys, noise, tool rules) ->
  finalize -> validate -> load. One `random.Random(seed)` drives every choice.
- Archetype names describe the mechanism and differ from category names (PG-1 uses
  `pool_size_regression`, category `config_change`), so banning archetype names from prompts
  never bans a category an agent must output.
- Incident ID = `inc_` + first 8 hex of `sha256(["incident", plan_id, seed, generator_version])`.
  Evidence IDs are numbered per kind in time order, so an ID's number carries no hint.
- Noise types built: `benign_warnings` (harmless WARN lines on unrelated services) and
  `log_gap` (one service's logs missing for 4-8 min). Noise never touches the root-cause
  service or any service that owns a decisive log.
- The validator returns `code: detail` problems; its truth checks use the same names as
  `truth.invariant_violations`. The loader runs the validator, then loads in one transaction
  that replaces any earlier build of the same plan ID and commits only if the view is empty.
- Not built yet: inconclusive incidents (the build refuses them until the first inconclusive
  archetype, step 9), and the check that `unreachable` evidence really is blocked by a
  persistent tool rule (needs the failure-injector semantics from step 3).

## 5. MCP integration

Five tools: `query_logs`, `query_metrics`, `get_change_history` (summaries, `include_diff` for
diffs), `get_service_dependencies`, `search_past_incidents`. Common envelope: `status`
(`ok|partial|timeout|error|empty`), `items` (each with an `evidence_id`), `truncated`,
`next_cursor`, `warnings`, `error`, `call_id`.

Gateway behaviour: validate arguments, check budget, de-duplicate identical in-flight calls,
`asyncio.wait_for` on every call, at most 3 attempts for `timeout`/`error`, never retry
`partial`/`empty`, log everything. Facts written to the blackboard must cite evidence IDs that
exist in this run's tool results (grounding enforced at write time).

Failure injection lives in the server and is deterministic:
`sha256(seed | tool | canonical_args | attempt)` mapped to [0, 1). Python's built-in `hash()` is
banned (a test parses `src/` and rejects any reference to the name `hash`). Modes:
`transient`, `persistent`, `partial`, `probabilistic`.

As built in step 1 (`stable_hash.py`): the parts are hashed as one canonical JSON array
(`[seed, tool, args, attempt]`, sorted keys) rather than joined with `|`, so a `|` inside a
value cannot cause a collision; the draw uses the top 53 bits of the digest, because dividing
a 64-bit value by 2**64 can round to exactly 1.0. Tool args are normalised by `ToolRequest`
(defaults filled in, times in UTC) before fingerprinting, so equivalent requests de-duplicate.

Extensibility: `EvidenceBackend` interface; `PostgresBackend` now, `PrometheusBackend` or
`LokiBackend` later without touching tools or agents.

As built in step 3:

- `mcp>=2.3,<3`. The server uses the low-level `mcp.server.lowlevel.Server` (handlers
  `on_list_tools` / `on_call_tool`), not the decorator API, so each tool's input schema is
  exactly its Pydantic args model. The SDK does not reject unknown arguments itself;
  `ToolService` does (an `incident_id` argument returns an `error` envelope).
- The envelope goes back as `structured_content` (and as JSON text); `is_error` is true for
  `timeout`/`error`. An unknown tool name is an MCP error with no envelope.
- `attempt` is counted per normalised call (fingerprint) inside the server, one server per run.
  So `transient` with `fail_first_n: 1` fails the first attempt of each distinct call and the
  retry passes. Counting per tool ("the first log query of the run") would make outcomes
  depend on call order, which the determinism test forbids.
- The injector seed is the incident ID. Failure rules get an optional `when` (match on
  normalised args, e.g. `{"include_diff": true}`) so one kind of call can be made unreachable.
  The generator's validator parses rules with the server's own parser.
- Failed attempts never reach the database; any backend exception becomes an `error`
  envelope. `query_logs` pages with an opaque keyset cursor; `contains` is a literal
  case-insensitive substring. `get_service_dependencies`: `downstream` = what the service
  calls, `upstream` = who calls it. `search_past_incidents` ranks by matching words.
- The pool (`psycopg_pool.AsyncConnectionPool`, agent_ro, default max 4) binds the incident
  per transaction; tests show the binding is gone when a connection returns to the pool and
  that a failed query gives its connection back.

Gateway as built in step 4 (`src/investigator/gateway/`):

- `Gateway.execute(requests)` runs a batch in parallel and returns outcomes in request order.
  Limits (`GatewayLimits`, each with a test): `max_tool_calls` 40 logical calls per run,
  `max_attempts` 3 (cannot be set higher), `max_concurrency` 4 attempts at once,
  `call_timeout_s` 20 per attempt (`asyncio.wait_for`), `retry_delay_s` 0.25 x attempts so
  far, optional `deadline_s` for the run (no new call or retry after it; an attempt is cut at
  it).
- De-duplication covers identical in-flight calls (shared) and, beyond the original plan, a
  repeat of a call that already finished ok/partial/empty (answered from the run's cache).
  Neither uses budget. Failed calls are not cached, so asking again tries again.
- Outcome `source`: `executed`, `shared_in_flight`, `cached`, `budget_exhausted`,
  `deadline_exceeded`; the last two carry no result.
- If the server never answers or the transport fails, the gateway writes a `timeout`/`error`
  result itself (retried like any other).
- One `eval.tool_calls` row per physical attempt, written by harness_rw on an autocommit
  connection. `seen_evidence_ids()` lists the evidence the run actually received; the
  blackboard's grounding check (step 5) uses it.

Blackboard as built in step 5 (`src/investigator/blackboard/`):

- One SQLite file per run, WAL, busy timeout 30 s; each worker opens its own connection.
  Every write is `BEGIN IMMEDIATE`, so read-check-write sequences cannot interleave.
- Grounding at write time: the runner feeds `record_evidence()` from gateway results; a fact
  or assumption citing anything else raises `GroundingError` and writes nothing.
- Hypotheses must cite existing facts; objections existing hypotheses; tasks existing tasks.
  IDs (`T-0001`, `F-0001`, `H-0001`, `O-0001`) come from an atomic counter.
- Saving a hypothesis again replaces the current version and appends to
  `hypothesis_revisions` (for the "ranking changed after the Critic" metric).
- Tasks: `claim_task` succeeds for exactly one caller; `claim_next(agent)` hands out the
  oldest pending task whose dependencies have finished (a failed dependency also unblocks,
  so the agent can report missing evidence); only the claimer can finish; finished is final.

LLM wrapper as built in step 6 (`src/investigator/llm/`), CLI 2.1.319:

- Command: `claude -p --output-format json --model <alias> --tools "" --safe-mode
  --no-session-persistence --system-prompt-file <tmp>`, prompt on stdin, cwd an empty temp
  directory. `--safe-mode` keeps CLAUDE.md (which names plan IDs), hooks, skills, plugins and
  MCP servers out of agent calls; `--bare` would too but refuses Pro-plan OAuth. No prompt
  text is ever an argument (on Windows `claude` is an npm `.cmd` shim and cmd.exe mangles
  quotes, `%` and newlines). A timed-out call has its whole process tree killed.
- Verified with real calls (each well under $0.001): the reply is in `result`; prompt tokens
  = `input_tokens + cache_creation_input_tokens + cache_read_input_tokens`; `haiku` resolves
  to claude-haiku-5-5; the per-call overhead with these flags is ~800 tokens.
- `LlmClient.structured()`: at most 3 attempts (timeouts and errors count); an invalid reply
  is followed by a repair prompt (original prompt + problem + bad reply); then
  `LlmOutputError`. Limits: per-attempt timeout, concurrency (default 2), LLM calls per run
  (default 60, every attempt counts, cache hits included so replays match).
- Record/replay cache keyed by sha256(model, system, prompt); `replay` never calls the model
  and raises `CacheMiss`. Replayed calls are logged with their recorded tokens and latency.
- `--json-schema` exists in the CLI but is not used yet (its output placement was not
  verified); local validation plus repair covers it.

Agents and loop as built in step 7 (`src/investigator/agents/`, `src/investigator/runner.py`):

- Per-agent output models in `agents/outputs.py`; IDs are always minted by Python. A `check`
  hook on `LlmClient.structured()` turns semantic problems (a tool the agent may not use,
  evidence never returned, unknown fact/hypothesis/objection IDs) into `invalid_output` and a
  repair, within the same 3 attempts.
- Observability (query_logs, query_metrics) and Dependency (dependencies, changes, past
  incidents) run in parallel inside the investigate node (`asyncio.gather`), each a loop of
  at most 3 steps: request tools -> results -> facts.
- Graph: plan -> investigate -> reason -> critique -> `next_step()` -> plan | report. Ends when
  no blocking objection is open (`completed`), at `max_rounds`, or when a tool/LLM budget or
  the deadline is hit (`budget_exhausted`, labelled in `unresolved`). Tools requested by
  blocking objections run at the start of the next round.
- Failures: a failed specialist marks its task `failed` and leaves a note; a failed
  orchestrator falls back to default tasks; a failed reporter (or a spent LLM budget) gets a
  Python-built inconclusive report from the blackboard.
- Runner: one `eval.runs` row, one stdio MCP server, one blackboard file and one LLM cache per
  run. On Windows the runner needs the Proactor loop (subprocesses) and async psycopg cannot
  use it, so the runner talks to Postgres through `db.ThreadedConnection` (sync psycopg in a
  worker thread) on every platform, and the MCP server process runs a Selector loop.
- Gate evidence: 10 stub end-to-end tests in Docker; one real Haiku run on PG-1
  (2026-11-07, run d6f4a0c4): completed in 1 round, 16 tool calls / 20 attempts, 12 LLM
  calls, outcome inconclusive, top hypothesis payment-router/resource_exhaustion (true cause
  ranked second as payment-router/config_change). Two generic weaknesses found: unknown
  metric names return a bare `empty`, and the brief's "data window" was read as the incident
  start, so the change history inside the window was never searched.
- Fixed generically (approved 2026-11-07): `query_metrics` with an unknown metric name returns
  `empty` plus a warning listing the real names (like unknown services); the brief and the
  Orchestrator/Dependency prompts say the symptoms began inside the data window and to search
  all of it. Second real run (302e2cd4): completed in 1 round, 15 tool calls / 19 attempts,
  13 LLM calls, root_cause_identified, top payment-router/config_change 0.85 citing the
  decisive change and first pool-timeout log; the decoy deploy ranked last (0.02) but was
  also listed as supporting evidence. One run each, so this is a smoke test, not a result.

## 6. Evaluation

Both systems: same model, same gateway, same tool schemas, comparable budgets (caps on LLM
calls, tool calls, rounds, wall-clock).

| Metric | Definition |
|---|---|
| Root-cause accuracy | top hypothesis component + category vs `truth.scenarios`; inconclusive incidents are correct only when the outcome is `inconclusive` |
| Grounding (existence) | fraction of cited evidence IDs present in that run's tool results |
| Grounding (recall) | fraction of the answer key's decisive evidence cited by the report |
| Decoy susceptibility | top hypothesis matches a planted decoy |
| Confidence calibration | reliability of stated confidence against correctness |
| Inconclusive handling | correct abstention vs confident wrong answer; whether the true cause was named as a candidate |
| Cost | logical tool calls and physical attempts, LLM calls, tokens, latency |
| Collaboration (multi-agent) | Critic objections that triggered new tool calls; ranking changes after the Critic round; final answer differs from the Reasoning agent's first draft |

Scorer as built in step 8 (`src/investigator/scorer/`; golden tests in `tests/unit/test_scorer.py`):

- `root_cause_correct`: identifiable incident -> the top hypothesis has the true component
  (trimmed, case-insensitive) and category, whatever outcome the report chose;
  inconclusive incident -> the report says inconclusive. `outcome_correct` is separate.
- `grounding_cited` / `grounding_existing`: distinct evidence IDs in the report / of which
  this run's tool results returned. `decisive_recall`: share of decisive evidence cited.
- `decoy_hit`: the top hypothesis is wrong and is what a decoy invites (a decoy change, log
  or metric -> its service, any category; a look-alike past incident -> its component and
  category). New column `decoys_cited_as_support` (added 2026-11-07): decoy evidence listed
  as supporting the top hypothesis. `grounding_supporting` stays NULL (never defined).
- Costs from eval.*: `tool_call_count` = logical calls (attempt-1 rows), LLM calls and token
  sums, latency = run wall clock. Reads as scorer_ro, writes as harness_rw; refuses unfinished
  or already-scored runs. `eval.runs.config.backend` records which LLM backend ran, so stub
  runs can be excluded from evaluation.

Single-agent baseline (`agents/baseline.py`): one "investigator" agent with all five tools
(test: exactly the team's tool set and tool descriptions), the same RunConfig (model,
gateway limits, LLM limits), up to `max_baseline_steps` (12) steps of: request tools, keep
grounded notes, or return the FinalReport. It sees its last two result batches plus its
notes. Same grounding check, same fallback and partial-outcome labelling as the team.

Evaluation protocol (decided 2026-11-08 after batch 1, label `eval-v1`):

- Development set = PG-1 + CX-2, PG-5, OTT-4, EC-1, FD-2 (the system was tuned after seeing
  these); test set = the other 19, run once with the system frozen. Headline numbers come
  from the test set only; dev-set numbers are shown separately and labelled.
- Batch-1 findings (10 runs): every run answered inconclusive; components were often written
  as sentences (scored wrong although right); the team spent its 40-call tool budget in
  every run, with many empty, repeated queries.
- Generic fixes ("Phase A"), none naming an incident or service: hypothesis components must
  be exact service names from the brief (checked and repaired like evidence IDs, both
  systems); commit when direct evidence names the cause, inconclusive only when the evidence
  cannot separate the explanations; every agent sees the tool calls already made in the run
  and is told to query the whole window first and not repeat calls. A test forbids any
  topology service name in fixed prompt text.
- Scorer unchanged (strict component match); accepting a third-party vendor's name was
  discussed and left undecided.
- eval-v2 (12 of the 25, both systems, after the Phase A fixes): 8/12 right each. The team
  found more decisive evidence (88% vs 83%) at about 3.5x the tokens. In 9 of its 12 runs it
  answered "inconclusive" because a blocking Critic objection was still open when the tool
  budget ran out (in 5 of those its top hypothesis was right).

Roadmap revision (2026-11-08, from the user's "Action Plan & Roadmap" PDF; user decisions):
- Keep Postgres, add one-command setup (`scripts/setup.ps1`, `make setup`).
- Remove the viewer login (read-only, 127.0.0.1 only).
- Critic verdict loop:
  - `approved: bool`;
  - a blocking objection names the competing explanation and a `follow_up_agent`, and
    Python turns it into a follow-up task for that specialist;
  - each hypothesis can be blocked once;
  - a fifth of the tool budget is reserved for follow-ups;
  - Reasoning gives 2–4 hypotheses.
- New evaluation set: the PDF's 12 stories (INC-01..INC-12) on three new platform topologies
  (online_retail, search_platform, saas_platform), built by the same generator (8 new
  archetypes, 2 new modes). The validator now allows a permanent tool outage on an
  identifiable incident when no decisive evidence depends on that tool (INC-10).
- eval-v3 = both systems on INC-01..12 with the new loop. eval-v2 stays as the earlier
  iteration. The stories were written before any run on them, so eval-v3 is not tuned on.

Agent-failure recovery: an agent whose output is invalid after 3 attempts, or whose call times
out, has its task marked `failed`; Reasoning and Reporting are told evidence is missing, the
report lists it under `unresolved` and confidence drops. Tested with a stub LLM where one agent
always fails.

## 7. Test plan

| Area | Test |
|---|---|
| Permissions | full role x table privilege matrix against the catalog; `PUBLIC` has nothing; default privileges apply to new tables |
| RLS | every evidence table: RLS on, three policies, `incident_id` column; unbound sees nothing; bound sees only its incident; pooled-connection reuse; concurrent servers disjoint; injection strings |
| Integrity | composite FKs reject cross-incident rows; CHECK constraints; truth rows must reference real evidence; cascade on incident delete; runs protect incidents from deletion |
| Truth invariants | `truth.invariant_violations` empty on seed data and populated by crafted violations |
| Generator | build-twice checksum identical; answer key never in evidence; solvable incidents' decisive evidence retrievable; inconclusive ones not |
| Tools | schema validity, unique IDs, timeout/partial/empty behaviour, failure determinism under shuffled order |
| Parallel MCP | 20 concurrent calls finish well under the sum of latencies, responses match requests |
| Pool | 50 calls on a pool of 4 complete; `pg_stat_activity` never exceeds pool max; failed query returns its connection |
| SQLite | 8 workers x 200 writes = 1600 rows; two workers claiming one task yield one winner |
| Retry limits | persistent tool failure shows exactly 3 attempts then `failed`; invalid LLM JSON shows exactly 3 attempts |
| Termination | an always-objecting Critic stops at `max_rounds`; budget exhaustion yields a labelled partial outcome |
| Leakage | no incident ID, plan ID or archetype name appears in any agent prompt; `hash(` absent from `src/` |
| Docs | `docs/technical_note.md` is at most 500 words |

## 8. Build steps and gates

| Step | Work | Gate |
|---|---|---|
| 0 | Repo, docker-compose (Postgres 16), SQL 001-007, roles, RLS loop | permissions, RLS, integrity and truth-invariant suites pass |
| 1 | Pydantic models, `stable_hash.py`, builtin-`hash` ban test | hashing and fingerprint tests pass |
| 2 | Generator for PG-1 (1 archetype x 1 domain), 2 noise types, evidence index, truth write, validator | build-twice checksum, truth invariants, leakage tests pass |
| 3 | MCP server, 5 tools, failure injector, async pool | tool, failure-determinism, parallel and pool tests pass |
| 4 | Gateway (semaphore, de-dupe, retries, budgets, logging) | parallel and retry-limit tests pass |
| 5 | SQLite blackboard | concurrency tests pass |
| 6 | `claude -p` wrapper, cache, repair retries, stub LLM | LLM retry-limit test passes |
| 7 | Six agents plus LangGraph loop | end-to-end run on the stub LLM, then one real Haiku run on PG-1 |
| 8 | Single-agent baseline and scorer | scorer golden tests; baseline uses the same tool list |
| 9 | Scale to 10 archetypes and 25 incidents | coverage test and solvability audit pass |
| 10 | Full evaluation (cached, in batches) | results table generated |
| 11 | Diagram, README, technical note (<= 500 words), limitations, transcript | clean-clone setup works |
| 12 | App schema, auth, sessions, lockout | auth tests |
| 13 | API routes, SSE, worker, live-run caps | API tests; worker env has no scorer credentials; truth endpoints 403 until run complete |
| 14 | Frontend: login, incidents, investigation, evaluation | manual walkthrough on a seeded run |
| 15 | Tailscale deployment notes, final README | a reviewer can log in and replay a run |

Critical path is steps 0-8. Steps 9-10 are scale; if time runs short, evaluate ~15 incidents that
cover every category and all four inconclusive cases, and say so in the limitations note.
Steps 12-15 are optional extras (the assignment requires no polished frontend); cut in this
order: evaluation-page charts, live runs (keep replay), live timeline.

## 9. UI and login (steps 12-15)

- API and worker are separate processes. The API holds `scorer_ro` credentials (to reveal ground
  truth after a run); the worker holds only `agent_ro` and `harness_rw`.
- Two seeded accounts with identical permissions; passwords stored as argon2 hashes; credentials
  come from `.env` through a seed script. Demo logins are listed in the README only.
- Session cookie: `HttpOnly`, `Secure`, `SameSite=Lax`, server-side session row, expiry;
  attempt limiting with lockout; identical error for wrong user or wrong password.
- Serve over Tailscale only (`tailscale serve`); do not use Funnel.
- Live runs spend the Pro plan: one live run at a time, a daily cap (default 10, configurable);
  replay of cached runs is uncapped.
- Screens: Login, Incidents, Investigation (timeline, facts, hypotheses, objections, report,
  ground-truth reveal), Evaluation.
- As built (2026-11-08, simplified at the user's request): one read-only FastAPI process
  (`investigator.web`) that replays finished runs. No worker, no live runs, no SSE, so it
  spends no quota. Accounts: argon2 hashes in the git-ignored `.web_users.json`. Sessions
  are in memory; 5 failures lock a username for 15 minutes. Test conditions and the answer
  key are shown only once the runs for the incident have finished.

## 9a. Decision change: agent-to-agent messages (user's request, 2026-11-09)

eval-v4 ran both systems on INC-01..12 with `--pattern exchange --company-context`: agents can
message each other through a capped mailbox on the blackboard, and both systems get a general
company background (healthy system only). eval-v3 stays as the blackboard-only result. See
`docs/architecture.md` and `docs/limitations.md`.

## 10. Risks

- Pro-plan limits: roughly 600-700 Haiku calls for the full evaluation; use the cache and batch.
- Haiku may pattern-match common failures; vary archetype wording across domains.
- `claude -p` has no temperature control: record/replay cache for reproducible reruns, variance
  on a subset if affordable. CLI overhead tokens inflate both systems equally.
- Reviewer setup needs Docker and an authenticated Claude CLI: documented external dependencies.
