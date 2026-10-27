// Renders /events: "state" snapshots (runs) here, "system" samples in system.js.
// Times that tick (elapsed, ETA, "ago") are spans with data-* epochs, refreshed every second.
import { startSystem } from "./system.js";

const $ = (s) => document.querySelector(s);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let skew = 0;       // server clock minus browser clock, s
let seen = null;    // active run keys in the previous snapshot (null before the first)
let system = null;  // set once system.js has built its widgets
const openRows = new Set();
const now = () => Date.now() / 1000 + skew;

function dur(s) {
  if (s == null || !isFinite(s)) return "—";
  s = Math.max(0, Math.round(s));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60), h = Math.floor(m / 60), d = Math.floor(h / 24);
  if (h === 0) return `${m}m${String(s % 60).padStart(2, "0")}s`;
  if (d === 0) return `${h}h${String(m % 60).padStart(2, "0")}m`;
  return `${d}d ${h % 24}h`;
}
function rate(r) {
  if (r == null) return "—";
  if (r >= 1 || r === 0) return `${r >= 10 || r === 0 ? r.toFixed(0) : r.toFixed(1)}/s`;
  if (r * 60 >= 1) return `${(r * 60).toFixed(1)}/min`;
  return `${(r * 3600).toFixed(1)}/h`;
}
function stamp(t) {
  if (!t) return "—";
  const d = new Date(t * 1000);
  const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  return d.toDateString() === new Date().toDateString() ? hm : `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${hm}`;
}
const since = (t) => (t ? `<span data-since="${t}"></span>` : "—");
const until = (t) => (t ? `<span data-until="${t}"></span>` : "—");
const ago = (t) => (t ? `<span data-ago="${t}"></span>` : "—");

function tick() {
  const t = now();
  for (const el of document.querySelectorAll("[data-since]")) el.textContent = dur(t - el.dataset.since);
  for (const el of document.querySelectorAll("[data-until]")) {
    const left = el.dataset.until - t;
    el.textContent = left > 0 ? dur(left) : "any moment";
  }
  for (const el of document.querySelectorAll("[data-ago]")) el.textContent = `${dur(t - el.dataset.ago)} ago`;
  $("#clock").textContent = new Date(t * 1000).toLocaleTimeString([], { hour12: false });
}

// ---------- pieces ----------
function metaHTML(r) {
  const commit = r.commit ? `${esc(r.commit)}${r.dirty ? '<span class="dirty">+dirty</span>' : ""}` : "—";
  return `<div class="meta">
    <span class="exp">${esc(r.experiment ?? "—")}</span>
    <span><span class="k">project</span> ${esc(r.project ?? "—")}</span>
    <span><span class="k">commit</span> ${commit}</span>
    ${r.pid != null ? `<span><span class="k">pid</span> ${esc(r.pid)}</span>` : ""}
  </div>`;
}

function chainHTML(c) {
  if (!c) return "";
  const idx = Number(c.index) || 0, total = Number(c.total) || 0;
  let segs = "";
  if (total > 0 && total <= 40) {
    for (let i = 1; i <= total; i++) segs += `<i class="${i < idx ? "past" : i === idx ? "cur" : ""}"></i>`;
  }
  return `<div class="chain"><span class="label">CHAIN</span><span class="pos">${idx}/${total || "?"}</span>
    <span class="segs">${segs}</span><span class="cname" title="${esc(c.name ?? "")}">${esc(c.name ?? "")}</span></div>`;
}

function phasesHTML(r) {
  return r.phases.map((p) => {
    const pct = p.total > 0 ? Math.min(100, (100 * p.done) / p.total) : 0;
    const cur = p.name === r.phase && r.state !== "done";
    const cls = ["phase", cur ? "cur" : "", p.total > 0 && p.done >= p.total ? "full" : ""].join(" ");
    return `<div class="${cls}">
      <div class="phead"><span class="pname">${esc(p.name)}</span>
        <span class="nums"><b>${p.done}</b> / ${p.total} · ${pct.toFixed(pct > 0 && pct < 10 ? 1 : 0)}%</span></div>
      <div class="pbar"><span style="width:${pct}%"></span></div>
    </div>`;
  }).join("");
}

function statsHTML(r) {
  const stat = (k, v, hl) => `<div class="stat"><span class="k">${k}</span><span class="v${hl ? " hl" : ""}">${v}</span></div>`;
  const running = r.state === "running";
  const elapsed = running ? since(r.started_ts) : dur((r.updated_ts ?? 0) - (r.started_ts ?? 0));
  let eta = "—";
  if (running) eta = r.eta_phase_at ? until(r.eta_phase_at) : r.rate == null ? "measuring…" : "—";
  let out = stat("elapsed", elapsed) + stat("eta", eta, running && r.eta_phase_at);
  if (r.eta_run_at) out += stat("run eta", until(r.eta_run_at));
  out += stat("rate", running ? `${rate(r.rate)}${r.rate_approx ? " <small>avg</small>" : ""}` : "—");
  out += stat("workers", esc(r.workers ?? "—"));
  out += stat("updated", ago(r.updated_ts));
  return `<div class="stats">${out}</div>`;
}

function notesHTML(r) {
  let out = "";
  if (r.state === "stalled") {
    out += `<div class="note warn"><b>STALLED</b> — pid ${esc(r.pid ?? "?")} is gone and the last update was ${ago(r.updated_ts)}.
      The process died without writing done/failed.</div>`;
  } else if (r.quiet) {
    out += `<div class="note warn">no update for ${since(r.updated_ts)} — pid ${esc(r.pid)} is still alive (slow step?)</div>`;
  }
  if (r.error) out += `<div class="note fail"><span class="k">error</span><pre>${esc(r.error)}</pre></div>`;
  return out;
}

const logHTML = (lines) =>
  lines.map((l) => (/error|traceback|exception|failed/i.test(l) ? `<span class="e">${esc(l)}</span>` : esc(l))).join("\n");

// ---------- active cards ----------
function setPart(card, part, html) {
  const node = card.querySelector(`[data-s="${part}"]`);
  if (node._h === html) return;
  const stick = node._h === undefined || node.scrollTop + node.clientHeight >= node.scrollHeight - 4;
  node.innerHTML = html;
  node._h = html;
  if (part === "log" && stick) node.scrollTop = node.scrollHeight;
}

function makeCard(key) {
  const el = document.createElement("article");
  el.className = "run-card";
  el.dataset.key = key;
  el.innerHTML = `<div data-s="head"></div><div data-s="chain"></div><div data-s="phases" class="phases"></div>
    <div data-s="stats"></div><div data-s="notes"></div><pre class="log" data-s="log"></pre><div class="rid" data-s="rid"></div>`;
  el.addEventListener("animationend", () => el.classList.remove("fresh"));
  return el;
}

function renderActive(runs) {
  const box = $("#active");
  if (!runs.length) {
    box.innerHTML = `<div class="empty"><div><div class="big">NO ACTIVE RUNS</div>
      waiting for run files in <code>${esc(window.__runsDir ?? "")}</code><br><br>
      try a fake one: <code>uv run exp-monitor-fake</code></div></div>`;
    return;
  }
  box.querySelector(".empty")?.remove();
  const cards = new Map([...box.querySelectorAll(".run-card")].map((c) => [c.dataset.key, c]));
  runs.forEach((r, i) => {
    let card = cards.get(r.key);
    if (!card) {
      card = makeCard(r.key);
      if (seen && !seen.has(r.key)) card.classList.add("fresh");
    }
    cards.delete(r.key);
    if (box.children[i] !== card) box.insertBefore(card, box.children[i] ?? null);
    for (const s of ["running", "done", "failed", "stalled"]) card.classList.toggle(`st-${s}`, r.state === s);
    setPart(card, "head", `<div class="head"><h3 class="title" title="${esc(r.title)}">${esc(r.title)}</h3><span class="badge">${esc(r.state)}</span></div>${metaHTML(r)}`);
    setPart(card, "chain", chainHTML(r.chain));
    setPart(card, "phases", phasesHTML(r));
    setPart(card, "stats", statsHTML(r));
    setPart(card, "notes", notesHTML(r));
    setPart(card, "log", logHTML(r.log_tail));
    setPart(card, "rid", `${esc(r.run_id)} · started ${stamp(r.started_ts)}`);
  });
  for (const c of cards.values()) c.remove();
}

// ---------- history ----------
function historyHTML(runs) {
  return runs.map((r) => {
    const took = r.started_ts && r.updated_ts ? dur(r.updated_ts - r.started_ts) : "—";
    const last = r.phases[r.phases.length - 1];
    const firstErr = r.error ? String(r.error).trim().split("\n").pop() : "";
    const tail = r.state === "failed" && firstErr ? `<span class="herr" title="${esc(firstErr)}">${esc(firstErr)}</span>`
      : r.state === "stalled" ? `<span class="hwarn">died at ${esc(r.phase ?? "?")}</span>`
      : last ? `${esc(last.name)} ${last.done}/${last.total}` : "";
    return `<details class="hrow st-${esc(r.state)}" data-key="${esc(r.key)}"${openRows.has(r.key) ? " open" : ""}>
      <summary>
        <span class="hbadge">${esc(r.state)}</span>
        <span class="htitle" title="${esc(r.title)}">${esc(r.title)}</span>
        <span class="c">${stamp(r.updated_ts)}</span>
        <span class="c hsub"><span class="exp">${esc(r.experiment ?? "")}</span>${tail ? ` · ${tail}` : ""}</span>
        <span class="c htook">${took}</span>
      </summary>
      <div class="hbody">
        ${metaHTML(r)}${chainHTML(r.chain)}
        <div class="phases">${phasesHTML(r)}</div>
        ${notesHTML(r)}
        ${r.log_tail.length ? `<pre class="log">${logHTML(r.log_tail)}</pre>` : ""}
        <div class="rid">${esc(r.run_id)} · started ${stamp(r.started_ts)} · workers ${esc(r.workers ?? "—")}</div>
      </div>
    </details>`;
  }).join("");
}

let historyCache = "";
let historyTotal = 0;
function renderHistory(runs, total) {
  const html = historyHTML(runs);
  historyTotal = total;
  if (html !== historyCache) {
    $("#history").innerHTML = html;
    historyCache = html;
  }
  fitHistory();
}

// The history panel has a fixed height: show the newest rows that fit whole, hide the rest (oldest first).
// The first row always shows, so an expanded one stays visible even when it's taller than the panel.
function fitHistory() {
  const box = $("#history");
  const rows = [...box.children];
  for (const r of rows) r.hidden = false;
  let shown = 0, full = false;
  for (const [i, r] of rows.entries()) {
    full ||= i > 0 && r.offsetTop + r.offsetHeight > box.clientHeight;
    r.hidden = full;
    if (!full) shown++;
  }
  $("#history-count").textContent = historyTotal > shown ? `latest ${shown} of ${historyTotal}` : historyTotal ? String(historyTotal) : "";
}
$("#history").addEventListener("toggle", (e) => {
  const key = e.target.dataset?.key;
  if (key) e.target.open ? openRows.add(key) : openRows.delete(key);
  fitHistory();
}, true);
window.addEventListener("resize", fitHistory);

// ---------- snapshot ----------
function apply(s) {
  skew = s.now - Date.now() / 1000;
  window.__runsDir = s.runs_dir;
  const running = s.active.filter((r) => r.state === "running").length;
  const stalled = s.active.length - running;
  $("#summary").innerHTML = `<span class="n-running"><b>${running}</b> running</span>` +
    (stalled ? `<span class="n-stalled"><b>${stalled}</b> stalled</span>` : "") +
    `<span><b>${s.history_total}</b> finished</span>`;
  document.title = running ? `(${running}) exp-monitor` : "exp-monitor";
  $("#foot-runs").textContent = `runs: ${s.runs_dir}`;
  renderActive(s.active);
  // two rows of two fit the panel; the rest scroll (scrollbar hidden to keep the cards aligned with the gauges)
  const more = s.active.length - 4;
  const ac = $("#active-count");
  ac.textContent = more > 0 ? `scroll for ${more} more` : s.active.length ? String(s.active.length) : "";
  ac.classList.toggle("more", more > 0);
  renderHistory(s.history, s.history_total);
  seen = new Set(s.active.map((r) => r.key));
  tick();
}

function setLive(live) {
  const c = $("#conn");
  c.dataset.live = live ? "1" : "0";
  c.querySelector(".conn-text").textContent = live ? "live" : "reconnecting";
}

function connect() {
  const es = new EventSource("/events");
  es.addEventListener("state", (e) => { setLive(true); apply(JSON.parse(e.data)); });
  es.addEventListener("system", (e) => system?.apply(JSON.parse(e.data)));
  es.onopen = () => setLive(true);
  es.onerror = () => setLive(false);  // EventSource retries by itself
}

connect();
setInterval(tick, 1000);
tick();
startSystem().then((s) => { system = s; }, (err) => {
  $("#foot-system").textContent = `system sensors unavailable: ${err.message}`;
});
