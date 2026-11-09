// Shared state and small text helpers for the case page.

const AGENT_LABELS = {
  orchestrator: "Orchestrator",
  observability: "Observability",
  dependency: "Dependency",
  reasoning: "Reasoning",
  critic: "Critic",
  reporting: "Reporting",
  investigator: "Investigator",
};

let DATA = null;

function incidentId() {
  const parts = window.location.pathname.split("/");
  return parts[parts.length - 1];
}

function shorten(text, length) {
  if (!text) return "";
  if (text.length <= length) return text;
  return text.slice(0, length - 1) + "…";
}

function bulletList(items) {
  const list = el("ul");
  for (const item of items) list.appendChild(el("li", null, item));
  return list;
}

function firstSentence(text) {
  if (!text) return "";
  const match = text.match(/^.*?[.;](\s|$)/);
  return match ? match[0].trim() : text;
}

function utcTimeSeconds(ms) {
  return new Date(ms).toISOString().slice(11, 19);
}

function renumberSections() {
  // Sections can be added or removed per case, so the numbers are set last.
  let number = 1;
  for (const badge of document.querySelectorAll(".step-section .num")) {
    badge.textContent = String(number);
    number += 1;
  }
}

function laneName(agent) {
  return AGENT_LABELS[agent] || agent;
}

function compactArgs(args) {
  const parts = [];
  for (const key of Object.keys(args || {})) {
    const value = args[key];
    if (value === null || value === undefined || value === "" || value === false) continue;
    if (Array.isArray(value) && value.length === 0) continue;
    parts.push(key + "=" + (Array.isArray(value) ? value.join(",") : String(value)));
  }
  return shorten(parts.join("  "), 140);
}
