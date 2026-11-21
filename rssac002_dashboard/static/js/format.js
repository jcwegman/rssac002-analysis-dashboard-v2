// Number, date and DOM helpers shared by every page.

const LOCALE = "en-GB";
const compact = new Intl.NumberFormat(LOCALE, { notation: "compact", maximumSignificantDigits: 3 });
const full = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 });
const dayMonth = new Intl.DateTimeFormat(LOCALE, { day: "numeric", month: "short", timeZone: "UTC" });
const dayMonthYear = new Intl.DateTimeFormat(LOCALE, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const weekday = new Intl.DateTimeFormat(LOCALE, { weekday: "short", timeZone: "UTC" });
const monthOnly = new Intl.DateTimeFormat(LOCALE, { month: "short", timeZone: "UTC" });
const monthYear = new Intl.DateTimeFormat(LOCALE, { month: "short", year: "numeric", timeZone: "UTC" });

export const toDate = (day) => new Date(`${day}T00:00:00Z`);
export const toDay = (date) => date.toISOString().slice(0, 10);
export const addDays = (day, count) => toDay(new Date(toDate(day).getTime() + count * 86400000));
export const daysBetween = (first, last) => Math.round((toDate(last) - toDate(first)) / 86400000);

export const formatDay = (day, withYear = false) => (withYear ? dayMonthYear : dayMonth).format(toDate(day));
export const formatDayLong = (day) => `${weekday.format(toDate(day))} ${dayMonthYear.format(toDate(day))}`;
export const formatMonth = (day, withYear = false) => (withYear ? monthYear : monthOnly).format(toDate(day));

export function formatRange(first, last) {
  const sameYear = first.slice(0, 4) === last.slice(0, 4);
  return `${formatDay(first, !sameYear)} to ${formatDay(last, true)}`;
}

function percent(value, digits) {
  return `${(value * 100).toFixed(digits)}%`;
}

/** Format a value for reading. `precise` gives the full figure for tooltips and tables. */
export function formatValue(value, unit, precise = false) {
  if (value === null || value === undefined || Number.isNaN(value)) return "no data";
  if (unit === "share") {
    if (value > 0 && value < 0.00005) return "<0.01%";
    return percent(value, Math.abs(value) < 0.01 && value !== 0 ? 2 : 1);
  }
  if (unit === "bytes") return `${full.format(value)} bytes`;
  if (unit === "ratio") return Math.abs(value) >= 100 ? full.format(value) : value.toFixed(2);
  if (unit === "index") return value.toFixed(1);
  return precise || Math.abs(value) < 10000 ? full.format(value) : compact.format(value);
}

/** Axis tick label: short, with just enough decimals for the tick spacing. */
export function formatTick(value, unit, step) {
  if (unit === "share") return percent(value, step < 0.001 ? 2 : step < 0.01 ? 1 : 0);
  if (unit === "ratio" || unit === "index") return step < 0.1 ? value.toFixed(2) : step < 1 ? value.toFixed(1) : full.format(value);
  if (unit === "bytes") return full.format(value);
  return Math.abs(value) < 10000 ? full.format(value) : compact.format(value);
}

/**
 * Trailing mean over `window` days, ending on each day. A day without a value stays a gap, and
 * a mean is only given when at least half of the window has values.
 */
export function rollingMean(values, window = 7) {
  return values.map((value, index) => {
    if (value === null || value === undefined) return null;
    const present = values.slice(Math.max(0, index - window + 1), index + 1).filter((one) => one !== null && one !== undefined);
    return present.length >= window / 2 ? present.reduce((sum, one) => sum + one, 0) / present.length : null;
  });
}

/** Descriptive statistics of the values that are present, or null when there are none. */
export function describe(values) {
  const present = values.filter((value) => value !== null && value !== undefined).sort((a, b) => a - b);
  const count = present.length;
  if (!count) return null;
  const mean = present.reduce((sum, value) => sum + value, 0) / count;
  const variance = count > 1 ? present.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (count - 1) : 0;
  const middle = Math.floor(count / 2);
  return { count, mean, median: count % 2 ? present[middle] : (present[middle - 1] + present[middle]) / 2, sd: Math.sqrt(variance), min: present[0], max: present[count - 1] };
}

/** Change between two period values, as text that names the direction. */
export function formatChange(current, previous, unit) {
  if (current === null || previous === null || current === undefined || previous === undefined) return null;
  if (unit === "share") {
    const points = (current - previous) * 100;
    if (Math.abs(points) < 0.05) return "No change";
    return `${Math.abs(points).toFixed(1)} points ${points > 0 ? "higher" : "lower"}`;
  }
  if (!previous) return null;
  const change = (current - previous) / previous;
  if (Math.abs(change) < 0.0005) return "No change";
  return `${(Math.abs(change) * 100).toFixed(1)}% ${change > 0 ? "higher" : "lower"}`;
}

/**
 * Build a DOM element. Strings become text nodes, so data never turns into markup.
 * Attributes starting with "on" are event listeners; `class`, `dataset` and the rest are set as given.
 */
export function h(tag, attrs = {}, ...children) {
  const element = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (name.startsWith("on")) element.addEventListener(name.slice(2), value);
    else if (name === "class") element.className = value;
    else if (name === "dataset") Object.assign(element.dataset, value);
    else if (name in element && name !== "list" && name !== "form") element[name] = value;
    else element.setAttribute(name, value === true ? "" : value);
  }
  element.append(...children.flat(Infinity).filter((child) => child !== null && child !== undefined && child !== false));
  return element;
}
