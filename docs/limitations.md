# Limitations, disclosures and external dependencies

## External dependencies

- **Docker** (or a local PostgreSQL 16) for the database; on Windows the test suite runs in
  Docker (`scripts/test_in_docker.ps1`) because the fixtures need `bash` and `psql`.
- **An authenticated Claude CLI** (`claude`, tested with 2.1.319) on the machine that runs real
  investigations. Calls go through `claude -p` on the user's plan, so a **Pro plan's usage
  limits** cap how many runs fit in a window. All replies are cached (`.llm_cache/`), so
  finished runs can be replayed without quota.
- **Python 3.11 or newer** and the packages pinned in `pyproject.toml`: `mcp` 2.x, LangGraph 1.2
  (the fixed pipeline only), Pydantic 2, psycopg 3 and PyYAML. The viewer also needs FastAPI and
  Uvicorn, and the tests need pytest.
- **Internet access.** Investigations call Anthropic's service through the Claude CLI. The viewer's
  pages load Chart.js, Mermaid and Google Fonts from public CDNs, so its charts, diagrams and
  fonts need a connection.
- **Tailscale** (optional), only to share the viewer through a link.

## Incomplete functionality

- **Simulated incidents only.** The system reads generated data and has no connection to real
  production systems. Live monitoring (Prometheus, Grafana) was not built.
- **Recommendations are not applied.** The report recommends corrective actions, but nothing
  executes them, and there is no human approval step.
- **Past-incident search is by keyword,** not by meaning.
- **Partial evaluation.** 37 incidents are generated, and the final evaluation covers 12 of them,
  with one run per system and one model (Haiku).
- **The viewer has no login and no daily limit on reruns** (see "Known gaps in the system").

## Evaluation scope and honesty notes

- **Three evaluations, each of 12 incidents. Twelve is a small sample:** a difference of one
  or two incidents is noise.

  | Label | Incident set | Team | Single agent |
  |---|---|---|---|
  | `eval-v2` (`results/eval-v2.md`) | 12 of the original 25 generated incidents | 8/12 | 8/12 |
  | `eval-v3` (`results/eval-v3.md`) | the 12 story incidents INC-01..12, with the Critic verdict loop | 9/12 | 11/12 |
  | `eval-v4` (`results/eval-v4.md`) | the same INC-01..12, with agent-to-agent messages and a company background | 11/12 | 8/12 |

  These counts are the top guess. **They include reports that ended inconclusive.** Counting
  only runs that also committed to the right outcome: eval-v3 team 8/12, single 10/12; eval-v4
  team 7/12, single 8/12.

- **The team hedged on four incidents in eval-v4.** INC-04, 05, 07 and 10 each had a blocking
  objection and no Critic approval at the round limit, so the report said inconclusive with
  confidence 0.25 to 0.35, although the top guess was right. The team's 11/12 overstates what
  it would have told an on-call engineer. It also used six times the tokens of the single
  agent, and its confidence was less well calibrated (Brier 0.324 against 0.248).
- **eval-v4 is not independent of eval-v3.** The agent-to-agent pattern and the prompt changes
  were made after seeing eval-v3, and were tried on INC-05 before the full run. They are
  generic, and the leakage test still passes. The company background describes the healthy
  system only.
- **The single agent moved from 11/12 to 8/12 between eval-v3 and eval-v4** with only its prompt
  wording and the background changed. This shows how noisy one run per incident is.
- **The mailbox is tight.** It refused 117 messages over the 12 team runs in eval-v4 (about ten
  per run), so some agents could not ask what they wanted to.

- **eval-v2 is not independent.** The system was improved after seeing results on six of its
  incidents (PG-1, CX-2, PG-5, OTT-4, EC-1, FD-2). All changes were generic, and a test
  forbids service names in fixed prompt text.
- **The eval-v3 stories were written before any run on them.** The verdict loop was designed
  from the eval-v2 finding and not tuned on eval-v3.
- **One eval-v3 incident was corrected after its first run.** INC-02's cache evicted keys while
  its hit ratio stayed healthy. The team's Critic rightly refused to accept that. The data
  was fixed (a regression test covers it), and both systems were re-run on INC-02 only.
- **Synthetic data is only partly consistent.** A story moves the metrics it names; other
  metrics of the faulty service can stay healthy (for example a service's own error rate
  while its callers fail). This can make a careful Critic hesitate (INC-12). It affects both
  systems.
- **The scorer needs the exact service and category.** Answers with the right service and
  mechanism but a neighbouring category count as wrong:
  - eval-v4, team: INC-06 (dependency failure vs bad deploy);
  - eval-v4, single agent: INC-02 (cache failure vs resource exhaustion), INC-06 (as above) and
    INC-12 (resource exhaustion vs dependency failure);
  - eval-v3, team: INC-06 (dependency failure vs bad deploy) and INC-12 (resource exhaustion
    vs dependency failure);
  - eval-v2, single agent: OTT-2.

  For third-party failures, naming the vendor instead of our client service counts as wrong
  (eval-v2: OTT-4 and FD-2, both systems).
- **One single-agent miss in eval-v3 (INC-04) was a correct diagnosis.** Its final report
  failed schema validation three times, so the fallback inconclusive report was scored.
- **In eval-v3 the team's Critic missed the inconclusive-by-design case.** In INC-11 it
  approved a guess between two simultaneous changes. The guess was the true cause, but the
  evidence could not separate them. In eval-v4 both systems said inconclusive there.
- **Each incident was run once per system.** `claude -p` has no temperature setting, so reruns
  differ. Cached replies make a recorded run reproducible.

## Known gaps in the system

- The inconclusive check is structural: it proves the decisive evidence is unreachable or
  withheld, not that no clever agent could guess the cause. In FD-3 the release record stays
  visible, so a bold guess can name the true cause.
- Metric results include every point (sampled to every 5th in prompts); large windows cost
  tokens.
- Row-level security is defence in depth; the hard boundary is that the MCP server exposes no
  SQL. A per-run login role would be stronger.
- The concurrency tests use threads sharing one SQLite file, not separate processes.
- The Critic cannot yet close its own blocking objection unless it lists it under resolved;
  an objection left open runs to the round limit and the report hedges (see eval-v4 above).
- The frontend shows recorded runs and can rerun one case at a time, as a separate worker
  under a new label, so official results never change. It has no login. The server listens on
  127.0.0.1, and for the review period it is also shared publicly through Tailscale Funnel, with
  reruns on, so anyone with the link can start one (one at a time). A rerun has no daily cap, so
  a "fresh" rerun spends quota every time. `--no-rerun` gives a read-only copy.
- Reruns and eval-v4 give the agents a general company background
  (`configs/company_context.yaml`, one for each of the three companies). Eval-v2 and eval-v3
  did not have it. It describes the healthy system only; root causes and the case stories never
  reach the agents.
- The one-command setup (`scripts/setup.ps1`: docker compose, random passwords, all 37
  incidents) was run end to end on Windows on 2026-11-08. `make setup` (Linux/macOS) runs
  the same steps but was not run. The recorded evaluation runs used the demo database in
  the test container (`scripts/demo_db_in_docker.ps1`), built with the same init scripts.
