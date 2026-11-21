// Where reports come from. Anyone can see the list; an admin can add, re-import and remove.

import { api } from "../api.js";
import { formatDay, h } from "../format.js";
import { card, countryName, field, help, message, pageHead, select } from "../ui.js";

const ACTIVE = ["queued", "running"];

function scanSummary(scan) {
  const counts = `${scan.found.toLocaleString("en-GB")} files found, ${scan.imported} imported, ${scan.updated} updated, ${scan.failed} failed`;
  return scan.message ? `${counts}. ${scan.message}` : counts;
}

export const sources = {
  title: "Sources",

  load: () => null,

  view(context) {
    let meta = context.meta;
    let timer = null;
    context.onLeave(() => clearTimeout(timer));
    const tableHolder = h("div");
    const adminHolder = h("div", { class: "stack" });
    const eventsHolder = h("div");
    const notice = h("div");
    const say = (text, isError = false) => notice.replaceChildren(text ? message(text, isError) : "");

    async function refresh() {
      api.clearCache();
      meta = await api.meta();
      context.setMeta(meta);
      drawTable();
    }

    function act(run, done) {
      return async () => {
        say("");
        try {
          await run();
          if (done) say(done);
          await refresh();
        } catch (error) {
          say(error.message, true);
        }
      };
    }

    async function showHistory(source, holder) {
      const { scans } = await api.scans(source.id);
      holder.replaceChildren(
        ...(scans.length ? scans : [null]).map((scan) =>
          scan
            ? h(
                "div",
                {},
                h("span", { class: `status ${scan.status}` }, `${scan.started_at.slice(0, 16).replace("T", " ")} UTC, ${scan.mode === "full" ? "full re-import" : "new reports"}: ${scanSummary(scan)}`),
                scan.errors.length ? h("ul", { class: "errors" }, scan.errors.slice(0, 20).map((error) => h("li", {}, h("span", { class: "mono" }, error.origin.split("/").at(-1)), `: ${error.error}`))) : null,
              )
            : h("p", { class: "muted" }, "No imports have run yet."),
        ),
      );
    }

    function drawTable() {
      clearTimeout(timer);
      const admin = meta.admin.signed_in;
      if (!meta.sources.length) {
        tableHolder.replaceChildren(message(admin ? "No sources yet. Add the first one below." : "No sources yet. An admin can add them here."));
        return;
      }
      const rows = meta.sources.flatMap((source) => {
        const scan = source.last_scan;
        const busy = scan && ACTIVE.includes(scan.status);
        const status = scan ? h("span", { class: `status ${scan.status}` }, busy ? "Importing now" : scan.status === "failed" ? "Last import failed" : `Checked ${formatDay(scan.started_at.slice(0, 10), true)}`) : h("span", { class: "muted" }, "Never checked");
        const history = h("div", { class: "stack" });
        const details = h("details", { class: "history", ontoggle: (event) => event.target.open && showHistory(source, history).catch((error) => history.replaceChildren(message(error.message, true))) }, h("summary", {}, "Import history"), history);
        const remove = () => {
          if (!window.confirm(`Remove ${source.name} and delete its ${source.reports.toLocaleString("en-GB")} imported reports from the dashboard?`)) return;
          act(() => api.removeSource(source.id), `Removed ${source.name}.`)();
        };
        const main = h(
          "tr",
          { class: admin ? "open" : "" },
          h("th", { scope: "row" }, source.name),
          h("td", {}, source.operator || h("span", { class: "muted" }, "Not given")),
          h("td", {}, countryName(source.country) || h("span", { class: "muted" }, "Not given")),
          h("td", { class: "num" }, source.reports.toLocaleString("en-GB")),
          h("td", {}, source.reports ? `${formatDay(source.first_day, true)} to ${formatDay(source.last_day, true)}` : h("span", { class: "muted" }, "No reports")),
          h("td", {}, status),
          admin &&
            h(
              "td",
              {},
              h(
                "div",
                { class: "actions" },
                h("button", { class: "button quiet", type: "button", disabled: busy, onclick: act(() => api.startScan(source.id, "new")) }, "Check for new reports"),
                h("button", { class: "button quiet", type: "button", disabled: busy, onclick: act(() => api.startScan(source.id, "full")) }, "Re-import everything"),
                h("button", { class: "button quiet danger", type: "button", onclick: remove }, "Remove"),
              ),
            ),
        );
        const extra = admin ? h("tr", {}, h("td", { colSpan: 7 }, h("div", { class: "muted mono" }, source.location), details)) : null;
        return [main, extra];
      });
      const headings = ["Service", "Operator", "Country", "Reports", "Covers", "Status", admin ? "" : null].filter((name) => name !== null);
      tableHolder.replaceChildren(
        h("div", { class: "card" }, h("table", { class: "data left" }, h("thead", {}, h("tr", {}, headings.map((name) => h("th", { scope: "col", class: name === "Reports" ? "num" : "" }, name)))), h("tbody", {}, rows))),
      );
      if (meta.sources.some((source) => source.last_scan && ACTIVE.includes(source.last_scan.status))) {
        timer = setTimeout(() => refresh().catch(() => {}), 2000);
      }
    }

    function addForm() {
      const location = h("input", { type: "text", required: true, placeholder: "https://github.com/operator/reports, https://example.org/rssac002/ or /srv/reports/nl", autocomplete: "off", spellcheck: false });
      const inputs = {
        name: h("input", { type: "text", placeholder: ".nl", autocapitalize: "none", spellcheck: false }),
        short_service: h("input", { type: "text", placeholder: "nl", autocapitalize: "none", spellcheck: false }),
        operator: h("input", { type: "text" }),
        country: h("input", { type: "text", placeholder: "NL", maxLength: 2 }),
        import_from: h("input", { type: "date" }),
      };
      const hint = h("p", { class: "muted" });
      const details = h(
        "div",
        { class: "form", hidden: true },
        hint,
        h("div", { class: "form-row" }, field("Name, such as .nl or b.root-servers.net", inputs.name), field("File name prefix (short service)", inputs.short_service)),
        h("div", { class: "form-row" }, field("Operator", inputs.operator), field("Country code (optional)", inputs.country)),
        h("div", { class: "form-row" }, field("Import reports from this day on (optional, for sources with a long history)", inputs.import_from)),
        h("div", {}, h("button", { class: "button primary", type: "submit" }, "Add source and import reports")),
      );
      const lookUp = h("button", { class: "button", type: "button" }, "Look up");
      lookUp.addEventListener("click", async () => {
        say("");
        try {
          const found = await api.previewSource({ location: location.value });
          location.value = found.location;
          for (const [name, input] of Object.entries(inputs)) input.value = found.profile[name] || "";
          hint.textContent = found.has_info ? "These details come from the info.yaml published there. Change them if needed." : "No info.yaml is published there. Fill in the details by hand.";
          details.hidden = false;
        } catch (error) {
          say(error.message, true);
        }
      });
      const form = h("form", { class: "form" }, h("div", { class: "form-row" }, field("Git repository, web address or folder on this server", location), lookUp), details);
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        if (details.hidden) return lookUp.click();
        const body = { location: location.value };
        for (const [name, input] of Object.entries(inputs)) body[name] = input.value.trim();
        await act(() => api.addSource(body), "Source added. Its reports are being imported now.")();
        if (!notice.querySelector(".error")) form.reset(), (details.hidden = true);
      });
      return card("Add another source", "The place must hold reports in the RSSAC002 layout: year, month, metric, then one YAML file per day. For a Git repository, paste the address of the repository or of the folder inside it.", form);
    }

    async function drawEvents() {
      const { events } = await api.events();
      const admin = meta.admin.signed_in;
      const nameOf = (id) => (id === null ? "All sources" : meta.sources.find((source) => source.id === id)?.name || "Removed source");
      const rows = events.map((event) =>
        h(
          "tr",
          {},
          h("th", { scope: "row" }, formatDay(event.day, true)),
          h("td", {}, nameOf(event.source_id)),
          h("td", {}, event.label),
          admin && h("td", {}, h("div", { class: "actions" }, h("button", { class: "button quiet danger", type: "button", onclick: () => act(() => api.removeEvent(event.id), "Event removed.")().then(drawEvents) }, "Remove"))),
        ),
      );
      const list = events.length
        ? h("table", { class: "data left wrap" }, h("thead", {}, h("tr", {}, ["Day", "Applies to", "What happened", admin ? "" : null].filter((name) => name !== null).map((name) => h("th", { scope: "col" }, name)))), h("tbody", {}, rows))
        : h("p", { class: "empty" }, admin ? "No events recorded yet." : "No events have been recorded.");
      let form = null;
      if (admin) {
        let scope = "";
        const day = h("input", { type: "date", required: true });
        const label = h("input", { type: "text", required: true, maxLength: 200, placeholder: "Capture outage on ns1" });
        form = h("form", { class: "form-row" }, field("Day (UTC)", day), field("Applies to", select([["", "All sources"], ...meta.sources.map((source) => [String(source.id), source.name])], "", (value) => (scope = value))), field("What happened", label), h("button", { class: "button primary", type: "submit" }, "Record event"));
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          await act(() => api.addEvent({ source_id: scope ? Number(scope) : null, day: day.value, label: label.value }), "Event recorded.")();
          drawEvents();
        });
      }
      eventsHolder.replaceChildren(card("Events", "Known incidents and changes, marked on the charts on their day so that readers do not mistake them for a change in DNS traffic.", list, form));
    }

    /** Known publishers: add all of a publisher's services in one step. */
    function importerCard() {
      const list = h("div", { class: "stack" }, h("p", { class: "muted" }, "Loading…"));
      api
        .importers()
        .then(({ importers }) =>
          list.replaceChildren(
            ...importers.map((importer) => {
              const since = h("input", { type: "date" });
              const done = importer.added === importer.services.length;
              const state = importer.added ? `${importer.added} of ${importer.services.length} added` : `${importer.services.length} ${importer.services.length === 1 ? "service" : "services"}`;
              const run = act(() => api.runImporter(importer.key, since.value), `Importing ${importer.title}. This can take a few minutes the first time.`);
              return h(
                "div",
                { class: "importer" },
                h("div", {}, h("b", {}, importer.title), help(importer.title, importer.description), h("span", { class: "muted" }, ` ${state}`)),
                h("div", { class: "form-row" }, done ? null : field("Import from (optional)", since), h("button", { class: done ? "button" : "button primary", type: "button", onclick: () => run().then(drawAdmin) }, done ? "Check for new reports" : "Add and import")),
              );
            }),
          ),
        )
        .catch((error) => list.replaceChildren(message(error.message, true)));
      return card("Add a known publisher", "Publishers whose location and file names the dashboard already knows.", list);
    }

    function drawAdmin() {
      drawEvents().catch((error) => eventsHolder.replaceChildren(message(error.message, true)));
      if (!meta.admin.configured) {
        adminHolder.replaceChildren(
          card(
            "Managing sources",
            null,
            h("p", {}, "No admin password is set on this server, so sources are managed from the command line: ", h("code", {}, "rssac002-dashboard add <web address or folder>"), "."),
            h("p", { class: "note" }, "To manage them here instead, create a password hash with ", h("code", {}, "rssac002-dashboard hash-password"), ", put it in the RSSAC002_ADMIN_PASSWORD_HASH environment variable and restart."),
          ),
        );
      } else if (!meta.admin.signed_in) {
        const password = h("input", { type: "password", required: true, autocomplete: "current-password" });
        const form = h("form", { class: "form-row" }, field("Admin password", password), h("button", { class: "button primary", type: "submit" }, "Sign in"));
        form.addEventListener("submit", async (event) => {
          event.preventDefault();
          await act(() => api.signIn(password.value))();
          drawAdmin();
        });
        adminHolder.replaceChildren(card("Managing sources", "Sign in to add, re-import or remove sources.", form));
      } else {
        const signOut = h("button", { class: "button", type: "button", onclick: async () => (await act(() => api.signOut())(), drawAdmin()) }, "Sign out");
        adminHolder.replaceChildren(importerCard(), addForm(), h("div", {}, signOut));
      }
    }

    drawTable();
    drawAdmin();
    return h(
      "div",
      { class: "stack" },
      pageHead("Sources", "Each source is a place where a DNS operator, such as a TLD registry or a root server operator, publishes its RSSAC002 reports: a Git repository, a web address with a directory listing, or a folder on this server. The dashboard checks every source for new reports on a schedule."),
      notice,
      tableHolder,
      eventsHolder,
      adminHolder,
    );
  },
};
