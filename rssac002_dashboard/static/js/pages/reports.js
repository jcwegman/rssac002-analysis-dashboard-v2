// The imported report files themselves: browse, read, download.

import { api } from "../api.js";
import { formatDay, formatRange, h } from "../format.js";
import { field, message, pageHead, select } from "../ui.js";

const PAGE_SIZE = 200;

export const reports = {
  title: "Report files",

  load: (context) => (context.source ? api.reports({ source: context.source.id, server: context.server, metric: context.query.get("metric"), from: context.first, to: context.last }) : null),

  view(context, data) {
    const { source, server, first, last, query, go, meta } = context;
    const rows = data.reports;
    const metric = meta.metrics.includes(query.get("metric")) ? query.get("metric") : "";
    let shown = PAGE_SIZE;
    const body = h("tbody");
    const more = h("button", { class: "button", type: "button", onclick: () => ((shown = rows.length), fill()) });

    async function toggleRow(row, tr, button) {
      if (tr.nextSibling?.classList?.contains("detail")) {
        tr.nextSibling.remove();
        tr.classList.remove("open");
        button.textContent = "Read";
        return;
      }
      const pre = h("pre", { class: "yaml", tabindex: 0 }, "Loading…");
      tr.after(h("tr", { class: "detail" }, h("td", { colSpan: 5 }, pre)));
      tr.classList.add("open");
      button.textContent = "Close";
      pre.textContent = await api.reportText(row.id).catch((error) => error.message);
    }

    function fill() {
      body.replaceChildren(
        ...rows.slice(0, shown).map((row) => {
          const read = h("button", { class: "button quiet", type: "button" }, "Read");
          const tr = h(
            "tr",
            {},
            h("td", {}, formatDay(row.day, true)),
            h("td", {}, row.metric),
            h("td", {}, h("span", { class: "mono" }, row.filename), row.warnings.map((warning) => h("div", { class: "muted" }, `Note: ${warning}`))),
            h("td", {}, row.fetched_at.slice(0, 16).replace("T", " ")),
            h("td", {}, h("div", { class: "actions" }, read, h("a", { class: "button quiet", href: `/api/reports/${row.id}?download=1` }, "Download"))),
          );
          read.addEventListener("click", () => toggleRow(row, tr, read));
          return tr;
        }),
      );
      more.hidden = rows.length <= shown;
      more.textContent = `Show all ${rows.length.toLocaleString("en-GB")} files`;
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
            h("table", { class: "data left" }, h("thead", {}, h("tr", {}, ["Day", "Metric", "File", "Imported (UTC)", ""].map((name) => h("th", { scope: "col" }, name)))), body),
            h("div", { class: "card-foot" }, more),
          )
        : message("No report files for this service and period. Choose a longer period above, or check the source on the Sources page."),
    );
  },
};
