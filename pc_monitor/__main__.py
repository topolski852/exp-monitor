"""Command line: ``pc-monitor [run|check|discover]``."""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import __version__, config as config_module, discover
from .monitor import Monitor

STATUS_COLORS = {"ok": "\033[92m", "warn": "\033[93m", "crit": "\033[91m", "missing": "\033[90m"}
RESET = "\033[0m"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pc-monitor", description="Config-driven hardware monitor dashboard.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="log debug output")
    # The run options also work without the word "run": `pc-monitor -c FILE`.
    _run_args(parser, argparse.SUPPRESS)
    parser.set_defaults(config=None, host=None, port=None, no_browser=False)
    commands = parser.add_subparsers(dest="command")

    run = commands.add_parser("run", help="start the dashboard (default)")
    _run_args(run, argparse.SUPPRESS)

    check = commands.add_parser("check", help="print every configured sensor once and exit")
    _config_arg(check, argparse.SUPPRESS)

    disc = commands.add_parser("discover", help="list and explain every sensor on this machine")
    disc.add_argument("--all", action="store_true", help="include sensors that are noise")
    disc.add_argument("--write", metavar="PATH", type=Path, help="write a starter config to PATH")
    disc.add_argument("--machine", default="", help="machine name for the starter config")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    try:
        if args.command == "discover":
            return cmd_discover(args)
        if args.command == "check":
            return cmd_check(args)
        return cmd_run(args)
    except config_module.ConfigError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2


# Options are declared on both the main parser and the subcommands with
# SUPPRESS defaults, so a value given in either place is kept.
def _config_arg(parser: argparse.ArgumentParser, default) -> None:
    parser.add_argument("-c", "--config", type=Path, default=default,
                        help="config file (default: $PC_MONITOR_CONFIG or ~/.config/pc-monitor/config.toml)")


def _run_args(parser: argparse.ArgumentParser, default) -> None:
    _config_arg(parser, default)
    parser.add_argument("--host", default=default,
                        help="address to listen on; 0.0.0.0 lets other devices on your network connect")
    parser.add_argument("--port", type=int, default=default)
    parser.add_argument("--no-browser", action="store_true", default=default,
                        help="don't open the dashboard in a browser")


def _load_config(args) -> config_module.Config:
    path = args.config or config_module.default_path()
    if path is None:
        raise config_module.ConfigError(
            "no config found. Create one with:\n"
            "  pc-monitor discover --write ~/.config/pc-monitor/config.toml")
    return config_module.load(path)


def cmd_run(args) -> int:
    from .server import make_server

    config = _load_config(args)
    host = args.host or config.app.host
    port = args.port or config.app.port
    monitor = Monitor(config)
    monitor.sample()

    stop = threading.Event()
    threading.Thread(target=monitor.run, args=(stop,), daemon=True, name="sampler").start()
    try:
        server = make_server(monitor, host, port)
    except OSError as e:
        print(f"Can't listen on {host}:{port}: {e}", file=sys.stderr)
        return 1

    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}/"
    print(f"PC Monitor running at {url}  (config: {config.path})")
    if host == "0.0.0.0":
        print(f"Other devices on your network can use http://{socket.gethostname()}:{port}/")
    if config.app.open_browser and not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
    return 0


def cmd_check(args) -> int:
    config = _load_config(args)
    monitor = Monitor(config)
    monitor.sample()
    time.sleep(1)  # CPU load and disk speed are measured between two samples
    snapshot = monitor.sample()

    width = max(len(s.name) for s in config.sensors)
    for s in config.sensors:
        e = snapshot["sensors"][s.id]
        value = "NO SIGNAL" if e["value"] is None else f"{e['value']:.{s.decimals}f} {e['unit']}"
        limits = "  ".join(f"{k} {e[k]:g}" for k in ("warn", "crit") if e[k] is not None)
        color = STATUS_COLORS[e["status"]]
        print(f"{color}{e['status'].upper():8}{RESET} {s.name:{width}}  {value:>14}   {limits}")
    for alert in snapshot["alerts"]:
        print(f"\n{STATUS_COLORS['crit' if alert['level'] == 'crit' else 'warn']}! {alert['title']}{RESET}\n"
              f"  {alert['message']}")
    return 1 if any(a["level"] == "crit" for a in snapshot["alerts"]) else 0


def cmd_discover(args) -> int:
    found, nvidia_problem, nvidia_gpu = discover.scan()
    print(discover.report(found, nvidia_problem, args.all))
    if nvidia_gpu and nvidia_problem:
        print("An NVIDIA GPU is installed, so its sensors are included in starter configs anyway.")
    if args.write:
        if args.write.exists():
            print(f"\n{args.write} already exists; not overwriting it.", file=sys.stderr)
            return 1
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(discover.starter_config(found, nvidia_gpu, args.machine))
        print(f"\nWrote {args.write}. Review the names and limits, then run: pc-monitor -c {args.write}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
