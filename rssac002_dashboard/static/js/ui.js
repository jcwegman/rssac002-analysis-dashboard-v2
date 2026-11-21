// Building blocks shared by the pages.

import { hint, legend, lineChart, saveFigure, seriesTable } from "./charts.js";
import { h } from "./format.js";

let helpCount = 0;

/**
 * A small "?" that explains the thing it sits beside when hovered, focused or tapped.
 * The text is also in the page for screen readers, tied to the button.
 */
export function help(subject, text) {
  if (!text) return null;
  const id = `help-${++helpCount}`;
  const button = h("button", { class: "help", type: "button", "aria-label": `About: ${subject}`, "aria-describedby": id }, "?");
  hint(button, text);
  return [button, h("span", { class: "sr-only", id }, text)];
}

/** A page title with its explanation behind a "?". */
export function pageHead(title, description) {
  return h("div", { class: "page-head" }, h("h1", {}, title, help(title, description)));
}

/** A panel with a title, an optional explanation behind a "?", and content. */
export function card(title, note, ...content) {
  return h("section", { class: "card" }, h("div", { class: "card-head" }, h("h3", {}, title, help(title, note))), content);
}

/** Buttons under a chart: reveal its numbers as a table, plus any extra actions. */
export function tableToggle(build, ...actions) {
  const holder = h("div", { hidden: true });
  const button = h("button", { class: "button quiet", type: "button", "aria-expanded": "false" }, "Show as table");
  button.addEventListener("click", () => {
    const open = holder.hidden;
    if (open && !holder.firstChild) holder.append(build());
    holder.hidden = !open;
    button.textContent = open ? "Hide table" : "Show as table";
    button.setAttribute("aria-expanded", String(open));
  });
  return [h("div", { class: "card-foot" }, actions, button), holder];
}

export function saveButton(plot, figure) {
  return h("button", { class: "button quiet", type: "button", onclick: () => saveFigure(plot, figure) }, "Save figure");
}

export const slug = (text) => text.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

/**
 * A line chart in a card, with legend (for two or more series), a caption that states the
 * axis, scale and smoothing, a table view and a figure download.
 * `axis` says what the vertical axis measures; `context` names the service and period for the saved figure.
 */
export function lineCard({ title, note, days, series, unit, scale, axis, smoothed, events, context = "", span = 6, height }) {
  const caption = [axis, scale === "log" && "logarithmic axis", smoothed && "7-day trailing mean"].filter(Boolean).join(", ");
  const plot = lineChart({ days, series, unit, scale, events, height, label: `${title}. Use the left and right arrow keys to read values.` });
  const figure = { title: context ? `${title}, ${context}` : title, series, caption, filename: slug(`${context} ${title}`) };
  const element = card(title, note, series.length > 1 && legend(series), plot, caption && h("p", { class: "caption" }, caption), tableToggle(() => seriesTable(days, series, unit), saveButton(plot, figure)));
  element.classList.add(`span-${span}`);
  return element;
}

export function section(title, description, ...content) {
  return h("section", { class: "section" }, h("div", { class: "section-head" }, h("h2", {}, title, help(title, description))), content);
}

export function message(text, isError = false) {
  return h("p", { class: isError ? "message error" : "message", role: isError ? "alert" : "status" }, text);
}

/** A labelled form control. */
export function field(label, control) {
  return h("label", { class: "field" }, label, control);
}

export function select(options, value, onChange) {
  const element = h("select", { onchange: (event) => onChange(event.target.value) }, options.map(([key, label]) => h("option", { value: key }, label)));
  element.value = value;
  return element;
}

export function countryName(code) {
  if (!code) return "";
  try {
    return new Intl.DisplayNames(["en"], { type: "region" }).of(code) || code;
  } catch (error) {
    return code;
  }
}
