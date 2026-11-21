// The dashboard's own charts, drawn as SVG. No chart library, no external requests.
//
// Every chart has a legend when it shows more than one series, a tooltip that lists all
// series at the pointed-at position, keyboard access to the same readout, and a table view.
// Series colours are classes (c1..c8) so light and dark themes swap in CSS alone.

import { formatDay, formatDayLong, formatMonth, formatTick, formatValue, h, toDate } from "./format.js";

const NS = "http://www.w3.org/2000/svg";

function s(tag, attrs = {}, ...children) {
  const element = document.createElementNS(NS, tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value !== null && value !== undefined && value !== false) element.setAttribute(name, value);
  }
  element.append(...children.filter(Boolean));
  return element;
}

// --- tooltip -----------------------------------------------------------------------

const tip = h("div", { class: "tip", hidden: true, role: "status" });

function showTip(content, x, y) {
  if (!tip.isConnected) document.body.append(tip);
  tip.replaceChildren(...content);
  tip.hidden = false;
  const box = tip.getBoundingClientRect();
  let left = x + 14;
  if (left + box.width > window.innerWidth - 8) left = x - box.width - 14;
  let top = y - box.height - 12;
  if (top < 8) top = y + 18;
  tip.style.left = `${Math.max(8, left)}px`;
  tip.style.top = `${top}px`;
}

function hideTip() {
  tip.hidden = true;
}

/** Show a short explanation beside an element while it is hovered or focused; a tap toggles it. */
export function hint(element, text) {
  const show = () => {
    const box = element.getBoundingClientRect();
    showTip([h("div", { class: "tip-text" }, text)], box.right - 8, box.top);
  };
  element.addEventListener("pointerenter", show);
  element.addEventListener("focus", show);
  element.addEventListener("pointerleave", hideTip);
  element.addEventListener("blur", hideTip);
  element.addEventListener("click", () => (tip.hidden ? show() : hideTip()));
  element.addEventListener("keydown", (event) => event.key === "Escape" && hideTip());
}

function tipRow(colorClass, value, label) {
  return h("div", { class: "tip-row" }, h("i", { class: `key ${colorClass}` }), h("b", {}, value), h("span", {}, label));
}

// --- sizing ------------------------------------------------------------------------

const observers = new Set();

/** Stop watching every chart on the page. Call before replacing the page content. */
export function disposeCharts() {
  for (const observer of observers) observer.disconnect();
  observers.clear();
  hideTip();
}

function responsive(container, draw) {
  let width = 0;
  const observer = new ResizeObserver((entries) => {
    const next = Math.floor(entries[0].contentRect.width);
    if (next > 0 && next !== width) {
      width = next;
      draw(width);
    }
  });
  observer.observe(container);
  observers.add(observer);
}

// --- scales ------------------------------------------------------------------------

function niceTicks(min, max, target = 4) {
  if (min === max) {
    if (min === 0) max = 1;
    else [min, max] = [Math.min(min * 0.9, min * 1.1), Math.max(min * 0.9, min * 1.1)];
  }
  const rough = (max - min) / target;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const ratio = rough / magnitude;
  const step = magnitude * (ratio <= 1 ? 1 : ratio <= 2 ? 2 : ratio <= 5 ? 5 : 10);
  const lo = Math.floor(min / step + 1e-9) * step;
  const hi = Math.ceil(max / step - 1e-9) * step;
  const ticks = [];
  for (let tick = lo; tick <= hi + step / 2; tick += step) ticks.push(Math.abs(tick) < step / 1e6 ? 0 : tick);
  return { lo, hi, step, ticks, position: (value) => (value - lo) / (hi - lo) };
}

/**
 * Value axis. Starts at zero unless the data sits far from zero, where zero would flatten the
 * trend. Even then it always spans at least a quarter of the largest value, so day-to-day noise
 * is not stretched to look like a swing.
 */
function valueScale(values) {
  const present = values.filter((value) => value !== null && value !== undefined);
  if (!present.length) return null;
  const min = Math.min(...present);
  const max = Math.max(...present);
  if (min < 0 || max <= 0) return niceTicks(min, max);
  return niceTicks(min <= 0.6 * max ? 0 : Math.min(min, 0.75 * max), max);
}

/** Logarithmic value axis: one gridline per power of ten around the data. Only positive values have a place. */
function logScale(values) {
  const present = values.filter((value) => value !== null && value !== undefined && value > 0);
  if (!present.length) return null;
  const lo = Math.floor(Math.log10(Math.min(...present)) + 1e-9);
  let hi = Math.ceil(Math.log10(Math.max(...present)) - 1e-9);
  if (hi <= lo) hi = lo + 1;
  const every = hi - lo > 8 ? 2 : 1;
  const ticks = [];
  for (let power = hi; power >= lo; power -= every) ticks.unshift(10 ** power);
  return { lo: 10 ** lo, hi: 10 ** hi, step: 1, ticks, position: (value) => (Math.log10(value) - lo) / (hi - lo) };
}

/** Whether two centred tick labels have room beside each other. */
function fitsBeside(a, b, x) {
  return Math.abs(x(a.index) - x(b.index)) >= ((a.label.length + b.label.length) * 6.2) / 2 + 12;
}

/**
 * Day positions to label, aligned to the calendar and thinned so labels never touch.
 * Short periods label days. Longer ones label month starts, every month or every second, third,
 * fourth, sixth or twelfth: steps that always land on January, the label that carries the year.
 * The first label carries the year too, so a period inside one year still names it.
 */
function dayTicks(days, x) {
  const count = days.length;
  if (count <= 45) {
    const ticks = [];
    days.forEach((day, index) => {
      if (count > 10 && toDate(day).getUTCDay() !== 1) return;
      const tick = { index, label: formatDay(day) };
      if (!ticks.length || fitsBeside(ticks.at(-1), tick, x)) ticks.push(tick);
    });
    return ticks;
  }
  const months = [];
  days.forEach((day, index) => {
    if (day.endsWith("-01")) months.push({ index, day, serial: Number(day.slice(0, 4)) * 12 + Number(day.slice(5, 7)) - 1 });
  });
  for (const stride of [1, 2, 3, 4, 6, 12, 24, 60, 120]) {
    const ticks = months.filter((month) => month.serial % stride === 0).map((month, position) => ({ index: month.index, label: formatMonth(month.day, position === 0 || month.serial % 12 === 0) }));
    if (ticks.every((tick, position) => position === 0 || fitsBeside(ticks[position - 1], tick, x))) return ticks;
  }
  return [];
}

// --- line chart --------------------------------------------------------------------

/**
 * A line per series over days.
 * spec: { days, series: [{ label, color, values }], unit, label, scale?: "log", events?: [{ day, label }] }
 */
export function lineChart(spec) {
  const { days, series, unit } = spec;
  const container = h("div", { class: "plot" });
  const log = spec.scale === "log";
  // Zero has no place on a log axis, so zeros are drawn as gaps there (the readout still says 0).
  const drawn = log ? series.map((one) => ({ ...one, values: one.values.map((value) => (value > 0 ? value : null)) })) : series;
  const scale = (log ? logScale : valueScale)(drawn.flatMap((one) => one.values));
  if (!scale) {
    container.append(h("p", { class: "empty" }, "No values to show for this period."));
    return container;
  }
  const height = spec.height || 230;
  const count = days.length;

  responsive(container, (width) => {
    const tickLabels = scale.ticks.map((tick) => formatTick(tick, unit, log ? tick : scale.step));
    const margin = { top: 12, bottom: 26, left: Math.max(...tickLabels.map((label) => label.length)) * 6.6 + 14, right: 14 };
    const plotHeight = height - margin.top - margin.bottom;
    const y = (value) => margin.top + plotHeight * (1 - scale.position(value));

    // Name each line at its right end when there are few of them and the names do not collide.
    const ends = drawn
      .map((one, index) => ({ index, label: one.label, value: one.values.findLast((value) => value !== null && value !== undefined) }))
      .filter((end) => end.value !== undefined)
      .sort((a, b) => y(a.value) - y(b.value));
    const collide = ends.some((end, i) => i > 0 && y(end.value) - y(ends[i - 1].value) < 15);
    const direct = series.length >= 2 && series.length <= 4 && width >= 520 && !collide && ends.length === series.length;
    if (direct) margin.right = Math.min(150, Math.max(...series.map((one) => one.label.length)) * 6.4 + 18);

    const plotWidth = width - margin.left - margin.right;
    const x = (index) => margin.left + (count === 1 ? plotWidth / 2 : (index / (count - 1)) * plotWidth);
    const root = s("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", tabindex: 0, "aria-label": spec.label });

    scale.ticks.forEach((tick, index) => {
      root.append(
        s("line", { class: tick === 0 ? "axis" : "grid", x1: margin.left, x2: width - margin.right, y1: y(tick), y2: y(tick) }),
        s("text", { class: "tick", x: margin.left - 8, y: y(tick) + 4, "text-anchor": "end" }, tickLabels[index]),
      );
    });
    root.append(s("line", { class: "axis", x1: margin.left, x2: width - margin.right, y1: height - margin.bottom, y2: height - margin.bottom }));
    for (const tick of dayTicks(days, x)) {
      root.append(s("text", { class: "tick", x: x(tick.index), y: height - 8, "text-anchor": "middle" }, tick.label));
    }

    drawn.forEach((one) => {
      const group = s("g", { class: `c${one.color}` });
      let path = "";
      one.values.forEach((value, index) => {
        if (value === null || value === undefined) return;
        const before = one.values[index - 1];
        const after = one.values[index + 1];
        const alone = (before === null || before === undefined) && (after === null || after === undefined);
        if (alone) group.append(s("circle", { class: "dot", cx: x(index), cy: y(value), r: 2.5 }));
        path += `${before === null || before === undefined ? "M" : "L"}${x(index).toFixed(1)},${y(value).toFixed(1)}`;
      });
      group.prepend(s("path", { class: "line", d: path }));
      root.append(group);
    });

    if (direct) {
      for (const end of ends) {
        root.append(s("text", { class: "end-label", x: width - margin.right + 8, y: y(end.value) + 4 }, end.label));
      }
    }

    // Events an admin has recorded: a marker above the plot and a hairline through it.
    const eventsByDay = new Map();
    for (const event of spec.events || []) {
      const index = days.indexOf(event.day);
      if (index < 0) continue;
      if (!eventsByDay.has(event.day)) {
        root.append(s("line", { class: "event", x1: x(index), x2: x(index), y1: margin.top, y2: height - margin.bottom }), s("path", { class: "event-mark", d: `M${x(index) - 4},${margin.top - 7}h8l-4,7z` }));
      }
      eventsByDay.set(event.day, [...(eventsByDay.get(event.day) || []), event.label]);
    }

    // Hover and keyboard readout: a hairline at the nearest day, a dot on each line, one tooltip.
    const cross = s("line", { class: "cross", y1: margin.top, y2: height - margin.bottom, visibility: "hidden" });
    const dots = series.map((one) => s("circle", { class: `dot ring c${one.color}`, r: 4, visibility: "hidden" }));
    root.append(cross, ...dots);
    let current = -1;
    const point = (index, clientX, clientY) => {
      current = index;
      cross.setAttribute("x1", x(index));
      cross.setAttribute("x2", x(index));
      cross.setAttribute("visibility", "visible");
      const rows = [h("div", { class: "tip-title" }, formatDayLong(days[index]))];
      series.forEach((one, i) => {
        const value = one.values[index];
        const placed = drawn[i].values[index];
        const visible = placed !== null && placed !== undefined;
        dots[i].setAttribute("visibility", visible ? "visible" : "hidden");
        if (visible) {
          dots[i].setAttribute("cx", x(index));
          dots[i].setAttribute("cy", y(placed));
        }
        rows.push(tipRow(`c${one.color}`, formatValue(value, unit, true), one.label));
      });
      for (const label of eventsByDay.get(days[index]) || []) rows.push(h("div", { class: "tip-note" }, `Event: ${label}`));
      const box = root.getBoundingClientRect();
      showTip(rows, clientX ?? box.left + x(index), clientY ?? box.top + margin.top + 20);
    };
    const leave = () => {
      current = -1;
      cross.setAttribute("visibility", "hidden");
      dots.forEach((dot) => dot.setAttribute("visibility", "hidden"));
      hideTip();
    };
    const nearest = (event) => {
      const offset = event.clientX - root.getBoundingClientRect().left - margin.left;
      return Math.max(0, Math.min(count - 1, Math.round((offset / Math.max(plotWidth, 1)) * (count - 1))));
    };
    root.addEventListener("pointermove", (event) => point(nearest(event), event.clientX, event.clientY));
    root.addEventListener("pointerleave", leave);
    root.addEventListener("blur", leave);
    root.addEventListener("keydown", (event) => {
      const step = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
      if (event.key === "Escape") leave();
      if (!step) return;
      event.preventDefault();
      point(Math.max(0, Math.min(count - 1, current < 0 ? count - 1 : current + step)));
    });
    container.replaceChildren(root);
  });
  return container;
}

// --- column chart (distributions) ---------------------------------------------------

function columnPath(x, y, width, height) {
  const radius = Math.min(4, width / 2, height);
  return `M${x},${y + height}V${y + radius}Q${x},${y} ${x + radius},${y}H${x + width - radius}Q${x + width},${y} ${x + width},${y + radius}V${y + height}Z`;
}

/**
 * Small multiples of columns over the same bins, sharing one value axis.
 * spec: { bins: [label], ticks: [{ index, label }], panels: [{ label, color, values, counts }], unit, label }
 */
export function columnChart(spec) {
  const { bins, panels, unit } = spec;
  const container = h("div", { class: "plot" });
  const scale = valueScale([0, ...panels.flatMap((panel) => panel.values)]);
  if (!panels.some((panel) => panel.values.some((value) => value > 0))) {
    container.append(h("p", { class: "empty" }, "No values to show for this period."));
    return container;
  }
  const panelHeight = spec.panelHeight || 118;

  responsive(container, (width) => {
    const tickLabels = scale.ticks.map((tick) => formatTick(tick, unit, scale.step));
    const margin = { top: 8, bottom: 26, left: Math.max(...tickLabels.map((label) => label.length)) * 6.6 + 14, right: 8 };
    const gap = 20;
    const height = margin.top + panels.length * panelHeight + (panels.length - 1) * gap + margin.bottom;
    const plotWidth = width - margin.left - margin.right;
    const band = plotWidth / bins.length;
    const barWidth = Math.max(1, Math.min(24, band - 2));
    const root = s("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", tabindex: 0, "aria-label": spec.label });
    const highlight = s("rect", { class: "band", width: band, y: margin.top, height: height - margin.top - margin.bottom, visibility: "hidden" });
    root.append(highlight);

    panels.forEach((panel, row) => {
      const top = margin.top + row * (panelHeight + gap);
      const inner = panelHeight - 18;
      const y = (value) => top + 18 + inner * (1 - (value - scale.lo) / (scale.hi - scale.lo));
      scale.ticks.forEach((tick, index) => {
        if (index % 2 && scale.ticks.length > 4) return;
        root.append(
          s("line", { class: tick === 0 ? "axis" : "grid", x1: margin.left, x2: width - margin.right, y1: y(tick), y2: y(tick) }),
          s("text", { class: "tick", x: margin.left - 8, y: y(tick) + 4, "text-anchor": "end" }, tickLabels[index]),
        );
      });
      const group = s("g", { class: `c${panel.color}` });
      panel.values.forEach((value, index) => {
        if (!(value > 0)) return;
        const barHeight = Math.max(1, y(0) - y(value));
        group.append(s("path", { class: "bar", d: columnPath(margin.left + index * band + (band - barWidth) / 2, y(0) - barHeight, barWidth, barHeight) }));
      });
      root.append(
        group,
        s("rect", { class: `swatch c${panel.color}`, x: margin.left + 2, y: top + 2, width: 10, height: 10, rx: 2 }),
        s("text", { class: "panel-label", x: margin.left + 18, y: top + 11 }, panel.label),
      );
    });
    for (const tick of spec.ticks) {
      root.append(s("text", { class: "tick", x: margin.left + tick.index * band, y: height - 8, "text-anchor": "middle" }, tick.label));
    }

    let current = -1;
    const point = (index, clientX, clientY) => {
      current = index;
      highlight.setAttribute("x", margin.left + index * band);
      highlight.setAttribute("visibility", "visible");
      const rows = [h("div", { class: "tip-title" }, bins[index])];
      for (const panel of panels) {
        rows.push(tipRow(`c${panel.color}`, formatValue(panel.values[index], unit, true), `${panel.label} (${formatValue(panel.counts[index], "count")})`));
      }
      const box = root.getBoundingClientRect();
      showTip(rows, clientX ?? box.left + margin.left + (index + 0.5) * band, clientY ?? box.top + margin.top + 24);
    };
    const leave = () => {
      current = -1;
      highlight.setAttribute("visibility", "hidden");
      hideTip();
    };
    root.addEventListener("pointermove", (event) => {
      const offset = event.clientX - root.getBoundingClientRect().left - margin.left;
      point(Math.max(0, Math.min(bins.length - 1, Math.floor(offset / band))), event.clientX, event.clientY);
    });
    root.addEventListener("pointerleave", leave);
    root.addEventListener("blur", leave);
    root.addEventListener("keydown", (event) => {
      const step = { ArrowLeft: -1, ArrowRight: 1 }[event.key];
      if (event.key === "Escape") leave();
      if (!step) return;
      event.preventDefault();
      point(Math.max(0, Math.min(bins.length - 1, current < 0 ? 0 : current + step)));
    });
    container.replaceChildren(root);
  });
  return container;
}

// --- small pieces ------------------------------------------------------------------

export function legend(series) {
  return h("div", { class: "legend" }, series.map((one) => h("span", {}, h("i", { class: `key c${one.color}` }), one.label)));
}

/** Rows ranked by share: name, bar, share, count. All bars one colour; the length carries the value. */
export function rankedBars(rows) {
  const max = Math.max(...rows.map((row) => row.share), 0) || 1;
  return h(
    "div",
    { class: "ranked", role: "table" },
    rows.map((row) => {
      const fill = h("div", { class: "ranked-fill" });
      fill.style.width = `${Math.max((row.share / max) * 100, 0.5)}%`;
      return h(
        "div",
        { class: "ranked-row", role: "row" },
        h("span", { class: "ranked-name", role: "cell" }, row.label),
        h("div", { class: "ranked-track", role: "presentation" }, fill),
        h("span", { class: "ranked-share", role: "cell" }, formatValue(row.share, "share")),
        h("span", { class: "ranked-count", role: "cell" }, formatValue(row.count, "count")),
      );
    }),
  );
}

export function sparkline(values) {
  const width = 104;
  const height = 30;
  const present = values.filter((value) => value !== null && value !== undefined);
  const root = s("svg", { class: "spark", width, height, viewBox: `0 0 ${width} ${height}`, "aria-hidden": "true" });
  if (present.length < 2) return root;
  const min = Math.min(...present);
  const max = Math.max(...present);
  const x = (index) => 3 + (index / (values.length - 1)) * (width - 8);
  const y = (value) => (max === min ? height / 2 : 4 + (height - 8) * (1 - (value - min) / (max - min)));
  let path = "";
  let last = null;
  values.forEach((value, index) => {
    if (value === null || value === undefined) return;
    path += `${values[index - 1] === null || values[index - 1] === undefined ? "M" : "L"}${x(index).toFixed(1)},${y(value).toFixed(1)}`;
    last = [x(index), y(value)];
  });
  root.append(s("path", { d: path }), s("circle", { cx: last[0], cy: last[1], r: 3 }));
  return root;
}

/** A table with the same numbers as a chart: one row per day, newest first. */
export function seriesTable(days, series, unit) {
  const rows = days.map((day, index) => h("tr", {}, h("th", { scope: "row" }, formatDay(day, true)), series.map((one) => h("td", {}, formatValue(one.values[index], unit, true)))));
  return h(
    "div",
    { class: "table-scroll" },
    h("table", { class: "data" }, h("thead", {}, h("tr", {}, h("th", { scope: "col" }, "Day"), series.map((one) => h("th", { scope: "col" }, one.label)))), h("tbody", {}, rows.reverse())),
  );
}

// --- coverage strip ----------------------------------------------------------------

/**
 * One cell per day: all reports present, some, or none. With several rows, one row per service
 * on a shared time axis, each labelled and keyed with its chart colour.
 * Drag across it to choose a date range; click a day to choose its month.
 * spec: { rows: [{ first, counts, label?, color? }], expected, selected: [first, last], onSelect(first, last), label }
 */
export function coverageStrip(spec) {
  const container = h("div", { class: "strip" });
  const rows = spec.rows.filter((row) => row.counts);
  if (!rows.length) return container;
  const DAY = 86400000;
  const start = Math.min(...rows.map((row) => toDate(row.first).getTime()));
  const end = Math.max(...rows.map((row) => toDate(row.first).getTime() + (row.counts.length - 1) * DAY));
  const total = Math.round((end - start) / DAY) + 1;
  const dayAt = (index) => new Date(start + index * DAY).toISOString().slice(0, 10);
  const indexOf = (day) => Math.round((toDate(day).getTime() - start) / DAY);
  const labelled = rows.length > 1;
  const band = labelled ? 10 : 16;
  const pitch = band + (labelled ? 4 : 0);
  const cells = rows.length * pitch - (pitch - band);

  responsive(container, (width) => {
    const left = labelled ? Math.min(190, Math.max(...rows.map((row) => row.label.length)) * 6.3 + 24) : 0;
    const plotWidth = width - left;
    const height = cells + 24;
    const x = (index) => left + (index / total) * plotWidth;
    const root = s("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": spec.label });

    rows.forEach((row, position) => {
      const top = position * pitch;
      const offset = indexOf(row.first);
      root.append(s("rect", { class: "strip-track", x: left, y: top, width: plotWidth, height: band, rx: labelled ? 2 : 3 }));
      // Draw runs of equal state as one rectangle each.
      let from = 0;
      for (let index = 1; index <= row.counts.length; index += 1) {
        if (index < row.counts.length && row.counts[index] === row.counts[from]) continue;
        const found = Number(row.counts[from]);
        if (found > 0) {
          root.append(s("rect", { class: found >= spec.expected ? "strip-full" : "strip-partial", x: x(offset + from), y: top, width: Math.max(x(offset + index) - x(offset + from), 0.6), height: band }));
        }
        from = index;
      }
      if (labelled) {
        root.append(s("circle", { class: `strip-key c${row.color}`, cx: 5, cy: top + band / 2, r: 4 }), s("text", { class: "strip-label", x: 15, y: top + band - 1 }, row.label));
      }
    });

    const labels = [];
    for (let index = 0; index < total; index += 1) {
      const day = dayAt(index);
      if (!day.endsWith("-01") && index !== 0) continue;
      const label = index === 0 || day.slice(5, 7) === "01" ? formatMonth(day, true) : formatMonth(day);
      if (labels.length && x(index) - labels.at(-1).x < labels.at(-1).label.length * 6.5 + 16) continue;
      labels.push({ x: x(index), label });
      if (index > 0) root.append(s("line", { class: "strip-tick", x1: x(index), x2: x(index), y1: cells, y2: cells + 5 }));
      root.append(s("text", { class: "tick", x: x(index) + (index === 0 ? 0 : 3), y: height - 6 }, label));
    }

    const selection = s("rect", { class: "strip-selection", y: 0.75, height: cells - 1.5, rx: 3 });
    const dimLeft = s("rect", { class: "strip-dim", x: left, y: 0, height: cells });
    const dimRight = s("rect", { class: "strip-dim", y: 0, height: cells });
    const place = (first, last) => {
      const a = Math.max(0, Math.min(total, first));
      const b = Math.max(0, Math.min(total, last + 1));
      selection.setAttribute("x", x(a) + 0.75);
      selection.setAttribute("width", Math.max(x(b) - x(a) - 1.5, 2));
      dimLeft.setAttribute("width", Math.max(x(a) - left, 0));
      dimRight.setAttribute("x", x(b));
      dimRight.setAttribute("width", Math.max(width - x(b), 0));
    };
    place(indexOf(spec.selected[0]), indexOf(spec.selected[1]));
    root.append(dimLeft, dimRight, selection);

    const at = (event) => Math.max(0, Math.min(total - 1, Math.floor(((event.clientX - root.getBoundingClientRect().left - left) / plotWidth) * total)));
    const rowAt = (event) => rows[Math.max(0, Math.min(rows.length - 1, Math.floor((event.clientY - root.getBoundingClientRect().top) / pitch)))];
    let anchor = null;
    root.addEventListener("pointerdown", (event) => {
      anchor = at(event);
      try {
        root.setPointerCapture(event.pointerId);
      } catch (error) {
        /* no capture available: the drag still works while the pointer stays on the strip */
      }
    });
    root.addEventListener("pointermove", (event) => {
      const index = at(event);
      if (anchor !== null) place(Math.min(anchor, index), Math.max(anchor, index));
      const row = rowAt(event);
      const found = Number(row.counts[index - indexOf(row.first)] || 0);
      const state = found >= spec.expected ? "All reports present" : found ? `${found} of ${spec.expected} reports present` : "No reports";
      showTip([h("div", { class: "tip-title" }, formatDayLong(dayAt(index))), h("div", { class: "tip-note" }, labelled ? `${row.label}: ${state.toLowerCase()}` : state)], event.clientX, event.clientY);
    });
    root.addEventListener("pointerleave", hideTip);
    root.addEventListener("pointerup", (event) => {
      if (anchor === null) return;
      const index = at(event);
      let [first, last] = [Math.min(anchor, index), Math.max(anchor, index)];
      anchor = null;
      if (first === last) {
        const month = dayAt(first).slice(0, 7);
        while (first > 0 && dayAt(first - 1).startsWith(month)) first -= 1;
        while (last < total - 1 && dayAt(last + 1).startsWith(month)) last += 1;
      }
      hideTip();
      spec.onSelect(dayAt(first), dayAt(last));
    });
    container.replaceChildren(root);
  });
  return container;
}

// --- heatmap -----------------------------------------------------------------------

function cssColor(name) {
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return [1, 3, 5].map((start) => parseInt(value.slice(start, start + 2), 16));
}

/** Position 0..1 on the three-stop sequential ramp, as [r, g, b]. */
function heatColor(stops, position) {
  const scaled = Math.min(Math.max(position, 0), 1) * (stops.length - 1);
  const index = Math.min(Math.floor(scaled), stops.length - 2);
  const mix = scaled - index;
  return stops[index].map((channel, i) => Math.round(channel + (stops[index + 1][i] - channel) * mix));
}

/** What each heatmap on the page shows, so a saved figure can draw its cells again in the light theme. */
const heatSpecs = new WeakMap();

/** A heatmap's cells in the colours of the theme in force: one canvas pixel per day and size step, which the browser enlarges without blurring. */
function heatCells({ days, rows, steps, min, max }) {
  const logMin = Math.log10(min || 1);
  const logSpan = Math.log10(max) - logMin || 1;
  const cells = h("canvas", { class: "heat-cells", width: days.length, height: steps });
  const context = cells.getContext("2d");
  const image = context.createImageData(days.length, steps);
  const stops = [cssColor("--heat-0"), cssColor("--heat-1"), cssColor("--heat-2")];
  const missing = cssColor("--line");
  rows.forEach((row, day) => {
    for (let step = 0; step < steps; step += 1) {
      const count = row ? row[step] : null;
      if (count === 0) continue;
      const color = row ? heatColor(stops, (Math.log10(count) - logMin) / logSpan) : missing;
      image.data.set([...color, 255], ((steps - 1 - step) * days.length + day) * 4);
    }
  });
  context.putImageData(image, 0, 0);
  return cells;
}

/**
 * Days across, size steps up, colour by message count on a log scale. Blank means none.
 * spec: { days, rows: [[value per step] | null per day], steps, stepBytes, openLower, min?, max, unit?, label }
 * `min` and `max` fix the ends of the colour scale, so several heatmaps can share one scale.
 */
export function heatmap(spec) {
  const { days, rows, steps, stepBytes } = spec;
  const container = h("div", { class: "plot" });
  if (!spec.max) {
    container.append(h("p", { class: "empty" }, "No values to show for this period."));
    return container;
  }
  heatSpecs.set(container, spec);
  const unit = spec.unit || "count";
  const plotHeight = spec.height || 190;
  const stepLabel = (step) => {
    const lower = step * stepBytes;
    return lower >= spec.openLower ? `${formatValue(lower, "count", true)} bytes or more` : `${formatValue(lower, "count", true)} to ${formatValue(lower + stepBytes - 1, "count", true)} bytes`;
  };

  responsive(container, (width) => {
    const margin = { top: 6, right: 8, bottom: 26, left: 48 };
    const height = margin.top + plotHeight + margin.bottom;
    const plotWidth = width - margin.left - margin.right;

    const cells = heatCells(spec);
    Object.assign(cells.style, { left: `${margin.left}px`, top: `${margin.top}px`, width: `${plotWidth}px`, height: `${plotHeight}px` });

    const x = (index) => margin.left + ((index + 0.5) / days.length) * plotWidth;
    const y = (step) => margin.top + plotHeight * (1 - step / steps);
    const axes = s("svg", { width, height, viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": spec.label });
    const byteStep = niceTicks(0, steps * stepBytes, 4).step;
    for (let bytes = 0; bytes <= steps * stepBytes; bytes += byteStep) {
      axes.append(s("text", { class: "tick", x: margin.left - 8, y: y(bytes / stepBytes) + 4, "text-anchor": "end" }, formatValue(bytes, "count", true)));
    }
    axes.append(s("line", { class: "axis", x1: margin.left, x2: width - margin.right, y1: margin.top + plotHeight, y2: margin.top + plotHeight }));
    for (const tick of dayTicks(days, x)) axes.append(s("text", { class: "tick", x: x(tick.index), y: height - 8, "text-anchor": "middle" }, tick.label));

    const frame = h("div", { class: "heat-frame" }, cells, axes);
    frame.style.height = `${height}px`;
    frame.addEventListener("pointermove", (event) => {
      const box = frame.getBoundingClientRect();
      const day = Math.floor(((event.clientX - box.left - margin.left) / plotWidth) * days.length);
      const step = Math.floor((1 - (event.clientY - box.top - margin.top) / plotHeight) * steps);
      if (day < 0 || day >= days.length || step < 0 || step >= steps) return hideTip();
      const row = rows[day];
      showTip([h("div", { class: "tip-title" }, formatDayLong(days[day])), h("div", { class: "tip-row" }, h("b", {}, row ? formatValue(row[step], unit, true) : "No report"), h("span", {}, stepLabel(step)))], event.clientX, event.clientY);
    });
    frame.addEventListener("pointerleave", hideTip);
    container.replaceChildren(frame);
  });
  return container;
}

/** The colour scale of a heatmap, from `min` to `max`. */
export function heatLegend(min, max, unit = "count") {
  const what = unit === "share" ? "share of that day\u2019s messages" : "messages per day";
  return h("div", { class: "heat-legend" }, h("span", {}, formatValue(min, unit)), h("i", { class: "heat-bar" }), h("span", {}, formatValue(max, unit)), h("span", { class: "muted" }, `${what}, logarithmic colour scale. Blank means none; grey means no report.`));
}

// --- figure export -----------------------------------------------------------------

const EXPORT_STYLES = ["fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "font-family", "font-size", "font-weight", "text-anchor"];

/** A copy of a chart's SVG that carries its colours and fonts as attributes, so it renders the same outside the page. */
function styledCopy(source) {
  const copy = source.cloneNode(true);
  const originals = [...source.querySelectorAll("*")];
  [...copy.querySelectorAll("*")].forEach((element, index) => {
    const computed = getComputedStyle(originals[index]);
    if (computed.visibility === "hidden" || originals[index].classList.contains("band")) return element.remove();
    for (const property of EXPORT_STYLES) element.setAttribute(property, computed.getPropertyValue(property));
    element.removeAttribute("class");
  });
  return copy;
}

/**
 * Run `work` with the page in the light theme. Saved figures go into papers and slides, so they
 * look the same whichever theme is on screen. Nothing is painted in between, so the page does not flash.
 */
function inLightTheme(work) {
  const page = document.documentElement;
  const shown = page.dataset.theme;
  page.dataset.theme = "light";
  try {
    return work();
  } finally {
    if (shown === undefined) delete page.dataset.theme;
    else page.dataset.theme = shown;
  }
}

function download(filename, blob) {
  const url = URL.createObjectURL(blob);
  const link = h("a", { href: url, download: filename });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function downloadText(filename, text, type = "text/csv") {
  download(filename, new Blob([text], { type }));
}

/**
 * Save a chart as a standalone SVG file with its title, legend and caption drawn in, for use in papers.
 * The file is always in the light theme.
 */
export function saveFigure(container, figure) {
  const source = container.querySelector("svg");
  if (source) inLightTheme(() => drawFigure(source, figure));
}

function drawFigure(source, { title, series = [], caption = "", filename }) {
  const width = Number(source.getAttribute("width"));
  const chartHeight = Number(source.getAttribute("height"));
  const root = getComputedStyle(document.documentElement);
  const font = getComputedStyle(document.body).fontFamily;
  const ink = root.getPropertyValue("--ink").trim();
  const header = series.length > 1 ? 46 : 26;
  const footer = caption ? 22 : 6;

  const copy = styledCopy(source);

  const figure = s("svg", { xmlns: NS, width, height: header + chartHeight + footer, viewBox: `0 0 ${width} ${header + chartHeight + footer}`, "font-family": font });
  figure.append(s("rect", { width: "100%", height: "100%", fill: root.getPropertyValue("--surface").trim() }), s("text", { x: 0, y: 15, "font-size": 14, "font-weight": 650, fill: ink }, title));
  let offset = 0;
  if (series.length > 1) {
    for (const one of series) {
      const color = root.getPropertyValue(`--series-${one.color}`).trim() || root.getPropertyValue("--muted").trim();
      figure.append(s("line", { x1: offset, x2: offset + 14, y1: 33, y2: 33, stroke: color, "stroke-width": 3 }), s("text", { x: offset + 20, y: 37, "font-size": 12, fill: ink }, one.label));
      offset += one.label.length * 6.6 + 40;
    }
  }
  const body = s("g", { transform: `translate(0 ${header})` });
  body.append(...copy.childNodes);
  figure.append(body);
  if (caption) figure.append(s("text", { x: 0, y: header + chartHeight + 14, "font-size": 11, fill: root.getPropertyValue("--muted").trim() }, caption));

  download(`${filename}.svg`, new Blob([new XMLSerializer().serializeToString(figure)], { type: "image/svg+xml" }));
}

/** Save one heatmap panel as a PNG image in the light theme: its cells and axes, with a title and caption drawn in. */
export async function saveHeatmap(container, { title, caption = "", filename }) {
  const frame = container.querySelector(".heat-frame");
  if (!frame) return;
  const shown = frame.querySelector("canvas").style;
  const axes = frame.querySelector("svg");
  const width = Number(axes.getAttribute("width"));
  const height = Number(axes.getAttribute("height"));
  const font = getComputedStyle(document.body).fontFamily;
  const light = inLightTheme(() => {
    const root = getComputedStyle(document.documentElement);
    const color = (name) => root.getPropertyValue(name).trim();
    return { surface: color("--surface"), ink: color("--ink"), muted: color("--muted"), cells: heatCells(heatSpecs.get(container)), axes: styledCopy(axes) };
  });
  const header = 24;
  const footer = caption ? 20 : 4;
  const canvas = h("canvas", { width: width * 2, height: (header + height + footer) * 2 });
  const context = canvas.getContext("2d");
  context.scale(2, 2);
  context.fillStyle = light.surface;
  context.fillRect(0, 0, width, header + height + footer);
  context.imageSmoothingEnabled = false;
  context.drawImage(light.cells, parseFloat(shown.left), header + parseFloat(shown.top), parseFloat(shown.width), parseFloat(shown.height));
  const copy = light.axes;
  copy.setAttribute("xmlns", NS);
  const image = new Image();
  await new Promise((resolve, reject) => {
    image.onload = resolve;
    image.onerror = reject;
    image.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(new XMLSerializer().serializeToString(copy))}`;
  });
  context.drawImage(image, 0, header, width, height);
  context.fillStyle = light.ink;
  context.font = `650 14px ${font}`;
  context.fillText(title, 0, 15);
  if (caption) {
    context.fillStyle = light.muted;
    context.font = `11px ${font}`;
    context.fillText(caption, 0, header + height + 14);
  }
  canvas.toBlob((blob) => download(`${filename}.png`, blob));
}
