// Drives the dashboard in headless Chrome over the DevTools protocol and checks what a user sees.
// Usage: node check.mjs <base-url> <path-to-chrome> [admin-password]
// Expects the demo data (three services with nameservers ns1-ns3). Run through tests/test_browser.py.

import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [base, chromePath, password = ""] = process.argv.slice(2);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const profile = mkdtempSync(join(tmpdir(), "rssac002-browser-"));
const port = 9400 + Math.floor(Math.random() * 400);
const chrome = spawn(chromePath, ["--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars", `--user-data-dir=${profile}`, `--remote-debugging-port=${port}`, "--window-size=1440,1000", "about:blank"], { stdio: "ignore" });

let target;
for (let attempt = 0; attempt < 60 && !target; attempt += 1) {
  await sleep(250);
  target = await fetch(`http://127.0.0.1:${port}/json`).then((response) => response.json()).then((list) => list.find((one) => one.type === "page")).catch(() => null);
}
const socket = new WebSocket(target.webSocketDebuggerUrl);
await new Promise((resolve) => socket.addEventListener("open", resolve));
let nextId = 0;
const pending = new Map();
const problems = [];
socket.addEventListener("message", (event) => {
  const message = JSON.parse(event.data);
  if (message.id && pending.has(message.id)) pending.get(message.id)(message.result || message);
  if (message.method === "Runtime.exceptionThrown") problems.push(`exception: ${message.params.exceptionDetails.exception?.description || message.params.exceptionDetails.text}`);
  if (message.method === "Runtime.consoleAPICalled" && message.params.type === "error") problems.push(`console.error: ${message.params.args.map((arg) => arg.value || arg.description).join(" ")}`);
  if (message.method === "Log.entryAdded" && message.params.entry.level === "error" && !/\b401\b/.test(message.params.entry.text)) problems.push(`log: ${message.params.entry.text} ${message.params.entry.url || ""}`);
});
const send = (method, params = {}) => new Promise((resolve) => { const id = ++nextId; pending.set(id, resolve); socket.send(JSON.stringify({ id, method, params })); });
const js = async (expression) => {
  const result = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || "evaluation failed");
  return result.result?.value;
};
/** Wait until an expression is truthy in the page, or give up after five seconds. */
const until = async (expression) => {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const value = await js(expression).catch(() => null);
    if (value) return value;
    await sleep(100);
  }
  return null;
};
const goto = async (hash, ready) => { await js(`location.hash = ${JSON.stringify(hash)}`); await sleep(150); return until(`!document.getElementById("main").classList.contains("loading") && (${ready})`); };
const mouse = (type, x, y, buttons = 0) => send("Input.dispatchMouseEvent", { type, x, y, button: "left", buttons, clickCount: 1 });
const box = (selector) => js(`(() => { const r = document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect(); return { x: r.left, y: r.top, w: r.width, h: r.height }; })()`);
const click = (text) => js(`[...document.querySelectorAll("button, summary, a.button")].find((one) => one.textContent.trim() === ${JSON.stringify(text)}).click()`);
const failures = [];
const check = (name, ok, detail = "") => { console.log(`${ok ? "ok  " : "FAIL"} ${name}${ok || !detail ? "" : `  (${detail})`}`); if (!ok) failures.push(name); };

await send("Runtime.enable"); await send("Log.enable"); await send("Page.enable");
await send("Page.navigate", { url: `${base}/#/overview?range=all` });
await until(`document.querySelectorAll(".plot > svg").length >= 12`);

// --- overview ---------------------------------------------------------------------------
check("the tab title names the service", (await js(`document.title`)) === ".example: Overview | RSSAC002 Analysis Dashboard", await js(`document.title`));
check("overview draws its charts and four heatmaps", (await js(`document.querySelectorAll(".plot > svg").length`)) >= 13 && (await js(`document.querySelectorAll("canvas.heat-cells").length`)) === 4);
const titles = await js(`[...document.querySelectorAll(".card h3")].map((one) => one.textContent).join("|")`);
check("overview has the breakdown and per-code charts", ["Queries received by transport and IP version", "Responses per response code"].every((title) => titles.includes(title)), titles);
const first = await box(".plot svg");
await mouse("mouseMoved", first.x + first.w * 0.5, first.y + first.h * 0.4);
const tip = await until(`document.querySelector(".tip:not([hidden])")?.textContent`);
check("hovering a line chart reads out every series", !!tip && tip.includes("Queries received") && tip.includes("Responses sent"), tip);
await mouse("mouseMoved", 5, 5);
await js(`document.querySelector(".plot svg").focus(); document.querySelector(".plot svg").dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }))`);
check("arrow keys give the same readout", !!(await until(`document.querySelector(".tip:not([hidden])")?.textContent.includes("Queries received")`)));
check("no description sits under a title", (await js(`document.querySelectorAll(".card-head p, .section-head p, .page-head p").length`)) === 0);
await js(`document.querySelector(".section .help").focus()`);
const explained = await until(`document.querySelector(".tip:not([hidden]) .tip-text")?.textContent`);
check("a question mark explains its section on focus", !!explained && explained.startsWith("From the traffic-volume metric"), explained);
await js(`document.activeElement.blur()`);
check("and the explanation goes away again", !!(await until(`document.querySelector(".tip").hidden`)));
await click("Show as table");
check("a chart can be shown as a table", (await until(`document.querySelector(".card table.data tbody")?.rows.length`)) > 20);
await js(`window.saved = []; const create = URL.createObjectURL; URL.createObjectURL = (blob) => { window.saved.push(blob); return create.call(URL, blob); };`);
await click("Save figure");
const figure = await until(`window.saved[0] && window.saved[0].text().then((text) => { const doc = new DOMParser().parseFromString(text, "image/svg+xml"); return { ok: !doc.querySelector("parsererror"), title: doc.querySelector("text").textContent, classes: doc.querySelectorAll("[class]").length }; })`);
check("a chart saves as a standalone SVG figure", !!figure && figure.ok && figure.title.startsWith("Queries and responses per day, .example") && figure.classes === 0, JSON.stringify(figure));
await js(`document.querySelector(".heat-grid").scrollIntoView({ block: "center" })`);
await js(`document.querySelector(".heat-head .button").click()`);
check("a heatmap saves as a PNG image", (await until(`window.saved[1] && window.saved[1].type`)) === "image/png");
await js(`[...document.querySelectorAll(".heat-head .button")].find((one) => one.textContent.includes("CSV")).click()`);
const matrix = await until(`window.saved[2] && window.saved[2].text()`);
check("a heatmap's counts download as CSV", !!matrix && matrix.startsWith("day,0-15,16-31") && matrix.split("\n").length > 20);
// Switching theme draws the page again. A chart can only be saved once it is drawn, so wait for
// that: a click on "Save figure" during the redraw does nothing, and the check would time out.
const redrawn = `!document.getElementById("main").classList.contains("loading") && document.querySelector(".plot > svg") && document.querySelector(".heat-frame canvas")`;
await click("Dark theme");
await until(`document.documentElement.dataset.theme === "dark" && ${redrawn}`);
await click("Save figure");
const darkFigure = await until(`window.saved[3] && window.saved[3].text().then((text) => new DOMParser().parseFromString(text, "image/svg+xml").querySelector("rect").getAttribute("fill"))`);
await js(`document.querySelector(".heat-head .button").click()`);
// The image saved from the dark page must be the very image saved from the light page: same size, same pixels.
await js(`window.pixels = (blob) => createImageBitmap(blob).then((image) => { const canvas = document.createElement("canvas"); canvas.width = image.width; canvas.height = image.height; const context = canvas.getContext("2d"); context.drawImage(image, 0, 0); const data = context.getImageData(0, 0, image.width, image.height).data; let sum = 0; for (let i = 0; i < data.length; i += 1) sum = (sum * 31 + data[i]) % 2147483647; return image.width + "x" + image.height + ":" + sum; })`);
const darkHeatmap = await until(`window.saved[4] && Promise.all([window.pixels(window.saved[1]), window.pixels(window.saved[4])])`);
check("figures are saved in the light theme while the dark theme is shown", darkFigure === "#ffffff" && !!darkHeatmap && darkHeatmap[0] === darkHeatmap[1] && (await js(`document.documentElement.dataset.theme`)) === "dark", `${darkFigure} ${darkHeatmap}`);
await click("Light theme");
await until(`document.documentElement.dataset.theme === "light" && ${redrawn}`);
await click("Share of the day");
check("heatmaps switch to shares", !!(await until(`location.hash.includes("heat=share") && document.querySelector(".heat-legend")?.textContent.includes("share of that day")`)));
await click("Cite this view");
check("the citation names the operator, period and address", (await js(`document.querySelector(".cite p").textContent`)).includes("Example Registry. RSSAC002 traffic reports for .example") && (await js(`document.querySelector(".cite p").textContent.includes(location.origin)`)));
const csv = await js(`fetch([...document.querySelectorAll("a.button")].find((one) => one.textContent.includes("CSV")).href).then((response) => response.text())`);
check("daily values download as CSV", csv.startsWith("day,queries,") && csv.split("\n").length > 20);
check("summary statistics list a row per measure", (await js(`document.querySelectorAll(".table-scroll.tall tbody tr").length`)) === 13);

await js(`document.getElementById("filters").scrollIntoView()`);
const strip = await box(".strip svg");
await mouse("mousePressed", strip.x + strip.w * 0.3, strip.y + 8, 1);
await mouse("mouseMoved", strip.x + strip.w * 0.6, strip.y + 8, 1);
await mouse("mouseReleased", strip.x + strip.w * 0.6, strip.y + 8);
check("dragging the coverage strip sets a custom period", !!(await until(`/range=custom/.test(location.hash) && /from=\\d{4}-\\d\\d-\\d\\d/.test(location.hash) && /to=\\d{4}/.test(location.hash) && document.querySelectorAll("#filters input[type=date]").length === 2`)), await js(`location.hash`));

await goto("#/overview?range=all&scale=log&avg=7&ns=ns1", `document.querySelector(".card .caption")`);
check("log axis, 7-day mean and nameserver are applied", (await js(`document.querySelector(".card .caption").textContent`)) === "Messages per UTC day, logarithmic axis, 7-day trailing mean" && (await js(`document.querySelector(".zone p").textContent`)).includes("Nameserver ns1 only"));

await goto("#/overview", `document.querySelector(".zone p")`);
check("the overview opens on the last six months", (await js(`[...document.querySelectorAll("#filters select")].some((one) => one.value === "6m")`)));
await goto("#/reports", `document.querySelector(".page-head")`);
check("and so do the other pages", (await js(`[...document.querySelectorAll("#filters select")].some((one) => one.value === "6m")`)));

// --- compare ----------------------------------------------------------------------------
await goto("#/compare?range=all", `document.querySelectorAll(".plot svg path.line").length === 3`);
check("compare draws a line per service", (await js(`document.querySelectorAll(".chip").length`)) === 3);
check("the timeline has a labelled row per ticked service", (await until(`document.querySelectorAll(".strip .strip-label").length === 3 && [...document.querySelectorAll(".strip .strip-label")].map((one) => one.textContent).join(",")`)) === ".example,.invalid,.test");
await js(`document.querySelectorAll(".chip input")[1].click()`);
check("unticking a series keeps the others' colours", (await until(`document.querySelectorAll(".plot svg path.line").length === 2 && [...document.querySelectorAll(".legend .key")].map((one) => one.className).join(",")`)) === "key c1,key c3");
check("and loses the row of an unticked one", (await until(`document.querySelectorAll(".strip .strip-label").length === 2 && [...document.querySelectorAll(".strip .strip-key")].map((one) => one.getAttribute("class")).join(",")`)) === "strip-key c1,strip-key c3");
const rows = await box(".strip svg");
await mouse("mousePressed", rows.x + rows.w * 0.5, rows.y + 20, 1);
await mouse("mouseMoved", rows.x + rows.w * 0.8, rows.y + 20, 1);
await mouse("mouseReleased", rows.x + rows.w * 0.8, rows.y + 20);
check("dragging across the rows sets the period", !!(await until(`/range=custom/.test(location.hash) && /from=\\d{4}/.test(location.hash) && document.querySelectorAll(".plot svg path.line").length === 2`)), await js(`location.hash`));
await click("Nameservers");
check("nameservers compare beside the combined figure", !!(await until(`document.querySelectorAll(".plot svg path.line").length === 4 && document.querySelector(".chip").textContent.includes("All nameservers")`)));
await click("A year earlier");
check("a period compares with 52 weeks earlier", !!(await until(`[...document.querySelectorAll(".legend span")].some((one) => one.textContent === "52 weeks earlier")`)));

// --- reports, quality, methods ------------------------------------------------------------
await goto("#/reports?range=7d", `document.querySelectorAll("table.data tbody tr").length >= 5`);
check("report files are listed one day per row, with a file per metric", (await js(`document.querySelectorAll("table.data thead th").length`)) === 5 && (await js(`[...document.querySelectorAll("table.data tbody tr")].every((row) => row.querySelectorAll("button").length === 4)`)));
await click("Open");
check("a report file can be read in place", !!(await until(`document.querySelector("pre.yaml")?.textContent.includes("version: rssac002v5") && document.querySelector(".file-name")?.textContent.includes("traffic-volume.yaml")`)));
check("and downloaded from there", (await js(`document.querySelector(".file-head a.button")?.getAttribute("href")`))?.endsWith("?download=1"));
await click("Close");
check("and closed again", !(await js(`document.querySelector("pre.yaml")`)));
// The first demo service has days without reports: they show as a row of their own, not as a silent jump.
await goto("#/reports?range=all", `document.querySelectorAll("table.data tbody tr").length >= 20`);
await click(await js(`[...document.querySelectorAll("button")].find((one) => one.textContent.startsWith("Show all")).textContent`));
check("days without files are a row that says so", (await until(`document.querySelectorAll("tr.gap").length`)) === 2 && (await js(`document.querySelector("tr.gap").textContent`)).includes("no files (1 day)"), await js(`[...document.querySelectorAll("tr.gap")].map((row) => row.textContent).join(" | ")`));
await goto("#/quality?range=all", `document.querySelectorAll(".figures.three .figure").length === 3`);
const quality = await js(`document.querySelector("main").textContent`);
check("data quality reports gaps and checks", quality.includes("Nameserver query counts add up") && quality.includes("Missing days") && /Holds on all \d+ days/.test(quality));
await goto("#/methods", `document.querySelectorAll(".notes dt").length >= 10`);
check("methods page defines the measures", (await js(`document.querySelectorAll("table.wrap tbody tr").length`)) >= 13 && (await js(`document.getElementById("filters").hidden`)));

// --- sources and admin --------------------------------------------------------------------
await goto("#/sources", `document.querySelectorAll("table.data tbody tr").length >= 3`);
if (password) {
  await js(`document.querySelector("input[type=password]").value = "wrong"; document.querySelector("input[type=password]").form.requestSubmit()`);
  check("a wrong password is explained", !!(await until(`document.querySelector(".message.error")?.textContent.includes("Wrong password")`)));
  await js(`document.querySelector("input[type=password]").value = ${JSON.stringify(password)}; document.querySelector("input[type=password]").form.requestSubmit()`);
  check("signing in shows the admin actions", !!(await until(`document.querySelectorAll(".actions").length >= 3 && document.querySelector("input[placeholder^='https://']")`)));
  await until(`[...document.querySelectorAll("button")].some((one) => one.textContent === "Record event")`);
  await js(`(() => { const form = [...document.querySelectorAll("form")].find((one) => one.textContent.includes("Record event")); const [day, label] = form.querySelectorAll("input"); day.value = "2099-01-02"; label.value = "Browser test event"; form.requestSubmit(); })()`);
  check("an event can be recorded", !!(await until(`[...document.querySelectorAll("table.data td")].some((one) => one.textContent === "Browser test event")`)));
  await js(`[...document.querySelectorAll("tr")].find((row) => row.textContent.includes("Browser test event")).querySelector("button").click()`);
  check("and removed again", !!(await until(`![...document.querySelectorAll("table.data td")].some((one) => one.textContent === "Browser test event")`)));
}
await js(`document.getElementById("theme").click()`);
check("the theme switches to dark", (await until(`getComputedStyle(document.body).backgroundColor`)) === "rgb(13, 17, 21)");

for (const problem of new Set(problems)) console.log(`FAIL page problem: ${problem}`);
socket.close();
chrome.kill();
await sleep(300);
rmSync(profile, { recursive: true, force: true });
process.exit(failures.length || problems.length ? 1 : 0);
