| Metric | multi_agent | single_agent |
|---|---|---|
| Runs | 12 | 12 |
| Root-cause accuracy | 92% | 67% |
| Right and committed to it | 58% | 67% |
| Accuracy on identifiable incidents | 91% | 64% |
| Inconclusive incidents handled | 1/1 | 1/1 |
| Grounding (returned / cited IDs) | 100% | 100% |
| Decisive-evidence recall | 100% | 83% |
| Decoy-hit rate | 0% | 8% |
| Decoy evidence cited as support | 1 | 2 |
| Brier score (lower is better) | 0.324 | 0.248 |
| Mean confidence when right | 0.44 | 0.75 |
| Mean confidence when wrong | 0.38 | 0.74 |
| Tool calls per run | 33.8 | 10.9 |
| Tool attempts incl. retries per run | 35.5 | 12.4 |
| LLM calls per run | 35.8 | 5.6 |
| Input tokens per run | 417412 | 75932 |
| Output tokens per run | 105001 | 13069 |
| Total tokens per run | 522413 | 89001 |
| Latency per run (s) | 422 | 69 |
| Latency p95 (s) | 715 | 157 |
| Blocking Critic objections | 14 | 0 |
| Tool calls the Critic asked for | 30 | 0 |
| Top hypothesis changed after a critique | 3 | 0 |
| Tasks that waited for another agent | 0 | 0 |
| Runs where the Critic approved | 7 | 0 |
| Follow-up tasks routed by the Critic | 14 | 0 |
| Repeat blocking objections downgraded | 0 | 0 |

| Incident | True category | Expected | multi_agent | single_agent |
|---|---|---|---|---|
| INC-01 | bad_deploy | identifiable | right (0.65; completed) | right (0.90; completed) |
| INC-02 | resource_exhaustion | identifiable | right (0.55; completed) | wrong (0.80; completed) |
| INC-03 | config_change | identifiable | right (0.68; completed) | right (0.90; completed) |
| INC-04 | cert_expiry | identifiable | right (0.35; max_rounds) | right (0.85; completed) |
| INC-05 | config_change | identifiable | right (0.25; max_rounds) | right (0.85; completed) |
| INC-06 | bad_deploy | identifiable | wrong (0.38; completed) | wrong (0.70; completed) |
| INC-07 | config_change | identifiable | right (0.25; max_rounds) | right (0.80; completed) |
| INC-08 | bad_deploy | identifiable | right (0.65; completed) | wrong (0.70; completed) |
| INC-09 | resource_exhaustion | identifiable | right (0.40; completed) | right (0.80; completed) |
| INC-10 | resource_exhaustion | identifiable | right (0.25; max_rounds) | right (0.60; completed) |
| INC-11 | bad_deploy | inconclusive | right (0.25; max_rounds) | right (0.30; completed) |
| INC-12 | dependency_failure | identifiable | right (0.60; completed) | wrong (0.75, decoy; completed) |
