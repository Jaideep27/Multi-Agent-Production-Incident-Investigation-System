// How each system reached its answer: a step-by-step walk-through of every agent turn (what it was asked, what it answered).
// Uses DATA and the helpers from incident.js; runs once the case has loaded.

const EXCHANGES = { multi_agent: null, single_agent: null };

const AGENT_JOBS = {
  orchestrator: "Plans the round: reads the brief and the blackboard and hands out tasks to the two specialists.",
  observability: "Investigates logs and metrics: asks for tool calls, reads the results, posts findings with evidence IDs.",
  dependency: "Investigates changes, service dependencies and past incidents: asks for tools, posts findings with evidence IDs.",
  reasoning: "Reads all findings and ranks 2–4 competing causes, each with a confidence and the facts for and against.",
  critic: "Checks the top cause: approves it, or blocks it once and sends a follow-up check to a specialist.",
  reporting: "Writes the final report from the blackboard: ranked causes, evidence, confidence, actions.",
  investigator: "The single agent: chooses tools, reads results, keeps notes and, when ready, writes the report.",
};

// ------------------------------------------------------------------ helpers

async function drawFlow(targetId, text) {
  const target = document.getElementById(targetId);
  try {
    const result = await mermaid.render(targetId + "-svg", text);
    target.innerHTML = result.svg;
  } catch (error) {
    target.innerHTML = "";
    target.appendChild(el("p", { class: "muted small", text: "The flowchart could not be drawn." }));
  }
}

// ------------------------------------------------------------------ the walk-through

const WALK = { system: null, index: 0 };

function walkSection(box, title, rows) {
  if (!rows.length) return;
  const section = el("div", { class: "walk-section" }, [el("div", { class: "walk-h", text: title })]);
  for (const row of rows) section.appendChild(row);
  box.appendChild(section);
}

function renderAnswer(parsed, inbox) {
  // What the agent received from colleagues and what it did, in readable form.
  const box = el("div");
  const received = [];
  for (const message of inbox || []) received.push(messageRow("From " + laneName(message.sender), message.intent, message.text, message.evidence_ids));
  walkSection(box, "Messages it received", received);
  if (!parsed) {
    box.appendChild(el("p", { class: "muted", text: "This reply could not be read, so the agent was asked to send it again." }));
    return box;
  }

  const tasks = [];
  for (let i = 0; i < (parsed.tasks || []).length; i++) {
    const task = parsed.tasks[i];
    tasks.push(el("div", { class: "fact-row" }, [chip(laneName(task.agent), "multi"),
      el("div", { text: task.goal + (task.after ? " (waits for task " + task.after + ")" : "") })]));
  }
  walkSection(box, "Tasks it handed out", tasks);
  if (parsed.rationale) box.appendChild(el("p", { class: "small muted", text: "Why: " + parsed.rationale }));

  const tools = [];
  for (const request of parsed.tool_requests || []) tools.push(toolRow(request.tool, request.args, null, request.reason));
  walkSection(box, "Tools it asked for", tools);

  const facts = [];
  for (const fact of parsed.facts || parsed.notes || []) facts.push(factRow(fact.kind, fact.statement, fact.evidence_ids));
  walkSection(box, "What it recorded", facts);

  if (parsed.hypotheses && !parsed.outcome) {
    const ranked = [];
    for (let i = 0; i < parsed.hypotheses.length; i++) {
      const cause = parsed.hypotheses[i];
      ranked.push(rankRow(i + 1, cause.component, cause.category, cause.confidence, cause.mechanism));
    }
    walkSection(box, "How it ranked the causes", ranked);
  }

  if (parsed.approved !== undefined) {
    const verdict = [el("div", null, [chip(parsed.approved ? "✓ Approved the top cause" : "Not approved yet", parsed.approved ? "ok" : "warn")])];
    for (const objection of parsed.objections || []) {
      let extra = objection.alternative ? "Competing explanation: " + objection.alternative : "";
      if (objection.follow_up_agent) extra += (extra ? " · " : "") + "Sent to " + laneName(objection.follow_up_agent);
      verdict.push(objectionRow(objection.blocking, objection.reason, extra));
    }
    walkSection(box, "Its verdict", verdict);
  }

  const sent = [];
  for (const message of parsed.messages || []) sent.push(messageRow("To " + laneName(message.to), message.intent, message.text, message.evidence_ids));
  walkSection(box, "Messages it sent", sent);

  const report = parsed.final_report || (parsed.outcome ? parsed : null);
  if (report) {
    walkSection(box, "The report", [el("div", null, [
      chip(report.outcome === "inconclusive" ? "Inconclusive" : "Root cause identified", report.outcome === "inconclusive" ? "warn" : "ok"),
      el("p", { style: "margin:8px 0 0", text: report.summary || "" }),
    ])]);
    const causes = [];
    for (const cause of report.hypotheses || []) causes.push(rankRow(cause.rank, cause.component, cause.category, cause.confidence, null));
    walkSection(box, "Ranked causes", causes);
    const actions = [];
    for (const action of report.recommended_actions || []) {
      actions.push(el("div", { class: "fact-row" }, [chip(action.priority || "Action"), el("div", { text: action.action })]));
    }
    walkSection(box, "Recommended actions", actions);
  }
  return box;
}

function countOf(items) {
  return (items || []).length;
}

function stepMeaning(exchange) {
  // One plain line for a step, from what the agent actually did in it.
  const parsed = exchange.parsed;
  if (!parsed || (exchange.status && exchange.status !== "ok")) return "Reply rejected, tried again";
  const report = parsed.final_report || (parsed.outcome ? parsed : null);
  if (report) return "Wrote the final report";
  if (parsed.tasks) return "Planned " + plural(parsed.tasks.length, "task");
  if (parsed.approved !== undefined) return parsed.approved ? "Approved the top cause" : "Challenged the top cause";
  if (parsed.hypotheses) return "Ranked " + plural(parsed.hypotheses.length, "cause");
  const parts = [];
  const tools = countOf(parsed.tool_requests);
  const facts = countOf(parsed.facts) + countOf(parsed.notes);
  const sent = countOf(parsed.messages);
  if (facts) parts.push("recorded " + plural(facts, parsed.notes ? "note" : "finding"));
  if (tools) parts.push("asked for " + plural(tools, "tool"));
  if (sent) parts.push("sent " + plural(sent, "message"));
  if (!parts.length) return "Finished its task";
  const line = parts.join(", ");
  return line.charAt(0).toUpperCase() + line.slice(1);
}

function drawRail() {
  const rail = document.getElementById("walk-rail");
  rail.innerHTML = "";
  const list = EXCHANGES[WALK.system] || [];
  for (let i = 0; i < list.length; i++) {
    const item = el("button", { class: "walk-rail-item" + (i === WALK.index ? " active" : ""), onclick: function () {
      WALK.index = i;
      showStep();
    } }, [
      el("span", { class: "walk-rail-number", text: String(i + 1) }),
      el("span", null, [el("b", { text: laneName(list[i].agent) }), el("span", { class: "walk-rail-meaning", text: stepMeaning(list[i]) })]),
    ]);
    rail.appendChild(item);
  }
  const current = rail.querySelector(".active");
  if (current) current.scrollIntoView({ block: "nearest" });
}

function showStep() {
  const list = EXCHANGES[WALK.system] || [];
  const total = list.length;
  const exchange = list[WALK.index];
  const body = document.getElementById("walk-body");
  body.innerHTML = "";
  document.getElementById("walk-count").textContent = total ? (WALK.index + 1) + " of " + total : "";
  document.getElementById("walk-bar").style.width = total ? Math.round(((WALK.index + 1) / total) * 100) + "%" : "0";
  document.getElementById("walk-prev").disabled = WALK.index === 0;
  document.getElementById("walk-next").disabled = WALK.index >= total - 1;
  if (!exchange) {
    document.getElementById("walk-step-title").textContent = "No recorded exchanges for this run";
    body.appendChild(el("p", { class: "muted", text: "This run's prompts and replies were not recorded." }));
    return;
  }
  const name = laneName(exchange.agent);
  document.getElementById("walk-step-title").textContent = "Step " + (WALK.index + 1) + " · " + name + ": " + stepMeaning(exchange);
  drawRail();
  body.appendChild(el("div", { class: "walk-job" }, [el("b", { text: "This agent's job: " }), AGENT_JOBS[exchange.agent] || ""]));
  if (exchange.status && exchange.status !== "ok") {
    body.appendChild(el("div", { class: "walk-retry small" }, [chip("Sent back", "warn"),
      " This reply failed a format check, so the agent tried again."]));
  }
  body.appendChild(renderAnswer(exchange.parsed, exchange.inbox));
  const more = exchange.prompt_length > exchange.prompt.length ? "\n… (" + (exchange.prompt_length - exchange.prompt.length) + " more characters)" : "";
  body.appendChild(el("details", { class: "raw" }, [el("summary", { text: "Exact prompt (" + exchange.prompt_length + " characters)" }),
    el("pre", { text: exchange.prompt + more })]));
  body.appendChild(el("details", { class: "raw" }, [el("summary", { text: "Exact reply" }), el("pre", { text: exchange.reply || "(no reply: the call failed)" })]));
}

async function loadExchanges(system) {
  if (EXCHANGES[system] !== null) return EXCHANGES[system];
  // each system's own run: with "Newest result" the two can come from different runs
  const shown = DATA.runs[system];
  const run = shown ? shown.label : chosenRun();
  const data = await api("/api/incidents/" + incidentId() + "/exchanges?system=" + system + (run ? "&run=" + encodeURIComponent(run) : ""));
  EXCHANGES[system] = data.exchanges;
  return data.exchanges;
}

async function openWalk(system) {
  WALK.system = system;
  WALK.index = 0;
  document.getElementById("walk-title").textContent = system === "multi_agent" ? "Multi-agent team · every agent turn" : "Single agent · every step";
  document.getElementById("walk").classList.remove("hidden");
  document.getElementById("walk-body").innerHTML = "<p class='muted'>Loading…</p>";
  await loadExchanges(system);
  showStep();
}

function setupWalk() {
  document.getElementById("walk-multi").addEventListener("click", function () { openWalk("multi_agent"); });
  document.getElementById("walk-single").addEventListener("click", function () { openWalk("single_agent"); });
  document.getElementById("walk-close").addEventListener("click", function () { document.getElementById("walk").classList.add("hidden"); });
  document.getElementById("walk-prev").addEventListener("click", function () { if (WALK.index > 0) { WALK.index -= 1; showStep(); } });
  document.getElementById("walk-next").addEventListener("click", function () {
    if (WALK.index < (EXCHANGES[WALK.system] || []).length - 1) { WALK.index += 1; showStep(); }
  });
  document.addEventListener("keydown", function (event) {
    if (document.getElementById("walk").classList.contains("hidden")) return;
    if (event.key === "ArrowRight") document.getElementById("walk-next").click();
    if (event.key === "ArrowLeft") document.getElementById("walk-prev").click();
    if (event.key === "Escape") document.getElementById("walk-close").click();
  });
}

// ------------------------------------------------------------------ start

async function afterCaseLoaded() {
  mermaid.initialize({ startOnLoad: false, theme: "base", themeVariables: { primaryColor: cssVar("--multi-soft"), primaryBorderColor: cssVar("--multi"), primaryTextColor: cssVar("--text"), lineColor: cssVar("--muted") }, flowchart: { htmlLabels: true, curve: "basis" }, securityLevel: "strict" });
  setupWalk();
  await drawRoutingDiagrams();   // routing.js
  // ?walk=multi_agent (or single_agent) opens the walk-through directly, e.g. for a demo link
  const walk = new URLSearchParams(window.location.search).get("walk");
  if (walk === "multi_agent" || walk === "single_agent") openWalk(walk);
}
