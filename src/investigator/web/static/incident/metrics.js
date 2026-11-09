// Section 6: the metric that proves the cause and what users felt.

function sortedSeries() {
  const list = DATA.series.slice();
  list.sort(function (a, b) { return b.shift - a.shift; });
  return list;
}

const changeLines = {
  id: "changeLines",
  afterDatasetsDraw: function (chart) {
    const changes = chart.options.plugins.changeLines.changes || [];
    const x = chart.scales.x;
    const area = chart.chartArea;
    const ctx = chart.ctx;
    let drawn = 0;
    for (const change of changes) {
      if (change.ts < x.min || change.ts > x.max) continue;
      drawn += 1;
      // Alternate label heights so two changes close together stay readable.
      const labelY = area.top + (drawn % 2 === 1 ? 12 : 28);
      const position = x.getPixelForValue(change.ts);
      const isDecoy = DATA.truth && DATA.truth.decoys.some(function (d) { return d.evidence_id === change.evidence_id; });
      ctx.save();
      ctx.strokeStyle = isDecoy ? cssVar("--bad") : cssVar("--warn");
      ctx.setLineDash([5, 4]);
      ctx.beginPath();
      ctx.moveTo(position, area.top);
      ctx.lineTo(position, area.bottom);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = ctx.strokeStyle;
      ctx.font = "11px sans-serif";
      const text = change.service + " " + change.change_type.replace("_", " ") + (isDecoy ? " (decoy)" : "");
      ctx.fillText(text, Math.min(position + 4, area.right - ctx.measureText(text).width), labelY);
      ctx.restore();
    }
  },
};

function median(values) {
  const sorted = values.slice().sort(function (a, b) { return a - b; });
  return sorted.length ? sorted[Math.floor(sorted.length / 2)] : null;
}

function keySeriesId() {
  // The metric in the answer key's proof, if there is one; otherwise the biggest mover.
  if (DATA.truth) {
    for (const id of DATA.truth.decisive_evidence) if (id.indexOf("M-") === 0) return id;
  }
  const sorted = sortedSeries();
  return sorted.length ? sorted[0].evidence_id : null;
}

function userSeriesId(exclude) {
  // What users felt: the entry service's success-rate metric, else the next biggest mover.
  for (const series of DATA.series) {
    if (series.metric_name.indexOf("success") >= 0 && series.evidence_id !== exclude) return series.evidence_id;
  }
  for (const series of sortedSeries()) if (series.evidence_id !== exclude) return series.evidence_id;
  return null;
}

function describeSeries(series) {
  // One plain sentence: the usual level, and the new level from when it changed.
  const values = series.points.map(function (p) { return p[1]; });
  if (values.length < 5) return series.service + " " + series.metric_name;
  const early = values.slice(0, Math.max(1, Math.floor(values.length / 5)));
  const before = median(early);
  const after = median(values.slice(Math.floor(values.length * 2 / 3)));
  const name = series.service + " " + series.metric_name.replace(/_pct$/, "").replace(/_/g, " ");
  function fmt(v) { return readable(v, series.unit); }
  // "Flat" means it moved no more than it normally wobbles. A share of the level would call a
  // drop from 99.6% to 93% flat.
  const normalWobble = Math.max.apply(null, early) - Math.min.apply(null, early);
  if (Math.abs(after - before) < Math.max(1, normalWobble)) {
    return name + " stays around " + fmt(before) + " the whole time.";
  }
  let when = null;
  for (const p of series.points) {
    if (Math.abs(p[1] - before) > Math.abs(after - before) / 2) { when = p[0]; break; }
  }
  return name + ": normally about " + fmt(before) + "; from " + (when ? utcTime(when) : "the onset") + " about " + fmt(after) + ".";
}

async function drawMetric(canvasId, textId, evidenceId) {
  if (!evidenceId) {
    document.getElementById(textId).textContent = "No metric recorded.";
    return;
  }
  const series = await api("/api/incidents/" + incidentId() + "/series/" + evidenceId);
  document.getElementById(textId).textContent = describeSeries(series);
  const points = series.points.map(function (p) { return { x: p[0], y: p[1] }; });
  new Chart(document.getElementById(canvasId), {
    type: "line",
    data: { datasets: [{ data: points, borderColor: cssVar("--multi"), pointRadius: 0, borderWidth: 2, tension: 0.2 }] },
    options: {
      animation: false,   // the chart may be drawn while the full story is still closed
      scales: {
        x: { type: "linear", ticks: { callback: function (v) { return utcTime(v); }, maxTicksLimit: 6 }, grid: { display: false } },
        y: { title: { display: true, text: series.unit } },
      },
      plugins: {
        legend: { display: false },
        changeLines: { changes: DATA.changes },
        tooltip: { callbacks: { title: function (items) { return utcTime(items[0].raw.x) + " UTC"; } } },
      },
    },
    plugins: [changeLines],
  });
}

async function renderKeyMetrics() {
  const keyId = keySeriesId();
  const hasProof = DATA.truth && DATA.truth.decisive_evidence.some(function (id) { return id.indexOf("M-") === 0; });
  if (!hasProof) {
    document.querySelector("#metric-key-text").previousElementSibling.textContent = "The biggest change in the data";
  }
  await drawMetric("metric-key", "metric-key-text", keyId);
  await drawMetric("metric-users", "metric-users-text", userSeriesId(keyId));
}
