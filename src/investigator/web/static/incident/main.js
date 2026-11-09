// Loads the case and draws every section. Loaded last, after the section files.

async function main() {
  await startPage();
  chartDefaults();
  const run = chosenRun();
  DATA = await api("/api/incidents/" + incidentId() + (run ? "?run=" + encodeURIComponent(run) : ""));
  renderBrief();            // summary.js: title
  renderRunChoices();       // rerun.js
  setupRerun();
  if (DATA.story) {
    renderFullStory();      // story.js: replaces "In short" and sections 1 and 2
  } else {
    renderInShort();        // summary.js
    renderStory();
    renderTruthCard();
  }
  renderResults();          // results.js: section 3
  renderCompare();
  setupConversation();      // conversation.js
  renumberSections();       // base.js
  await renderKeyMetrics(); // metrics.js: section 6 (fetches the two series)
  await afterCaseLoaded();  // walkthrough.js: section 4, drawn last because it is the slowest
  await renderStoryMaps();  // story.js: needs the diagram library set up by the line above
}

main();
