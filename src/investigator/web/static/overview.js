// Evaluation overview: how it works, the shared rules, the headline numbers and the test cases.

const NOISE_NAMES = {
  conflicting_sources: "Conflicting data",
  clock_skew: "Clock skew",
  log_gap: "Missing logs",
};

function renderLimits(limits) {
  const box = document.getElementById("limits");
  const items = [
    ["Model", limits.model],
    ["Tool calls", limits.max_tool_calls],
    ["Attempts per call", limits.tool_attempts],
    ["LLM calls", limits.max_llm_calls],
    ["Team rounds", limits.max_rounds],
    ["Single-agent steps", limits.max_baseline_steps],
  ];
  for (const item of items) {
    box.appendChild(el("span", null, [item[0] + " ", el("b", { text: String(item[1]) })]));
  }
}

function kpiTile(label, multiText, singleText) {
  return el("div", { class: "card kpi" }, [
    el("div", { class: "label", text: label }),
    el("div", { class: "pair" }, [
      el("div", { class: "multi" }, [el("div", { class: "value", text: multiText }), el("div", { class: "who", text: "Team" })]),
      el("div", { class: "single" }, [el("div", { class: "value", text: singleText }), el("div", { class: "who", text: "Single" })]),
    ]),
  ]);
}

function renderKpis(summary) {
  const m = summary.multi_agent || {};
  const s = summary.single_agent || {};
  const box = document.getElementById("kpis");
  box.appendChild(kpiTile("Final score", m.root_cause_right + "/" + m.runs, s.root_cause_right + "/" + s.runs));
  box.appendChild(kpiTile("Key evidence found", pct(m.decisive_recall), pct(s.decisive_recall)));
  box.appendChild(kpiTile("Tokens per run", num(m.mean_total_tokens / 1000) + "K", num(s.mean_total_tokens / 1000) + "K"));
  box.appendChild(kpiTile("Time per run", num(m.mean_latency_s / 60, 1) + " min", num(s.mean_latency_s / 60, 1) + " min"));
}

function traps(incident) {
  const box = el("div", { class: "chips" });
  const c = incident.conditions;
  if (incident.expected_outcome === "inconclusive") box.appendChild(chip("Inconclusive by design", "warn"));
  if (c.decoys > 0) box.appendChild(chip("Misleading evidence", "bad"));
  for (const noise of c.noise) {
    if (NOISE_NAMES[noise]) box.appendChild(chip(NOISE_NAMES[noise]));
  }
  if (c.tool_failures.length > 0) box.appendChild(chip("Tool failures", "warn"));
  return box;
}

function resultPill(name, run, kind) {
  // the newest result of this system (a rerun is marked as such)
  if (!run) return el("div", { class: "pill" }, [el("span", { class: "who", text: name }), el("span", { class: "muted", text: "not run" })]);
  const mark = run.correct ? el("span", { class: "mark ok", text: "✓" }) : el("span", { class: "mark bad", text: "✗" });
  const parts = [el("span", { class: "who", text: name }), mark];
  if (run.rerun) parts.push(el("span", { class: "muted small", text: "rerun" }));
  return el("div", { class: "pill " + kind }, parts);
}

function renderCaseList(incidents) {
  const list = document.getElementById("cases");
  for (const incident of incidents) {
    const row = el("a", { class: "case", href: "/incident/" + incident.incident_id }, [
      el("div", { class: "case-id", text: incident.plan_id }),
      el("div", { class: "case-main" }, [
        el("div", { class: "case-title", text: incident.summary }),
        el("div", { class: "case-meta" }, [
          el("span", { class: "muted small", text: incident.domain.replace(/_/g, " ") }),
          traps(incident),
        ]),
      ]),
      el("div", { class: "case-results" }, [
        resultPill("Team", (incident.newest || incident.runs).multi_agent, "multi"),
        resultPill("Single", (incident.newest || incident.runs).single_agent, "single"),
      ]),
    ]);
    list.appendChild(row);
  }
}

// ------------------------------------------------------------------ how it works

const TEAM_AGENTS = [
  ["Orchestrator", "Plans the investigation", "Reads the incident and the blackboard, then splits the work into tasks for the two specialists. Plans again if it gets a message."],
  ["Observability", "Logs and metrics", "Finds when the symptoms start, which service fails first and what the errors say. Tools: logs, metrics."],
  ["Dependency", "Changes and topology", "Checks deployments and config changes, who calls whom, and similar past incidents. Tools: change history, dependencies, past incidents."],
  ["Reasoning", "Compares causes", "Turns the findings into 2–4 competing root causes, ranked with a confidence and the facts for and against each."],
  ["Critic", "Challenges the answer", "Approves the top cause, or can block each cause once and send a follow-up check back to a specialist (the feedback loop)."],
  ["Reporting", "Writes the report", "Final report: ranked causes, evidence, confidence and recommended actions. May answer “inconclusive”."],
];

const TOOLS = [
  ["Logs", "query_logs", "Search log lines by service, level, text and time."],
  ["Metrics", "query_metrics", "Read a metric's time series, e.g. latency or error rate."],
  ["Deployments & changes", "get_change_history", "Deploys, config changes, feature flags; the diff on request."],
  ["Dependencies", "get_service_dependencies", "Who calls whom, with timeouts, retries, pool sizes."],
  ["Past incidents", "search_past_incidents", "Earlier incidents with similar symptoms."],
];

function glanceTile(value, label, note) {
  return el("div", { class: "card glance" }, [
    el("div", { class: "glance-value", text: value }),
    el("div", { class: "glance-label", text: label }),
    el("div", { class: "glance-note", text: note }),
  ]);
}

function agentRow(index, name, role, text, kind) {
  return el("div", { class: "agent-row" }, [
    el("div", { class: "agent-num " + kind, text: String(index) }),
    el("div", null, [
      el("div", null, [el("strong", { text: name }), el("span", { class: "muted small", text: " · " + role })]),
      el("div", { class: "small muted", text: text }),
    ]),
  ]);
}

function renderHowItWorks(caseCount, limits) {
  const glance = document.getElementById("at-a-glance");
  glance.appendChild(glanceTile(String(caseCount), "test cases", "incidents with a hidden answer key"));
  glance.appendChild(glanceTile(String(TOOLS.length), "tools", "shared by both systems"));
  glance.appendChild(glanceTile(String(TEAM_AGENTS.length), "agents in the team", "each with one job"));
  glance.appendChild(glanceTile("1", "single agent", "does every job alone"));

  const team = document.getElementById("team-agents");
  for (let i = 0; i < TEAM_AGENTS.length; i++) {
    team.appendChild(agentRow(i + 1, TEAM_AGENTS[i][0], TEAM_AGENTS[i][1], TEAM_AGENTS[i][2], "multi"));
  }
  team.appendChild(el("p", { class: "small", style: "margin:12px 0 0" }, [el("strong", { text: "How they talk: " }),
    "each agent posts structured records (tasks, facts with evidence IDs, ranked causes, objections) to a shared blackboard, and agents can also send each other a limited number of direct messages. Python decides who speaks next. The two specialists work in parallel."]));

  const single = document.getElementById("single-agent");
  single.appendChild(agentRow(1, "Investigator", "does everything", "Plans, queries all " + TOOLS.length + " tools, keeps notes, weighs the causes and writes the same kind of report, in one loop of up to " + limits.max_baseline_steps + " steps.", "single"));
  const differences = el("ul", { class: "diff-list" }, [
    el("li", null, ["Same model, same " + TOOLS.length + " tools, same limits (" + limits.max_tool_calls + " tool calls, " + limits.tool_attempts + " attempts per call)."]),
    el("li", null, ["No division of labour: one context holds everything."]),
    el("li", null, ["No critic: nobody challenges its answer before it reports."]),
    el("li", null, ["No hand-offs or follow-up tasks: it decides alone what to check next."]),
  ]);
  single.appendChild(el("p", { class: "small", style: "margin:12px 0 4px" }, [el("strong", { text: "What is different from the team:" })]));
  single.appendChild(differences);

  document.getElementById("tool-count").textContent = String(TOOLS.length);
  document.getElementById("case-count").textContent = String(caseCount);
  const tools = document.getElementById("tool-overview");
  for (const tool of TOOLS) {
    tools.appendChild(el("div", { class: "tool-chip" }, [
      el("div", null, [el("strong", { text: tool[0] })]),
      el("div", { class: "mono small muted", text: tool[1] }),
      el("div", { class: "small", style: "margin-top:4px", text: tool[2] }),
    ]));
  }
}

async function main() {
  await startPage();
  const data = await api("/api/evaluation");
  renderLimits(data.limits);
  renderKpis(data.summary);
  renderHowItWorks(data.incidents.length, data.limits);
  renderCaseList(data.incidents);
}

main();
