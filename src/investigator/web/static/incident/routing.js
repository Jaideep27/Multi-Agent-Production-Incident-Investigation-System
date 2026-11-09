// The routing diagram: who spoke to whom, in order, drawn from what really happened in the run.
// It is redrawn from the run's history, so during a rerun it grows as the agents work. Every
// arrow and badge can be clicked: it highlights the agents involved and shows the detail.

const ROUTE_LANES = {
  multi_agent: ["orchestrator", "observability", "dependency", "reasoning", "critic", "reporting"],
  single_agent: ["investigator", "tools", "notes", "report"],
};
const ROUTE_LEGEND = [["task", "Task"], ["finding", "Finding"], ["question", "Question"], ["challenge", "Challenge"], ["answer", "Answer"]];
const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
const ROUTE_WIDTH = 980;
const ROUTE_HEADER = 50;
const ROUTE_ROW = 42;
const SPECIALIST_AGENTS = ["observability", "dependency"];
const TOOL_OWNER = {
  query_logs: "observability", query_metrics: "observability",
  get_change_history: "dependency", get_service_dependencies: "dependency", search_past_incidents: "dependency",
};

const ROUTE_STATE = { multi_agent: { history: [], selected: null }, single_agent: { history: [], selected: null } };

function svgEl(tag, attrs, children) {
  const node = document.createElementNS(SVG_NAMESPACE, tag);
  for (const key of Object.keys(attrs || {})) node.setAttribute(key, attrs[key]);
  for (const child of children || []) {
    if (child) node.appendChild(child);
  }
  return node;
}

function svgText(x, y, text, className) {
  const node = svgEl("text", { x: x, y: y, class: className, "text-anchor": "middle" });
  node.textContent = text;
  return node;
}

function linesIn(text) {
  return text ? text.split("\n").length : 0;
}

function plural(count, word) {
  return count + " " + word + (count === 1 ? "" : "s");
}

function arrow(index, from, to, kind, label) {
  return { type: "arrow", index: index, from: from, to: to, kind: kind, label: label };
}

function pill(index, lane, kind, label) {
  return { type: "pill", index: index, lane: lane, kind: kind, label: label };
}

function topCauseOf(rankingText) {
  const match = String(rankingText || "").match(/^1\. (.+?) \/ .+? \(confidence ([\d.]+)\)/);
  return match ? match[1] + " " + num(match[2], 2) : "";
}

function reportWords(text) {
  return /^inconclusive/.test(text) ? "inconclusive" : "root cause found";
}

function singleRouteItems(entry, index) {
  if (entry.intent === "tools") return [arrow(index, "investigator", "tools", "task", plural(linesIn(entry.text), "tool call"))];
  if (entry.intent === "facts") return [arrow(index, "investigator", "notes", "finding", plural(linesIn(entry.text), "note"))];
  if (entry.intent === "report") return [arrow(index, "investigator", "report", "answer", reportWords(entry.text))];
  return [];
}

function teamRouteItems(entry, index) {
  const text = entry.text;
  if (entry.intent === "task") return [arrow(index, entry.sender, entry.recipient, "task", "task")];
  if (entry.intent === "tools") return [pill(index, entry.sender, "tools", plural(linesIn(text), "tool call"))];
  if (entry.intent === "facts") return [pill(index, entry.sender, "tools", plural(linesIn(text), "finding"))];
  if (entry.intent === "ranking") return [arrow(index, "reasoning", "critic", "finding", "top: " + topCauseOf(text))];
  if (entry.intent === "report") return [pill(index, "reporting", "answer", reportWords(text))];
  if (entry.intent === "verdict") {
    if (/^Approved/.test(text)) return [arrow(index, "critic", "reporting", "answer", "approved")];
    if (/Blocking objection/.test(text)) return [pill(index, "critic", "challenge", "blocked")];
    return [pill(index, "critic", "question", "not approved")];
  }
  return [arrow(index, entry.sender, entry.recipient, entry.intent, entry.intent)];
}

function routeRows(system, history) {
  // Work done side by side (parallel specialists) shares a row, so the diagram stays compact.
  const rows = [];
  for (let index = 0; index < history.length; index++) {
    const entry = history[index];
    const items = system === "single_agent" ? singleRouteItems(entry, index) : teamRouteItems(entry, index);
    for (const item of items) {
      const last = rows[rows.length - 1];
      let joined = false;
      if (item.type === "pill" && last) {
        joined = true;
        for (const other of last) {
          if (other.type !== "pill" || other.lane === item.lane) joined = false;
        }
      }
      if (joined) last.push(item);
      else rows.push([item]);
    }
  }
  return rows;
}

function lanesOf(item) {
  return item.type === "arrow" ? [item.from, item.to] : [item.lane];
}

function setLanesLit(svg, lanes, lit) {
  for (const lane of lanes) {
    for (const node of svg.querySelectorAll('[data-lane="' + lane + '"]')) node.classList.toggle("lane-on", lit);
  }
}

function routeItemGroup(system, item, parts) {
  const group = svgEl("g", { class: "route-item route-" + item.kind, "data-index": item.index, tabindex: 0, role: "button" }, parts);
  group.addEventListener("mouseenter", function () { setLanesLit(group.ownerSVGElement, lanesOf(item), true); });
  group.addEventListener("mouseleave", function () { refreshRouteHighlight(system); });
  group.addEventListener("click", function () { selectRouteItem(system, item); });
  group.addEventListener("keydown", function (event) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectRouteItem(system, item);
    }
  });
  return group;
}

function drawArrow(system, item, lanes, laneX, y) {
  const x1 = laneX(lanes.indexOf(item.from));
  const x2 = laneX(lanes.indexOf(item.to));
  const direction = x2 > x1 ? 1 : -1;
  const tip = x2 - direction * 2;
  const head = "M" + tip + " " + y + " l" + (-direction * 9) + " -5 l0 10 z";
  const middle = (x1 + x2) / 2;
  const width = item.label.length * 6.6 + 14;
  const left = Math.min(x1, x2);
  return routeItemGroup(system, item, [
    svgEl("rect", { x: left, y: y - 21, width: Math.abs(x2 - x1), height: 30, class: "route-hit" }),
    svgEl("line", { x1: x1, y1: y, x2: tip - direction * 6, y2: y, class: "route-line" }),
    svgEl("path", { d: head, class: "route-head" }),
    svgEl("rect", { x: middle - width / 2, y: y - 19, width: width, height: 16, rx: 4, class: "route-label-back" }),
    svgText(middle, y - 7, item.label, "route-label"),
  ]);
}

function drawPill(system, item, lanes, laneX, y) {
  const x = laneX(lanes.indexOf(item.lane));
  const width = item.label.length * 6.6 + 18;
  return routeItemGroup(system, item, [
    svgEl("rect", { x: x - width / 2, y: y - 12, width: width, height: 24, rx: 12, class: "route-pill" }),
    svgText(x, y + 4, item.label, "route-pill-text"),
  ]);
}

function routeDiagram(system, rows) {
  const lanes = ROUTE_LANES[system];
  const laneWidth = ROUTE_WIDTH / lanes.length;
  function laneX(index) { return laneWidth * (index + 0.5); }
  const height = ROUTE_HEADER + Math.max(rows.length, 1) * ROUTE_ROW + 10;
  const parts = [];
  for (let i = 0; i < lanes.length; i++) {
    const x = laneX(i);
    parts.push(svgEl("line", { x1: x, y1: ROUTE_HEADER - 12, x2: x, y2: height - 4, class: "route-lane", "data-lane": lanes[i] }));
    parts.push(svgEl("rect", { x: x - laneWidth / 2 + 6, y: 4, width: laneWidth - 12, height: 32, rx: 8, class: "route-header", "data-lane": lanes[i] }));
    parts.push(svgText(x, 25, speakerName(lanes[i]), "route-header-text"));
  }
  for (let r = 0; r < rows.length; r++) {
    const y = ROUTE_HEADER + r * ROUTE_ROW + ROUTE_ROW / 2 + 6;
    for (const item of rows[r]) {
      parts.push(item.type === "arrow" ? drawArrow(system, item, lanes, laneX, y) : drawPill(system, item, lanes, laneX, y));
    }
  }
  return svgEl("svg", { viewBox: "0 0 " + ROUTE_WIDTH + " " + height, class: "route-svg", "data-system": system }, parts);
}

// ------------------------------------------------------------------ clicking a step

function routeBox(system) {
  return document.getElementById(system === "multi_agent" ? "flow-multi" : "flow-single");
}

function selectedLanes(system) {
  const selected = ROUTE_STATE[system].selected;
  if (selected === null) return [];
  const entry = ROUTE_STATE[system].history[selected];
  const items = system === "single_agent" ? singleRouteItems(entry, selected) : teamRouteItems(entry, selected);
  let lanes = [];
  for (const item of items) lanes = lanes.concat(lanesOf(item));
  return lanes;
}

function refreshRouteHighlight(system) {
  const box = routeBox(system);
  const svg = box.querySelector(".route-svg");
  if (!svg) return;
  const selected = ROUTE_STATE[system].selected;
  svg.classList.toggle("has-selection", selected !== null);
  for (const node of svg.querySelectorAll(".route-item")) {
    node.classList.toggle("selected", selected !== null && Number(node.getAttribute("data-index")) === selected);
  }
  for (const node of svg.querySelectorAll(".lane-on")) node.classList.remove("lane-on");
  setLanesLit(svg, selectedLanes(system), true);
}

function showRouteDetail(system) {
  const holder = routeBox(system).querySelector(".route-detail");
  holder.innerHTML = "";
  const selected = ROUTE_STATE[system].selected;
  if (selected === null) {
    holder.appendChild(el("p", { class: "small muted", text: "Click any arrow or badge to see what happened in that step." }));
    return;
  }
  const row = conversationEntry(ROUTE_STATE[system].history[selected]);
  for (const details of row.querySelectorAll("details")) details.open = true;
  holder.appendChild(row);
}

function selectRouteItem(system, item) {
  const state = ROUTE_STATE[system];
  state.selected = state.selected === item.index ? null : item.index;
  refreshRouteHighlight(system);
  showRouteDetail(system);
}

function clearRouteSelection(system) {
  ROUTE_STATE[system].selected = null;
  refreshRouteHighlight(system);
  showRouteDetail(system);
}

function clearSelectionOnOutsideClick(event) {
  // Dragging over the text to copy it is not a request to clear, and neither is using a
  // control inside the open step.
  if (window.getSelection().toString()) return;
  if (event.target.closest(".route-detail summary, .route-detail a, .route-detail button")) return;
  const item = event.target.closest(".route-item");
  const clickedSystem = item ? item.ownerSVGElement.getAttribute("data-system") : null;
  for (const system of Object.keys(ROUTE_STATE)) {
    // a click on a step in this graph has already been handled by that step
    if (system !== clickedSystem && ROUTE_STATE[system].selected !== null) clearRouteSelection(system);
  }
}

document.addEventListener("click", clearSelectionOnOutsideClick);

// ------------------------------------------------------------------ drawing

function routeSummary(system, history) {
  let tools = 0;
  let facts = 0;
  let direct = 0;
  let tasks = 0;
  let reviews = 0;
  let steps = 0;
  for (const entry of history) {
    if (entry.intent === "tools") tools += linesIn(entry.text);
    if (entry.intent === "facts") facts += linesIn(entry.text);
    if (entry.intent === "task") tasks += 1;
    if (entry.intent === "verdict") reviews += 1;
    if (DIRECT_KINDS.indexOf(entry.intent) >= 0) direct += 1;
    if (entry.round > steps) steps = entry.round;
  }
  if (system === "single_agent") {
    const parts = [plural(steps, "step"), plural(tools, "tool call")];
    if (facts) parts.push(plural(facts, "note"));
    return parts.join(" · ");
  }
  return [plural(tasks, "task"), plural(direct, "direct message"), plural(tools, "tool call"), plural(reviews, "Critic review")].join(" · ");
}

function runOutcomeLine(run) {
  const truth = DATA.truth;
  const real = truth ? " · real cause: " + truth.root_component : "";
  return el("div", { class: "route-result" }, [chip((run.score.correct ? "✓ Right" : "✗ Wrong") + real, run.score.correct ? "ok" : "bad")]);
}

function drawRouting(system, history, showOutcome, live, rebuilt) {
  const box = routeBox(system);
  const state = ROUTE_STATE[system];
  // a selection survives a live redraw, but not a new run with different steps
  if (state.selected !== null) {
    const before = state.history[state.selected];
    const after = history[state.selected];
    if (!after || after.text !== before.text) state.selected = null;
  }
  state.history = history;
  box.innerHTML = "";
  const line = [];
  if (live) line.push(el("span", { class: "route-live", text: "● Live " }));
  line.push(history.length ? routeSummary(system, history) : "Waiting for the first step…");
  if (rebuilt) line.push(el("span", { class: "muted", text: " · rebuilt from an older run" }));
  box.appendChild(el("div", { class: "route-summary small" }, line));
  const graph = el("div", { class: "route-graph" }, [el("div", { class: "route-scroll" }, [routeDiagram(system, routeRows(system, history))])]);
  if (system === "multi_agent") {
    const legend = el("div", { class: "route-legend small" });
    for (const pair of ROUTE_LEGEND) legend.appendChild(el("span", { class: "route-key route-" + pair[0] }, [el("i"), pair[1]]));
    graph.appendChild(legend);
  }
  // beside the graph, so a click near the top of a tall diagram shows its detail in view
  box.appendChild(el("div", { class: "route-body" }, [graph, el("div", { class: "route-detail" })]));
  refreshRouteHighlight(system);
  showRouteDetail(system);
  const run = DATA.runs[system];
  if (showOutcome && run) box.appendChild(runOutcomeLine(run));
}

// ------------------------------------------------------------------ runs with no saved history

function loggedCallLine(call) {
  return call.tool + " " + JSON.stringify(call.args || {}) + " -> " + call.status + ", " + call.evidence_count + " items";
}

function historyFromBlackboard(run) {
  // Older team runs kept no history: the same picture, rebuilt from what they did store.
  const board = run.blackboard;
  const rounds = [];
  for (const task of board.tasks) if (rounds.indexOf(task.round) < 0) rounds.push(task.round);
  for (const item of board.hypotheses) if (rounds.indexOf(item.round) < 0) rounds.push(item.round);
  rounds.sort(function (a, b) { return a - b; });
  const history = [];
  function add(sender, recipient, intent, text, round) {
    history.push({ sender: sender, recipient: recipient, intent: intent, text: text, evidence_ids: [], round: round });
  }
  for (const round of rounds) {
    for (const task of board.tasks) if (task.round === round) add("orchestrator", task.agent, "task", task.goal, round);
    for (const agent of SPECIALIST_AGENTS) {
      if (round === rounds[0]) {
        // which round a tool call belonged to was not stored, so they are shown with the first
        const calls = [];
        for (const call of run.tool_calls || []) {
          if (call.attempt === 1 && TOOL_OWNER[call.tool] === agent) calls.push(loggedCallLine(call));
        }
        if (calls.length) add(agent, "tools", "tools", calls.join("\n"), round);
      }
      const statements = [];
      for (const fact of board.facts) if (fact.round === round && fact.author === agent) statements.push("[" + fact.kind + "] " + fact.statement);
      if (statements.length) add(agent, "blackboard", "facts", statements.join("\n"), round);
    }
    const ranked = [];
    for (const item of board.hypotheses) if (item.round === round) ranked.push(item);
    if (ranked.length) {
      ranked.sort(function (a, b) { return b.confidence - a.confidence; });
      const lines = [];
      for (let i = 0; i < ranked.length; i++) lines.push((i + 1) + ". " + ranked[i].component + " / " + ranked[i].category + " (confidence " + ranked[i].confidence + "): " + ranked[i].mechanism);
      add("reasoning", "blackboard", "ranking", lines.join("\n"), round);
    }
    const blocked = [];
    for (const objection of board.objections) if (objection.round === round && objection.blocking) blocked.push("Blocking objection: " + objection.reason);
    if (blocked.length) add("critic", "team", "verdict", "Not approved yet.\n" + blocked.join("\n"), round);
    else if (round === rounds[rounds.length - 1] && (run.stats || {}).critic_approvals) add("critic", "team", "verdict", "Approved the top answer.", round);
  }
  add("reporting", "team", "report", (run.report.outcome || "").replace(/_/g, " ") + ": " + (run.report.summary || ""), 0);
  return history;
}

function historyFromSteps(run) {
  // Older single-agent runs: rebuilt from the logged steps and their tool calls.
  const history = [];
  let step = 0;
  for (const logged of run.steps || []) {
    if (logged.status !== "ok") continue;
    step += 1;
    const calls = [];
    for (const call of logged.tool_calls) {
      if (call.attempt === 1) calls.push(loggedCallLine(call));
    }
    if (calls.length) history.push({ sender: "investigator", recipient: "tools", intent: "tools", text: calls.join("\n"), evidence_ids: [], round: step });
  }
  history.push({ sender: "investigator", recipient: "report", intent: "report", text: (run.report.outcome || "").replace(/_/g, " ") + ": " + (run.report.summary || ""), evidence_ids: [], round: step });
  return history;
}

async function savedHistory(system) {
  // Returns the run's history, and whether it had to be rebuilt from an older run's records.
  const run = DATA.runs[system];
  const saved = await api("/api/runs/" + run.run_id + "/conversation");
  if (saved.history.length) return { history: saved.history, rebuilt: false };
  if (system === "single_agent") return { history: historyFromSteps(run), rebuilt: true };
  return { history: run.blackboard ? historyFromBlackboard(run) : [], rebuilt: true };
}

async function drawRoutingDiagrams() {
  for (const system of ["multi_agent", "single_agent"]) {
    if (DATA.runs[system]) {
      const found = await savedHistory(system);
      drawRouting(system, found.history, true, false, found.rebuilt);
    } else {
      routeBox(system).innerHTML = "<p class='muted small'>Not run.</p>";
    }
  }
}
