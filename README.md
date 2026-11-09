# Multi-agent production incident investigator

Six LLM agents (Orchestrator, Observability, Dependency, Reasoning, Critic, Reporting)
investigate simulated production incidents through five tools, rank root-cause hypotheses
with cited evidence, and may answer "inconclusive". A single-agent baseline with the same
model, tools and budgets is evaluated against it on generated incidents.

- Architecture and agent coordination: [`docs/architecture.md`](docs/architecture.md)
- Technical note (≤ 500 words): [`docs/technical_note.md`](docs/technical_note.md)
- Limitations and external dependencies: [`docs/limitations.md`](docs/limitations.md)
- Evaluation results: [`results/`](results/)
- Full design, decisions and build log: [`docs/implementation_plan.md`](docs/implementation_plan.md)
- Coding-assistant transcript: [`transcripts/`](transcripts/)

## Layout

```
configs/incident_plan.yaml   37 incidents: 25 generated + the 12 INC-* stories (answer-key side)
db/init/                     schema, roles, row-level security (evidence / truth / eval)
src/investigator/
  generator/                 seeded incident generator, validator, loader
  tools/  mcp_server.py      the five tools, failure injection, MCP server (one per run)
  gateway/                   tool gateway: validation, dedupe, parallelism, retries, budgets
  blackboard/                shared SQLite state: tasks, facts, hypotheses, objections
  llm/                       `claude -p` backend, stub backend, record/replay cache
  agents/                    six agents, LangGraph loop, single-agent baseline
  runner.py  batch.py        one investigation / a resumable evaluation batch
  scorer/                    scoring against the answer key, results table
  web/                       read-only viewer: app.py (routes), data.py (queries) + one module
                             per topic; static/incident/ holds one script per page section
tests/                       unit and integration tests (integration tests build their own DB)
```

## Requirements

Python 3.11+, Docker, and for real investigations an authenticated Claude CLI (`claude`)
on the same machine. Instructions below are for Windows PowerShell (the development
machine); on Linux use `bash scripts/start_local_pg.sh` and `pytest` instead of the Docker
test script.

## One-command setup

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1    # Windows
make setup                                                    # Linux / macOS
```

Setup does four things:
1. creates `.venv` and installs the package;
2. writes a git-ignored `.env` with random database passwords;
3. starts Postgres with `docker compose` (port 54330);
4. loads the incidents.

Then, in the same shell:

```powershell
. .\scripts\load_env.ps1                                       # (make targets read .env themselves)
.venv\Scripts\python -m pytest tests/unit                      # make test-unit
.venv\Scripts\python scripts\run_eval.py --label eval-v4 --pattern exchange --company-context --cache-mode off   # make eval (spends Claude quota)
.venv\Scripts\python -m investigator.web --label eval-v4       # make viewer
powershell -ExecutionPolicy Bypass -File scripts\test_in_docker.ps1   # full suite incl. DB tests
```

## Generate the dataset and run an investigation

```powershell
# demo database on 127.0.0.1:54329 (created once) + load incidents; prints plan ID -> incident ID
powershell -ExecutionPolicy Bypass -File scripts\demo_db_in_docker.ps1 --only PG-1 CX-4
. .\scripts\demo_env.ps1

.venv\Scripts\python -m investigator.runner --incident inc_3d0e8a6c                       # multi-agent
.venv\Scripts\python -m investigator.runner --incident inc_3d0e8a6c --system single_agent  # baseline
```

Every run stores its final report in `eval.runs.final_report` and logs every tool and LLM
call to `eval.*`. The team's blackboard is kept in `runs/<run_id>/blackboard.sqlite`. The
single-run command above also writes `runs/<run_id>/report.json`. LLM replies are cached in `.llm_cache/`;
`--cache-mode replay` reruns without calling the model.

## Evaluation

```powershell
.venv\Scripts\python scripts\run_eval.py --label eval-v4 --pattern exchange --company-context --cache-mode off   # the 12 INC-* stories
.venv\Scripts\python scripts\export_incidents.py --label eval-v4 --out results\eval-v4-incidents.md
```

`run_eval.py` turns plan IDs into incident IDs, runs both systems on each incident through
`investigator.batch`, scores every run and writes `results/<label>.md`. It skips runs
already finished under the same label, so it can be restarted after an interruption.

**Results (12 incidents each; details and caveats in `docs/limitations.md`):**

| | eval-v4: agents message each other | eval-v3: blackboard only | eval-v2: 12 of the original 25 |
|---|---|---|---|
| Team, top guess right | 11/12 | 9/12 | 8/12 |
| Single agent, top guess right | 8/12 | 11/12 | 8/12 |
| Team, right and committed to it | 7/12 | 8/12 | not reported |
| Single agent, right and committed to it | 8/12 | 10/12 | not reported |
| Decisive evidence found (team / single) | 100% / 83% | 97% / 85% | 88% / 83% |
| Tokens per run (team / single) | 522K / 89K | 322K / 108K | 565K / 149K |
| Time per run (team / single) | 422 s / 69 s | 243 s / 94 s | 351 s / 109 s |

"Top guess right" also counts a run whose final report said inconclusive. In eval-v4 the team
did that on four incidents it should have called, so its 11/12 overstates what it would have
told an on-call engineer. Eval-v4 and eval-v3 are on the same 12 incidents; in eval-v4 both
systems also had a general company background.

Full tables: `results/eval-v4.md`, `results/eval-v3.md` and `results/eval-v2.md`. Per-incident
detail: `results/eval-v4-incidents.md`, `results/eval-v3-incidents.md` and
`results/eval-v2-incidents.md`.

## Web viewer (read-only)

A small FastAPI app. The home page explains how the system works, then shows the overall score, then lists the
12 test cases (with traps and ✓/✗ per system). Each case page has four sections:
- **the story**: a short summary, with "Read the full story" for the services, the timeline,
  the data tables, the metric graphs and the root cause (marked as hidden from the agents);
- **the results**: what the team and the single agent concluded, with buttons for the key
  evidence and the reason for each confidence;
- **the agent history**: every message and step, for both systems, live during a rerun;
- **the flow and walk-through**: a clickable graph of how each system reached its answer,
  next to every agent turn with its exact prompt and reply. New runs record these in
  `runs/<run_id>/llm_transcript.jsonl`; older runs are rebuilt from the answer cache.

Each page also has a **Rerun** button. It starts `investigator.batch` as a separate worker under a new label
  (`eval-v4-rerun-<time>`), so the official results never change:
  - *Replay* reuses recorded Claude answers (normally free);
  - *Fresh* calls Claude again (uses quota);
  - the model can be *Haiku* (used for the official results) or *Sonnet*. Answers are
    recorded per model, so the first Sonnet run of a case calls the model even in Replay.

  One rerun at a time. Switch between the official run and reruns at the top of the page.

The home page also explains how it works (6 agents vs 1, the 5 tools). The
**Agent prompts** page shows each agent's exact instructions and answer format.

```powershell
.\start      # starts the demo database and the viewer: http://127.0.0.1:8000
.\stop       # stops the viewer (or press Ctrl+C in its window)
```

There is no login: the data is synthetic and the server listens on 127.0.0.1 only. To share
it, put Tailscale in front of it. Use `tailscale serve` for your tailnet only. For a public
link, start the viewer with `--no-rerun` first, so visitors cannot spend Claude quota:

```powershell
.venv\Scripts\python -m investigator.web --label eval-v4 --no-rerun
tailscale funnel --bg --https=10000 http://127.0.0.1:8000      # public link
tailscale funnel --https=10000 off                             # take it down
```

Rerun needs the same environment as
an evaluation run (all role passwords and an authenticated `claude` CLI).
