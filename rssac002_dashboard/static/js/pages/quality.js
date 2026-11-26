// How complete and how internally consistent a source's reports are.

import { api } from "../api.js";
import { formatDay, formatRange, formatValue, h } from "../format.js";
import { card, message, pageHead } from "../ui.js";

const dayList = (days, total) => days.map((day) => formatDay(day, true)).join(", ") + (total > days.length ? `, and ${total - days.length} more` : "");

export const quality = {
  title: "Data quality",

  load: (context) => (context.source ? api.quality({ source: context.source.id, from: context.first, to: context.last }) : null),

  view(context, data) {
    const { source, first, last, meta } = context;
    const nameOf = (short) => meta.sources.find((one) => one.short_service === short)?.name || short;
    // Per run of days: the services without a report, each with its metrics when it lacks only some.
    const without = (metrics) => {
      const members = [...new Set(Object.values(metrics).flat())];
      return members
        .map((member) => {
          const lacking = Object.keys(metrics).filter((metric) => metrics[metric].includes(member));
          return lacking.length === 3 ? nameOf(member) : `${nameOf(member)} (${lacking.join(" and ")} only)`;
        })
        .join("; ");
    };
    const days = (run) => Math.round((new Date(run.last) - new Date(run.first)) / 86400000) + 1;
    const failing = data.checks.filter((check) => check.days_failing);
    const figure = (label, value, foot) => h("div", { class: "figure" }, h("div", { class: "figure-label" }, label), h("div", { class: "figure-value" }, value), h("div", { class: "figure-change" }, foot));

    const gaps = Object.entries(data.missing).filter(([, found]) => found.count);
    const checks = data.checks.map((check) => {
      const status = check.days_failing ? h("span", { class: "status failed" }, `Differs on ${check.days_failing} of ${check.days_checked} days`) : h("span", { class: "status done" }, `Holds on all ${check.days_checked} days`);
      const examples = check.examples.length
        ? h(
            "details",
            { class: "history" },
            h("summary", {}, "Largest differences"),
            h("table", { class: "data" }, h("thead", {}, h("tr", {}, ["Day", "Expected", "Found", "Difference"].map((name) => h("th", { scope: "col" }, name)))), h("tbody", {}, check.examples.map((row) => h("tr", {}, h("th", { scope: "row" }, formatDay(row.day, true)), h("td", {}, formatValue(row.expected, "count", true)), h("td", {}, formatValue(row.actual, "count", true)), h("td", {}, formatValue(row.difference, "share")))))),
          )
        : null;
      return h("tr", {}, h("th", { scope: "row" }, check.name), h("td", {}, check.description, check.tolerance ? ` Differences up to ${formatValue(check.tolerance, "share")} are accepted.` : "", examples), h("td", {}, status));
    });

    return h(
      "div",
      { class: "stack" },
      pageHead(`Data quality of ${source.name}, ${formatRange(first, last)}`, "Gaps, import notes, and checks on numbers that the RSSAC002 metrics count in two ways and that should therefore agree. A difference points at the reports or at the tool that produced them, not at the DNS traffic."),
      h(
        "div",
        { class: "figures three" },
        figure("Days with all four reports", `${data.complete_days} of ${data.days}`, data.complete_days === data.days ? "No gaps in this period" : "See the missing days below"),
        figure("Consistency checks that hold", `${data.checks.length - failing.length} of ${data.checks.length}`, data.checks.length ? (failing.length ? "See the differences below" : "Every check holds on every day") : "Nothing to check in this period"),
        figure("Reports imported with a note", String(data.notes.count), data.notes.count ? "Listed at the bottom" : "No notes"),
      ),
      card(
        "Missing days",
        "Days in the period without a combined report, per metric. A missing day is a gap in every chart, never a zero.",
        gaps.length
          ? h("table", { class: "data left wrap" }, h("thead", {}, h("tr", {}, ["Metric", "Days missing", "Which"].map((name) => h("th", { scope: "col" }, name)))), h("tbody", {}, gaps.map(([metric, found]) => h("tr", {}, h("th", { scope: "row" }, metric), h("td", {}, String(found.count)), h("td", {}, dayList(found.days, found.count))))))
          : h("p", { class: "empty" }, "No day is missing in this period."),
      ),
      data.partial.days
        ? card(
            "Days without every service",
            "This service is the total of several services. On these days some of them had no report, so there is no total: the charts show the sum of the others as a dotted line, and no figure or check uses it. A day on which fewer than three quarters of the services reported is not shown at all.",
            h("table", { class: "data left wrap" }, h("thead", {}, h("tr", {}, ["Days", "Number", "Without a report"].map((name) => h("th", { scope: "col" }, name)))), h("tbody", {}, data.partial.runs.map((run) => h("tr", {}, h("th", { scope: "row" }, run.first === run.last ? formatDay(run.first, true) : formatRange(run.first, run.last)), h("td", {}, String(days(run))), h("td", {}, without(run.metrics)))))),
          )
        : null,
      card(
        "Consistency checks",
        source.servers.length ? null : "This source has no per-nameserver reports, so the checks that add up nameservers are left out.",
        checks.length ? h("table", { class: "data left wrap" }, h("thead", {}, h("tr", {}, ["Check", "What is compared", "Result"].map((name) => h("th", { scope: "col" }, name)))), h("tbody", {}, checks)) : message("No reports in this period."),
      ),
      card(
        "Import notes",
        "Reports that were imported although something in them was unexpected.",
        data.notes.count
          ? h(
              "table",
              { class: "data left wrap" },
              h("thead", {}, h("tr", {}, ["Day", "Metric", "Nameserver", "Note"].map((name) => h("th", { scope: "col" }, name)))),
              h("tbody", {}, data.notes.reports.map((report) => h("tr", {}, h("th", { scope: "row" }, formatDay(report.day, true)), h("td", {}, report.metric), h("td", {}, report.server || "All"), h("td", {}, report.warnings.join("; "))))),
            )
          : h("p", { class: "empty" }, "No report in this period was imported with a note."),
        data.notes.count > data.notes.reports.length ? h("p", { class: "caption" }, `Showing the latest ${data.notes.reports.length} of ${data.notes.count}.`) : null,
      ),
    );
  },
};
