# Technical note

## Key decisions

Six agents share a SQLite blackboard of typed records instead of passing prompts along:
- the Orchestrator writes tasks;
- the Observability and Dependency specialists, running in parallel, write facts that must
  cite evidence IDs the tools really returned;
- Reasoning writes 2–4 ranked hypotheses;
- the Critic writes a verdict.

Separating hypothesis generation from falsification is the point of the design. The Critic
either approves the top hypothesis, or blocks it once, naming the competing explanation and
the specialist who can settle it. Python turns that into a follow-up task.

On top of the blackboard, agents can also message each other through a capped mailbox (six
new messages per run, replies free). Python still decides who speaks next.

Routing, budgets, retries and grounding checks are plain Python; the model never decides when
to stop. Tools live behind a custom MCP server with deterministic failure injection. A
gateway validates, retries, deduplicates and budgets every call, for both systems alike.

Incidents come from a seeded generator, with the answer key in a schema the agents cannot
read.

## Trade-offs (12 story incidents, eval-v4)

| | Team | Single agent |
|---|---|---|
| Top guess right | 11/12 | 8/12 |
| Right and committed | 7/12 | 8/12 |
| Decisive evidence found | 100% | 83% |
| Tokens per run | 522K | 89K |
| Time per run | 422 s | 69 s |
| Brier score | 0.32 | 0.25 |

"Top guess right" includes reports that ended inconclusive. The team's top guess was better,
but on four identifiable incidents it hit the round limit without Critic approval and
reported inconclusive at low confidence. Counting committed answers, the systems are level,
and the team costs six times the tokens.

Against eval-v3 (blackboard only, no company background), the team's top guess rose from 9
to 11, but its inconclusive reports rose from 2 to 5 and tokens from 322K to 522K. The single
agent fell from 11 to 8 with only prompt wording and background changed, so much of this
movement is run-to-run noise.

## Limitations

- Twelve incidents from one model (Haiku), one run each. Differences of one or two
  incidents are noise.
- Synthetic stories move only the metrics they name, so some signals contradict. One
  incident was corrected after its first run (disclosed).
- `claude -p` runs as a subprocess, which adds process overhead, and has no temperature
  control. Replies are cached for replay.
- The caps (3 Critic reviews, 40 tool calls) limit long multi-hop chains. The mailbox refused
  about ten messages per run.
- The messaging pattern was tried on one of these incidents first.

## Improvements

- Let the Critic close its own objection when new facts answer it; skip the Critic loop
  when the first ranking is decisive.
- Score service and category separately.
- Generate correlated metrics per failure mode.
- Add a human approval gate before any remediation is executed.
