| Metric | multi_agent | single_agent |
|---|---|---|
| Runs | 5 | 5 |
| Root-cause accuracy | 60% | 40% |
| Accuracy on identifiable incidents | 50% | 25% |
| Inconclusive incidents handled | 1/1 | 1/1 |
| Grounding (returned / cited IDs) | 100% | 100% |
| Decisive-evidence recall | 40% | 80% |
| Decoy-hit rate | 20% | 0% |
| Decoy evidence cited as support | 1 | 0 |
| Brier score (lower is better) | 0.239 | 0.301 |
| Mean confidence when right | 0.45 | 0.45 |
| Mean confidence when wrong | 0.38 | 0.53 |
| Tool calls per run | 40.0 | 34.6 |
| LLM calls per run | 29.4 | 16.6 |
| Input tokens per run | 294590 | 216988 |
| Output tokens per run | 87566 | 37676 |
| Latency per run (s) | 294 | 200 |

| Incident | True category | Expected | multi_agent | single_agent |
|---|---|---|---|---|
| CX-2 | resource_exhaustion | identifiable | wrong (0.40; budget_exhausted) | right (0.60; completed) |
| EC-1 | db_contention | identifiable | right (0.40; budget_exhausted) | wrong (0.50; completed) |
| FD-2 | rate_limiting | identifiable | wrong (0.35, decoy; budget_exhausted) | wrong (0.55; completed) |
| OTT-4 | dependency_failure | identifiable | right (0.45; budget_exhausted) | wrong (0.55; completed) |
| PG-5 | cert_expiry | inconclusive | right (0.50; budget_exhausted) | right (0.30; completed) |
