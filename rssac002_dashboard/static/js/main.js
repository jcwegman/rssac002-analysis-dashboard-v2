// Page frame: routing, the shared filter row, and theme. Everything a view shows is
// described by the URL (#/page?service=1&range=30d), so any view can be shared as a link.

import { api } from "./api.js";
import { coverageStrip, disposeCharts } from "./charts.js";
import { addDays, h, toDay } from "./format.js";
import { candidates, compare } from "./pages/compare.js";
import { methods } from "./pages/methods.js";
import { quality } from "./pages/quality.js";
import { overview } from "./pages/overview.js";
import { reports } from "./pages/reports.js";
import { sources } from "./pages/sources.js";
import { field, message, select } from "./ui.js";

const PAGES = { overview, compare, reports, quality, sources, methods };
// Pages that show stored reports rather than settings or text, and so need reports to exist.
const DATA_PAGES = ["overview", "compare", "reports", "quality"];
const PRESETS = [
  ["7d", "Last 7 days"],
  ["30d", "Last 30 days"],
  ["90d", "Last 90 days"],
  ["6m", "Last 6 months"],
  ["1y", "Last 12 months"],
  ["all", "All reports"],
  ["custom", "Custom range"],
];
const PRESET_DAYS = { "7d": 7, "30d": 30, "90d": 90, "6m": 183, "1y": 365 };
// The period shown when the address does not name one.
const DEFAULT_PRESET = "6m";
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

let meta = null;
let generation = 0;
let leaveHandlers = [];

const main = document.getElementById("main");
const filterBar = document.getElementById("filters");

function parseHash() {
  const [route, search = ""] = location.hash.replace(/^#\/?/, "").split("?");
  return { route: PAGES[route] ? route : "overview", query: new URLSearchParams(search) };
}

function navigate(route, changes = {}) {
  const { query } = parseHash();
  for (const [name, value] of Object.entries(changes)) {
    if (value === null || value === undefined || value === "") query.delete(name);
    else query.set(name, value);
  }
  const search = query.toString();
  const hash = `#/${route}${search ? `?${search}` : ""}`;
  if (hash === location.hash) render();
  else location.hash = hash;
}

/** The date range the URL asks for, relative to the newest report rather than to today. */
function resolveRange(query, firstData, lastData) {
  let preset = query.get("range") || (query.get("from") && query.get("to") ? "custom" : DEFAULT_PRESET);
  let first;
  let last;
  if (preset === "custom" && ISO_DAY.test(query.get("from") || "") && ISO_DAY.test(query.get("to") || "")) {
    [first, last] = [query.get("from"), query.get("to")].sort();
  } else if (preset === "all") {
    [first, last] = [firstData, lastData];
  } else {
    if (!PRESET_DAYS[preset]) preset = DEFAULT_PRESET;
    last = lastData;
    first = addDays(last, -(PRESET_DAYS[preset] - 1));
  }
  return { preset, first, last, firstData, lastData };
}

function resolveSelection(route, query) {
  const today = toDay(new Date());
  const withReports = meta.sources.filter((source) => source.reports);
  if (route === "compare" && !["ns", "year"].includes(query.get("by"))) {
    const lastData = withReports.map((source) => source.last_day).sort().at(-1) || today;
    const firstData = withReports.map((source) => source.first_day).sort()[0] || lastData;
    return { source: null, server: "", ...resolveRange(query, firstData, lastData) };
  }
  const source = meta.sources.find((one) => String(one.id) === query.get("service")) || withReports[0] || meta.sources[0] || null;
  const server = source && source.servers.includes(query.get("ns")) ? query.get("ns") : "";
  const lastData = source?.last_day || today;
  return { source, server, ...resolveRange(query, source?.first_day || lastData, lastData) };
}

function sourceLabel(source) {
  const twins = meta.sources.filter((other) => other.name === source.name).length > 1;
  return twins && source.operator ? `${source.name} (${source.operator})` : source.name;
}

async function renderFilters(route, selection, token) {
  if (!DATA_PAGES.includes(route) || !meta.sources.length) {
    filterBar.hidden = true;
    filterBar.replaceChildren();
    return;
  }
  const go = (changes) => navigate(route, changes);
  const row = h("div", { class: "filter-row" });
  if (selection.source) {
    row.append(field("Service", select(meta.sources.map((source) => [String(source.id), sourceLabel(source)]), String(selection.source.id), (value) => go({ service: value, ns: null, set: null }))));
    if (selection.source.servers.length && ["overview", "reports"].includes(route)) {
      const servers = [["", "All nameservers"], ...selection.source.servers.map((server) => [server, server])];
      row.append(field("Nameserver", select(servers, selection.server, (value) => go({ ns: value }))));
    }
  }
  row.append(
    field(
      "Period",
      select(PRESETS, selection.preset, (value) =>
        value === "custom" ? go({ range: "custom", from: selection.first, to: selection.last }) : go({ range: value, from: null, to: null }),
      ),
    ),
  );
  if (selection.preset === "custom") {
    const dateInput = (name, value) =>
      h("input", { type: "date", value, min: selection.firstData, max: selection.lastData, onchange: (event) => event.target.value && go({ range: "custom", [name]: event.target.value }) });
    row.append(field("From", dateInput("from", selection.first)), field("To", dateInput("to", selection.last)));
  }
  if (["overview", "compare"].includes(route)) {
    const query = parseHash().query;
    row.append(
      field("Count axes", select([["", "Linear"], ["log", "Logarithmic"]], query.get("scale") === "log" ? "log" : "", (value) => go({ scale: value }))),
      field("Values", select([["", "Daily"], ["7", "7-day mean"]], query.get("avg") === "7" ? "7" : "", (value) => go({ avg: value }))),
    );
  }
  const wrap = h("div", { class: "wrap" }, row);
  filterBar.replaceChildren(wrap);
  filterBar.hidden = false;
  // One row for the chosen service, or on Compare one row for each service that is ticked.
  let rows = [];
  if (selection.source) {
    const coverage = await api.coverage(selection.source.id, selection.server).catch(() => null);
    if (coverage?.counts) rows = [{ first: coverage.first, counts: coverage.counts }];
  } else if (route === "compare") {
    const { chosen } = candidates({ meta, query: parseHash().query, source: null, first: selection.first, last: selection.last });
    const found = await Promise.all(chosen.map((one) => api.coverage(one.params.source, "").catch(() => null)));
    rows = chosen.map((one, index) => ({ label: one.label, color: one.color, first: found[index]?.first, counts: found[index]?.counts })).filter((one) => one.counts);
  }
  if (token !== generation || !rows.length) return;
  row.append(
    h("div", { class: "strip-legend" }, h("span", {}, h("i", { class: "full" }), "All reports"), h("span", {}, h("i", { class: "partial" }), "Some missing"), h("span", {}, h("i", { class: "none" }), "No reports")),
  );
  wrap.append(
    coverageStrip({
      rows,
      expected: meta.metrics.length,
      selected: [selection.first, selection.last],
      label: "Days with reports. Drag to choose a period.",
      onSelect: (first, last) => go({ range: "custom", from: first, to: last }),
    }),
  );
}

async function render() {
  const token = ++generation;
  const { route, query } = parseHash();
  main.classList.add("loading");
  try {
    if (!meta) meta = await api.meta();
    if (token !== generation) return;
    for (const link of document.querySelectorAll(".top nav a")) {
      const search = query.toString();
      link.href = `#/${link.dataset.route}${search ? `?${search}` : ""}`;
      if (link.dataset.route === route) link.setAttribute("aria-current", "page");
      else link.removeAttribute("aria-current");
    }
    const page = PAGES[route];
    const selection = resolveSelection(route, query);
    const context = {
      meta,
      query,
      ...selection,
      go: (changes) => navigate(route, changes),
      reload,
      setMeta: (next) => (meta = next),
      onLeave: (handler) => leaveHandlers.push(handler),
    };
    const hasData = meta.sources.some((source) => source.reports);
    const usable = hasData || !DATA_PAGES.includes(route);
    const data = usable ? await page.load(context) : null;
    if (token !== generation) return;

    leaveHandlers.forEach((handler) => handler());
    leaveHandlers = [];
    disposeCharts();
    document.title = `${page.title} | RSSAC002 Analysis Dashboard`;
    await renderFilters(route, selection, token);
    if (token !== generation) return;
    main.replaceChildren(h("div", { class: "wrap" }, usable ? page.view(context, data) : emptyState()));
  } catch (error) {
    if (token !== generation) return;
    disposeCharts();
    main.replaceChildren(h("div", { class: "wrap" }, message(error.message, true)));
  } finally {
    if (token === generation) main.classList.remove("loading");
  }
}

function reload() {
  meta = null;
  api.clearCache();
  return render();
}

// --- theme ---------------------------------------------------------------------------

const themeButton = document.getElementById("theme");
const systemDark = window.matchMedia("(prefers-color-scheme: dark)");
const currentTheme = () => document.documentElement.dataset.theme || (systemDark.matches ? "dark" : "light");
const labelTheme = () => (themeButton.textContent = currentTheme() === "dark" ? "Light theme" : "Dark theme");
themeButton.addEventListener("click", () => {
  const next = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try {
    localStorage.setItem("theme", next);
  } catch (error) {
    /* storage unavailable: the choice lasts for this visit */
  }
  labelTheme();
  render(); // heatmap colours are read from the theme when drawn
});
systemDark.addEventListener("change", labelTheme);
labelTheme();

window.addEventListener("hashchange", render);
render();
