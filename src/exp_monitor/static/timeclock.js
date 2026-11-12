// The clock-in timers in the first strip card. The server owns the state (exp_monitor/timeclock.py); this shows it
// and sends button presses. Every open window gets each change as a "clock" event.
const $ = (s) => document.querySelector(s);
let clock = null;  // last view from the server
let skew = 0;      // server clock minus browser clock, s

// H:MM. There are no seconds, so a running timer's colon blinks with them instead: on for the first half of
// each second, off for the second.
function hm(seconds, blinkOff) {
  const m = Math.floor(Math.max(0, seconds) / 60);
  return `${Math.floor(m / 60)}<span class="tc-colon${blinkOff ? " off" : ""}">:</span>${String(m % 60).padStart(2, "0")}`;
}

function tick() {
  if (!clock) return;
  const now = Date.now() / 1000 + skew;
  const secondHalf = Date.now() % 1000 >= 500;
  clock.timers.forEach((t, i) => {
    const running = t.since != null;
    $(`#tc${i + 1}`).innerHTML = hm(t.acc + (running ? now - t.since : 0), running && secondHalf);
  });
}

// Tick on every half-second boundary so the blink lands on the seconds.
function schedule() {
  setTimeout(() => { tick(); schedule(); }, 500 - (Date.now() % 500) + 5);
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
  schedule();
}
