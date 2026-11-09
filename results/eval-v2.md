| Metric | multi_agent | single_agent |
|---|---|---|
| Runs | 12 | 12 |
| Root-cause accuracy | 67% | 67% |
| Accuracy on identifiable incidents | 60% | 60% |
| Inconclusive incidents handled | 2/2 | 2/2 |
| Grounding (returned / cited IDs) | 100% | 100% |
| Decisive-evidence recall | 88% | 83% |
| Decoy-hit rate | 8% | 8% |
| Decoy evidence cited as support | 2 | 1 |
| Brier score (lower is better) | 0.276 | 0.241 |
| Mean confidence when right | 0.49 | 0.69 |
| Mean confidence when wrong | 0.51 | 0.73 |
| Tool calls per run | 38.5 | 15.5 |
| Tool attempts incl. retries per run | 42.8 | 17.8 |
| LLM calls per run | 36.1 | 7.9 |
| Input tokens per run | 461234 | 130043 |
| Output tokens per run | 104048 | 19425 |
| Total tokens per run | 565282 | 149468 |
| Latency per run (s) | 351 | 109 |
| Latency p95 (s) | 434 | 284 |
| Blocking Critic objections | 21 | 0 |
| Tool calls the Critic asked for | 41 | 0 |
| Top hypothesis changed after a critique | 1 | 0 |
| Tasks that waited for another agent | 4 | 0 |

| Incident | True category | Expected | multi_agent | single_agent |
|---|---|---|---|---|
| CX-1 | capacity_scaling | identifiable | wrong (0.60; completed) | right (0.80; completed) |
| CX-2 | resource_exhaustion | identifiable | right (0.65; budget_exhausted) | right (0.80; completed) |
| CX-4 | db_contention | inconclusive | right (0.35; budget_exhausted) | right (0.45, decoy; completed) |
| EC-1 | db_contention | identifiable | right (0.40; budget_exhausted) | wrong (-; completed) |
| EC-3 | bad_deploy | identifiable | right (0.55; budget_exhausted) | right (0.85; completed) |
| FD-2 | rate_limiting | identifiable | wrong (0.70; budget_exhausted) | wrong (0.80; completed) |
| FD-5 | cert_expiry | identifiable | wrong (0.35, decoy; budget_exhausted) | right (0.65; completed) |
| OTT-2 | cache_failure | identifiable | right (0.45; budget_exhausted) | wrong (0.80; completed) |
| OTT-4 | dependency_failure | identifiable | wrong (0.40; budget_exhausted) | wrong (0.60; completed) |
| PG-1 | config_change | identifiable | right (0.60; completed) | right (0.85; completed) |
| PG-2 | lb_imbalance | identifiable | right (0.40; budget_exhausted) | right (0.80; completed) |
| PG-5 | cert_expiry | inconclusive | right (0.50; budget_exhausted) | right (0.35; completed) |
