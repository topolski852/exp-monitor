"""Stdlib HTTP server: the page, a JSON snapshot, and a Server-Sent Events stream of snapshots."""
from __future__ import annotations

import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from exp_monitor.store import Store

STATIC = Path(__file__).parent / "static"
HEARTBEAT = 15.0


def make_server(store: Store, host: str, port: int) -> ThreadingHTTPServer:
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
            if path == "/events":
                return self._events()
            if path.startswith("/static/"):
                name = path[len("/static/"):]
                f = STATIC / name
                if "/" not in name and f.is_file():
                    ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                    if ctype.startswith("text/") or ctype.endswith(("javascript", "json", "svg+xml")):
                        ctype += "; charset=utf-8"
                    return self._send(200, f.read_bytes(), ctype)
            self._send(404, b"not found\n", "text/plain")

        def _events(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with store.cond:
                store.clients += 1
            last = -1
            try:
                self.wfile.write(b"retry: 2000\n\n")
                while True:
                    with store.cond:
                        if store.version == last:
                            store.cond.wait(timeout=HEARTBEAT)
                        version, snap = store.version, store.snapshot
                    if version != last:
                        self.wfile.write(f"event: state\ndata: {snap}\n\n".encode())
                        last = version
                    else:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except OSError:  # client went away
                pass
            finally:
                with store.cond:
                    store.clients -= 1

    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    return httpd
