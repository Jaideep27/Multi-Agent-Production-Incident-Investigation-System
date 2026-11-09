// Top of the page, section 1 (the story) and section 2 (the real cause).

function renderBrief() {
  const incident = DATA.incident;
  document.getElementById("case-id").textContent = (incident.plan_id || incident.incident_id) + " · " +
    incident.domain.replace(/_/g, " ");
  document.getElementById("title").textContent = incident.summary;
  styleTitle(document.getElementById("title"));
}

function shortVerdict(system) {
  const run = DATA.runs[system];
  if (!run) return "not run";
  const top = ((run.report || {}).hypotheses || [])[0];
  return (run.score.correct ? "✓ right" : "✗ wrong") + " (said " + (top ? top.component : "nothing") + ")";
}

function renderInShort() {
  // The whole case in four lines, for someone seeing it for the first time.
  const box = document.getElementById("in-short");
  const truth = DATA.truth;
  const traps = trapSentences();
  const rows = [
    ["What users saw", DATA.incident.summary],
    ["What really caused it", truth ? truth.root_component + " — " + firstSentence(truth.mechanism) : "(shown once the runs finish)"],
    ["The trap", traps.length ? traps[0] + (traps.length > 1 ? " (+" + (traps.length - 1) + " more below)" : "") : "No planted trap: a clean case."],
    ["Who got it right", "Multi-agent team: " + shortVerdict("multi_agent") + " · Single agent: " + shortVerdict("single_agent")],
  ];
  box.appendChild(el("div", { class: "eyebrow", text: "In short" }));
  for (const row of rows) {
    box.appendChild(el("div", { class: "short-row" }, [el("div", { class: "k", text: row[0] }), el("div", { text: row[1] })]));
  }
}

function trapSentences() {
  // Every trap of this case, explained in one plain sentence.
  const sentences = [];
  const truth = DATA.truth;
  const c = DATA.conditions;
  if (truth && truth.expected_outcome === "inconclusive") {
    sentences.push("The evidence that would prove the cause is missing, so the honest answer is “inconclusive”.");
  }
  if (truth) {
    for (const decoy of truth.decoys) {
      const what = decoy.text;
      if (decoy.decoy_type === "red_herring_change") sentences.push("A change happened shortly before the failure but did not cause it: " + what + ".");
      else if (decoy.decoy_type === "lookalike_history") sentences.push("A " + what + " had the same symptoms but a different cause.");
      else if (decoy.decoy_type === "downstream_symptom") sentences.push("Another service shows errors only as a side effect: " + what + ".");
      else if (decoy.decoy_type === "unrelated_spike") sentences.push("A metric spikes near the failure for an unrelated reason: " + what + ".");
    }
  }
  if (c) {
    for (const noise of c.noise) {
      const p = noise.params || {};
      if (noise.noise_type === "clock_skew") sentences.push(p.service + "'s clock is off by " + Math.abs(p.seconds) + " seconds, so its log times are misleading.");
      else if (noise.noise_type === "conflicting_sources") sentences.push("Two monitoring sources disagree about " + p.service + " " + p.metric + ".");
      else if (noise.noise_type === "log_gap") sentences.push("Logs from " + p.service + " are missing for a few minutes.");
    }
    for (const tool of c.tool_failures) {
      const rules = Array.isArray(tool.rules) ? tool.rules[0] : tool.rules;
      if (rules.mode === "persistent") sentences.push("The " + tool.tool_name + " tool is down for the whole investigation.");
      else if (rules.mode === "partial") sentences.push("The " + tool.tool_name + " tool returns only part of its results.");
      else sentences.push("The " + tool.tool_name + " tool fails on the first try (" + (rules.status || "error") + "), so it has to be retried.");
    }
  }
  return sentences;
}

function renderStory() {
  const incident = DATA.incident;
  const box = document.getElementById("story");
  box.classList.add("story");
  box.appendChild(el("p", null, [el("strong", { text: "What users reported: " }), "“" + incident.summary + "” (reported by " + incident.reporter + ")."]));
  box.appendChild(el("p", null, [el("strong", { text: "The system: " }),
    incident.domain.replace(/_/g, " ") + " with " + incident.services.length + " services. The data covers " +
    utcTime(incident.window_start) + " to " + utcTime(incident.window_end) + " UTC."]));
  box.appendChild(el("p", null, [el("strong", { text: "The task: " }),
    "find the root cause using only five tools (logs, metrics, change history, service dependencies, past incidents). Both systems got exactly this brief."]));
  const traps = trapSentences();
  if (traps.length) {
    box.appendChild(el("p", { style: "margin-bottom:0" }, [el("strong", { text: "What makes it tricky:" })]));
    const list = el("ul", { class: "trap-list" });
    for (const sentence of traps) list.appendChild(el("li", { text: sentence }));
    box.appendChild(list);
  }
}

function renderTruthCard() {
  const box = document.getElementById("truth-card");
  const truth = DATA.truth;
  if (!truth) {
    box.appendChild(el("p", { class: "muted", text: "Shown once the runs have finished." }));
    return;
  }
  box.appendChild(el("div", { class: "answer-line", text: truth.root_component + " — " + truth.root_category.replace(/_/g, " ") }));
  box.appendChild(el("p", { class: "why", text: truth.mechanism }));
  if (truth.unidentifiable_reason) {
    box.appendChild(el("p", { class: "small muted", text: "Why the right answer is “inconclusive”: " + truth.unidentifiable_reason }));
  }
  if (truth.decisive_described.length) {
    box.appendChild(el("div", { class: "small", style: "margin-top:8px" }, [el("strong", { text: "The proof in the data:" })]));
    const items = [];
    for (const item of truth.decisive_described) items.push([shorten(item.text, 150)]);
    box.appendChild(bulletList(items));
  }
}
