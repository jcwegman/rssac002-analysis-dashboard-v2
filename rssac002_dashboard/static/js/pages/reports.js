// The imported report files themselves: browse, read, download.

import { api } from "../api.js";
import { formatDay, formatRange, h } from "../format.js";
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
    // One table row per day, newest first, with that day's file for each metric.
    const days = [];
    for (const row of rows) {
      if (days.at(-1)?.day !== row.day) days.push({ day: row.day, files: {} });
      days.at(-1).files[row.metric] = row;
    }
    let shown = DAYS_SHOWN;
    const body = h("tbody");
    const more = h("button", { class: "button", type: "button", onclick: () => ((shown = days.length), fill()) });

    /** Show one file under its day's row, or close it when it is the one shown. */
    async function toggleFile(file, tr, button) {
      const open = tr.nextSibling?.classList?.contains("detail") ? tr.nextSibling : null;
      const same = open?.dataset.file === String(file.id);
      open?.remove();
      for (const other of tr.querySelectorAll("button")) other.textContent = "Read";
      tr.classList.toggle("open", !same);
      if (same) return;
      const pre = h("pre", { class: "yaml", tabindex: 0 }, "Loading…");
      const about = h("p", { class: "file-name" }, h("span", { class: "mono" }, file.filename), `, imported ${file.fetched_at.slice(0, 16).replace("T", " ")} UTC`);
      tr.after(h("tr", { class: "detail", dataset: { file: String(file.id) } }, h("td", { colSpan: metrics.length + 1 }, about, pre)));
      button.textContent = "Close";
      pre.textContent = await api.reportText(file.id).catch((error) => error.message);
    }

    function fill() {
      body.replaceChildren(
        ...days.slice(0, shown).map(({ day, files }) => {
          const tr = h("tr", {}, h("th", { scope: "row" }, formatDay(day, true)));
          for (const name of metrics) {
            const file = files[name];
            if (!file) {
              tr.append(h("td", { class: "muted" }, "No file"));
              continue;
            }
            const read = h("button", { class: "button quiet", type: "button", "aria-label": `Read ${file.filename}` }, "Read");
            read.addEventListener("click", () => toggleFile(file, tr, read));
            const download = h("a", { class: "button quiet", href: `/api/reports/${file.id}?download=1`, title: file.filename, "aria-label": `Download ${file.filename}` }, "Download");
            tr.append(h("td", {}, h("div", { class: "actions start" }, read, download), file.warnings.map((warning) => h("div", { class: "muted" }, `Note: ${warning}`))));
          }
          return tr;
        }),
      );
      more.hidden = days.length <= shown;
      more.textContent = `Show all ${days.length.toLocaleString("en-GB")} days`;
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
            { class: "card" },
            h("div", { class: "table-scroll tall" }, h("table", { class: "data left" }, h("thead", {}, h("tr", {}, ["Day", ...metrics].map((name) => h("th", { scope: "col" }, name)))), body)),
            h("div", { class: "card-foot" }, more),
          )
        : message("No report files for this service and period. Choose a longer period above, or check the source on the Sources page."),
    );
  },
};
