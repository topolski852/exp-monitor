"""Local web server: the dashboard page plus a JSON API and a live Server-Sent Events stream.

    GET /               dashboard
    GET /static/<file>  dashboard assets
    GET /api/meta       sensor names, descriptions and layout (changes only with the config)
    GET /api/snapshot   latest readings
    GET /api/history    recent values for the sparklines
    GET /api/stream     a new snapshot every refresh (text/event-stream)
"""

from __future__ import annotations

import json
import logging
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .monitor import Monitor

log = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).parent / "web"
KEEPALIVE_SECONDS = 15


def make_server(monitor: Monitor, host: str, port: int) -> ThreadingHTTPServer:
    # Only files that ship with the app can be served, so no path tricks reach the rest of the disk.
    static = {p.name: p for p in WEB_ROOT.iterdir() if p.is_file()}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                return self._file(static["index.html"])
            if path.startswith("/static/") and path[len("/static/"):] in static:
                return self._file(static[path[len("/static/"):]])
            if path == "/api/meta":
                return self._json(monitor.meta())
            if path == "/api/snapshot":
                return self._json(monitor.snapshot())
            if path == "/api/history":
                return self._json(monitor.history_data())
            if path == "/api/stream":
                return self._stream()
            self.send_error(HTTPStatus.NOT_FOUND)

        def _file(self, path: Path):
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data):
            body = json.dumps(data).encode()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _stream(self):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            seq = 0
            try:
                while True:
                    seq, snapshot = monitor.wait_for_update(seq, KEEPALIVE_SECONDS)
                    message = f"data: {json.dumps(snapshot)}\n\n" if snapshot else ": keepalive\n\n"
                    self.wfile.write(message.encode())
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # the browser tab closed

        def log_message(self, format, *args):
            log.debug("%s %s", self.address_string(), format % args)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
