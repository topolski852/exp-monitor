"""exp-monitor command line: serve, open, status, install-service."""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

from exp_monitor import HOST, PORT, RUNS_DIR, desktop, service
from exp_monitor.store import Store

OPEN_COOLDOWN = 30.0  # s; a just-launched window takes a moment to connect, don't launch a second one


def log(msg: str):
    print(f"exp-monitor: {msg}", flush=True)


def ping(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/ping", timeout=1) as r:
            info = json.load(r)
        return info if info.get("app") == "exp-monitor" else None
    except (OSError, ValueError):
        return None


def fmt_duration(s: float | None) -> str:
    if s is None:
        return "—"
    s = int(max(s, 0))
    h, m = divmod(s // 60, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m{s % 60:02d}s" if m else f"{s}s"


class Monitor:
    def __init__(self, args):
        self.args = args
        self.url = f"http://{HOST}:{args.port}/"
        self.store = Store(args.runs_dir, on_event=self.on_event)
        self.last_open = 0.0
        self.lock = threading.Lock()

    def open_window(self, reason: str):
        with self.lock:
            if time.time() - self.last_open < OPEN_COOLDOWN:
                return
            self.last_open = time.time()
        log(f"{reason}: opening dashboard with {desktop.open_dashboard(self.url)}")

    def on_event(self, kind: str, run: dict):
        title = run["title"]
        log(f"{kind}: {run['key']}")
        if kind == "new":
            # a file that shows up already finished (copied in, backfilled) is not worth a window
            if self.args.auto_open and run["state"] == "running" and self.store.clients == 0:
                threading.Thread(target=self.open_window, args=(f"new run {run['key']}",), daemon=True).start()
            return
        if not self.args.notify:
            return
        took = fmt_duration((run["updated_ts"] or 0) - (run["started_ts"] or 0)) if run["started_ts"] else "—"
        where = f"{run['project']} · {run['experiment']}"
        if kind == "done":
            msg = (f"done: {title}", f"{where}\nfinished in {took}", "normal")
        elif kind == "failed":
            err = str(run["error"] or "no error message").strip().splitlines()
            msg = (f"FAILED: {title}", f"{where} after {took}\n{err[-1] if err else ''}", "critical")
        elif kind == "stalled":
            msg = (f"stalled: {title}", f"{where}\npid {run['pid']} is gone and no update for over a minute",
                   "critical")
        else:
            return
        threading.Thread(target=desktop.notify, args=msg, daemon=True).start()

    def serve(self, open_now: bool):
        from exp_monitor.server import make_server
        try:
            httpd = make_server(self.store, HOST, self.args.port)
        except OSError as e:
            log(f"cannot listen on {HOST}:{self.args.port}: {e.strerror}")
            if ping(self.args.port):
                log("an exp-monitor is already running there (systemctl --user status exp-monitor?)")
            sys.exit(1)
        self.store.start()
        log(f"watching {self.store.runs_dir}")
        log(f"serving {self.url}  (auto-open {'on' if self.args.auto_open else 'off'}, "
            f"notifications {'on' if self.args.notify else 'off'})")
        if open_now:
            threading.Thread(target=self.open_window, args=("start",), daemon=True).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


def cmd_default(args):
    """The one command to remember: show the dashboard, starting the server if none is running."""
    if ping(args.port):
        log(f"already running, opening with {desktop.open_dashboard(f'http://{HOST}:{args.port}/')}")
        return
    Monitor(args).serve(open_now=True)


def cmd_serve(args):
    Monitor(args).serve(open_now=args.open)


def cmd_open(args):
    if not ping(args.port):
        log(f"no server on port {args.port}; run `exp-monitor` (or install the service) first")
        sys.exit(1)
    log(f"opening with {desktop.open_dashboard(f'http://{HOST}:{args.port}/')}")


def cmd_status(args):
    try:
        with urllib.request.urlopen(f"http://{HOST}:{args.port}/api/state", timeout=2) as r:
            snap = json.load(r)
    except OSError:
        log(f"no server on port {args.port}")
        sys.exit(1)
    now = snap["now"]
    for v in snap["active"]:
        phases = "  ".join(f"{p['name']} {p['done']}/{p['total']}" for p in v["phases"])
        eta = fmt_duration(v["eta_phase_at"] - now) if v["eta_phase_at"] else "—"
        print(f"{v['state'].upper():8} {v['title']}  [{v['experiment']}]  {phases}  eta {eta}")
    if not snap["active"]:
        print("no active runs")
    for v in snap["history"][:5]:
        when = time.strftime("%m-%d %H:%M", time.localtime(v["updated_ts"])) if v["updated_ts"] else "?"
        print(f"{v['state']:8} {v['title']}  [{v['experiment']}]  {when}")


def _common(p: argparse.ArgumentParser, top: bool):
    """Shared options, accepted before or after the command. Only the top level carries real defaults."""
    d = (lambda v: v) if top else (lambda v: argparse.SUPPRESS)
    p.add_argument("--port", type=int, default=d(PORT), help=f"HTTP port on {HOST} (default {PORT}, env EXP_MONITOR_PORT)")
    p.add_argument("--runs-dir", type=Path, default=d(RUNS_DIR),
                   help=f"run files folder (default {RUNS_DIR}, env EXP_MONITOR_DIR)")
    p.add_argument("--no-auto-open", dest="auto_open", action="store_false", default=d(True),
                   help="don't pop the window open when a new run file appears")
    p.add_argument("--no-notify", dest="notify", action="store_false", default=d(True),
                   help="no desktop notifications on done / failed / stalled")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="exp-monitor", description="Live dashboard for experiment runs. "
                                 "With no command: open the dashboard, starting the server if needed.")
    _common(ap, top=True)
    ap.set_defaults(func=cmd_default)
    sub = ap.add_subparsers(title="commands", metavar="")

    def add(name, func, help):
        p = sub.add_parser(name, help=help)
        _common(p, top=False)
        p.set_defaults(func=func)
        return p

    add("serve", cmd_serve, "run the server (what the systemd service runs)").add_argument(
        "--open", action="store_true", help="also open the window at start")
    add("open", cmd_open, "open the window for the running server")
    add("status", cmd_status, "print active runs in the terminal")
    add("install-service", service.install, "install + start the systemd user service").add_argument(
        "--print", action="store_true", help="only print the unit file")
    add("uninstall-service", service.uninstall, "stop + remove the systemd user service")
    args = ap.parse_args(argv)
    args.func(args)
