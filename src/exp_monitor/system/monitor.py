"""Samples every configured source on a timer and keeps the latest snapshot and history."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

from . import thresholds
from .config import Config
from .sources import SOURCES, Source

log = logging.getLogger(__name__)

# Source options that the monitor handles itself instead of passing to the source.
MONITOR_OPTIONS = {"required"}


class Monitor:
    def __init__(self, config: Config, sources: dict[str, Source] | None = None):
        self.config = config
        self.sources = sources if sources is not None else build_sources(config)
        self.keys = {s.id: self.sources[s.source].canonical(s.path) for s in config.sensors}
        points = max(1, config.app.history_seconds * 1000 // config.app.refresh_ms)
        self.history = {s.id: deque(maxlen=points) for s in config.sensors}
        self._snapshot: dict = {}
        self._seq = 0
        self._changed = threading.Condition()

    def meta(self) -> dict:
        """Everything about the dashboard that doesn't change between samples."""
        c = self.config
        return {
            "app": vars(c.app),
            "config_path": str(c.path) if c.path else None,
            "sensors": {s.id: {"name": s.name, "kind": s.kind, "about": s.about, "spec": s.spec,
                               "decimals": s.decimals, "direction": s.direction} for s in c.sensors},
            "layout": {"gauges": c.layout.gauges, "panels": [vars(p) for p in c.layout.panels]},
        }

    def sample(self) -> dict:
        readings, alerts, info = {}, [], {}
        for name, source in self.sources.items():
            try:
                for path, reading in source.sample().items():
                    readings[(name, path)] = reading
                info.update(source.info())
                health = source.health()
            except Exception as e:  # one broken source must not take the dashboard down
                log.exception("source %s failed", name)
                health = source.health()
                health.ok, health.message = False, f"{type(e).__name__}: {e}"
            if not health.ok:
                required = self.config.sources.get(name, {}).get("required", False)
                alerts.append({"level": "crit" if required else "warn",
                               "title": health.title or f"{name.upper()} UNAVAILABLE", "message": health.message})

        sensors = {}
        for s in self.config.sensors:
            reading = readings.get((s.source, self.keys[s.id]))
            entry = thresholds.evaluate(s, reading)
            if reading and reading.series is not None:
                entry["series"] = [round(v, 1) for v in reading.series]
            for field in ("value", "max", "warn", "crit"):
                entry[field] = _round(entry[field])
            sensors[s.id] = entry
            self.history[s.id].append(entry["value"])
            if entry["status"] == thresholds.CRIT:
                limit = "below" if s.direction == "low" else "at or over"
                alerts.append({"level": "crit", "title": f"{s.name.upper()} CRITICAL",
                               "message": f"{entry['value']:g} {entry['unit']} is {limit} the "
                                          f"{entry['crit']:g} {entry['unit']} limit. {s.spec}".strip()})

        snapshot = {"time": time.time(), "sensors": sensors, "alerts": alerts, "info": info}
        with self._changed:
            self._snapshot = snapshot
            self._seq += 1
            self._changed.notify_all()
        return snapshot

    def snapshot(self) -> dict:
        with self._changed:
            return self._snapshot

    def wait_for_update(self, seq: int, timeout: float) -> tuple[int, dict | None]:
        """Block until a snapshot newer than ``seq`` exists; returns (seq, snapshot or None on timeout)."""
        with self._changed:
            self._changed.wait_for(lambda: self._seq > seq, timeout)
            return (self._seq, self._snapshot) if self._seq > seq else (seq, None)

    def history_data(self) -> dict:
        return {"interval_ms": self.config.app.refresh_ms,
                "sensors": {sensor_id: list(values) for sensor_id, values in self.history.items()}}

    def run(self, stop: threading.Event, on_sample=None) -> None:
        interval = self.config.app.refresh_ms / 1000
        next_tick = time.monotonic()
        while not stop.is_set():
            self.sample()
            if on_sample:
                on_sample()
            next_tick += interval
            stop.wait(max(0.0, next_tick - time.monotonic()))


def build_sources(config: Config) -> dict[str, Source]:
    """Create each source that at least one configured sensor uses."""
    sources = {}
    for name in sorted({s.source for s in config.sensors}):
        options = {k: v for k, v in config.sources.get(name, {}).items() if k not in MONITOR_OPTIONS}
        sources[name] = SOURCES[name](**options)
    return sources


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 2)
