// Choosing between the official run and reruns, and starting a rerun.

function chosenRun() {
  return new URLSearchParams(window.location.search).get("run");
}

function showRun(label) {
  const url = "/incident/" + incidentId() + (label ? "?run=" + encodeURIComponent(label) : "");
  window.location.href = url;
}

function runName(label) {
  // "Official evaluation · haiku" or "Rerun 11/8/2026, 10:20 PM · sonnet"
  for (const option of DATA.available_runs || []) {
    if (option.label !== label) continue;
    let name = option.official ? "Official evaluation" : "Rerun " + new Date(option.started_at).toLocaleString();
    if (option.model) name += " · " + option.model;
    if (option.pattern === "exchange") name += " · agents talking";
    return name;
  }
  return label || "";
}

function renderRunChoices() {
  // "Newest" (the default) shows each system's latest result; the others show one run.
  const box = document.getElementById("run-choices");
  const choices = [{ label: null, name: "Newest result" }];
  for (const option of DATA.available_runs || []) choices.push({ label: option.label, name: runName(option.label) });
  if (choices.length === 2) {   // never rerun: nothing to choose, so no empty "Showing:" line
    box.parentElement.classList.add("hidden");
    return;
  }
  for (const choice of choices) {
    const active = choice.label === (DATA.label || null);
    box.appendChild(el("button", { class: "chip " + (active ? "multi" : ""), onclick: function () {
      showRun(choice.label);
    } }, [choice.name]));
  }
}

function progressLine(row) {
  const done = row.status !== "running";
  const verdict = row.correct === null || row.correct === undefined ? "" : (row.correct ? " · ✓ right" : " · ✗ wrong");
  return el("div", { class: "item" }, [
    el("strong", { text: SYSTEM_NAMES[row.system] }), " · ",
    (done ? row.status.replace("_", " ") : "running") + " · " + row.llm_calls + " LLM calls · " +
    row.tool_calls + " tool attempts · " + row.elapsed_s + " s" + verdict,
  ]);
}

async function pollRerun() {
  const status = await api("/api/rerun");
  const box = document.getElementById("rerun-progress");
  const message = document.getElementById("rerun-message");
  box.innerHTML = "";
  if (status.state === "disabled") {
    // reruns are off (the public, read-only copy): no button at all
    document.getElementById("rerun-open").classList.add("hidden");
    return;
  }
  if (status.state === "idle" || status.incident_id !== incidentId()) {
    return;
  }
  document.getElementById("rerun-panel").classList.remove("hidden");
  for (const row of status.progress || []) box.appendChild(progressLine(row));
  followConversation(status);
  if (status.state === "running") {
    message.textContent = "Running (" + status.model + ", " + status.mode + "), " + status.elapsed_s + " s so far…";
    setTimeout(pollRerun, 3000);
  } else if (status.state === "finished") {
    message.textContent = "Finished. ";
    message.appendChild(el("button", { class: "link", onclick: function () { showRun(status.label); } }, ["Show the new run"]));
  } else {
    message.textContent = "The rerun failed.";
    box.appendChild(el("pre", { class: "mono small", text: status.log_tail || "" }));
  }
}

function setupRerun() {
  document.getElementById("rerun-open").addEventListener("click", function () {
    document.getElementById("rerun-panel").classList.toggle("hidden");
  });
  document.getElementById("rerun-start").addEventListener("click", async function () {
    const systems = [];
    if (document.getElementById("rr-multi").checked) systems.push("multi_agent");
    if (document.getElementById("rr-single").checked) systems.push("single_agent");
    const mode = document.querySelector("input[name=rr-mode]:checked").value;
    const model = document.querySelector("input[name=rr-model]:checked").value;
    const message = document.getElementById("rerun-message");
    const response = await fetch("/api/incidents/" + incidentId() + "/rerun", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ systems: systems, mode: mode, model: model }),
    });
    const body = await response.json();
    if (!response.ok) {
      message.textContent = body.error || "Could not start the rerun.";
      return;
    }
    message.textContent = "Started.";
    pollRerun();
  });
  pollRerun();
}
