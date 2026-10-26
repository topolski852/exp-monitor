// Circular gauge in the GPU Tweak style: a 270° arc whose outer ring shows the
// manufacturer zones (green / amber / red) and whose inner arc shows the value.

const SVG = "http://www.w3.org/2000/svg";
const START = -135;
const SWEEP = 270;

export function svg(name, attrs = {}, parent = null) {
  const node = document.createElementNS(SVG, name);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (parent) parent.appendChild(node);
  return node;
}

// Angle in degrees, clockwise from 12 o'clock.
function point(r, deg) {
  const a = (deg * Math.PI) / 180;
  return [100 + r * Math.sin(a), 100 - r * Math.cos(a)];
}

function arc(r, a0, a1) {
  const [x0, y0] = point(r, a0);
  const [x1, y1] = point(r, a1);
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${a1 - a0 > 180 ? 1 : 0} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

export function fraction(value, min, max) {
  if (value == null || max == null || max <= min) return 0;
  return Math.min(1, Math.max(0, (value - min) / (max - min)));
}

// [from, to, status] bands covering 0..1 for a sensor's limits.
export function zones(entry, direction) {
  const { min, max, warn, crit } = entry;
  if (warn == null && crit == null) return [];
  const w = warn == null ? null : fraction(warn, min, max);
  const c = crit == null ? null : fraction(crit, min, max);
  if (direction === "low") {
    const bands = [];
    let at = 0;
    if (c != null) { bands.push([0, c, "crit"]); at = c; }
    if (w != null) { bands.push([at, w, "warn"]); at = w; }
    bands.push([at, 1, "ok"]);
    return bands.filter(([a, b]) => b > a);
  }
  const okEnd = w ?? c;
  const bands = [[0, okEnd, "ok"]];
  if (w != null) bands.push([w, c ?? 1, "warn"]);
  if (c != null) bands.push([c, 1, "crit"]);
  return bands.filter(([a, b]) => b > a);
}

export function format(value, decimals = 0) {
  if (value == null) return "--";
  return Number(value).toFixed(decimals);
}

export function sparkline(node, values, min, max) {
  const width = 160, height = 34;
  node.replaceChildren();
  const points = values.filter((v) => v != null);
  if (points.length < 2) return;
  const span = max > min ? max - min : 1;
  const step = width / (values.length - 1);
  const coords = [];
  values.forEach((v, i) => {
    if (v == null) return;
    const y = height - 2 - ((Math.min(max, Math.max(min, v)) - min) / span) * (height - 4);
    coords.push(`${(i * step).toFixed(1)},${y.toFixed(1)}`);
  });
  const first = coords[0].split(",")[0];
  const last = coords[coords.length - 1].split(",")[0];
  svg("path", { d: `M ${first},${height} L ${coords.join(" L ")} L ${last},${height} Z`, class: "spark-area" }, node);
  svg("polyline", { points: coords.join(" "), class: "spark-line" }, node);
}

export function createGauge(id, meta) {
  const card = document.createElement("article");
  card.className = "gauge card";
  card.dataset.sensor = id;

  const face = svg("svg", { viewBox: "0 0 200 200", class: "gauge-face", role: "img", "aria-label": meta.name });
  const defs = svg("defs", {}, face);
  const glow = svg("filter", { id: `glow-${id}`, x: "-50%", y: "-50%", width: "200%", height: "200%" }, defs);
  svg("feGaussianBlur", { stdDeviation: "3.2", result: "blur" }, glow);
  const merge = svg("feMerge", {}, glow);
  svg("feMergeNode", { in: "blur" }, merge);
  svg("feMergeNode", { in: "SourceGraphic" }, merge);

  const ticks = svg("g", { class: "ticks" }, face);
  for (let i = 0; i <= 27; i++) {
    const deg = START + (SWEEP * i) / 27;
    const major = i % 9 === 0;
    const [x0, y0] = point(major ? 79 : 81, deg);
    const [x1, y1] = point(86, deg);
    svg("line", { x1: x0, y1: y0, x2: x1, y2: y1, class: major ? "tick major" : "tick" }, ticks);
  }
  const zoneRing = svg("g", { class: "zones" }, face);
  svg("path", { d: arc(68, START, START + SWEEP), class: "track", pathLength: 100 }, face);
  const valueArc = svg("path", {
    d: arc(68, START, START + SWEEP), class: "value-arc", pathLength: 100,
    "stroke-dasharray": "0 100", filter: `url(#glow-${id})`,
  }, face);
  const valueText = svg("text", { x: 100, y: 104, class: "gauge-value" }, face);
  const unitText = svg("text", { x: 100, y: 128, class: "gauge-unit" }, face);
  const minText = svg("text", { x: 52, y: 168, class: "gauge-scale" }, face);
  const maxText = svg("text", { x: 148, y: 168, class: "gauge-scale" }, face);

  const label = document.createElement("h3");
  label.className = "gauge-label";
  label.textContent = meta.name;
  const sub = document.createElement("p");
  sub.className = "gauge-sub";
  const spark = svg("svg", { viewBox: "0 0 160 34", preserveAspectRatio: "none", class: "spark" });
  const about = document.createElement("p");
  about.className = "about";
  about.textContent = [meta.about, meta.spec].filter(Boolean).join(" · ");

  card.append(face, label, sub, spark, about);

  let zoneKey = "";
  return {
    el: card,
    update(entry, history) {
      const neutral = entry.warn == null && entry.crit == null;
      card.dataset.status = neutral && entry.status === "ok" ? "neutral" : entry.status;
      const key = [entry.min, entry.max, entry.warn, entry.crit].join("|");
      if (key !== zoneKey) {
        zoneKey = key;
        zoneRing.replaceChildren();
        const bands = zones(entry, meta.direction);
        if (!bands.length) bands.push([0, 1, "neutral"]);
        for (const [a, b, status] of bands) {
          svg("path", { d: arc(88, START + SWEEP * a, START + SWEEP * b), class: `zone zone-${status}` }, zoneRing);
        }
        minText.textContent = format(entry.min);
        maxText.textContent = format(entry.max);
      }
      const pct = fraction(entry.value, entry.min, entry.max) * 100;
      valueArc.setAttribute("stroke-dasharray", `${pct.toFixed(2)} 100`);
      valueText.textContent = entry.value == null ? "NO SIGNAL" : format(entry.value, meta.decimals);
      valueText.classList.toggle("small", entry.value == null);
      unitText.textContent = entry.value == null ? "" : entry.unit;
      sub.textContent = subline(meta, entry);
      sparkline(spark, history, entry.min, entry.max ?? 100);
    },
  };
}

function subline(meta, entry) {
  if (entry.value == null) return "SENSOR OFFLINE";
  if (meta.kind === "memory" && entry.max) return `OF ${format(entry.max, 1)} ${entry.unit}`;
  if (entry.crit != null) return `LIMIT ${format(entry.crit)} ${entry.unit}`;
  if (entry.max != null && meta.kind !== "percent") return `MAX ${format(entry.max)} ${entry.unit}`;
  return "";
}
