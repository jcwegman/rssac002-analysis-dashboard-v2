// Overview of one service: headline figures, one section per RSSAC002 metric, then summary statistics.

import { api } from "../api.js";
import { columnChart, downloadText, heatLegend, heatmap, rankedBars, saveHeatmap, sparkline } from "../charts.js";
import { describe, formatChange, formatDay, formatRange, formatValue, h, rollingMean, toDay } from "../format.js";
import { card, countryName, lineCard, saveButton, section, serviceCode, slug, tableToggle } from "../ui.js";

const FIGURES = [
  ["queries", "Queries per day", "count"],
  ["ipv6_share", "IPv6 share of queries", "share"],
  ["tcp_share", "TCP share of queries", "share"],
  ["nxdomain_share", "NXDOMAIN share", "share"],
  ["sources", "Unique sources per day", "count"],
];

// Response codes with a line of their own, each with a fixed colour. All others are summed.
const NAMED_RCODES = [
  ["0", "NOERROR", 1],
  ["3", "NXDOMAIN", 2],
  ["5", "REFUSED", 3],
  ["-1", "No response", 4],
  ["1", "FORMERR", 5],
  ["9", "NOTAUTH", 6],
  ["4", "NOTIMP", 7],
  ["2", "SERVFAIL", 8],
];

const BREAKDOWN = [
  ["udp_ipv4", "UDP over IPv4", 1],
  ["udp_ipv6", "UDP over IPv6", 2],
  ["tcp_ipv4", "TCP over IPv4", 3],
  ["tcp_ipv6", "TCP over IPv6", 4],
];

// Transports outside RSSAC002, which a few operators report in fields of their own.
// Their lines are drawn only for a service that reports them in the period.
const EXTRA_BREAKDOWN = [
  ["tls_ipv4", "TLS over IPv4", 5],
  ["tls_ipv6", "TLS over IPv6", 6],
  ["quic_ipv4", "QUIC over IPv4", 7],
  ["quic_ipv6", "QUIC over IPv6", 8],
];
const EXTRA_STATISTICS = ["queries_udp_tcp", "encrypted_share"];
const EXTRA_NOTE = "TLS and QUIC are not part of RSSAC002: the operator reports them in fields of its own.";

const HEATMAPS = [
  ["udp-request-sizes", "UDP queries"],
  ["udp-response-sizes", "UDP responses"],
  ["tcp-request-sizes", "TCP queries"],
  ["tcp-response-sizes", "TCP responses"],
  // Only present when the operator publishes sizes for DNS over TLS.
  ["tls-request-sizes", "TLS queries"],
  ["tls-response-sizes", "TLS responses"],
];

const STATISTICS = ["queries", "queries_udp_tcp", "responses", "answer_ratio", "ipv6_share", "tcp_share", "encrypted_share", "noerror_share", "nxdomain_share", "noresponse_share", "sources_ipv4", "sources_ipv6", "sources_ipv6_64", "queries_per_source", "udp_response_p50"];

function figures(data, days) {
  const { current, previous } = data.summary;
  return h(
    "div",
    { class: "figures" },
    FIGURES.map(([key, label, unit]) => {
      const change = formatChange(current[key], previous[key], unit);
      return h(
        "div",
        { class: "figure" },
        h("div", { class: "figure-label" }, label),
        h("div", { class: "figure-value" }, formatValue(current[key], unit)),
        h("div", { class: "figure-foot" }, h("span", { class: "figure-change" }, change ? `${change} than the ${days} days before` : ""), sparkline(data.derived[key])),
      );
    }),
  );
}

/**
 * The name of the line that sums all other codes. A service can report hundreds of them, as one
 * root server does, so only the first few are named. The CSV download has a column per code.
 */
function otherCodesLabel(codes) {
  const named = 6;
  const list = codes.length > named + 1 ? `${codes.slice(0, named).join(", ")} and ${codes.length - named} more` : codes.join(", ");
  return `Other codes (${list})`;
}

/**
 * One line per named response code that occurs, plus the sum of all other codes.
 * `unnamed` is what the server has already added up: { codes, values }, for codes without a name.
 */
function rcodeSeries(rcodes, unnamed, prepare) {
  const named = new Set(NAMED_RCODES.map(([code]) => code));
  const series = NAMED_RCODES.filter(([code]) => rcodes[code]).map(([code, label, color]) => ({ label: code === "-1" ? label : `${label} (${code})`, color, values: prepare(rcodes[code]) }));
  const listed = Object.keys(rcodes).filter((code) => !named.has(code));
  const others = [...listed.map(Number), ...(unnamed?.codes || [])].sort((a, b) => a - b);
  if (others.length) {
    const first = listed.length ? rcodes[listed[0]] : unnamed.values;
    const sum = first.map((value, day) => (value === null ? null : listed.reduce((total, code) => total + rcodes[code][day], unnamed ? unnamed.values[day] : 0)));
    series.push({ label: otherCodesLabel(others), color: 0, values: prepare(sum) });
  }
  return series;
}

/** Size histogram as shares per display bin, for one protocol pair. */
function distribution(histograms, side, binBuckets) {
  const panels = [["udp", "UDP", 1], ["tcp", "TCP", 2]].map(([proto, label, color]) => {
    const { lower, count, open_lower: openLower } = histograms[`${proto}-${side}-sizes`];
    const total = count.reduce((sum, value) => sum + value, 0);
    const counts = [];
    for (let index = 0; index < lower.length; index += binBuckets) {
      const isOpen = lower[index] >= openLower;
      counts.push(count.slice(index, isOpen ? lower.length : index + binBuckets).reduce((sum, value) => sum + value, 0));
    }
    return { label, color, counts, values: counts.map((value) => (total ? value / total : 0)), lower, openLower };
  });
  const { lower, openLower } = panels[0];
  const width = binBuckets * 16;
  const bins = panels[0].counts.map((_, index) => {
    const start = lower[index * binBuckets];
    return start >= openLower ? `${formatValue(start, "count", true)} bytes or more` : `${formatValue(start, "count", true)} to ${formatValue(start + width - 1, "count", true)} bytes`;
  });
  const tickEvery = openLower > 1000 ? 512 : 64;
  const ticks = [];
  for (let start = 0; start <= openLower; start += tickEvery) ticks.push({ index: start / width, label: formatValue(start, "count", true) });
  return { bins, ticks, panels };
}

function distributionCard(title, note, data, side, binBuckets, span, context) {
  const { bins, ticks, panels } = distribution(data.histograms, side, binBuckets);
  const table = () =>
    h(
      "div",
      { class: "table-scroll" },
      h(
        "table",
        { class: "data" },
        h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Size"), panels.map((panel) => h("th", { scope: "col" }, `${panel.label} share`)), panels.map((panel) => h("th", { scope: "col" }, `${panel.label} messages`)))),
        h("tbody", {}, bins.map((bin, index) => h("tr", {}, h("th", { scope: "row" }, bin), panels.map((panel) => h("td", {}, formatValue(panel.values[index], "share"))), panels.map((panel) => h("td", {}, formatValue(panel.counts[index], "count", true)))))),
      ),
    );
  const plot = columnChart({ bins, ticks, panels, unit: "share", label: `${title}. Use the left and right arrow keys to read values.` });
  const caption = "Share of messages per size step; horizontal axis in bytes";
  const figure = { title: `${title}, ${context}`, caption, filename: `${context} ${title}`.toLowerCase().replace(/[^a-z0-9]+/g, "-") };
  const element = card(title, note, plot, h("p", { class: "caption" }, caption), tableToggle(table, saveButton(plot, figure)));
  element.classList.add(`span-${span}`);
  return element;
}

/** The counts behind one heatmap panel as CSV: a row per day, a column per size step. */
function matrixCsv(days, rows, stepBytes, openLower) {
  const steps = rows.find(Boolean)?.length || 0;
  const header = Array.from({ length: steps }, (_, step) => (step * stepBytes >= openLower ? `${step * stepBytes}+` : `${step * stepBytes}-${(step + 1) * stepBytes - 1}`));
  return [["day", ...header].join(","), ...days.map((day, index) => [day, ...(rows[index] || Array(steps).fill(""))].join(","))].join("\n") + "\n";
}

// Responses are counted in 16-byte steps up to 4,096 bytes: more rows than a panel has pixels.
// Their panels are drawn in rows of four steps.
const RESPONSE_ROW_STEPS = 4;

/** The rows of a panel with every `size` steps added up into one: { rows, steps }. */
function coarserRows(rows, steps, size) {
  const groups = Math.ceil(steps / size);
  const sum = (values) => values.reduce((total, value) => total + value, 0);
  return { rows: rows.map((row) => row && Array.from({ length: groups }, (_, group) => sum(row.slice(group * size, (group + 1) * size)))), steps: groups };
}

function heatmapCard(sizes, share, go, context) {
  const panels = HEATMAPS.filter(([name]) => sizes.histograms[name]).map(([name, label]) => {
    const { rows: counted, steps: counts, open_lower: openLower } = sizes.histograms[name];
    const response = name.endsWith("response-sizes");
    const { rows, steps } = response ? coarserRows(counted, counts, RESPONSE_ROW_STEPS) : { rows: counted, steps: counts };
    const stepBytes = sizes.step_bytes * (response ? RESPONSE_ROW_STEPS : 1);
    const shown = share
      ? rows.map((row) => {
          const total = row ? row.reduce((sum, count) => sum + count, 0) : 0;
          return row && total ? row.map((count) => count / total) : row;
        })
      : rows;
    // `counted` keeps every 16-byte step, for the CSV download.
    return { label, counted, shown, steps, stepBytes, openLower };
  });
  let max = 0;
  for (const panel of panels) for (const row of panel.shown) if (row) for (const value of row) if (value > max) max = value;
  const [min, unit] = share ? [1e-6, "share"] : [1, "count"];
  if (share && max) max = 1;
  const captionOf = (panel) => `${share ? "Share of the day\u2019s messages" : "Messages per day"} in each ${panel.stepBytes}-byte size step, logarithmic colour scale; vertical axis in bytes`;
  const toggle = h(
    "div",
    { class: "segmented", role: "group", "aria-label": "Heatmap values" },
    h("button", { type: "button", "aria-pressed": String(!share), onclick: () => go({ heat: null }) }, "Counts"),
    h("button", { type: "button", "aria-pressed": String(share), onclick: () => go({ heat: "share" }) }, "Share of the day"),
  );
  const element = card(
    "Message sizes per day",
    share ? "Each column is a day and adds up to 100%, so a change in colour is a change in the size mix, not in traffic volume." : "Each column is a day, each row a size step: 16 bytes for queries, 64 bytes for responses. All panels share one colour scale.",
    h(
      "div",
      { class: "heat-grid" },
      panels.map((panel) => {
        const plot = heatmap({ days: sizes.days, rows: panel.shown, steps: panel.steps, stepBytes: panel.stepBytes, openLower: panel.openLower, min, max, unit, label: `${panel.label}: messages per day and size step.` });
        const name = slug(`${context} ${panel.label} sizes`);
        return h(
          "div",
          {},
          h(
            "div",
            { class: "heat-head" },
            h("h4", {}, panel.label),
            h("button", { class: "button quiet", type: "button", onclick: () => saveHeatmap(plot, { title: `${panel.label}, message sizes per day, ${context}`, caption: captionOf(panel), filename: name }) }, "Save figure"),
            h("button", { class: "button quiet", type: "button", onclick: () => downloadText(`${name}.csv`, matrixCsv(sizes.days, panel.counted, sizes.step_bytes, panel.openLower)) }, "Download counts (CSV)"),
          ),
          plot,
        );
      }),
    ),
    max ? heatLegend(min, max, unit) : null,
    h("p", { class: "caption" }, "Vertical axis: message size in bytes, cut off above the largest size observed"),
  );
  element.querySelector(".card-head").append(toggle);
  element.classList.add("span-12");
  return element;
}

function responseCodes(data) {
  const total = data.rcode_totals.reduce((sum, row) => sum + row.count, 0);
  if (!total) return card("Response codes over the period", null, h("p", { class: "empty" }, "No reports in this period."));
  const top = data.rcode_totals.slice(0, 7);
  const rest = data.rcode_totals.slice(7);
  const rows = top.map((row) => ({ label: row.code >= 0 ? `${row.name} (${row.code})` : row.name, count: row.count, share: row.count / total }));
  if (rest.length) {
    const count = rest.reduce((sum, row) => sum + row.count, 0);
    rows.push({ label: `${rest.length} other codes`, count, share: count / total });
  }
  return card("Response codes over the period", "Totals over the period, as a share of all counted exchanges and as a count. “No response” is the generator’s code −1: a query with no matched response.", rankedBars(rows));
}

function statistics(data, measures, keys) {
  const rows = keys.map((key) => {
    const { label, unit } = measures[key];
    const stats = describe(data.derived[key]);
    if (!stats) return h("tr", {}, h("th", { scope: "row" }, label), h("td", { colSpan: 7 }, "no data"));
    const cell = (value) => h("td", {}, formatValue(value, unit, unit === "count"));
    return h("tr", {}, h("th", { scope: "row" }, label), h("td", {}, String(stats.count)), cell(stats.mean), cell(stats.median), cell(stats.sd), cell(stats.min), cell(stats.max), h("td", {}, stats.mean ? `${((stats.sd / stats.mean) * 100).toFixed(1)}%` : "n/a"));
  });
  return h(
    "div",
    { class: "card" },
    h("div", { class: "table-scroll tall" }, h("table", { class: "data" }, h("thead", {}, h("tr", {}, ["Measure", "Days (n)", "Mean", "Median", "Std. dev.", "Minimum", "Maximum", "CV"].map((name) => h("th", { scope: "col" }, name)))), h("tbody", {}, rows))),
    h("p", { class: "caption" }, "Over the daily values in the period, unsmoothed. Std. dev. is the sample standard deviation; CV is the standard deviation divided by the mean."),
  );
}

export const overview = {
  title: "Overview",

  async load(context) {
    if (!context.source) return null;
    const params = { source: context.source.id, server: context.server, from: context.first, to: context.last };
    const [data, sizes, events] = await Promise.all([api.series(params), api.sizes(params), api.events(context.source.id).catch(() => ({ events: [] }))]);
    return { ...data, sizes, events: events.events };
  },

  view(context, data) {
    const { source, server, first, last, query, go, meta } = context;
    const { days, derived, series, summary } = data;
    const smoothed = query.get("avg") === "7";
    const scale = query.get("scale") === "log" ? "log" : undefined;
    const prepare = (values) => (smoothed ? rollingMean(values) : values);
    const line = (label, color, values) => ({ label, color, values: prepare(values) });
    const quantile = query.get("size") === "p95" ? "p95" : "p50";
    const quantileName = quantile === "p95" ? "95th percentile" : "Median";
    const scopeName = server ? `${source.name} ${server}` : source.name;
    const events = data.events.filter((event) => event.day >= first && event.day <= last);
    const base = { days, smoothed, events, context: `${scopeName}, ${formatRange(first, last)}` };
    const params = { source: source.id, server, from: first, to: last };

    // TLS and QUIC, where the service reports them: a value on any day, zero included.
    const reported = (values) => values.some((value) => value !== null && value !== undefined);
    const breakdown = (side) => [...BREAKDOWN, ...EXTRA_BREAKDOWN.filter(([key]) => reported(series[`${side}_${key}`]))];
    const encrypted = reported(derived.encrypted_share);
    const shareSpan = encrypted ? 6 : 4;

    const who = [source.operator, countryName(source.country)].filter(Boolean).join(", ");
    const scope = server ? `Nameserver ${server} only. ` : "";
    const coverage = `${formatRange(first, last)} (UTC days), reports for ${summary.days_with_data} of ${summary.days} days.`;

    // The cited address has to show this view later too. So it names the service by its code and
    // the days by date: a period such as "last 90 days" moves on, and a source's number can change.
    const lasting = new URLSearchParams({ service: serviceCode(source, meta.sources), ...(server ? { ns: server } : {}), from: first, to: last });
    for (const [name, value] of query) if (!["service", "ns", "from", "to", "range"].includes(name)) lasting.set(name, value);
    const address = `${window.location.origin}${window.location.pathname}#/overview?${lasting}`;
    const reference = `${source.operator || `Operator of ${source.name}`}. RSSAC002 traffic reports for ${scopeName}, ${formatRange(first, last)} (UTC days). Retrieved ${formatDay(toDay(new Date()), true)} from ${address}`;
    const citeBlock = h(
      "div",
      { class: "cite", hidden: true },
      h("p", {}, reference),
      h("button", { class: "button", type: "button", onclick: (event) => navigator.clipboard?.writeText(reference).then(() => (event.target.textContent = "Copied")) }, "Copy"),
      h("p", { class: "caption" }, "The address names the service and the days, so it shows this view later too. Check the operator\u2019s own site for its preferred citation and licence."),
    );
    const citeButton = h("button", { class: "button quiet", type: "button", onclick: () => (citeBlock.hidden = !citeBlock.hidden) }, "Cite this view");

    const sizeToggle = h(
      "div",
      { class: "segmented", role: "group", "aria-label": "Size statistic" },
      [["p50", "Median"], ["p95", "95th percentile"]].map(([key, label]) => h("button", { type: "button", "aria-pressed": String(quantile === key), onclick: () => go({ size: key === "p50" ? null : key }) }, label)),
    );
    const sizeAxis = "Bytes, upper edge of the 16-byte step";
    const sizes = section(
      "Message sizes",
      "From the traffic-sizes metric, which counts messages in 16-byte steps. Percentiles are therefore the upper edge of the step they fall in.",
      h(
        "div",
        { class: "grid" },
        heatmapCard(data.sizes, query.get("heat") === "share", go, base.context),
        lineCard({ ...base, title: `${quantileName} query size`, note: "By transport.", unit: "bytes", axis: sizeAxis, series: [line("UDP", 1, series[`udp_request_${quantile}`]), line("TCP", 2, series[`tcp_request_${quantile}`])] }),
        lineCard({ ...base, title: `${quantileName} response size`, note: "By transport.", unit: "bytes", axis: sizeAxis, series: [line("UDP", 1, series[`udp_response_${quantile}`]), line("TCP", 2, series[`tcp_response_${quantile}`])] }),
        distributionCard("Response sizes over the period", "Each transport’s responses, in 64-byte steps.", data, "response", 4, 12, base.context),
        distributionCard("Query sizes over the period", "Each transport’s queries, in 16-byte steps.", data, "request", 1, 12, base.context),
      ),
    );
    sizes.querySelector(".section-head").append(sizeToggle);

    const summaryStatistics = section("Summary statistics", "Descriptive statistics of the daily values, for quoting or checking against your own analysis.", statistics(data, meta.measures, STATISTICS.filter((key) => encrypted || !EXTRA_STATISTICS.includes(key))));
    summaryStatistics.querySelector(".section-head").append(h("a", { class: "button", href: api.csvUrl(params) }, "Download daily values (CSV)"));

    return h(
      "div",
      {},
      h("div", { class: "zone" }, h("h1", {}, source.name), h("p", {}, who ? `${who}. ` : "", scope, coverage), citeButton),
      citeBlock,
      figures(data, summary.days),
      events.length ? h("section", { class: "events" }, h("h2", {}, "Recorded events in this period"), h("ul", {}, events.map((event) => h("li", {}, h("b", {}, formatDay(event.day, true)), event.label)))) : null,

      section(
        "Traffic volume",
        `From the traffic-volume metric: DNS queries received and responses sent per UTC day, by transport and IP version.${encrypted ? ` The totals include DNS over TLS and QUIC. ${EXTRA_NOTE}` : ""}`,
        h(
          "div",
          { class: "grid" },
          lineCard({ ...base, title: "Queries and responses per day", unit: "count", scale, axis: "Messages per UTC day", span: 12, series: [line("Queries received", 1, derived.queries), line("Responses sent", 2, derived.responses)] }),
          lineCard({
            ...base,
            title: "Queries received by transport and IP version",
            note: `On a logarithmic axis, because UDP carries roughly a hundred times more queries than TCP.${encrypted ? ` ${EXTRA_NOTE}` : ""}`,
            unit: "count",
            scale: "log",
            axis: "Queries per UTC day",
            series: breakdown("q").map(([key, label, color]) => line(label, color, series[`q_${key}`])),
          }),
          lineCard({ ...base, title: "Responses sent by transport and IP version", note: "Same axis as the queries beside it.", unit: "count", scale: "log", axis: "Responses per UTC day", series: breakdown("r").map(([key, label, color]) => line(label, color, series[`r_${key}`])) }),
          lineCard({ ...base, title: "IPv6 share of queries", note: "All transports together.", unit: "share", axis: "Share of queries received", span: shareSpan, series: [line("IPv6 share of queries", 1, derived.ipv6_share)] }),
          lineCard({ ...base, title: "TCP share of queries", note: `A rise can point to truncated UDP responses.${encrypted ? " Of the queries over UDP and TCP, without TLS and QUIC." : ""}`, unit: "share", axis: "Share of queries received over UDP and TCP", span: shareSpan, series: [line("TCP share of queries", 1, derived.tcp_share)] }),
          encrypted ? lineCard({ ...base, title: "TLS and QUIC share of queries", note: `Queries over TLS and QUIC divided by all queries received. ${EXTRA_NOTE}`, unit: "share", axis: "Share of queries received", span: shareSpan, series: [line("TLS and QUIC share of queries", 1, derived.encrypted_share)] }) : null,
          lineCard({ ...base, title: "Responses per query", note: "Below 1.00, some queries went unanswered.", unit: "ratio", axis: "Responses sent divided by queries received", span: shareSpan, series: [line("Responses per query", 1, derived.answer_ratio)] }),
        ),
      ),

      section(
        "Response codes",
        "From the rcode-volume metric: responses per DNS response code (RCODE) per UTC day.",
        h(
          "div",
          { class: "grid" },
          lineCard({
            ...base,
            title: "Responses per response code",
            note: "On a logarithmic axis, so that rare codes stay visible beside NOERROR and NXDOMAIN. A day on which a code did not occur is a gap in its line.",
            unit: "count",
            scale: "log",
            axis: "Responses per UTC day",
            span: 12,
            height: 320,
            series: rcodeSeries(data.rcodes, data.rcodes_unnamed, prepare),
          }),
          lineCard({ ...base, title: "NOERROR and NXDOMAIN shares", note: "NXDOMAIN: the queried name does not exist in the zone.", unit: "share", axis: "Share of all counted exchanges", series: [line("NXDOMAIN (3)", 2, derived.nxdomain_share), line("NOERROR (0)", 1, derived.noerror_share)] }),
          Object.assign(responseCodes(data), { className: "card span-6" }),
        ),
      ),

      sizes,

      section(
        "Unique sources",
        "From the unique-sources metric: distinct client addresses per UTC day. These counts cannot be added up across days or nameservers, because the same address can appear in each.",
        h(
          "div",
          { class: "grid" },
          lineCard({ ...base, title: "Unique sources per day", unit: "count", scale, axis: "Distinct addresses or prefixes per UTC day", series: [line("IPv4 addresses", 1, derived.sources_ipv4), line("IPv6 addresses", 2, derived.sources_ipv6), line("IPv6 /64 prefixes", 3, derived.sources_ipv6_64)] }),
          lineCard({ ...base, title: "Queries per source", note: "Queries received divided by IPv4 plus IPv6 source addresses.", unit: "ratio", axis: "Queries per source address per day", series: [line("Queries per source", 1, derived.queries_per_source)] }),
        ),
      ),

      summaryStatistics,
    );
  },
};
