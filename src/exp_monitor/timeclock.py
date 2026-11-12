"""Clock-in / clock-out timers: timer 1 (pausable) and timer 2 (runs from clock-in to clock-out).

Each timer is ``acc`` seconds already counted plus, while it runs, the time since ``since`` (epoch
seconds), so the state survives restarts and reboots without ticking. It lives in one JSON file.

    off     both stopped    in: reset both to 0 and start both   play: start both, no reset
    on      both running    out: stop both                       pause: stop timer 1
    paused  only timer 2    out: stop both                       play: restart timer 1
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

ACTIONS = ("in", "out", "toggle")


def _timer(acc: float = 0.0, since: float | None = None) -> dict:
    return {"acc": acc, "since": since}


def _stop(timer: dict, now: float):
    if timer["since"] is not None:
        timer["acc"] += now - timer["since"]
        timer["since"] = None


class TimeClock:
    def __init__(self, path: Path, cond: threading.Condition):
        self.path = path
        self.cond = cond  # the run store's condition, so /events wakes for clock presses too
        self.version = 0
        self.timers = [_timer(), _timer()]
        try:
            saved = json.loads(path.read_text())
            timers = [_timer(float(t["acc"]), t["since"] and float(t["since"])) for t in saved["timers"]]
            if len(timers) == 2:
                self.timers = timers
        except (OSError, ValueError, KeyError, TypeError):
            pass

    @property
    def state(self) -> str:
        t1, t2 = (t["since"] is not None for t in self.timers)
        return "on" if t1 and t2 else "paused" if t2 else "off"

    def view(self) -> str:
        with self.cond:
            return json.dumps({"now": time.time(), "state": self.state, "timers": self.timers})

    def press(self, action: str) -> bool:
        """Apply a button press; False when it does nothing in the current state."""
        with self.cond:  # presses can arrive on several request threads at once
            now, state = time.time(), self.state
            t1, t2 = self.timers
            if action == "in" and state == "off":
                self.timers = [_timer(0.0, now), _timer(0.0, now)]
            elif action == "out" and state != "off":
                for t in self.timers:
                    _stop(t, now)
            elif action == "toggle":
                if state == "on":
                    _stop(t1, now)
                elif state == "paused":
                    t1["since"] = now
                else:
                    t1["since"] = t2["since"] = now
            else:
                return False
            self._save()
            self.version += 1
            self.cond.notify_all()
        return True

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"timers": self.timers}))
        os.replace(tmp, self.path)
