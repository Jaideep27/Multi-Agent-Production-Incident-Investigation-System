// The history of a run, for the team or the single agent: opened with a button, and followed
// live during a rerun. History only grows, so new lines are appended and anything already
// opened stays open.

const DIRECT_KINDS = ["finding", "question", "challenge", "answer"];
const KIND_LABELS = {
  task: "gave a task", facts: "wrote to the blackboard", tools: "used tools",
  ranking: "ranked the causes", verdict: "gave a verdict", report: "wrote the report",
  finding: "shared a finding", question: "asked", challenge: "challenged", answer: "answered",
};
const NON_AGENT_NAMES = { blackboard: "Blackboard", tools: "Tools", team: "Team", notes: "Its notes", report: "Final report" };
const HISTORY_SYSTEMS = ["multi_agent", "single_agent"];

let historyRuns = {};                // system -> run ID being shown
let historySystem = "multi_agent";
let historyPickedByUser = false;
let conversationHistory = [];
let conversationShown = 0;
let conversationDirectOnly = false;
let conversationClosedByUser = false;
let conversationFollowing = false;   // a rerun was seen running while this page was open
let conversationLastStep = null;     // the single agent's history is grouped by step

function speakerName(name) {
  return NON_AGENT_NAMES[name] || laneName(name);
}

function kindLabel(entry) {
  if (entry.intent === "facts" && entry.recipient === "notes") return "wrote notes";
  return KIND_LABELS[entry.intent] || entry.intent;
}

function toolLines(text) {
  // Each line reads: tool {"arguments"} -> result
  const box = el("div", { class: "tool-rows" });
  for (const line of text.split("\n")) {
    const match = line.match(/^(\S+) (\{.*\}) -> (.*)$/);
    if (!match) {
      box.appendChild(el("div", { class: "talk-text small", text: line }));
      continue;
    }
    let args = {};
    try { args = JSON.parse(match[2]); } catch (error) { args = {}; }
    box.appendChild(toolRow(match[1], args, match[3] === "requested" ? null : match[3]));
  }
  return box;
}

function factLines(entry) {
  // Each line reads: [fact] statement, or [assumption] statement
  const lines = entry.text.split("\n");
  const list = el("div", { class: "fact-rows" });
  for (const line of lines) {
    const match = line.match(/^\[(fact|assumption)\] (.*)$/);
    list.appendChild(match ? factRow(match[1], match[2], []) : el("div", { text: line }));
  }
  const word = entry.recipient === "notes" ? "note" : "finding";
  return el("details", null, [el("summary", { text: plural(lines.length, word) + " recorded" }), list]);
}

function rankingLines(text) {
  const list = el("div", { class: "rank-rows" });
  for (const line of text.split("\n")) {
    const match = line.match(/^(\d+)\. (.+?) \/ (.+?) \(confidence ([\d.]+)\): (.*)$/);
    list.appendChild(match ? rankRow(match[1], match[2], match[3], match[4], match[5]) : el("div", { text: line }));
  }
  return list;
}

function verdictLines(text) {
  const lines = text.split("\n");
  const approved = /^Approved/.test(lines[0]);
  const box = el("div", { class: "fact-rows" }, [el("div", null, [chip(approved ? "✓ Approved" : "Not approved yet", approved ? "ok" : "warn")])]);
  for (let i = 1; i < lines.length; i++) {
    const match = lines[i].match(/^(Blocking|Minor) objection: (.*)$/);
    box.appendChild(match ? objectionRow(match[1] === "Blocking", match[2]) : el("div", { text: lines[i] }));
  }
  return box;
}

function reportLines(text) {
  const split = text.indexOf(": ");
  const outcome = split > 0 ? text.slice(0, split) : "";
  const summary = split > 0 ? text.slice(split + 2) : text;
  return el("div", null, [
    outcome ? chip(outcome.charAt(0).toUpperCase() + outcome.slice(1), outcome === "inconclusive" ? "warn" : "ok") : null,
    el("div", { class: "talk-text", style: "margin-top:6px", text: summary }),
  ]);
}

function entryBody(entry) {
  if (entry.intent === "tools") return toolLines(entry.text);
  if (entry.intent === "facts") return factLines(entry);
  if (entry.intent === "ranking") return rankingLines(entry.text);
  if (entry.intent === "verdict") return verdictLines(entry.text);
  if (entry.intent === "report") return reportLines(entry.text);
  return el("div", { class: "talk-text", text: entry.text });
}

function conversationEntry(entry) {
  const direct = DIRECT_KINDS.indexOf(entry.intent) >= 0;
  const row = el("div", { class: "talk " + (direct ? "direct " + entry.intent : "") });
  row.appendChild(el("div", { class: "talk-head" }, [
    el("div", null, [el("strong", { text: speakerName(entry.sender) }), el("span", { class: "muted", text: " → " }), el("strong", { text: speakerName(entry.recipient) })]),
    el("span", { class: "talk-kind", text: kindLabel(entry) }),
  ]));
  row.appendChild(entryBody(entry));
  const ids = evidenceChips(entry.evidence_ids);
  if (ids) row.appendChild(ids);
  return row;
}

function showsInFeed(entry) {
  return !conversationDirectOnly || DIRECT_KINDS.indexOf(entry.intent) >= 0;
}

function drawNewConversationLines(scrollToEnd) {
  const feed = document.getElementById("conversation-feed");
  const note = document.getElementById("conversation-note");
  if (note) note.remove();
  for (let i = conversationShown; i < conversationHistory.length; i++) {
    const entry = conversationHistory[i];
    if (!showsInFeed(entry)) continue;
    if (entry.sender === "investigator" && entry.round !== conversationLastStep) {
      conversationLastStep = entry.round;
      feed.appendChild(el("div", { class: "step-divider", text: "Step " + entry.round }));
    }
    feed.appendChild(conversationEntry(entry));
  }
  const added = conversationHistory.length > conversationShown;
  conversationShown = conversationHistory.length;
  if (added && scrollToEnd) feed.scrollTop = feed.scrollHeight;
}

function showConversationEmptyNote() {
  const feed = document.getElementById("conversation-feed");
  if (feed.children.length > 0) return;
  let note = "No history was recorded for this run: it is from before histories were kept. Press “Rerun this case” to see one.";
  if (conversationHistory.length > 0) note = "No agent messaged another directly in this run.";
  if (!historyRuns[historySystem]) note = "This system has not run yet.";
  feed.appendChild(el("p", { id: "conversation-note", class: "muted small", text: note }));
}

function redrawConversation() {
  document.getElementById("conversation-feed").innerHTML = "";
  conversationShown = 0;
  conversationLastStep = null;
  drawNewConversationLines(false);
  showConversationEmptyNote();
}

function countDirectMessages() {
  let direct = 0;
  for (const entry of conversationHistory) {
    if (DIRECT_KINDS.indexOf(entry.intent) >= 0) direct += 1;
  }
  return direct;
}

async function loadConversation(scrollToEnd) {
  const run = historyRuns[historySystem];
  if (!run) {
    showConversationEmptyNote();
    return;
  }
  const answer = await api("/api/runs/" + run + "/conversation");
  conversationHistory = answer.history;
  drawNewConversationLines(scrollToEnd);
  showConversationEmptyNote();
}

function openConversation(open) {
  document.getElementById("conversation-panel").classList.toggle("hidden", !open);
  document.getElementById("conversation-toggle").textContent = open ? "Hide agent history" : "Show agent history";
}

function setConversationFilter(directOnly) {
  conversationDirectOnly = directOnly;
  document.getElementById("conversation-all").className = "chip " + (directOnly ? "" : "multi");
  document.getElementById("conversation-direct").className = "chip " + (directOnly ? "multi" : "");
  redrawConversation();
}

function clearConversationFeed() {
  conversationHistory = [];
  conversationShown = 0;
  conversationLastStep = null;
  document.getElementById("conversation-feed").innerHTML = "";
}

async function showHistoryOf(system) {
  historySystem = system;
  for (const name of HISTORY_SYSTEMS) {
    document.getElementById("history-" + name).className = "chip " + (name === system ? "multi" : "");
  }
  // the single agent works alone, so there is no talk to filter
  document.getElementById("conversation-filters").classList.toggle("hidden", system !== "multi_agent");
  if (system !== "multi_agent") conversationDirectOnly = false;
  clearConversationFeed();
  await loadConversation(false);
}

function setupConversation() {
  for (const system of HISTORY_SYSTEMS) {
    const run = DATA.runs[system];
    historyRuns[system] = run ? run.run_id : null;
    document.getElementById("history-" + system).addEventListener("click", function () {
      historyPickedByUser = true;
      showHistoryOf(system);
    });
  }
  document.getElementById("conversation-toggle").addEventListener("click", async function () {
    const opening = document.getElementById("conversation-panel").classList.contains("hidden");
    conversationClosedByUser = !opening;
    openConversation(opening);
    if (opening) await showHistoryOf(historySystem);
  });
  document.getElementById("conversation-all").addEventListener("click", function () { setConversationFilter(false); });
  document.getElementById("conversation-direct").addEventListener("click", function () { setConversationFilter(true); });
}

async function updateRoutingLive(status) {
  // The diagrams follow the rerun too; a system that has not started yet shows as waiting.
  const seen = [];
  for (const row of status.progress || []) {
    if (!row.run_id) continue;
    seen.push(row.system);
    const answer = await api("/api/runs/" + row.run_id + "/conversation");
    drawRouting(row.system, answer.history, false, row.status === "running");
  }
  for (const system of status.systems || []) {
    if (seen.indexOf(system) < 0 && status.state === "running") drawRouting(system, [], false, true);
  }
}

function liveLine(active) {
  const name = SYSTEM_NAMES[active.system];
  if (active.system === "multi_agent") return "● Live · " + name + " · " + countDirectMessages() + " direct message(s) so far";
  return "● Live · " + name;
}

async function followConversation(status) {
  // Called by the rerun progress poll. A rerun that ended before this page opened is left
  // alone, so a run picked with the chips at the top keeps its own history.
  if (status.state === "running") conversationFollowing = true;
  if (!conversationFollowing) return;

  let active = null;
  let currentChanged = false;
  for (const row of status.progress || []) {
    if (!row.run_id) continue;
    if (historyRuns[row.system] !== row.run_id) {
      historyRuns[row.system] = row.run_id;
      if (row.system === historySystem) currentChanged = true;
    }
    if (row.status === "running") active = row;
  }
  if (currentChanged) clearConversationFeed();

  if (active && !conversationClosedByUser) openConversation(true);
  if (active && !historyPickedByUser && active.system !== historySystem) {
    await showHistoryOf(active.system);
  } else {
    await loadConversation(true);
  }
  await updateRoutingLive(status);
  const line = document.getElementById("conversation-state");
  line.textContent = active ? liveLine(active) : "Finished";
}
