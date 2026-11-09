// Small display pieces shared by the agent history and the walk-through, so a tool call, a
// finding or a ranked cause looks the same wherever it appears.

const TOOL_NAMES = {
  query_logs: "Logs",
  query_metrics: "Metrics",
  get_change_history: "Change history",
  get_service_dependencies: "Dependencies",
  search_past_incidents: "Past incidents",
};
const HIDDEN_ARGS = ["start", "end", "limit", "cursor"];

function isEmptyArg(value) {
  if (value === null || value === undefined || value === "" || value === false) return true;
  return Array.isArray(value) && value.length === 0;
}

function clockOf(value) {
  const text = String(value || "");
  return text.length >= 16 ? text.slice(11, 16) : text;
}

function argChips(args) {
  const box = el("div", { class: "arg-list" });
  const given = args || {};
  if (given.start || given.end) {
    box.appendChild(el("span", { class: "arg", text: clockOf(given.start) + "–" + clockOf(given.end) }));
  }
  for (const key of Object.keys(given)) {
    if (HIDDEN_ARGS.indexOf(key) >= 0 || isEmptyArg(given[key])) continue;
    const value = given[key];
    let text = key.replace(/_/g, " ") + ": " + (Array.isArray(value) ? value.join(", ") : value);
    if (value === true) text = key.replace(/_/g, " ");
    box.appendChild(el("span", { class: "arg", text: text }));
  }
  return box;
}

function resultPill(result) {
  const found = String(result || "").match(/^ok, (\d+) items/);
  if (found) return chip(found[1] + (found[1] === "1" ? " item" : " items"), "ok");
  if (/^empty/.test(result)) return chip("nothing found", "warn");
  if (/^partial/.test(result)) return chip("partial result", "warn");
  if (/^not run/.test(result)) return chip("not run");
  return chip(shorten(result, 24), "bad");
}

function toolRow(tool, args, result, reason) {
  const parts = [
    el("div", { class: "tool-row-name" }, [el("strong", { text: TOOL_NAMES[tool] || tool })]),
    argChips(args),
    el("div", { class: "tool-row-result" }, [result ? resultPill(result) : ""]),
  ];
  const row = el("div", { class: "tool-row" }, parts);
  if (reason) row.appendChild(el("div", { class: "tool-row-why small muted", text: reason }));
  return row;
}

function evidenceChips(ids) {
  if (!ids || !ids.length) return null;
  const box = el("div", { class: "arg-list evidence-list" });
  for (const id of ids) box.appendChild(el("span", { class: "arg mono", text: id }));
  return box;
}

function factRow(kind, statement, ids) {
  const isFact = kind === "fact";
  return el("div", { class: "fact-row" }, [
    chip(isFact ? "Fact" : "Assumption", isFact ? "ok" : "warn"),
    el("div", null, [el("div", { text: statement }), evidenceChips(ids)]),
  ]);
}

function rankRow(rank, component, category, confidence, mechanism) {
  const share = Math.round(Number(confidence) * 100);
  return el("div", { class: "rank-row" }, [
    el("div", { class: "rank-number", text: String(rank) }),
    el("div", null, [
      el("div", null, [el("strong", { text: component }), el("span", { class: "muted", text: " · " + category.replace(/_/g, " ") })]),
      mechanism ? el("div", { class: "small muted", text: mechanism }) : null,
    ]),
    el("div", { class: "rank-confidence" }, [
      el("div", { class: "rank-bar" }, [el("span", { style: "width:" + share + "%" })]),
      el("div", { class: "small", text: num(confidence, 2) }),
    ]),
  ]);
}

function objectionRow(blocking, reason, extra) {
  return el("div", { class: "fact-row" }, [
    chip(blocking ? "Blocking" : "Minor", blocking ? "bad" : ""),
    el("div", null, [el("div", { text: reason }), extra ? el("div", { class: "small muted", text: extra }) : null]),
  ]);
}

function messageRow(person, intent, text, ids) {
  return el("div", { class: "message-row " + intent }, [
    el("div", { class: "small" }, [el("strong", { text: person }), el("span", { class: "muted", text: " · " + intent })]),
    el("div", { text: text }),
    evidenceChips(ids),
  ]);
}
