// Shared helpers for the viewer pages. Plain DOM code, no framework.

const SYSTEM_NAMES = { multi_agent: "Multi-agent team", single_agent: "Single agent" };

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function systemColor(system) {
  return system === "multi_agent" ? cssVar("--multi") : cssVar("--single");
}

async function api(path) {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error("request failed: " + response.status);
  }
  return response.json();
}

// el("div", {class: "x"}, [child, "text"])
function el(tag, attrs, children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const key of Object.keys(attrs)) {
      const value = attrs[key];
      if (value === null || value === undefined || value === false) continue;
      if (key === "text") node.textContent = value;
      else if (key === "onclick") node.addEventListener("click", value);
      else node.setAttribute(key, value);
    }
  }
  if (children) {
    for (const child of children) {
      if (child === null || child === undefined) continue;
      if (typeof child === "string" || typeof child === "number") {
        node.appendChild(document.createTextNode(String(child)));
      } else {
        node.appendChild(child);
      }
    }
  }
  return node;
}

function chip(text, kind) {
  return el("span", { class: "chip " + (kind || "") }, [text]);
}

function pct(value) {
  if (value === null || value === undefined) return "-";
  return Math.round(value * 100) + "%";
}

function num(value, digits) {
  if (value === null || value === undefined) return "-";
  return Number(value).toLocaleString(undefined, {
    maximumFractionDigits: digits === undefined ? 0 : digits,
  });
}

function utcTime(ms) {
  const date = new Date(ms);
  return date.toISOString().slice(11, 16);
}

function statusKind(status) {
  if (status === "ok" || status === "completed") return "ok";
  if (status === "timeout" || status === "error" || status === "failed") return "bad";
  if (status === "partial" || status === "invalid_output" || status === "budget_exhausted") return "warn";
  return "";
}

function verdictChip(run) {
  if (!run) return chip("not run");
  if (run.correct) return chip("right", "ok");
  return chip("wrong", "bad");
}

function renderHeader() {
  const header = document.querySelector("header.top");
  header.innerHTML = "";
  header.appendChild(el("a", { href: "/", class: "brand", text: "Incident Investigator" }));
  header.appendChild(el("nav", null, [
    el("a", { href: "/prompts", text: "Agent prompts" }),
  ]));
  header.appendChild(el("div", { class: "spacer" }));
  header.appendChild(el("span", { class: "user", text: "recorded runs · each case can be rerun" }));
}

async function startPage() {
  const info = await api("/api/info");
  renderHeader();
  return info;
}

function chartDefaults() {
  Chart.defaults.color = cssVar("--muted");
  Chart.defaults.borderColor = cssVar("--line");
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  Chart.defaults.maintainAspectRatio = false;
}

function styleTitle(heading) {
  // By position, so that no page or incident title needs its own rule.
  const words = heading.textContent.trim().split(/\s+/);
  if (!words[0]) return;
  heading.textContent = "";
  heading.appendChild(el("span", { class: "title-stamp", text: words[0] }));
  if (words.length === 1) return;
  const middle = words.slice(1, -1).join(" ");
  heading.appendChild(document.createTextNode(middle ? " " + middle + " " : " "));
  heading.appendChild(el("em", { text: words[words.length - 1] }));
}

document.addEventListener("DOMContentLoaded", function () {
  for (const heading of document.querySelectorAll("h1")) styleTitle(heading);
});
