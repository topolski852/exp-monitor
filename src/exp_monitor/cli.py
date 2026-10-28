"""exp-monitor command line: serve, open, status, install-service, system."""
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
from exp_monitor.system import commands as system_commands

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


class App:
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
        log(f"{kind}: {run['key']}")
        # a file that shows up already finished (copied in, backfilled) is not worth a window
        if kind == "new" and self.args.auto_open and run["state"] == "running" and self.store.clients == 0:
            threading.Thread(target=self.open_window, args=(f"new run {run['key']}",), daemon=True).start()

    def start_system(self):
        """The hardware sensors. Without a usable config the dashboard still runs, minus the System section."""
        if not self.args.system:
            return None, "turned off with --no-system"
        from exp_monitor.system.config import ConfigError
        from exp_monitor.system.monitor import Monitor
        try:
            monitor = Monitor(system_commands.load_config(self.args.system_config))
            monitor.sample()
        except (ConfigError, OSError) as e:
            log(f"system sensors off: {e}")
            return None, str(e)

        def wake_streams():  # /events waits on the run store's condition for both feeds
            with self.store.cond:
                self.store.cond.notify_all()

        threading.Thread(target=monitor.run, args=(threading.Event(), wake_streams), daemon=True,
                         name="sampler").start()
        log(f"system sensors from {monitor.config.path}")
        return monitor, ""

    def serve(self, open_now: bool):
        from exp_monitor.server import make_server
        monitor, system_error = self.start_system()
        try:
            httpd = make_server(self.store, HOST, self.args.port, monitor, system_error)
        except OSError as e:
            log(f"cannot listen on {HOST}:{self.args.port}: {e.strerror}")
            if ping(self.args.port):
                log("an exp-monitor is already running there (systemctl --user status exp-monitor?)")
            sys.exit(1)
        self.store.start()
        log(f"watching {self.store.runs_dir}")
        log(f"serving {self.url}  (auto-open {'on' if self.args.auto_open else 'off'})")
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
    App(args).serve(open_now=True)


def cmd_serve(args):
    App(args).serve(open_now=args.open)


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
    p.add_argument("--system-config", type=Path, default=d(None),
                   help="hardware sensor config (default: $EXP_MONITOR_SYSTEM_CONFIG, "
                        "~/.config/exp-monitor/system.toml, then the bundled configs/isaac-sim-rig.toml)")
    p.add_argument("--no-system", dest="system", action="store_false", default=d(True),
                   help="runs only, no hardware sensors")


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
    system_commands.add_parser(sub)
    args = ap.parse_args(argv)
    args.func(args)
