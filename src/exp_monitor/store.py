"""Poll the runs folder and derive what the dashboard shows: state, rates, ETAs, history."""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path

POLL = 0.5                      # s between folder scans
STALE_AFTER = 60.0              # s without an update before a run with a dead pid counts as stalled
RATE_WINDOW = 60.0              # s of history the "recent rate" is measured over
MIN_RATE_SPAN = 3.0             # s of history needed before the recent rate is trusted
RETIRE_STALLED_AFTER = 15 * 60  # s; a stalled run this old leaves the active grid for history
REFRESH = 5.0                   # s; rebuild the snapshot at least this often so decaying rates show
HISTORY_LIMIT = 100


def parse_ts(value) -> float | None:
    """ISO timestamp -> epoch seconds. Naive timestamps are local time, as the writers produce."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def pid_alive(pid) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie still answers kill(0) but is finished
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


def _int(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


class Run:
    """One run file plus the progress samples seen so far (for the recent rate)."""

    def __init__(self, key: str, data: dict, sig: tuple):
        self.key = key
        self.data = data
        self.sig = sig
        self.samples: deque[tuple[float, dict[str, int]]] = deque()
        self.state: str | None = None
        self.sample(time.time())

    def update(self, data: dict, sig: tuple, now: float):
        self.data, self.sig = data, sig
        self.sample(now)

    def phases(self) -> list[dict]:
        out = []
        for p in self.data.get("phases") or []:
            if isinstance(p, dict):
                out.append(dict(name=str(p.get("name", "?")), done=_int(p.get("done")), total=_int(p.get("total"))))
        return out

    def sample(self, now: float):
        t = parse_ts(self.data.get("updated")) or now
        done = {p["name"]: p["done"] for p in self.phases()}
        if self.samples and t <= self.samples[-1][0]:
            if t == self.samples[-1][0]:
                self.samples[-1] = (t, done)  # several writes within one second of `updated`
            return
        self.samples.append((t, done))

    def rate(self, now: float, phase: str | None) -> float | None:
        """Items/s over the last RATE_WINDOW, for one phase or (phase=None) all phases together."""
        # Keep the newest sample older than the window as the anchor, drop the rest.
        while len(self.samples) > 1 and self.samples[1][0] <= now - RATE_WINDOW:
            self.samples.popleft()
        if len(self.samples) < 2:
            return None
        (t0, d0), (_, d1) = self.samples[0], self.samples[-1]
        span = now - t0  # measured up to now, so a run that went quiet shows a falling rate
        if span < MIN_RATE_SPAN:
            return None
        if phase is None:
            delta = sum(d1.values()) - sum(d0.get(k, 0) for k in d1)
        else:
            delta = d1.get(phase, 0) - d0.get(phase, 0)
        return max(delta, 0) / span

    def view(self, now: float) -> dict:
        d = self.data
        started, updated = parse_ts(d.get("started")), parse_ts(d.get("updated"))
        status = str(d.get("status") or "running")
        alive = pid_alive(d.get("pid"))
        age = now - updated if updated else None
        quiet = status == "running" and age is not None and age > STALE_AFTER
        if status in ("done", "failed"):
            state = status
        elif quiet and not alive:
            state = "stalled"
        else:
            state = "running"
        self.state = state

        phases = self.phases()
        names = [p["name"] for p in phases]
        current = d.get("phase") if d.get("phase") in names else \
            next((p["name"] for p in phases if p["done"] < p["total"]), None)

        out = dict(
            key=self.key, run_id=d.get("run_id") or self.key, project=d.get("project"),
            experiment=d.get("experiment"), title=d.get("title") or d.get("experiment") or self.key,
            commit=d.get("commit"), dirty=bool(d.get("dirty")), pid=d.get("pid"), pid_alive=alive,
            workers=d.get("workers"), started_ts=started, updated_ts=updated, status=status, state=state,
            quiet=quiet and alive, phase=current, chain=d.get("chain") if isinstance(d.get("chain"), dict) else None,
            log_tail=[str(x) for x in (d.get("log_tail") or [])][-40:], error=d.get("error"),
            phases=phases, rate=None, rate_approx=False, eta_phase_at=None, eta_run_at=None,
        )
        if state != "running":
            return out

        # Recent rate per phase; before there is enough history, fall back to the whole-run average.
        approx = False
        rate = self.rate(now, None)
        if rate is None and started and updated and updated > started:
            rate, approx = sum(p["done"] for p in phases) / (updated - started), True
        for p in phases:
            p["rate"] = self.rate(now, p["name"])
        cur = next((p for p in phases if p["name"] == current), None)
        cur_rate = cur["rate"] if cur and cur["rate"] is not None else rate
        if cur and cur["rate"] is None:
            approx = True
        out.update(rate=cur_rate, rate_approx=approx)

        if cur_rate:
            if cur:
                out["eta_phase_at"] = now + max(cur["total"] - cur["done"], 0) / cur_rate
            remaining = sum(max(p["total"] - p["done"], 0) for p in phases)
            # Only worth showing separately when phases beyond the current one are still to come.
            if cur and remaining > cur["total"] - cur["done"]:
                out["eta_run_at"] = now + remaining / cur_rate
        return out


class Store:
    """Watches the folder in a thread; `snapshot` is the JSON the page renders, `version` bumps on change."""

    def __init__(self, runs_dir: Path, on_event=None):
        self.runs_dir = Path(runs_dir)
        self.on_event = on_event or (lambda kind, run: None)
        self.runs: dict[str, Run] = {}
        self.cond = threading.Condition()
        self.version = 0
        self.snapshot = "{}"
        self.clients = 0          # live /events connections, i.e. "the dashboard is open"
        self._primed = False
        self._sig = None
        self._built = 0.0

    def start(self):
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.scan()
        threading.Thread(target=self._loop, name="scan", daemon=True).start()

    def _loop(self):
        while True:
            time.sleep(POLL)
            try:
                self.scan()
            except Exception as e:  # keep watching whatever a single bad pass did
                print(f"exp-monitor: scan error: {e!r}", flush=True)

    @staticmethod
    def _read(path: str) -> dict | None:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return None  # vanished or half-written: keep the last good copy
        return data if isinstance(data, dict) else None

    def scan(self):
        now = time.time()
        events = []
        present = set()
        try:
            entries = list(os.scandir(self.runs_dir))
        except FileNotFoundError:
            entries = []
        for e in entries:
            # writers use <run_id>.tmp + rename, so only *.json is ever a complete file
            if not e.name.endswith(".json") or e.name.startswith("."):
                continue
            key = e.name[:-5]
            try:
                st = e.stat()
            except FileNotFoundError:
                continue
            present.add(key)
            sig = (st.st_mtime_ns, st.st_size, st.st_ino)
            run = self.runs.get(key)
            if run and run.sig == sig:
                continue
            data = self._read(e.path)
            if data is None:
                continue
            if run:
                run.update(data, sig, now)
            else:
                self.runs[key] = Run(key, data, sig)
                if self._primed:
                    events.append(("new", key))
        for key in set(self.runs) - present:
            del self.runs[key]

        views = []
        for run in self.runs.values():
            before = run.state
            v = run.view(now)
            views.append(v)
            if self._primed and before is not None and before != v["state"] and v["state"] != "running":
                events.append((v["state"], run.key))
        self._primed = True

        sig = tuple(sorted((r.key, r.sig, v["state"], v["quiet"], v["pid_alive"])
                           for r, v in zip(self.runs.values(), views)))
        if sig != self._sig or now - self._built >= REFRESH:
            self._sig, self._built = sig, now
            self._publish(now, views)

        by_key = {v["key"]: v for v in views}
        for kind, key in events:
            if key in by_key:
                self.on_event(kind, by_key[key])

    def _publish(self, now: float, views: list[dict]):
        def active(v):
            if v["state"] == "running":
                return True
            return v["state"] == "stalled" and now - (v["updated_ts"] or 0) < RETIRE_STALLED_AFTER

        live = sorted((v for v in views if active(v)),
                      key=lambda v: (v["state"] != "running", v["started_ts"] or 0, v["key"]))
        done = sorted((v for v in views if not active(v)), key=lambda v: -(v["updated_ts"] or 0))
        snap = dict(now=now, runs_dir=str(self.runs_dir), active=live,
                    history=done[:HISTORY_LIMIT], history_total=len(done))
        with self.cond:
            self.version += 1
            snap["version"] = self.version
            self.snapshot = json.dumps(snap)
            self.cond.notify_all()
