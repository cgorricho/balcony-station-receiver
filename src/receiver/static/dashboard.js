"use strict";

// Data from the server is always inserted with textContent, never as HTML.

const params = new URLSearchParams(location.search);
const REFRESH_SECONDS = Math.max(5, Number(params.get("refresh")) || 60);
const state = {
  node: params.get("node") || null,
  days: Number(params.get("days")) || 7,
};

const CARDS = [
  ["air_temp_c", "Air", "°F", toF, 1],
  ["tray_temp_c", "Tray", "°F", toF, 1],
  ["air_rh_pct", "Humidity", "%", null, 0],
  ["lux", "Light", "lux", null, 0],
  ["pellet_moisture_raw", "Pellet moisture", "raw", null, 0],
  ["battery_v", "Battery", "V", null, 2],
  ["power_source", "Power", "", null, 0],
  ["rssi_dbm", "WiFi signal", "dBm", null, 0],
];

const CHARTS = [
  { id: "chart-temp", title: "Temperature (°F)", names: ["air_temp_c", "tray_temp_c"], line45: true },
  { id: "chart-lux", title: "Light (lux)", names: ["lux"] },
  { id: "chart-moisture", title: "Pellet moisture (raw)", names: ["pellet_moisture_raw"] },
  { id: "chart-battery", title: "Battery (V)", names: ["battery_v"] },
];

function toF(c) {
  return c * 9 / 5 + 32;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function css(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function ago(seconds) {
  if (seconds < 90) return `${Math.max(0, Math.round(seconds))} s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}

function renderControls(data) {
  const select = document.getElementById("node");
  select.replaceChildren(...data.nodes.map((n) => {
    const option = el("option", null, n);
    option.value = n;
    option.selected = n === data.node;
    return option;
  }));
  select.disabled = data.nodes.length < 2;
  document.getElementById("days").value = String(state.days);
  document.getElementById("days-label").textContent = String(data.days);
}

function renderLatest(data, flaggedSeqs) {
  const cards = document.getElementById("cards");
  const meta = document.getElementById("latest-meta");
  const latest = data.latest;
  if (!latest) {
    cards.replaceChildren(el("div", "card", "No readings yet"));
    meta.textContent = "";
    return;
  }
  const flagged = flaggedSeqs.has(latest.seq);
  cards.replaceChildren(...CARDS.filter(([name]) => name in latest.metrics).map(([name, label, unit, convert, digits]) => {
    let value = latest.metrics[name];
    if (typeof value === "number") value = (convert ? convert(value) : value).toFixed(digits);
    const card = el("div", flagged ? "card flagged" : "card");
    card.append(el("div", "label", label));
    const big = el("div", "value", String(value));
    if (unit) big.append(el("span", "unit", unit));
    card.append(big);
    return card;
  }));
  const synced = latest.ts_synced ? "" : " · node clock not synced";
  meta.textContent =
    `Received ${latest.received_local} (${ago(latest.age_seconds)}) · seq ${latest.seq}` +
    ` · firmware ${latest.fw_version || "unknown"}${synced}`;
}

function renderWarnings(data) {
  const section = document.getElementById("warnings-section");
  const list = document.getElementById("warnings");
  section.hidden = data.warnings.length === 0;
  list.replaceChildren(...data.warnings.slice(0, 20).map((w) => {
    const item = el("li", null, w.message);
    item.append(el("span", "when", `${w.node_id} · seq ${w.seq} · received ${w.received_local}`));
    return item;
  }));
  if (data.warnings.length > 20) {
    list.append(el("li", null, `…and ${data.warnings.length - 20} more`));
  }
}

function renderCharts(data) {
  const empty = document.getElementById("empty");
  const hasData = data.series.length > 0;
  empty.hidden = hasData;
  document.getElementById("window").textContent = data.window
    ? `${data.window.start} to ${data.window.end} (${data.tz})`
    : "";

  const text = css("--text");
  const muted = css("--muted");
  const border = css("--border");
  const gapColor = css("--gap");
  const palette = { air_temp_c: "#e8743b", tray_temp_c: "#2f9e8f", lux: "#d6a400", pellet_moisture_raw: "#5b7fde", battery_v: "#8a63d2" };
  const series = Object.fromEntries(data.series.map((s) => [s.name, s]));

  for (const chart of CHARTS) {
    const div = document.getElementById(chart.id);
    const present = chart.names.filter((n) => series[n]);
    div.hidden = present.length === 0;
    if (present.length === 0) continue;
    const traces = present.map((n) => ({
      x: series[n].x,
      y: series[n].y,
      name: series[n].label,
      type: "scatter",
      mode: "lines",
      connectgaps: false,
      line: { color: palette[n], width: 2 },
      hovertemplate: `%{y} ${series[n].unit}<extra>${series[n].label}</extra>`,
    }));
    const shapes = data.gaps.map((g) => ({
      type: "rect", xref: "x", yref: "paper", x0: g.start, x1: g.end, y0: 0, y1: 1,
      fillcolor: gapColor, line: { width: 0 }, layer: "below",
    }));
    if (chart.line45) {
      shapes.push({
        type: "line", xref: "paper", yref: "y", x0: 0, x1: 1, y0: 45, y1: 45,
        line: { color: muted, width: 1, dash: "dot" },
      });
    }
    const layout = {
      title: { text: chart.title, font: { size: 14, color: text }, x: 0.01, xanchor: "left" },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { color: muted },
      margin: { l: 48, r: 12, t: 36, b: 36 },
      xaxis: { gridcolor: border, linecolor: border },
      yaxis: { gridcolor: border, linecolor: border, zeroline: false },
      showlegend: present.length > 1,
      legend: { orientation: "h", x: 1, xanchor: "right", y: 1.15 },
      shapes,
      hovermode: "x unified",
    };
    // On a phone the toolbar would cover the title and legend.
    const config = {
      displaylogo: false,
      responsive: true,
      displayModeBar: window.innerWidth >= 600 ? "hover" : false,
      modeBarButtonsToRemove: ["select2d", "lasso2d"],
    };
    Plotly.react(div, traces, layout, config);
  }
}

function renderSummary(data) {
  const body = document.getElementById("summary");
  const fmt = (v, suffix = "") => (v === null || v === undefined ? "—" : `${v}${suffix}`);
  body.replaceChildren(...data.summary.slice().reverse().map((d) => {
    const row = el("tr");
    const airMin = el("td", d.air_min_f !== null && d.air_min_f < 45 ? "cold" : null, fmt(d.air_min_f));
    row.append(
      el("td", null, d.day), airMin, el("td", null, fmt(d.air_max_f)),
      el("td", null, fmt(d.tray_min_f)), el("td", null, fmt(d.tray_max_f)),
      el("td", null, fmt(d.hours_below_45f)), el("td", null, fmt(d.dli)),
      el("td", null, fmt(d.sun_start)), el("td", null, fmt(d.sun_end)),
      el("td", null, fmt(d.coverage_pct, "%")),
    );
    return row;
  }));
}

async function refresh() {
  const query = new URLSearchParams({ days: String(state.days) });
  if (state.node) query.set("node", state.node);
  const status = document.getElementById("status");
  try {
    const response = await fetch(`/dashboard/data?${query}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    state.node = data.node;
    const flaggedSeqs = new Set(
      data.warnings.filter((w) => w.node_id === data.node && w.kind === "out_of_range").map((w) => w.seq),
    );
    renderControls(data);
    renderLatest(data, flaggedSeqs);
    renderWarnings(data);
    renderCharts(data);
    renderSummary(data);
    status.textContent = `Updated ${data.generated_at} · refreshes every ${REFRESH_SECONDS} s`;
  } catch (error) {
    status.textContent = `Could not load data (${error.message}). Retrying in ${REFRESH_SECONDS} s.`;
  }
}

function updateUrl() {
  const query = new URLSearchParams(location.search);
  if (state.node) query.set("node", state.node);
  query.set("days", String(state.days));
  history.replaceState(null, "", `?${query}`);
}

document.getElementById("node").addEventListener("change", (event) => {
  state.node = event.target.value;
  updateUrl();
  refresh();
});
document.getElementById("days").addEventListener("change", (event) => {
  state.days = Number(event.target.value);
  updateUrl();
  refresh();
});
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", refresh);

refresh();
setInterval(refresh, REFRESH_SECONDS * 1000);
