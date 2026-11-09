// Section 2: each system's answer, its report, what it spent, and why the numbers are what they are.

function detailRows(run) {
  // the same log line can appear many times; once is enough here
  const rows = [];
  const shown = [];
  for (const item of run.cited || []) {
    if (shown.indexOf(item.text) >= 0) continue;
    shown.push(item.text);
    rows.push(el("div", { class: "cited-row", text: item.text }));
  }
  return rows;
}

function confidenceBar(value) {
  return el("div", { class: "rank-bar answer-bar" }, [el("span", { style: "width:" + Math.round(Number(value) * 100) + "%" })]);
}

function resultCard(system) {
  const run = DATA.runs[system];
  const kind = system === "multi_agent" ? "multi" : "single";
  const card = el("div", { class: "card result-card" });
  if (!run) {
    card.appendChild(el("div", { class: "who-line" }, [chip(SYSTEM_NAMES[system], kind)]));
    card.appendChild(el("p", { class: "muted", text: "No finished run." }));
    return card;
  }
  const report = run.report || {};
  const top = (report.hypotheses || [])[0];
  const verdict = run.score.correct
    ? el("span", { class: "big mark ok", text: "✓ Right" })
    : el("span", { class: "big mark bad", text: "✗ Wrong" });
  card.appendChild(el("div", { class: "who-line" }, [chip(SYSTEM_NAMES[system], kind), verdict]));
  card.appendChild(el("div", { class: "small muted result-source", text: "From: " + runName(run.label) }));

  if (top) {
    const answer = el("div", { class: "result-answer" }, [
      el("div", { class: "result-label", text: "Its answer" }),
      el("div", { class: "answer-line", text: top.component + " — " + top.category.replace(/_/g, " ") }),
      el("div", { class: "answer-confidence" }, [
        confidenceBar(top.confidence),
        el("span", { class: "small", text: num(top.confidence, 2) + " confidence" }),
        chip(report.outcome === "inconclusive" ? "Said inconclusive" : "Committed to this answer", report.outcome === "inconclusive" ? "warn" : ""),
      ]),
    ]);
    card.appendChild(answer);
    card.appendChild(el("div", { class: "result-label", text: "Its reasoning" }));
    card.appendChild(el("p", { class: "result-reason", text: firstSentence(top.mechanism) }));
  } else {
    card.appendChild(el("div", { class: "result-answer" }, [
      el("div", { class: "result-label", text: "Its answer" }),
      el("div", { class: "answer-line", text: "None" }),
    ]));
    card.appendChild(el("p", { class: "result-reason", text: report.summary || "It did not produce a valid report." }));
  }

  const cited = detailRows(run);
  if (cited.length) card.appendChild(el("details", { class: "result-more" }, [el("summary", { text: "Evidence it relied on" }), el("div", { class: "cited-list" }, cited)]));
  card.appendChild(fullReport(report));
  return card;
}

function reportBlock(box, title, rows) {
  if (!rows.length) return;
  const block = el("div", { class: "report-block" }, [el("div", { class: "result-label", text: title })]);
  for (const row of rows) block.appendChild(row);
  box.appendChild(block);
}

function fullReport(report) {
  // The parts of the final report the assignment asks for, one click away.
  const body = el("div", { class: "report-body" });
  if (report.summary) body.appendChild(el("p", { class: "result-reason", text: report.summary }));
  const causes = [];
  for (const h of report.hypotheses || []) {
    const support = (h.supporting_evidence_ids || []).length + " for, " + (h.contradicting_evidence_ids || []).length + " against";
    causes.push(rankRow(h.rank, h.component, h.category, h.confidence, "Evidence: " + support));
  }
  reportBlock(body, "Ranked causes", causes);
  const actions = [];
  for (const a of report.recommended_actions || []) {
    actions.push(el("div", { class: "fact-row" }, [chip((a.priority || "action").replace(/_/g, " ")), el("div", { text: a.action })]));
  }
  reportBlock(body, "Recommended actions", actions);
  const notes = [];
  for (const fact of (report.facts || []).slice(0, 4)) notes.push(factRow("fact", fact.statement, []));
  for (const a of (report.assumptions || []).slice(0, 3)) notes.push(factRow("assumption", typeof a === "string" ? a : (a.statement || ""), []));
  const total = (report.facts || []).length + " facts and " + (report.assumptions || []).length + " assumptions";
  reportBlock(body, "Facts and assumptions (" + total + ")", notes);
  const open = [];
  for (const item of report.unresolved || []) open.push(el("div", { class: "cited-row", text: item }));
  reportBlock(body, "Still unresolved", open);
  return el("details", { class: "result-more" }, [el("summary", { text: "Full report" }), body]);
}

function renderResults() {
  const box = document.getElementById("results");
  box.appendChild(resultCard("multi_agent"));
  box.appendChild(resultCard("single_agent"));
}

function compareRow(label, multiValue, singleValue, format) {
  const largest = Math.max(multiValue || 0, singleValue || 0, 0.0001);
  function line(value, system) {
    const share = Math.round(((value || 0) / largest) * 100);
    return el("div", { class: "line" }, [
      el("div", { class: "bar " + (system === "single_agent" ? "single" : "") }, [el("span", { style: "width:" + share + "%" })]),
      el("div", { text: value === null || value === undefined ? "-" : format(value) }),
    ]);
  }
  return el("div", { class: "compare-row" }, [
    el("div", { class: "label", text: label }),
    el("div", { class: "compare-bars" }, [line(multiValue, "multi_agent"), line(singleValue, "single_agent")]),
  ]);
}

// ------------------------------------------------------------------ why the numbers are what they are

function systemHeading(system, note) {
  return el("div", { class: "explain-head" }, [chip(SYSTEM_NAMES[system], system === "multi_agent" ? "multi" : "single"), el("span", { class: "small muted", text: note })]);
}

function keyEvidenceColumn(system) {
  const run = DATA.runs[system];
  const column = el("div", { class: "explain-column" });
  if (!run || !run.key_evidence) {
    column.appendChild(el("p", { class: "muted small", text: "Not available for this run." }));
    return column;
  }
  let found = 0;
  for (const item of run.key_evidence) if (item.found) found += 1;
  column.appendChild(systemHeading(system, found + " of " + run.key_evidence.length + " found"));
  for (const item of run.key_evidence) {
    column.appendChild(el("div", { class: "fact-row" }, [
      chip(item.found ? "✓ Found" : "✗ Missed", item.found ? "ok" : "bad"),
      el("div", { text: item.text }),
    ]));
  }
  return column;
}

function explainStat(label, value) {
  return el("div", { class: "explain-stat" }, [el("span", { class: "muted", text: label }), el("b", { text: value })]);
}

function confidenceColumn(system) {
  const run = DATA.runs[system];
  const column = el("div", { class: "explain-column" });
  const report = run ? run.report || {} : {};
  const causes = report.hypotheses || [];
  if (!causes.length) {
    column.appendChild(el("p", { class: "muted small", text: "This system named no cause, so it gave no confidence." }));
    return column;
  }
  const top = causes[0];
  column.appendChild(systemHeading(system, num(top.confidence, 2) + " confidence"));
  column.appendChild(el("p", { class: "explain-reason", text: top.confidence_reason || "This run did not record a stated reason. Reruns from now on do." }));
  column.appendChild(explainStat("Evidence for its answer", String((top.supporting_evidence_ids || []).length)));
  column.appendChild(explainStat("Evidence against it", String((top.contradicting_evidence_ids || []).length)));
  if (causes[1]) column.appendChild(explainStat("Next best cause", causes[1].component + " · " + num(causes[1].confidence, 2)));
  if (system === "multi_agent") column.appendChild(explainStat("Critic", (run.stats || {}).critic_approvals ? "Approved" : "Did not approve"));
  column.appendChild(explainStat("Questions left open", String((report.unresolved || []).length)));
  return column;
}

function explainPanel(columnFor, note) {
  const panel = el("div", { class: "explain-panel" }, [el("div", { class: "grid two" }, [columnFor("multi_agent"), columnFor("single_agent")])]);
  panel.appendChild(el("p", { class: "small muted explain-note", text: note }));
  return panel;
}

const EXPLANATIONS = {
  evidence: [keyEvidenceColumn, "Key evidence is the data the test marks as proving the cause (hidden from the agents). One counts as found when the final report cites it."],
  confidence: [confidenceColumn, "Confidence is each system's own judgement, from 0 to 1. No formula calculates it. The evaluation only checks afterwards whether high confidence went with right answers."],
};

function addExplainButtons(box) {
  const buttons = el("div", { class: "explain-buttons" });
  const holder = el("div");
  let open = null;
  function add(name, label) {
    const button = el("button", { class: "explain-button", text: label });
    button.addEventListener("click", function () {
      holder.innerHTML = "";
      for (const other of buttons.children) other.classList.remove("active");
      if (open === name) {
        open = null;
        return;
      }
      open = name;
      button.classList.add("active");
      holder.appendChild(explainPanel(EXPLANATIONS[name][0], EXPLANATIONS[name][1]));
    });
    buttons.appendChild(button);
  }
  add("evidence", "See the key evidence");
  add("confidence", "Why this confidence?");
  box.appendChild(buttons);
  box.appendChild(holder);
}

function renderCompare() {
  const box = document.getElementById("compare");
  const m = (DATA.runs.multi_agent || {}).score || {};
  const s = (DATA.runs.single_agent || {}).score || {};
  box.appendChild(el("div", { class: "legend" }, [
    el("span", null, [el("i", { style: "background:var(--multi)" }), "Multi-agent team"]),
    el("span", null, [el("i", { style: "background:var(--single)" }), "Single agent"]),
  ]));
  function tokens(score) { return score.input_tokens === undefined ? null : (score.input_tokens + score.output_tokens); }
  box.appendChild(compareRow("Tokens", tokens(m), tokens(s), function (v) { return num(v / 1000) + "K"; }));
  box.appendChild(compareRow("Tool calls", m.tool_calls, s.tool_calls, function (v) { return num(v); }));
  box.appendChild(compareRow("LLM calls", m.llm_calls, s.llm_calls, function (v) { return num(v); }));
  box.appendChild(compareRow("Time", m.latency_s, s.latency_s, function (v) { return num(v) + " s"; }));
  box.appendChild(compareRow("Key evidence found", m.decisive_recall, s.decisive_recall, function (v) { return pct(v); }));
  box.appendChild(compareRow("Confidence", m.confidence, s.confidence, function (v) { return num(v, 2); }));
  addExplainButtons(box);
}
