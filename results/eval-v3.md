| Metric | multi_agent | single_agent |
|---|---|---|
| Runs | 12 | 12 |
| Root-cause accuracy | 75% | 92% |
| Accuracy on identifiable incidents | 82% | 91% |
| Inconclusive incidents handled | 0/1 | 1/1 |
| Grounding (returned / cited IDs) | 100% | 100% |
| Decisive-evidence recall | 97% | 85% |
| Decoy-hit rate | 8% | 0% |
| Decoy evidence cited as support | 2 | 1 |
| Brier score (lower is better) | 0.194 | 0.089 |
| Mean confidence when right | 0.60 | 0.75 |
| Mean confidence when wrong | 0.43 | - |
| Tool calls per run | 22.6 | 12.2 |
| Tool attempts incl. retries per run | 24.9 | 13.5 |
| LLM calls per run | 24.8 | 6.8 |
| Input tokens per run | 255008 | 91903 |
| Output tokens per run | 66646 | 16026 |
| Total tokens per run | 321654 | 107928 |
| Latency per run (s) | 243 | 94 |
| Latency p95 (s) | 441 | 181 |
| Blocking Critic objections | 4 | 0 |
| Tool calls the Critic asked for | 9 | 0 |
| Top hypothesis changed after a critique | 0 | 0 |
| Tasks that waited for another agent | 7 | 0 |
| Runs where the Critic approved | 10 | 0 |
| Follow-up tasks routed by the Critic | 4 | 0 |
| Repeat blocking objections downgraded | 0 | 0 |

| Incident | True category | Expected | multi_agent | single_agent |
|---|---|---|---|---|
| INC-01 | bad_deploy | identifiable | right (0.85; completed) | right (0.90; completed) |
| INC-02 | resource_exhaustion | identifiable | right (0.45; completed) | right (0.80; completed) |
| INC-03 | config_change | identifiable | right (0.60; completed) | right (0.90; completed) |
| INC-04 | cert_expiry | identifiable | right (0.72; completed) | wrong (-; completed) |
| INC-05 | config_change | identifiable | right (0.75; completed) | right (0.90; completed) |
| INC-06 | bad_deploy | identifiable | wrong (0.55; completed) | right (0.75; completed) |
| INC-07 | config_change | identifiable | right (0.40; completed) | right (0.80; completed) |
| INC-08 | bad_deploy | identifiable | right (0.72; completed) | right (0.85; completed) |
| INC-09 | resource_exhaustion | identifiable | right (0.60; completed) | right (0.80; completed) |
| INC-10 | resource_exhaustion | identifiable | right (0.28; max_rounds) | right (0.60; completed) |
| INC-11 | bad_deploy | inconclusive | wrong (0.40; completed) | right (0.30; completed) |
| INC-12 | dependency_failure | identifiable | wrong (0.35, decoy; max_rounds) | right (0.70; completed) |
