// One measure on one axis, compared three ways: between services, between the nameservers of
// one service, or between a period and the same period 52 weeks earlier.
// Each series keeps its colour whichever others are shown.

import { api } from "../api.js";
import { addDays, formatRange, formatValue, h, rollingMean } from "../format.js";
import { card, field, lineCard, pageHead, select } from "../ui.js";

const MAX_SERIES = 8;
const YEAR = 364; // 52 weeks, so weekdays line up
const MODES = [
  ["service", "Services"],
  ["ns", "Nameservers"],
  ["year", "A year earlier"],
];

const modeOf = (query) => (["ns", "year"].includes(query.get("by")) ? query.get("by") : "service");

/** What can be compared in this mode, and which of it is ticked: [{ key, label, color, params }]. */
export function candidates(context) {
  const { meta, query, source, first, last } = context;
  const mode = modeOf(query);
  let all = [];
  if (mode === "service") {
    all = meta.sources.filter((one) => one.reports).map((one) => ({ key: String(one.id), label: one.name, params: { source: one.id, from: first, to: last } }));
  } else if (mode === "ns" && source) {
    all = ["", ...source.servers].map((server) => ({ key: server || "all", label: server || "All nameservers", params: { source: source.id, server, from: first, to: last } }));
  } else if (source) {
    all = [
      { key: "now", label: formatRange(first, last), color: 1, params: { source: source.id, from: first, to: last } },
      { key: "before", label: "52 weeks earlier", color: 0, params: { source: source.id, from: addDays(first, -YEAR), to: addDays(last, -YEAR) } },
    ];
  }
  // Eight colours exist. With up to eight candidates each keeps its own colour whatever is ticked;
  // with more, at most eight can be ticked and colours follow the order of the ticked ones.
  const stable = all.length <= MAX_SERIES;
  const asked = (query.get("set") || "").split(",").filter(Boolean);
  let chosen = mode === "year" ? all : asked.length ? all.filter((one) => asked.includes(one.key)) : all.slice(0, stable ? MAX_SERIES : 6);
  chosen = chosen.slice(0, MAX_SERIES);
  all = all.map((one, index) => ({ color: stable ? index + 1 : chosen.indexOf(one) + 1, ...one }));
  chosen = all.filter((one) => chosen.some((picked) => picked.key === one.key));
  return { mode, all, chosen, full: chosen.length >= MAX_SERIES };
}

function stats(values) {
  const present = values.filter((value) => value !== null && value !== undefined);
  if (!present.length) return null;
  return { mean: present.reduce((sum, value) => sum + value, 0) / present.length, min: Math.min(...present), max: Math.max(...present), last: present.at(-1) };
}

export const compare = {
  title: "Compare",

  load(context) {
    return Promise.all(candidates(context).chosen.map((one) => api.series({ ...one.params, parts: "derived" })));
  },

  view(context, results) {
    const { meta, query, go, first, last, source } = context;
    const { mode, all, chosen, full } = candidates(context);
    const measure = meta.measures[query.get("m")] ? query.get("m") : "queries";
    const { label, unit } = meta.measures[measure];
    const indexed = unit !== "share" && query.get("index") === "1";
    const smoothed = query.get("avg") === "7";

    // The current period's days carry every series; the earlier year is drawn against the same axis.
    const days = results[0]?.days || [];
    const series = chosen.map((one, position) => {
      let values = results[position].derived[measure];
      if (indexed) {
        const base = values.find((value) => value !== null && value !== undefined && value !== 0);
        values = values.map((value) => (value === null || value === undefined || !base ? null : (value / base) * 100));
      }
      return { label: one.label, color: one.color, values: smoothed ? rollingMean(values) : values };
    });
    const shownUnit = indexed ? "index" : unit;

    const toggle = (one, checked) => {
      const keys = chosen.filter((other) => other !== one).map((other) => other.key);
      if (checked) keys.push(one.key);
      go({ set: keys.join(",") || "none" });
    };
    const chips =
      mode === "year"
        ? null
        : h(
            "div",
            { class: "chips", role: "group", "aria-label": "Series to compare" },
            all.map((one) => h("label", { class: "chip" }, h("input", { type: "checkbox", checked: chosen.includes(one), disabled: full && !chosen.includes(one), onchange: (event) => toggle(one, event.target.checked) }), chosen.includes(one) && h("i", { class: `key c${one.color}` }), one.label)),
            all.length > MAX_SERIES && h("span", { class: "muted" }, "Up to eight at a time."),
          );

    const toolbar = h(
      "div",
      { class: "toolbar" },
      h(
        "div",
        { class: "segmented", role: "group", "aria-label": "Compare" },
        MODES.map(([key, name]) => h("button", { type: "button", "aria-pressed": String(mode === key), onclick: () => go({ by: key === "service" ? null : key, set: null }) }, name)),
      ),
      field("Measure", select(Object.entries(meta.measures).map(([key, one]) => [key, one.label]), measure, (value) => go({ m: value === "queries" ? null : value }))),
      unit !== "share" &&
        h(
          "div",
          { class: "segmented", role: "group", "aria-label": "Scale" },
          h("button", { type: "button", "aria-pressed": String(!indexed), onclick: () => go({ index: null }) }, "Actual values"),
          h("button", { type: "button", "aria-pressed": String(indexed), onclick: () => go({ index: "1" }) }, "Growth since first day"),
        ),
    );

    const about = {
      service: "The same measure for several services, such as TLDs and root servers. Shares compare directly; for counts, switch to growth to compare services of different size.",
      ns: `The nameservers of ${source?.name || "a service"} beside the combined figure. Query and response counts of the nameservers add up to the combined figure; unique-source counts overlap and do not.`,
      year: `${source?.name || "A service"} in this period beside the same weekdays 52 weeks earlier.`,
    }[mode];
    const scope = mode === "service" ? "" : `${source?.name}, `;
    const note = indexed ? "Each series’ first day with a report is set to 100, so series of very different size can be compared by growth." : null;
    const chart = series.length
      ? lineCard({
          title: label,
          note,
          days,
          series,
          unit: shownUnit,
          scale: unit === "count" && !indexed && query.get("scale") === "log" ? "log" : undefined,
          axis: indexed ? "Index, first day with a report = 100" : unit === "share" ? "Share" : unit === "bytes" ? "Bytes" : "Per UTC day",
          smoothed,
          context: `${scope}${formatRange(first, last)}`,
          span: 12,
          height: 340,
        })
      : card(label, null, h("p", { class: "empty" }, "Tick at least one series above."));

    const summary = card(
      `${label}, ${scope}${formatRange(first, last)}`,
      "Statistics of the daily values, unsmoothed and not indexed.",
      h(
        "div",
        { class: "table-scroll" },
        h(
          "table",
          { class: "data" },
          h("thead", {}, h("tr", {}, ["Series", "Average", "Lowest day", "Highest day", "Latest day"].map((name) => h("th", { scope: "col" }, name)))),
          h(
            "tbody",
            {},
            chosen.map((one, position) => {
              const values = stats(results[position].derived[measure]);
              const cell = (value) => h("td", {}, values ? formatValue(value, unit) : "no data");
              return h("tr", {}, h("th", { scope: "row" }, h("i", { class: `key c${one.color}` }), ` ${one.label}`), cell(values?.mean), cell(values?.min), cell(values?.max), cell(values?.last));
            }),
          ),
        ),
      ),
    );

    return h(
      "div",
      {},
      pageHead("Compare", about),
      toolbar,
      chips && h("div", { class: "toolbar" }, chips),
      h("div", { class: "stack" }, chart, chosen.length ? summary : null),
    );
  },
};
