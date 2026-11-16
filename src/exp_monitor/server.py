"""Stdlib HTTP server: the page, JSON snapshots, and one Server-Sent Events stream.

    GET /events               "state" events (runs), "system" events (sensor samples), "clock" events (timers)
    GET /api/state            runs snapshot
    GET /api/clock            clock-in timers
    POST /api/clock           {"action": "in" | "out" | "toggle"}, a button press
    GET /api/system/meta      sensor names, descriptions and layout ({"enabled": false, ...} without a config)
    GET /api/system/history   recent sensor values for the sparklines
    GET /api/queue            the Stampede Queue's snapshot (stampede-queue writes it every pass), or {"enabled": false}
"""
from __future__ import annotations

import json
import time
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from exp_monitor.store import Store
from exp_monitor.system.monitor import Monitor
from exp_monitor.timeclock import ACTIONS, TimeClock

STATIC = Path(__file__).parent / "static"
HEARTBEAT = 15.0


QUEUE_SNAPSHOT = Path(os.environ.get("STAMPEDE_QUEUE_SNAPSHOT", Path(os.environ.get(
    "XDG_STATE_HOME", Path.home() / ".local/state")) / "stampede-queue" / "snapshot.json"))


def queue_snapshot() -> dict:
    """The queue's own snapshot; stale (the daemon stopped) after 30 s."""
    try:
        snap = json.loads(QUEUE_SNAPSHOT.read_text())
    except (OSError, ValueError):
        return {"enabled": False}
    snap["enabled"] = True
    snap["stale"] = time.time() - snap.get("t", 0) > 30
    return snap


def make_server(store: Store, host: str, port: int, monitor: Monitor | None = None,
                system_error: str = "", clock: TimeClock | None = None) -> ThreadingHTTPServer:
    # Only files that ship with the app are served, so no path tricks reach the rest of the disk.
    static = {p.relative_to(STATIC).as_posix(): p for p in STATIC.rglob("*") if p.is_file()}

    class Handler(BaseHTTPRequestHandler):
        server_version = "exp-monitor"

        def log_message(self, fmt, *args):  # keep the journal for events, not request lines
            pass

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                path = "/static/index.html"
            if path == "/api/state":
                with store.cond:
                    snap = store.snapshot
                return self._send(200, snap.encode(), "application/json")
            if path == "/api/clock" and clock:
                return self._send(200, clock.view().encode(), "application/json")
            if path == "/api/ping":
                body = json.dumps(dict(app="exp-monitor", pid=os.getpid(), clients=store.clients,
                                       runs_dir=str(store.runs_dir)))
                return self._send(200, body.encode(), "application/json")
            if path == "/api/system/meta":
                meta = dict(monitor.meta(), enabled=True) if monitor else dict(enabled=False, reason=system_error)
                return self._json(meta)
            if path == "/api/system/history" and monitor:
                return self._json(monitor.history_data())
            if path == "/api/queue":
                return self._json(queue_snapshot())
            if path == "/events":
                return self._events()
            f = static.get(path[len("/static/"):]) if path.startswith("/static/") else None
            if f:
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype.endswith(("javascript", "json", "svg+xml")):
                    ctype += "; charset=utf-8"
                return self._send(200, f.read_bytes(), ctype)
            self._send(404, b"not found\n", "text/plain")

        def do_POST(self):
            # Requiring a JSON body means another site's page can't press the buttons with a plain form post.
            if self.path != "/api/clock" or not clock or self.headers.get_content_type() != "application/json":
                return self._send(404, b"not found\n", "text/plain")
            try:
                action = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))["action"]
            except (ValueError, KeyError, TypeError):
                action = None
            if action not in ACTIONS:
                return self._send(400, b"action must be in, out or toggle\n", "text/plain")
            clock.press(action)
            self._send(200, clock.view().encode(), "application/json")

        def _json(self, data):
            self._send(200, json.dumps(data).encode(), "application/json")

        def _events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with store.cond:
                store.clients += 1
            last, sys_seq, clock_seen = -1, 0, -1

            def clock_version():
                return clock.version if clock else -1

            def system_seq():
                return monitor.wait_for_update(sys_seq, 0)[0] if monitor else 0

            try:
                self.wfile.write(b"retry: 2000\n\n")
                while True:
                    # The sampler thread notifies store.cond too, so one wait covers both feeds.
                    with store.cond:
                        if store.version == last and system_seq() == sys_seq and clock_version() == clock_seen:
                            store.cond.wait(timeout=HEARTBEAT)
                        version, snap = store.version, store.snapshot
                    out = []
                    if clock and clock.version != clock_seen:
                        clock_seen = clock.version
                        out.append(f"event: clock\ndata: {clock.view()}\n\n")
                    if version != last:
                        out.append(f"event: state\ndata: {snap}\n\n")
                        last = version
                    if monitor:
                        seq, sample = monitor.wait_for_update(sys_seq, 0)
                        if sample:
                            out.append(f"event: system\ndata: {json.dumps(sample)}\n\n")
                            sys_seq = seq
                    self.wfile.write(("".join(out) or ": ping\n\n").encode())
                    self.wfile.flush()
            except OSError:  # client went away
                pass
            finally:
                with store.cond:
                    store.clients -= 1

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd
