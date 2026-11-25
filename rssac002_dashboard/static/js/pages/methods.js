// How the numbers on the dashboard are defined, so they can be cited and reproduced.

import { formatDay, h } from "../format.js";
import { countryName } from "../ui.js";

const MEASURES = [
  ["Queries received", "Sum of the four counters dns-{udp,tcp}-queries-received-{ipv4,ipv6}, plus the queries over TLS and QUIC where the operator reports them (see below).", "traffic-volume"],
  ["UDP and TCP queries", "Sum of the four counters dns-{udp,tcp}-queries-received-{ipv4,ipv6} only: the queries RSSAC002 defines, for comparing services whether or not they report TLS and QUIC.", "traffic-volume"],
  ["Responses sent", "Sum of the four counters dns-{udp,tcp}-responses-sent-{ipv4,ipv6}, plus the responses over TLS and QUIC where the operator reports them.", "traffic-volume"],
  ["Responses per query", "Responses sent divided by queries received.", "traffic-volume"],
  ["IPv6 share of queries", "Queries received over IPv6 (all transports) divided by queries received.", "traffic-volume"],
  ["TCP share of queries", "Queries received over TCP (IPv4 and IPv6) divided by the queries received over UDP and TCP. TLS and QUIC are left out, because the share is read as a sign of truncated UDP responses.", "traffic-volume"],
  ["TLS and QUIC share of queries", "Queries received over TLS and QUIC divided by queries received. Only given for a service that reports these transports.", "traffic-volume"],
  ["Response code shares", "The count for a code divided by the sum of all counts in the report, including code −1. NOERROR is 0, NXDOMAIN 3, SERVFAIL 2, REFUSED 5.", "rcode-volume"],
  ["Unanswered share", "The count for code −1 divided by the sum of all counts. Code −1 is not a DNS response code: the ENTRADA-based generator uses it for queries without a matched response.", "rcode-volume"],
  ["Unique IPv4 and IPv6 sources", "num-sources-ipv4 and num-sources-ipv6: distinct source addresses seen during the day.", "unique-sources"],
  ["Unique IPv6 /64 prefixes", "num-sources-ipv6-aggregate: distinct /64 prefixes among the IPv6 sources. One network often uses many addresses within a /64, so this is the better estimate of the number of IPv6 networks.", "unique-sources"],
  ["Unique sources per day", "IPv4 sources plus IPv6 sources.", "unique-sources"],
  ["Queries per source", "Queries received divided by unique sources per day.", "traffic-volume, unique-sources"],
  ["Median and 95th percentile size", "The upper edge of the 16-byte step that contains the 50th or 95th percentile of messages. For the open-ended last step (288 bytes for queries, 4,096 for responses) the lower edge is given.", "traffic-sizes"],
];

// The document that defines the measurements and the file format.
const ADVISORY = "https://www.icann.org/en/system/files/files/rssac-002-measurements-root-22jun23-en.pdf";
const advisory = (text) => h("a", { href: ADVISORY, rel: "noopener" }, text);

const NOTES = [
  ["Days are UTC days", "Each report covers one day starting at 00:00 UTC. A report whose window starts at another time is imported with a note, which is shown beside the file on the Report files page."],
  ["Missing days are gaps", "A day without a report has no value. It is never shown or counted as zero, and it is left out of every mean, median and total. It does not mean the servers received no queries."],
  ["Unique sources do not add up", "The same address can query on several days and several nameservers. Summing daily or per-nameserver counts overstates the number of distinct sources; only the combined report for a day gives that day’s figure."],
  ["Period figures", "Queries per day and unique sources per day are the mean of the daily values in the period. Shares over a period are the ratio of the period’s totals, not the mean of the daily shares."],
  ["Change against the period before", "The headline figures compare the chosen period with the period of equal length immediately before it. The comparison is left out when that earlier period has reports for fewer than 90% of its days; a full year set against part of an earlier year would not be a real change."],
  ["Logarithmic axes", "Used where series differ by orders of magnitude. Zero has no position on such an axis, so a day with a count of zero appears as a gap in that line; the readout and the table still give 0."],
  ["7-day trailing mean", "With this option each point is the mean of that day and the six days before it, which removes the weekly rhythm. A day without a report stays a gap, and a mean is shown only when at least four of the seven days have values. Tables under smoothed charts show the smoothed values; the summary statistics and the CSV download always use the daily values."],
  ["Size heatmaps", "Colour encodes the number of messages in a size step on a day, on a logarithmic scale shared by the panels. Blank cells are zero; grey columns are days without a report. Queries are drawn in the 16-byte steps of the reports. Responses are drawn in 64-byte steps: in 16-byte steps up to 4,096 bytes a panel would have more rows than pixels. The CSV download of a panel always has every 16-byte step."],
  ["Share-based heatmaps", "With \u201cShare of the day\u201d each cell is its count divided by that day\u2019s total for the panel, so the colour shows the size mix independently of traffic growth."],
  ["A year earlier", "The comparison uses the same period shifted back 364 days (52 weeks), so that weekdays line up."],
  ["Events", "Markers above a chart are events recorded by the dashboard\u2019s administrator, such as a capture outage. They are annotations, not derived from the data."],
  ["Consistency checks", "The Data quality page compares numbers that the metrics count in two ways, for example nameserver totals against the combined report. A difference above the stated tolerance is listed with the days concerned."],
  ["DNS over TLS and QUIC", "RSSAC002 counts DNS over UDP and TCP only. Operators that also serve DNS over TLS or QUIC, such as b and h root, report it in fields of their own, which the advisory allows. The dashboard counts these in queries received and responses sent, and draws them as lines of their own. A service that does not report them shows them as not reported, not as zero. \u201cUDP and TCP queries\u201d on the Compare page gives the figure without them."],
  ["Totals of several services", "A service such as root-servers.net is computed by the dashboard: for each day, the reports of all its members are added up. A day is given only when every member has a report for that metric; otherwise it is a gap, because a sum over fewer members is not the total. Unique sources are never added up, since an address that queries several members would be counted more than once, so such a service has none. TLS and QUIC counts are added up over the members that report them."],
  ["Summary statistics", "Computed over the daily values in the period. The standard deviation is the sample standard deviation (n − 1); CV is the standard deviation divided by the mean."],
  ["Validation", "Reports are checked on import. A report with a wrong type, a negative count or a malformed size step is rejected and listed in the source’s import history. A value explicitly set to null is treated as not reported."],
];

export const methods = {
  title: "Methods",

  load: () => null,

  view(context) {
    const sources = context.meta.sources.filter((source) => source.reports);
    const table = (headings, rows) => h("div", { class: "card" }, h("table", { class: "data left wrap" }, h("thead", {}, h("tr", {}, headings.map((name) => h("th", { scope: "col" }, name)))), h("tbody", {}, rows)));
    return h(
      "div",
      { class: "prose" },
      h(
        "div",
        { class: "page-head" },
        h("h1", {}, "Methods and definitions"),
        h("p", {}, "What each number on this dashboard means and how it is derived from the published reports, so that figures taken from it can be described and reproduced."),
      ),

      h("h2", {}, "Data"),
      h("p", {}, "The dashboard holds RSSAC002-style reports as published by each operator: one YAML file per metric per UTC day, for all nameservers together and, where published, per nameserver. Files are stored unchanged and can be read or downloaded on the ", h("a", { href: "#/reports" }, "Report files"), " page. Everything shown is computed from those files; the dashboard does not see DNS traffic itself."),
      h(
        "p",
        {},
        "RSSAC002 is an advisory of ICANN\u2019s Root Server System Advisory Committee that defines these measurements and the format of the files: ",
        advisory("RSSAC002 version 5, \u201cRSSAC Advisory on Measurements of the Root Server System\u201d, 22 June 2023"),
        ". It is written for the root servers; other operators, such as TLD registries, publish reports in the same format.",
      ),
      sources.length
        ? table(
            ["Service", "Operator", "Country", "Reports", "First day", "Last day"],
            sources.map((source) => h("tr", {}, h("th", { scope: "row" }, source.name), h("td", {}, source.operator || "Not given"), h("td", {}, countryName(source.country) || "Not given"), h("td", { class: "num" }, source.reports.toLocaleString("en-GB")), h("td", {}, formatDay(source.first_day, true)), h("td", {}, formatDay(source.last_day, true)))),
          )
        : null,

      h("h2", {}, "Measures"),
      table(["Measure", "Definition", "Metric"], MEASURES.map(([name, definition, metric]) => h("tr", {}, h("th", { scope: "row" }, name), h("td", {}, definition), h("td", { class: "mono" }, metric)))),

      h("h2", {}, "Reading the charts and tables"),
      h("dl", { class: "notes" }, NOTES.map(([term, text]) => [h("dt", {}, term), h("dd", {}, text)])),

      h("h2", {}, "Reusing figures and data"),
      h("p", {}, "Every chart can be saved as an SVG figure with its title, legend and caption, and shown as a table. Saved figures are always in the light theme. The daily values behind the overview can be downloaded as CSV from its Summary statistics section, and any view can be shared by its address. The reports belong to the operators that publish them: cite the operator, and check its licence, when you reuse figures or data."),
    );
  },
};
