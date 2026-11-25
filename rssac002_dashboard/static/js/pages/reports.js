// The imported report files themselves: browse, read, download.

import { api } from "../api.js";
import { addDays, daysBetween, formatDay, formatRange, h } from "../format.js";
import { field, message, pageHead, select } from "../ui.js";

const DAYS_SHOWN = 31;

export const reports = {
  title: "Report files",

  load: (context) => (context.source ? api.reports({ source: context.source.id, server: context.server, metric: context.query.get("metric"), from: context.first, to: context.last }) : null),

  view(context, data) {
    const { source, server, first, last, query, go, meta } = context;
    const rows = data.reports;
    const metric = meta.metrics.includes(query.get("metric")) ? query.get("metric") : "";
    const metrics = metric ? [metric] : meta.metrics;

    // One entry per day with files, newest first, and one per run of days without any:
    // { day, files: { metric: file } } or { gap: [first day, last day] }.
    const entries = [];
    const gapBetween = (later, earlier) => daysBetween(earlier, later) > 1 && entries.push({ gap: [addDays(earlier, 1), addDays(later, -1)] });
    for (const row of rows) {
      const previous = entries.at(-1);
      if (previous?.day === row.day) {
        previous.files[row.metric] = row;
        continue;
      }
      gapBetween(previous ? previous.day : addDays(last, 1), row.day);
      entries.push({ day: row.day, files: { [row.metric]: row } });
    }
    if (entries.length) gapBetween(entries.at(-1).day, addDays(first, -1));
    const days = entries.filter((entry) => entry.day).length;

    let shown = DAYS_SHOWN;
    const body = h("tbody");
    const more = h("button", { class: "button", type: "button", onclick: () => ((shown = days), fill()) });
    const notes = (file) => `${file.warnings.length} ${file.warnings.length === 1 ? "note" : "notes"}`;

    /** Show one file under its day's row, or close it when it is the one shown. */
    async function toggleFile(file, tr, button) {
      const open = tr.nextSibling?.classList?.contains("detail") ? tr.nextSibling : null;
      const same = open?.dataset.file === String(file.id);
      open?.remove();
      for (const other of tr.querySelectorAll("button")) {
        other.textContent = "Open";
        other.setAttribute("aria-expanded", "false");
      }
      tr.classList.toggle("open", !same);
      if (same) return;
      const pre = h("pre", { class: "yaml", tabindex: 0 }, "Loading…");
      const head = h(
        "div",
        { class: "file-head" },
        h("p", { class: "file-name" }, h("span", { class: "mono" }, file.filename), `, imported ${file.fetched_at.slice(0, 16).replace("T", " ")} UTC`),
        h("a", { class: "button", href: `/api/reports/${file.id}?download=1` }, "Download"),
      );
      const noted = file.warnings.map((warning) => h("p", { class: "file-note" }, `Note: ${warning}`));
      tr.after(h("tr", { class: "detail", dataset: { file: String(file.id) } }, h("td", { colSpan: metrics.length + 1 }, head, noted, pre)));
      button.textContent = "Close";
      button.setAttribute("aria-expanded", "true");
      pre.textContent = await api.reportText(file.id).catch((error) => error.message);
    }

    function dayRow({ day, files }) {
      const tr = h("tr", {}, h("th", { scope: "row" }, formatDay(day, true)));
      for (const name of metrics) {
        const file = files[name];
        if (!file) {
          tr.append(h("td", { class: "muted", dataset: { label: name } }, "No file"));
          continue;
        }
        const open = h("button", { class: "button quiet", type: "button", "aria-expanded": "false", "aria-label": `Open ${file.filename}` }, "Open");
        open.addEventListener("click", () => toggleFile(file, tr, open));
        tr.append(h("td", { dataset: { label: name } }, open, file.warnings.length ? h("span", { class: "muted" }, notes(file)) : null));
      }
      return tr;
    }

    function gapRow([from, to]) {
      const count = daysBetween(from, to) + 1;
      const when = from === to ? formatDay(from, true) : formatRange(from, to);
      return h("tr", { class: "gap" }, h("td", { colSpan: metrics.length + 1 }, `${when}: no ${metric ? `${metric} ` : ""}files (${count} ${count === 1 ? "day" : "days"})`));
    }

    function fill() {
      // The first `shown` days, with the gaps that lie between them.
      const visible = [];
      let counted = 0;
      for (const entry of entries) {
        if (entry.day && counted === shown) break;
        if (entry.day) counted += 1;
        visible.push(entry);
      }
      if (counted < days && visible.at(-1)?.gap) visible.pop();
      body.replaceChildren(...visible.map((entry) => (entry.day ? dayRow(entry) : gapRow(entry.gap))));
      more.hidden = days <= shown;
      more.textContent = `Show all ${days.toLocaleString("en-GB")} days`;
    }
    fill();

    const scope = server ? `nameserver ${server} of ${source.name}` : source.name;
    return h(
      "div",
      {},
      pageHead("Report files", `The YAML reports for ${scope} exactly as the operator published them, ${formatRange(first, last)}. The same files are served under /rssac002/ in the standard directory layout, for other tools to fetch.`),
      h(
        "div",
        { class: "toolbar" },
        field("Metric", select([["", "All metrics"], ...meta.metrics.map((name) => [name, name])], metric, (value) => go({ metric: value }))),
        rows.length && rows.length <= meta.limits.archive_reports ? h("a", { class: "button", href: api.archiveUrl({ source: source.id, server, metric, from: first, to: last }) }, `Download ${rows.length.toLocaleString("en-GB")} files as zip`) : null,
        rows.length > meta.limits.archive_reports ? h("span", { class: "muted" }, `Choose a shorter period to download as zip (up to ${meta.limits.archive_reports.toLocaleString("en-GB")} files).`) : null,
        h("a", { class: "button quiet", href: "/rssac002/" }, "Browse the published file tree"),
      ),
      rows.length
        ? h(
            "div",
            { class: "card files" },
            h("table", { class: "data left" }, h("thead", {}, h("tr", {}, ["Day", ...metrics].map((name) => h("th", { scope: "col" }, name)))), body),
            h("div", { class: "card-foot" }, more),
          )
        : message("No report files for this service and period. Choose a longer period above, or check the source on the Sources page."),
    );
  },
};
