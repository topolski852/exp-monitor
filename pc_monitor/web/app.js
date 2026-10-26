import { createGauge, format, fraction, zones } from "./gauge.js";

const $ = (selector) => document.querySelector(selector);
const STATUS_VAR = { ok: "--ok", warn: "--warn", crit: "--crit", missing: "--missing", neutral: "--accent" };

let meta;
let historyLength = 300;
const history = {};
const widgets = [];

async function boot() {
  meta = await (await fetch("/api/meta")).json();
  const past = await (await fetch("/api/history")).json();
  historyLength = Math.max(2, Math.round((meta.app.history_seconds * 1000) / past.interval_ms));
  Object.assign(history, past.sensors);

  const machine = meta.app.machine || "";
  $("#machine").textContent = machine;
  document.title = machine ? `${machine} · PC Monitor` : "PC Monitor";
  $("#footer-info").textContent =
    `${meta.config_path ?? ""} · every ${meta.app.refresh_ms} ms`;

  for (const id of meta.layout.gauges) {
    const gauge = createGauge(id, meta.sensors[id]);
    $("#gauges").appendChild(gauge.el);
    widgets.push({ id, update: gauge.update });
  }
  for (const panel of meta.layout.panels) {
    $("#panels").appendChild(panel.widget === "threads" ? threadsPanel(panel) : rowsPanel(panel));
  }

  setupExplain();
  tickClock();
  setInterval(tickClock, 1000);
  connect();
}

function connect() {
  const link = $("#link");
  const stream = new EventSource("/api/stream");
  stream.onopen = () => { link.textContent = "● LIVE"; link.dataset.state = "live"; };
  stream.onerror = () => { link.textContent = "○ RECONNECTING…"; link.dataset.state = "lost"; };
  stream.onmessage = (event) => apply(JSON.parse(event.data));
}

function apply(snapshot) {
  for (const [id, entry] of Object.entries(snapshot.sensors)) {
    const values = (history[id] ??= []);
    values.push(entry.value);
    if (values.length > historyLength) values.splice(0, values.length - historyLength);
  }
  for (const widget of widgets) {
    const entry = snapshot.sensors[widget.id];
    if (entry) widget.update(entry, history[widget.id]);
  }
  renderAlerts(snapshot.alerts);
  renderChips(snapshot.info, snapshot.alerts);
}

// ------------------------------------------------------------------ panels

function panelShell(title, extraClass = "") {
  const panel = document.createElement("section");
  panel.className = `panel card ${extraClass}`.trim();
  const heading = document.createElement("h2");
  heading.textContent = title;
  panel.appendChild(heading);
  return panel;
}

function rowsPanel(panel) {
  const el = panelShell(panel.title);
  for (const id of panel.sensors) {
    const row = sensorRow(id, meta.sensors[id]);
    el.appendChild(row.el);
    widgets.push({ id, update: row.update });
  }
  return el;
}

function sensorRow(id, info) {
  const row = document.createElement("div");
  row.className = "row";
  row.tabIndex = 0;
  row.innerHTML = `
    <div class="row-top">
      <span class="dot"></span>
      <span class="name"></span>
      <span class="val"><b></b><small></small></span>
    </div>
    <div class="bar"><div class="bar-zones"></div><div class="bar-fill"></div></div>
    <p class="about"></p>`;
  row.querySelector(".name").textContent = info.name;
  const about = row.querySelector(".about");
  about.textContent = info.about;
  if (info.spec) {
    const spec = document.createElement("em");
    spec.textContent = info.spec;
    about.append(document.createElement("br"), spec);
  }
  const toggle = () => row.classList.toggle("open");
  row.addEventListener("click", toggle);
  row.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } });

  const value = row.querySelector(".val b");
  const unit = row.querySelector(".val small");
  const fill = row.querySelector(".bar-fill");
  const zoneBar = row.querySelector(".bar-zones");
  let zoneKey = "";

  return {
    el: row,
    update(entry) {
      const neutral = entry.warn == null && entry.crit == null;
      row.dataset.status = neutral && entry.status === "ok" ? "neutral" : entry.status;
      value.textContent = entry.value == null ? "NO SIGNAL" : format(entry.value, info.decimals);
      unit.textContent = entry.value == null ? "" : ` ${entry.unit}`;
      fill.style.width = `${(fraction(entry.value, entry.min, entry.max) * 100).toFixed(1)}%`;
      const key = [entry.min, entry.max, entry.warn, entry.crit].join("|");
      if (key !== zoneKey) {
        zoneKey = key;
        zoneBar.style.background = zoneGradient(zones(entry, info.direction));
      }
    },
  };
}

function zoneGradient(bands) {
  if (!bands.length) return "none";
  const stops = bands.map(([a, b, status]) =>
    `var(${STATUS_VAR[status]}) ${(a * 100).toFixed(1)}% ${(b * 100).toFixed(1)}%`);
  return `linear-gradient(90deg, ${stops.join(", ")})`;
}

function threadsPanel(panel) {
  const el = panelShell(panel.title, "threads-panel");
  const id = panel.sensors[0];
  const summary = document.createElement("p");
  summary.className = "threads-summary";
  const grid = document.createElement("div");
  grid.className = "threads";
  el.append(summary, grid);

  widgets.push({
    id,
    update(entry) {
      const series = entry.series ?? [];
      if (grid.children.length !== series.length) {
        grid.replaceChildren(...series.map((_, i) => {
          const bar = document.createElement("div");
          bar.className = "thread";
          bar.title = `Thread ${i}`;
          bar.innerHTML = "<i></i>";
          return bar;
        }));
      }
      series.forEach((load, i) => {
        const bar = grid.children[i];
        bar.firstChild.style.height = `${Math.max(2, load)}%`;
        bar.classList.toggle("hot", load >= 90);
        bar.title = `Thread ${i}: ${load.toFixed(0)}%`;
      });
      const busy = series.filter((v) => v >= 50).length;
      summary.textContent = series.length
        ? `${busy} of ${series.length} threads busy · average ${format(entry.value)}%`
        : "No per-thread data";
    },
  });
  return el;
}

// ------------------------------------------------------------------ header, alerts

function renderAlerts(alerts) {
  const box = $("#alerts");
  box.replaceChildren(...alerts.map((alert) => {
    const el = document.createElement("div");
    el.className = `alert alert-${alert.level}`;
    const title = document.createElement("strong");
    title.textContent = alert.title;
    const message = document.createElement("span");
    message.textContent = alert.message;
    el.append(title, message);
    return el;
  }));
}

function renderChips(info, alerts) {
  const gpuDown = alerts.some((a) => a.title.startsWith("GPU") || a.title.startsWith("NVIDIA"));
  const chips = [
    ["HOST", info.hostname],
    ["CPU", info.cpu?.replace(/ \d+-Core Processor$/, "")],
    ["GPU", info.gpu0 ?? (gpuDown ? "OFFLINE" : null), gpuDown ? "crit" : ""],
    ["DRIVER", info.nvidia_driver],
    ["KERNEL", info.kernel],
    ["UPTIME", info.uptime],
  ].filter(([, value]) => value);

  $("#chips").replaceChildren(...chips.map(([label, value, status]) => {
    const chip = document.createElement("span");
    chip.className = `chip ${status ?? ""}`.trim();
    const k = document.createElement("b");
    k.textContent = label;
    chip.append(k, document.createTextNode(` ${value}`));
    return chip;
  }));
}

function setupExplain() {
  const button = $("#explain");
  let on = false;
  try { on = localStorage.getItem("pc-monitor.explain") === "1"; } catch {}
  const set = (value) => {
    on = value;
    document.body.classList.toggle("explain", on);
    button.setAttribute("aria-pressed", String(on));
    try { localStorage.setItem("pc-monitor.explain", on ? "1" : "0"); } catch {}
  };
  set(on);
  button.addEventListener("click", () => set(!on));
}

function tickClock() {
  $("#clock").textContent = new Date().toLocaleTimeString([], { hour12: false });
}

boot().catch((error) => {
  $("#link").textContent = `ERROR: ${error.message}`;
  $("#link").dataset.state = "lost";
});
