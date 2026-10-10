// Small SVG charts: stacked daily bars and a line with crosshair. No library, one y-axis each.

const NS = "http://www.w3.org/2000/svg";
const PAD = { top: 10, right: 12, bottom: 26, left: 48 };

function el(name, attrs = {}, parent) {
  const node = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

export function niceTicks(max, count = 4) {
  if (!(max > 0)) return [0, 1];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
  const ticks = [];
  for (let v = 0; v <= max + step * 0.001; v += step) ticks.push(v);
  if (ticks[ticks.length - 1] < max) ticks.push(ticks[ticks.length - 1] + step);
  return ticks;
}

function frame(container, height) {
  container.innerHTML = "";
  container.classList.add("chart");
  const width = Math.max(container.clientWidth, 280);
  const svg = el("svg", { viewBox: `0 0 ${width} ${height}`, height, role: "img" }, container);
  const tip = document.createElement("div");
  tip.className = "tooltip hidden";
  container.appendChild(tip);
  return { svg, tip, width, plotW: width - PAD.left - PAD.right, plotH: height - PAD.top - PAD.bottom };
}

function yAxis(svg, ticks, scaleY, width, fmt) {
  for (const t of ticks) {
    const y = scaleY(t);
    el("line", { x1: PAD.left, x2: width - PAD.right, y1: y, y2: y, class: t === 0 ? "baseline" : "gridline" }, svg);
    el("text", { x: PAD.left - 8, y: y + 4, "text-anchor": "end", class: "tick" }, svg).textContent = fmt(t);
  }
}

function placeTip(tip, container, x, html) {
  tip.innerHTML = html;
  tip.classList.remove("hidden");
  const w = tip.offsetWidth;
  const left = x + 14 + w > container.clientWidth ? x - w - 14 : x + 14;
  tip.style.left = `${Math.max(left, 0)}px`;
  tip.style.top = "6px";
}

/** days: ["2026-10-01", ...]; series: [{label, color, values: [..]}] stacked bottom-up. */
export function stackedBars(container, { days, series, height = 240, fmt, dayFmt, tipHtml }) {
  const { svg, tip, width, plotW, plotH } = frame(container, height);
  const totals = days.map((_, i) => series.reduce((s, ser) => s + (ser.values[i] || 0), 0));
  const ticks = niceTicks(Math.max(...totals, 0));
  const top = ticks[ticks.length - 1] || 1;
  const scaleY = (v) => PAD.top + plotH - (v / top) * plotH;
  yAxis(svg, ticks, scaleY, width, fmt);
  const slot = plotW / days.length;
  const barW = Math.max(Math.min(slot - 2, 22), 2);
  const labelEvery = Math.ceil(days.length / Math.max(Math.floor(plotW / 64), 1));
  days.forEach((day, i) => {
    const x = PAD.left + i * slot + (slot - barW) / 2;
    let base = 0;
    const visible = series.filter((s) => (s.values[i] || 0) > 0);
    visible.forEach((ser, j) => {
      const v = ser.values[i] || 0;
      const y0 = scaleY(base), y1 = scaleY(base + v);
      const gap = j > 0 ? 1 : 0; // 2px surface gap between stacked segments (1px off each side)
      const h = Math.max(y0 - y1 - gap - (j < visible.length - 1 ? 1 : 0), 0.5);
      const r = j === visible.length - 1 ? Math.min(3, barW / 2, h) : 0;
      el("path", { d: roundedTop(x, y1 + (j < visible.length - 1 ? 1 : 0), barW, h, r), fill: ser.color }, svg);
      base += v;
    });
    if (i % labelEvery === 0 || i === days.length - 1) {
      el("text", { x: PAD.left + i * slot + slot / 2, y: height - 8, "text-anchor": "middle", class: "tick" }, svg)
        .textContent = dayFmt(day);
    }
  });
  const hit = el("rect", { x: PAD.left, y: PAD.top, width: plotW, height: plotH, fill: "transparent" }, svg);
  const hover = el("rect", { y: PAD.top, height: plotH, fill: "rgba(255,255,255,0.06)", visibility: "hidden" }, svg);
  svg.insertBefore(hover, svg.firstChild);
  hit.addEventListener("mousemove", (e) => {
    const box = svg.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * width;
    const i = Math.min(Math.max(Math.floor((px - PAD.left) / slot), 0), days.length - 1);
    hover.setAttribute("x", PAD.left + i * slot);
    hover.setAttribute("width", slot);
    hover.setAttribute("visibility", "visible");
    placeTip(tip, container, (PAD.left + i * slot + slot) * (box.width / width), tipHtml(i));
  });
  hit.addEventListener("mouseleave", () => { tip.classList.add("hidden"); hover.setAttribute("visibility", "hidden"); });
}

function roundedTop(x, y, w, h, r) {
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
}

/** points: [[x, y], ...] with numeric x; draws a 2px line and a hover crosshair. */
export function lineChart(container, { points, color, height = 200, fmtY, fmtX, yMax, tipHtml, area = true }) {
  const { svg, tip, width, plotW, plotH } = frame(container, height);
  if (!points.length) return;
  const xs = points.map((p) => p[0]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const ticks = niceTicks(yMax ?? Math.max(...points.map((p) => p[1]), 0));
  const top = ticks[ticks.length - 1] || 1;
  const scaleX = (v) => PAD.left + (x1 === x0 ? plotW / 2 : ((v - x0) / (x1 - x0)) * plotW);
  const scaleY = (v) => PAD.top + plotH - (Math.min(v, top) / top) * plotH;
  yAxis(svg, ticks, scaleY, width, fmtY);
  for (const v of [x0, (x0 + x1) / 2, x1]) {
    el("text", { x: scaleX(v), y: height - 8, "text-anchor": v === x0 ? "start" : v === x1 ? "end" : "middle", class: "tick" }, svg)
      .textContent = fmtX(v);
  }
  const d = points.map((p, i) => `${i ? "L" : "M"}${scaleX(p[0]).toFixed(1)},${scaleY(p[1]).toFixed(1)}`).join(" ");
  if (area) {
    el("path", { d: `${d} L${scaleX(x1)},${scaleY(0)} L${scaleX(x0)},${scaleY(0)} Z`, fill: color, opacity: 0.12 }, svg);
  }
  el("path", { d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linejoin": "round" }, svg);
  const cross = el("line", { y1: PAD.top, y2: PAD.top + plotH, stroke: "#898781", "stroke-dasharray": "3 3", visibility: "hidden" }, svg);
  const dot = el("circle", { r: 4.5, fill: color, stroke: "#181818", "stroke-width": 2, visibility: "hidden" }, svg);
  const hit = el("rect", { x: PAD.left, y: PAD.top, width: plotW, height: plotH, fill: "transparent" }, svg);
  hit.addEventListener("mousemove", (e) => {
    const box = svg.getBoundingClientRect();
    const px = ((e.clientX - box.left) / box.width) * width;
    let best = 0;
    points.forEach((p, i) => { if (Math.abs(scaleX(p[0]) - px) < Math.abs(scaleX(points[best][0]) - px)) best = i; });
    const [vx, vy] = points[best];
    for (const node of [cross, dot]) node.setAttribute("visibility", "visible");
    cross.setAttribute("x1", scaleX(vx)); cross.setAttribute("x2", scaleX(vx));
    dot.setAttribute("cx", scaleX(vx)); dot.setAttribute("cy", scaleY(vy));
    placeTip(tip, container, scaleX(vx) * (box.width / width), tipHtml(points[best]));
  });
  hit.addEventListener("mouseleave", () => {
    tip.classList.add("hidden");
    for (const node of [cross, dot]) node.setAttribute("visibility", "hidden");
  });
}
