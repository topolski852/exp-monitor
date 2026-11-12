// The clock-in timers in the first strip card. The server owns the state (exp_monitor/timeclock.py); this shows it
// and sends button presses. Every open window gets each change as a "clock" event.
const $ = (s) => document.querySelector(s);
let clock = null;  // last view from the server
let skew = 0;      // server clock minus browser clock, s

function hm(seconds) {
  const m = Math.floor(Math.max(0, seconds) / 60);
  return `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
}

function tick() {
  if (!clock) return;
  const now = Date.now() / 1000 + skew;
  clock.timers.forEach((t, i) => {
    $(`#tc${i + 1}`).textContent = hm(t.acc + (t.since == null ? 0 : now - t.since));
  });
}

export function applyClock(view) {
  clock = view;
  skew = view.now - Date.now() / 1000;
  const card = $("#timeclock");
  card.dataset.state = view.state;
  card.querySelector('[data-action="in"]').disabled = view.state !== "off";
  card.querySelector('[data-action="out"]').disabled = view.state === "off";
  $("#tc-toggle").textContent = view.state === "on" ? "❚❚ PAUSE" : "▶ PLAY";
  tick();
}

export function startClock() {
  $("#timeclock").addEventListener("click", async (e) => {
    const action = e.target.closest("button")?.dataset.action;
    if (!action) return;
    const r = await fetch("/api/clock", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action }),
    });
    if (r.ok) applyClock(await r.json());
  });
  setInterval(tick, 1000);
}
