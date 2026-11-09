# CLAUDE.md

Guidance for Claude Code working in this repository. Read this first, then
`docs/assignment.pdf` (the requirements) and `docs/implementation_plan.md` (the approved
design, decisions, dataset table and step gates). If this file and the plan disagree, the plan
wins; tell the user about the conflict.

## What this is

A take-home assignment: a multi-agent system that investigates simulated production incidents,
ranks root-cause hypotheses with evidence, and can answer "inconclusive". It is compared with a
single-agent baseline (same model, tools, budgets) on 25 synthetic incidents.

- Deadline: Monday 9 Nov 2026. The goal is to have a complete, tested slice early, then scale.
- Phase 1 = the assignment as written (this repo). Phase 2 (live monitoring with Prometheus and
  Grafana) is out of scope: do not build or scaffold it.
- Steps 12-15 (login and UI) are optional extras; the assignment says no polished frontend is
  required. Do them only after steps 0-11 pass, and cut them first if time is short.

## Current status

Update this section at the end of every step.

- [x] Step 0: Postgres schemas, roles, row-level security, 81 passing tests
  (re-verified 2026-11-07 on Windows via `scripts/test_in_docker.ps1`)
- [x] Step 1: `stable_hash.py`, Pydantic models in `src/investigator/models/` (common, tools,
  blackboard, report, scores), AST-based ban on built-in `hash`; 111 unit tests, 192 total
  passing. Per-agent output schemas are deliberately left to step 7.
- [x] Schema follow-up: dependency edges are evidence (`D-` ids, kind `dependency`); 201 passing.
- [x] Step 2: generator for PG-1 in `src/investigator/generator/` (plan file
  `configs/incident_plan.yaml`, archetype `pool_size_regression`, decoys, noise
  `benign_warnings` + `log_gap`, validator, loader, read-back); 273 passing. No CLI yet: load
  with `load_incident()` (a CLI comes when step 3 needs a populated database).
- [x] Step 3: MCP server (`src/investigator/mcp_server.py`, mcp 2.x low-level API), tools in
  `src/investigator/tools/` (backend interface, Postgres backend + async pool, failure
  injector, ToolService); 343 passing.
- [x] Step 4: gateway in `src/investigator/gateway/` (limits, retries, in-flight sharing and
  result cache, budget, deadline, eval.tool_calls logging, seen evidence IDs); 370 passing.
- [x] Step 5: SQLite blackboard in `src/investigator/blackboard/` (grounding and reference
  checks at write time, atomic task claims, hypothesis history); 398 passing.
- [x] Step 6: LLM layer in `src/investigator/llm/` (`claude -p` backend, stub backend,
  record/replay cache, repair retries capped at 3, eval.llm_calls logging); 426 passing +
  1 live test (skipped by default, passed once on 2026-11-07).
- [x] Step 7: six agents + LangGraph loop (`src/investigator/agents/`), runner
  (`src/investigator/runner.py`), generator CLI, demo DB script; 448 passing + 1 live. Real
  Haiku runs on PG-1: first inconclusive (true cause 2nd); after two generic fixes (metric-name
  warning, data-window wording) correct at 0.85. See plan §5 notes.
- [x] Step 8: scorer (`src/investigator/scorer/`, golden tests) and single-agent baseline
  (`agents/baseline.py`, same tools/limits); 471 passing + 1 live. Both real PG-1 runs scored.
- [x] Step 9: 25 incidents (11 archetypes on `generator/story.py`, 5 topologies, 4
  inconclusive); coverage test + solvability audit; 559 passing + 1 live. PG-1 unchanged.
- [ ] Step 10: full evaluation (cached, in batches). Built: `investigator.batch` (resumable,
  label `eval-v1`) and `investigator.scorer.report`; 567 passing. Batch 1 = CX-2, PG-5,
  OTT-4, EC-1, FD-2 (both systems). PG-1's older smoke runs have no label and are not counted.
  Batch 1 done (results/eval-v1-batch1.md). Phase A generic fixes done (577 passing); see
  plan §6 "Evaluation protocol": dev set = PG-1 + batch 1, test set = other 19, frozen.
  Next: Phase B (re-run dev set as eval-v2), then Phase C (test set). User asked to keep
  token use low and never hardcode.
  eval-v2 finished 2026-11-08 (12 incidents x 2 systems, results/eval-v2.md): 8/12 right
  each; team under-commits (says inconclusive when a blocking objection is still open at
  budget end).
- [x] Roadmap revision (user's PDF, 2026-11-08; plan §6 "Roadmap revision"):
  - login removed;
  - p95 latency, tool attempts and total tokens added to the results;
  - one-command setup;
  - transcript exporter (`scripts/export_transcript.py`);
  - Critic `approved` + follow-up tasks + block-once + tool reserve
    (tests/unit/test_critic_loop.py);
  - 12 story incidents INC-01..12 on 3 new topologies (all validated, loaded in the demo
    DB, solvability audit passes);
  - 484 unit tests passing, plus the stub and audit integration tests in Docker.
- [x] eval-v3 done 2026-11-08 (results/eval-v3.md, results/eval-v3-incidents.md): team 9/12,
  single 11/12; team found 97% of decisive evidence vs 85%, at 3x the tokens. INC-02 data
  fixed after its first run and re-run (disclosed in docs/limitations.md).
  Technical note (418 words), limitations, README results table and architecture are
  updated. One-command setup ran end to end. Full Docker suite: 644 passed, 1 skipped
  (live). Transcript exported to transcripts/session_transcript.md (re-export at the end of
  each session).
- [ ] Steps 11-15: read-only viewer built early at the user's request
  (`src/investigator/web/`, FastAPI + Chart.js, no login since the roadmap revision).
  Case stories (user's request, 2026-11-09): `configs/case_stories.yaml` holds answers and is
  viewer-only; `configs/company_context.yaml` is per company, healthy-system only, and reruns
  give it to the agents (`--company-context`). All 12 INC stories are written. Reruns use the
  agent-to-agent pattern (`agents/exchange.py`, `--pattern exchange`).
- [x] eval-v4 done 2026-11-09 (results/eval-v4.md, results/eval-v4-incidents.md; exchange pattern
  and company background, both systems): top guess right, team 11/12 and single 8/12; right and
  committed, team 7/12 and single 8/12; team 522K tokens per run against 89K. The viewer shows
  eval-v4.

## How to work

1. One step at a time, in plan order. Do not scaffold later steps early.
2. A step is done only when its gate (last column of the plan's step table) passes. Run the
   tests and show the real output; never say "should work".
3. Say plainly what you did not verify (for example anything needing a real `claude -p` call or
   Docker). Do not claim coverage you did not run.
4. If a design decision is not in the plan, or you want to change an approved one, stop and ask
   the user. Small implementation details you may decide yourself and mention in one line.
5. When a step adds a test, prove it can fail: break the thing on purpose once, see the test
   fail, restore. (This was done for step 0 and caught a real bug in the test fixtures.)
6. Keep `docs/implementation_plan.md` current when a decision changes.

## Hard rules (these protect the assignment's own constraints)

- **No hardcoded answers.** Incidents come from parametrized generators (archetype x domain
  topology x seeded noise). No agent prompt, tool, or scorer branch may mention a specific
  incident, plan ID or archetype. A leakage test enforces this; keep it passing.
- **Agents never see the answer key.** Only `scorer_ro` reads `truth.*`. The MCP server uses
  `agent_ro` only. The incident is bound per transaction with
  `set_config('app.incident_id', ..., true)`, never session-level, and is never a tool argument.
  The MCP server exposes no raw SQL.
- **Agents do not call MCP themselves.** An agent returns `tool_requests` as JSON; the Python
  `ToolGateway` executes them (validation, timeout, retry, de-duplication, budget, logging).
  `claude -p` runs with its built-in tools disabled. The single-agent baseline goes through the
  same gateway with the same schemas and budgets.
- **Determinism.** Use `random.Random(seed)` and `hashlib.sha256`. Never use Python's built-in
  `hash()` (randomized per process). Never iterate over a `set` when generating data. Failure
  injection is keyed on `sha256(seed | tool | canonical_args | attempt)`.
- **Bounded everything.** Retries (at most 3 attempts for timeout/error; never retry
  `partial`/`empty`), rounds, LLM calls, tool calls, wall-clock time. Every limit has a test.
- **Grounding.** A fact may only cite evidence IDs that exist in this run's tool results;
  enforce this when the fact is written to the blackboard.
- **Inconclusive is a valid outcome**, and an inconclusive incident keeps its real ground truth
  in `truth.scenarios`. `truth.invariant_violations` must always be empty.
- **Secrets.** Never commit `.env`. No passwords in code or docs except the demo logins, which
  go in the README only. Serve over Tailscale `serve`. Exception (user's decision,
  2026-11-08, for about 2 days): the viewer is public via Funnel on port 10000 with reruns
  ON (the user accepted that visitors can spend Claude quota; one rerun at a time; Haiku or
  Sonnet). `--no-rerun` gives a read-only copy. Turn the link off with
  `tailscale funnel --https=10000 off`.
- **Transcript requirement.** The assignment requires the coding-assistant transcript. Keep
  `transcripts/` up to date (export this session's transcript at the end of each work session).

## Code style

The user wants simple, spelled-out code, and said earlier solutions were "too complex":

- plain `for` loops and `if/else`, explicit intermediate variables;
- no clever one-liners, nested comprehensions, `functools`/`itertools` tricks or other shortcuts,
  even when the plain version is longer;
- small modules with one job each; docstrings explain why, not what.

Use Pydantic v2 for every schema that crosses a boundary (tool inputs/outputs, agent outputs,
blackboard rows, the final report, scores). Python 3.11+.

## Commands

```bash
pip install -e ".[dev]"            # in the Claude sandbox add --break-system-packages
bash scripts/start_local_pg.sh     # native PostgreSQL 16 on 127.0.0.1:54329 (no Docker needed)
pytest                             # tests build and drop their own `incident_test` database
bash scripts/stop_local_pg.sh
docker compose up -d db            # normal path where Docker exists (not yet run here)

# Windows (the user's machine): full suite in Docker, no bash/psql needed on the host
powershell -ExecutionPolicy Bypass -File scripts\test_in_docker.ps1        # extra args go to pytest
powershell -ExecutionPolicy Bypass -File scripts\test_in_docker.ps1 -Clean # remove test containers
.venv\Scripts\python -m pytest tests/unit   # unit tests natively (needs the venv: mcp 2.x)

# real runs from Windows: project venv (mcp 2.x, langgraph 1.2), demo DB at 127.0.0.1:54329
python -m venv .venv; .venv\Scripts\pip install -e ".[dev]"
powershell -ExecutionPolicy Bypass -File scripts\demo_db_in_docker.ps1 --only PG-1   # prints inc_ id
$env:IV_PG_HOST="127.0.0.1"; $env:IV_PG_PORT="54329"; $env:IV_PG_DB="incidents"
$env:AGENT_RO_PASSWORD="agent-test-pw"; $env:HARNESS_RW_PASSWORD="harness-test-pw"
.venv\Scripts\python -m investigator.runner --incident inc_3d0e8a6c     # spends Haiku quota
.venv\Scripts\python -m investigator.runner --incident inc_3d0e8a6c --system single_agent
$env:SCORER_RO_PASSWORD="scorer-test-pw"; .venv\Scripts\python -m investigator.scorer --all
# evaluation: resumable batch (skips finished runs with the same label), then the table
.venv\Scripts\python -m investigator.batch --label eval-v1 --incidents inc_a inc_b ...
.venv\Scripts\python -m investigator.scorer.report --label eval-v1 --out results/eval-v1.md

# one real claude -p call through the LLM wrapper (spends a little quota; Windows-native)
$env:IV_LIVE_LLM = "1"; python -m pytest tests/live -q

# MCP server for one incident (stdio); needs IV_PG_* and AGENT_RO_PASSWORD in the environment
INCIDENT_ID=inc_xxxxxxxx python -m investigator.mcp_server
```

Test defaults: host 127.0.0.1, port 54329, user `postgres`, password `postgres-test-pw`
(override with `IV_TEST_PG_*`). Tests fail loudly, not silently, if no server is reachable.

## Gotchas already hit (do not rediscover)

- psycopg returns `bytes` for text if the server encoding is `SQL_ASCII`. The local cluster must
  be UTF8 (the start script does this) and test databases are created `ENCODING 'UTF8'`.
- `SHOW x` comes back as bytes through psycopg; use `SELECT current_setting('x')`.
- `agent_ro` is read-only by default. A test that checks privileges (not the setting) must run
  `SET default_transaction_read_only = off` first, otherwise it sees a read-only error instead of
  "permission denied".
- Postgres refuses to run as root; the scripts hand over to the `postgres` OS user with
  `runuser`. The Docker daemon may be unavailable in the sandbox.
- CHECK constraints fire before foreign-key checks, and NOT NULL before CHECK; tests that expect
  a specific violation must account for that order.
- On Windows, plain `pytest` fails all DB tests in setup: the fixture runs `bash` (resolves to
  the WSL launcher, no distro installed) and `psql` (not installed). Use
  `scripts/test_in_docker.ps1`. Inside it, pytest args cannot contain spaces (PowerShell 5.1
  mangles embedded quotes passed to native programs).
- A "rejects X" test must start from otherwise-valid input, or it can pass because some other
  field is missing (caught in step 1: `search_past_incidents` requires `query`).
- When proving tests can fail, a break that makes a shared fixture's baseline invalid (for
  example a leak in the generated data) errors every test using that fixture and hides the
  other breaks. Apply such breaks on their own (caught in step 2).
- `ORDER BY` on text must say `COLLATE "C"` wherever Python compares the order (checksums,
  round trips): the test database is C-collated but a compose database may not be.
- mcp is 2.x (pinned `>=2.3,<3`): `FastMCP` is now `MCPServer`, `mcp.shared.memory`'s
  in-process helper is gone (use `mcp.Client(server)`), and types use snake_case attributes
  (`input_schema`, `structured_content`, `is_error`). The SDK passes unknown tool arguments
  through, so validation must stay in ToolService. The user's native Windows Python has
  mcp 1.x, so `tests/unit/test_mcp_server.py` only runs in Docker unless that env is upgraded.
- A test that creates `eval.runs` rows must delete them (as admin; harness_rw cannot) before
  the `pg1` fixture tears down: runs block incident deletion, and a leftover run makes every
  later module's PG-1 load fail (caught in step 4: 34 errors from one missing cleanup).
- `claude -p` flags (tool disabling, `--output-format json`, `--model`) must be verified against
  the installed CLI (`claude --help`) before relying on them. `claude -p` has no temperature
  control, so keep the record/replay cache for reproducible reruns.
- LangGraph runs a plain (sync) conditional-edge function in a worker thread; anything that
  touches the blackboard (SQLite, thread-bound) must be an `async def` node or router.
- On Windows, async psycopg refuses the Proactor loop but subprocesses need it: the runner uses
  `db.ThreadedConnection`; the MCP server process uses a Selector loop.
- `claude -p` run inside this repo would load CLAUDE.md (plan IDs!) into the agent: always
  `--safe-mode` and an empty temp cwd. `--bare` is not an option (API key only, no Pro OAuth).
  `--system-prompt-file` works although `--help` lists it only inside the `--bare` text.
- On Windows `claude` is `claude.cmd` (npm shim): never pass prompt text as an argument, and
  kill the process tree (`taskkill /T`) on timeout or node keeps running. PowerShell 5.1 also
  drops empty-string arguments such as `--tools ""`; use the Bash tool for manual CLI probes.

## Architecture in one paragraph

A seeded generator writes incidents into Postgres (`evidence` schema, plus the hidden `truth`
schema). A custom MCP server (one per run, bound to one incident, `agent_ro`) exposes five tools:
`query_logs`, `query_metrics`, `get_change_history`, `get_service_dependencies`,
`search_past_incidents`, with deterministic failure injection. A Python gateway (MCP client)
mediates every call. A LangGraph graph runs six agents (Orchestrator, Observability, Dependency,
Reasoning, Critic, Reporting), each a `claude -p` call (Haiku by default) that reads and writes a
SQLite blackboard (tasks, facts, hypotheses, objections); routing, limits and termination are
deterministic Python. A separate scorer compares the final report with `truth`. Everything is
logged to `eval.*`.

## Deliverables checklist

- [x] Runnable implementation with setup instructions (README)
- [x] Architecture diagram and explanation of agent coordination (`docs/`)
- [x] 25-incident dataset and automated tests
- [x] Evaluation results: multi-agent vs single-agent (accuracy, grounding, decoy hits,
      calibration, tool calls, tokens, latency)
- [x] Technical note, at most 500 words (`docs/technical_note.md`, length enforced by a test)
- [x] `docs/limitations.md` documenting incomplete functionality and external dependencies
      (Docker, authenticated Claude CLI, Pro-plan limits)
- [x] Coding-assistant transcript in `transcripts/`
