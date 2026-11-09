// The story of a case: a short summary first, and the full story one click away. It is the
// problem statement only, so it says nothing about how the systems did. It takes the place of
// "In short" and the first two sections, and only appears for cases that have a written story.

const HIDDEN_TAG = "(hidden from agents)";

function storyPart(box, number, title, hidden) {
  const heading = el("h3", null, [number + ". " + title]);
  if (hidden) heading.appendChild(el("span", { class: "hidden-tag", text: " " + HIDDEN_TAG }));
  const part = el("div", { class: "card story-part" }, [heading]);
  box.appendChild(part);
  return part;
}

function addParagraphs(parent, paragraphs) {
  for (const text of paragraphs) parent.appendChild(el("p", { text: text }));
}

function readable(value, unit) {
  // 8214 ms reads better as 8.2 seconds. 99.6% keeps its decimal: rounded it would say 100%.
  if (unit === "ms" && value >= 1000) return num(value / 1000, 1) + " seconds";
  if (unit === "ms" && value >= 100) return num(value / 1000, 2) + " seconds";
  if (unit === "ms") return num(value) + " ms";
  if (unit === "percent") return num(value, value < 10 || value >= 99 ? 1 : 0) + "%";
  return num(value) + " " + unit;
}

function clockTime(ms) {
  return utcTimeSeconds(ms) + " UTC";
}

function mono(text) {
  return el("span", { class: "mono", text: text });
}

function simpleTable(headers, rows) {
  const headRow = [];
  for (const header of headers) headRow.push(el("th", { text: header }));
  const bodyRows = [];
  for (const row of rows) {
    const cells = [];
    for (const cell of row) cells.push(el("td", null, [cell]));
    bodyRows.push(el("tr", null, cells));
  }
  return el("div", { class: "table-wrap" }, [
    el("table", { class: "story-table" }, [el("thead", null, [el("tr", null, headRow)]), el("tbody", null, bodyRows)]),
  ]);
}

function beforeDuringTable(items) {
  const rows = [];
  for (const item of items) {
    rows.push([item.label, readable(item.before, item.unit), el("strong", { text: readable(item.during, item.unit) })]);
  }
  return simpleTable(["What we measured", "Normally", "During the problem"], rows);
}

// ------------------------------------------------------------------ the map of services

function serviceMapText(brokenService) {
  // Who calls whom, from the database. With a broken service, it and everyone waiting on it
  // are coloured, so the path of the failure can be seen.
  const dependencies = DATA.incident.dependencies;
  const names = [];
  for (const edge of dependencies) {
    if (names.indexOf(edge.caller) < 0) names.push(edge.caller);
    if (names.indexOf(edge.callee) < 0) names.push(edge.callee);
  }
  const waiting = [];
  if (brokenService) {
    // keep adding callers of anything already affected, until nothing new turns up
    let grew = true;
    while (grew) {
      grew = false;
      for (const edge of dependencies) {
        const affected = edge.callee === brokenService || waiting.indexOf(edge.callee) >= 0;
        if (affected && waiting.indexOf(edge.caller) < 0) {
          waiting.push(edge.caller);
          grew = true;
        }
      }
    }
  }
  const lines = ["flowchart LR"];
  for (let i = 0; i < names.length; i++) lines.push('  n' + i + '["' + names[i] + '"]');
  for (const edge of dependencies) lines.push("  n" + names.indexOf(edge.caller) + " --> n" + names.indexOf(edge.callee));
  if (brokenService) {
    lines.push("  style n" + names.indexOf(brokenService) + " fill:" + cssVar("--bad-soft") + ",stroke:" + cssVar("--bad") + ",stroke-width:3px");
    for (const name of waiting) lines.push("  style n" + names.indexOf(name) + " fill:" + cssVar("--warn-soft") + ",stroke:" + cssVar("--warn"));
    for (let i = 0; i < dependencies.length; i++) {
      const callee = dependencies[i].callee;
      if (callee === brokenService || waiting.indexOf(callee) >= 0) lines.push("  linkStyle " + i + " stroke:" + cssVar("--bad") + ",stroke-width:2px");
    }
  }
  return lines.join("\n");
}

async function renderStoryMaps() {
  if (!DATA.story) return;
  await drawFlow("story-map", serviceMapText(null));
  await drawFlow("story-map-broken", serviceMapText(DATA.truth.root_component));
}

// ------------------------------------------------------------------ the summary

function summaryNumbers(story) {
  // only the metrics the story marks as worth a place in the summary
  const lines = [];
  for (const item of story.impact) {
    if (!item.short) continue;
    lines.push(el("div", null, [item.short + ": " + readable(item.before, item.unit) + " → ", el("strong", { text: readable(item.during, item.unit) })]));
  }
  return el("div", null, lines);
}

function renderSummary(box, story) {
  const text = story.summary;
  const rows = [
    ["The company", text.company, false],
    ["The problem", text.problem, false],
    ["What the numbers show", summaryNumbers(story), false],
    ["The false lead", text.false_lead, false],
    ["The real cause", text.cause, true],
    ["Why everyone felt it", text.why_everyone, true],
    ["The fix", text.fix, true],
  ];
  const card = el("div", { class: "card in-short" });
  for (const row of rows) {
    if (!row[1]) continue;
    const label = el("div", { class: "k" }, [row[0]]);
    if (row[2]) label.appendChild(el("div", { class: "hidden-tag", text: HIDDEN_TAG }));
    card.appendChild(el("div", { class: "short-row" }, [label, el("div", null, [row[1]])]));
  }
  card.appendChild(el("p", { class: "small muted", style: "margin:10px 0 0", text: "The company is invented for this test. The numbers are the actual test data." }));
  box.appendChild(card);
}

function addFullStoryButton(box, full) {
  const button = el("button", { class: "primary wide", text: "Read the full story", style: "margin:14px 0" });
  button.addEventListener("click", function () {
    const opening = full.classList.contains("hidden");
    full.classList.toggle("hidden");
    button.textContent = opening ? "Hide the full story" : "Read the full story";
  });
  box.appendChild(button);
}

// ------------------------------------------------------------------ the full story

function renderServicesPart(box, company) {
  const part = storyPart(box, 1, "Available services");
  part.appendChild(el("div", { id: "story-map", class: "flowchart" }));
  const rows = [];
  for (const service of company.services) rows.push([mono(service.name), service.plain, service.role.replace(/_/g, " "), String(service.copies)]);
  part.appendChild(simpleTable(["Service", "Its job", "Type", "Copies"], rows));
  part.appendChild(el("h4", { text: "Who calls whom" }));
  const links = [];
  for (const edge of DATA.incident.dependencies) {
    const settings = [];
    for (const key of Object.keys(edge.config)) settings.push(key + ": " + edge.config[key]);
    links.push([mono(edge.caller), mono(edge.callee), mono(settings.join(", "))]);
  }
  part.appendChild(simpleTable(["Caller", "Calls", "Settings"], links));
}

function renderStoryPart(box, company) {
  const part = storyPart(box, 2, "The story");
  addParagraphs(part, [company.what_it_does, company.how_it_works]);
}

function renderObservedPart(box, story) {
  const part = storyPart(box, 3, "What users observed");
  part.appendChild(el("p", { class: "quote", text: "“" + DATA.incident.summary + "”" }));
  part.appendChild(el("p", { class: "small muted", text: "Reported by " + DATA.incident.reporter + " at " + clockTime(DATA.incident.reported_at) + "." }));
  addParagraphs(part, story.what_went_wrong);
  part.appendChild(beforeDuringTable(story.impact));
  part.appendChild(takeChart("metric-users", false));
}

function takeChart(canvasId, hidden) {
  // The chart's card is moved here from the metrics section; metrics.js still draws into it.
  const chart = document.getElementById(canvasId).closest(".card");
  chart.classList.remove("card");
  chart.classList.add("story-chart");
  if (hidden) chart.querySelector("h3").appendChild(el("span", { class: "hidden-tag", text: " " + HIDDEN_TAG }));
  chart.appendChild(el("p", { class: "small muted", style: "margin:6px 0 0", text: "A dashed line marks a recorded change." }));
  return chart;
}

function renderDataPart(box, story) {
  const part = storyPart(box, 4, "The data behind this case");
  part.appendChild(el("p", { class: "small muted", text: "Case " + DATA.incident.incident_id + " · data from " + utcTime(DATA.incident.window_start) + " to " + utcTime(DATA.incident.window_end) + " UTC on " + new Date(DATA.incident.window_start).toISOString().slice(0, 10) }));
  const rows = [];
  for (const entry of story.inventory) {
    rows.push([
      mono(entry.table),
      entry.holds,
      String(entry.rows),
      entry.reached ? entry.reached : el("span", { class: "hidden-tag", text: "Hidden from agents" }),
    ]);
  }
  part.appendChild(simpleTable(["Table", "What it holds", "Rows", "How agents reach it"], rows));
}

function evidenceCell(clue) {
  if (clue.kind === "change") {
    const lines = [el("div", { class: "small muted", text: clockTime(clue.ts) + " · " + clue.service + " · by " + clue.author }), el("div", { text: clue.summary })];
    for (const line of clue.diff) {
      lines.push(el("div", { class: "mono small", text: line.field + ": " + (line.before === null ? "(nothing)" : line.before) + " → " + line.after }));
    }
    return el("div", null, lines);
  }
  if (clue.kind === "log") {
    return el("div", null, [
      el("div", { class: "small muted", text: clockTime(clue.ts) + " · " + clue.service + " · " + clue.level + " · appears " + clue.repeats + " times" }),
      el("div", { class: "mono small", text: clue.message }),
    ]);
  }
  return el("div", null, [
    el("div", { class: "small muted", text: clue.service + " · " + clue.metric.replace(/_/g, " ") }),
    el("div", null, [readable(clue.before, clue.unit) + " → ", el("strong", { text: readable(clue.during, clue.unit) })]),
  ]);
}

function renderEvidencePart(box, story) {
  const part = storyPart(box, 5, "The evidence that matters");
  if (story.clues.length) {
    part.appendChild(el("p", { class: "small muted", text: "Agents can reach this evidence with their tools, but they are not told which pieces matter." }));
    const rows = [];
    for (const clue of story.clues) rows.push([el("strong", { text: clue.title }), evidenceCell(clue), clue.meaning]);
    part.appendChild(simpleTable(["Clue", "What the data shows", "What it means"], rows));
  }
  if (story.clues_note) part.appendChild(el("p", { text: story.clues_note }));
  if (story.missing_evidence.length) {
    const missing = [];
    for (const item of story.missing_evidence) missing.push([item.description, item.message ? el("span", { class: "mono", text: item.message }) : "A clear reading at the moment of the crash"]);
    part.appendChild(el("h4", { text: "Evidence removed on purpose" }));
    part.appendChild(simpleTable(["What is missing", "What it would have shown"], missing));
  }
  part.appendChild(takeChart("metric-key", true));
}

function signalCell(item) {
  const lines = [el("strong", { text: item.title })];
  if (item.past_incident) {
    const past = item.past_incident;
    lines.push(el("div", { class: "small muted", text: new Date(past.occurred_at).toISOString().slice(0, 10) + ": " + past.title }));
  } else if (item.text) {
    lines.push(el("div", { class: "small muted", text: item.text }));
  }
  return el("div", null, lines);
}

function realityCell(item) {
  const lines = [el("div", { text: item.why_wrong })];
  for (const check of item.checks) {
    lines.push(el("div", { class: "small muted", text: check.label + ": " + readable(check.before, check.unit) + " → " + readable(check.during, check.unit) }));
  }
  return el("div", null, lines);
}

function renderMisleadingPart(box, story) {
  const part = storyPart(box, 6, "Misleading signals", true);
  const rows = [];
  for (const item of story.misleading) rows.push([signalCell(item), item.why_tempting, realityCell(item)]);
  part.appendChild(simpleTable(["Signal", "Why it misleads", "What is really going on"], rows));
}

function renderCausePart(box, story) {
  const part = storyPart(box, 7, "The root cause", true);
  const hiddenCause = DATA.truth.root_component + " — " + DATA.truth.root_category.replace(/_/g, " ");
  if (DATA.truth.expected_outcome === "inconclusive") {
    part.appendChild(el("div", { class: "answer-line", text: "The right answer is: inconclusive" }));
    part.appendChild(el("p", { class: "small muted", text: "The test's hidden cause, which no investigator can see: " + hiddenCause }));
  } else {
    part.appendChild(el("div", { class: "answer-line", text: hiddenCause }));
  }
  part.appendChild(el("p", null, [el("strong", { text: story.answer.short })]));
  addParagraphs(part, story.answer.paragraphs);
  part.appendChild(el("div", { id: "story-map-broken", class: "flowchart" }));
  part.appendChild(el("p", { class: "small muted", text: "Red is where the fault started. Orange services were left waiting on it." }));
  part.appendChild(el("h4", { text: "Timeline" }));
  const rows = [];
  for (const event of story.timeline) rows.push([mono(utcTimeSeconds(event.ts)), event.text]);
  part.appendChild(simpleTable(["Time (UTC)", "What happened"], rows));
  part.appendChild(el("p", { class: "small", style: "margin-top:10px", text: story.answer.category_note }));
  part.appendChild(el("p", { class: "small muted", text: "The answer key, word for word: " + DATA.truth.mechanism }));
}

function renderFixPart(box, story) {
  const part = storyPart(box, 8, "The fix", true);
  const items = [];
  for (const fix of story.fixes) items.push([fix]);
  part.appendChild(bulletList(items));
  part.appendChild(el("p", { class: "small muted", text: "The test data ends before any fix, so this is advice and not something that was recorded." }));
}

function renderGlossaryPart(box, story) {
  const part = storyPart(box, 9, "Words used on this page");
  const rows = [];
  for (const entry of story.glossary) rows.push([el("strong", { text: entry.term }), entry.meaning]);
  part.appendChild(el("details", null, [el("summary", { text: "Show the meanings" }), simpleTable(["Word", "Meaning"], rows)]));
}

function renderFullStory() {
  const story = DATA.story;
  // the story replaces "In short" and the separate answer card
  document.getElementById("in-short").remove();
  document.getElementById("truth-card").closest("section").remove();
  document.getElementById("story-heading").textContent = "The case in short";

  const page = document.getElementById("story");
  page.classList.remove("card");
  renderSummary(page, story);
  const box = el("div", { id: "full-story", class: "hidden" });
  addFullStoryButton(page, box);
  page.appendChild(box);
  renderServicesPart(box, story.company);
  renderStoryPart(box, story.company);
  renderObservedPart(box, story);
  renderDataPart(box, story);
  renderEvidencePart(box, story);
  renderMisleadingPart(box, story);
  renderCausePart(box, story);
  renderFixPart(box, story);
  renderGlossaryPart(box, story);
  document.getElementById("metrics-section").remove();   // both charts now live in the story
}
