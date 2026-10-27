"""Stdlib HTTP server: the page, JSON snapshots, and one Server-Sent Events stream.

    GET /events               "state" events (runs) and "system" events (sensor samples)
    GET /api/state            runs snapshot
    GET /api/system/meta      sensor names, descriptions and layout ({"enabled": false, ...} without a config)
    GET /api/system/history   recent sensor values for the sparklines
"""
from __future__ import annotations

import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from exp_monitor.store import Store
from exp_monitor.system.monitor import Monitor

STATIC = Path(__file__).parent / "static"
HEARTBEAT = 15.0


def make_server(store: Store, host: str, port: int, monitor: Monitor | None = None,
                system_error: str = "") -> ThreadingHTTPServer:
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
            if path == "/api/ping":
                body = json.dumps(dict(app="exp-monitor", pid=os.getpid(), clients=store.clients,
                                       runs_dir=str(store.runs_dir)))
                return self._send(200, body.encode(), "application/json")
            if path == "/api/system/meta":
                meta = dict(monitor.meta(), enabled=True) if monitor else dict(enabled=False, reason=system_error)
                return self._json(meta)
            if path == "/api/system/history" and monitor:
                return self._json(monitor.history_data())
            if path == "/events":
                return self._events()
            f = static.get(path[len("/static/"):]) if path.startswith("/static/") else None
            if f:
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype.endswith(("javascript", "json", "svg+xml")):
                    ctype += "; charset=utf-8"
                return self._send(200, f.read_bytes(), ctype)
            self._send(404, b"not found\n", "text/plain")

        def _json(self, data):
            self._send(200, json.dumps(data).encode(), "application/json")

        def _events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with store.cond:
                store.clients += 1
            last, sys_seq = -1, 0

            def system_seq():
                return monitor.wait_for_update(sys_seq, 0)[0] if monitor else 0

            try:
                self.wfile.write(b"retry: 2000\n\n")
                while True:
                    # The sampler thread notifies store.cond too, so one wait covers both feeds.
                    with store.cond:
                        if store.version == last and system_seq() == sys_seq:
                            store.cond.wait(timeout=HEARTBEAT)
                        version, snap = store.version, store.snapshot
                    out = []
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
