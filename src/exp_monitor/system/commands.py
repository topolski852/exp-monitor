"""``exp-monitor system [check|discover]``: the hardware side's own commands."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from . import config as config_module, discover
from .monitor import Monitor

STATUS_COLORS = {"ok": "\033[92m", "warn": "\033[93m", "crit": "\033[91m", "missing": "\033[90m"}
RESET = "\033[0m"


def add_parser(commands) -> None:
    system = commands.add_parser("system", help="hardware sensors: check readings, discover sensors")
    sub = system.add_subparsers(dest="system_command", required=True, metavar="{check,discover}")

    check = sub.add_parser("check", help="print every configured sensor once and exit")
    check.add_argument("-c", "--config", type=Path, help=f"system config (default: {config_module.USER_PATH} "
                                                         f"if it exists, else the bundled rig config)")
    check.set_defaults(func=cmd_check)

    disc = sub.add_parser("discover", help="list and explain every sensor on this machine")
    disc.add_argument("--all", action="store_true", help="include sensors that are noise")
    disc.add_argument("--write", metavar="PATH", type=Path, nargs="?", const=config_module.USER_PATH,
                      help=f"write a starter config (default PATH: {config_module.USER_PATH})")
    disc.add_argument("--machine", default="", help="machine name for the starter config")
    disc.set_defaults(func=cmd_discover)


def load_config(path: Path | None) -> config_module.Config:
    path = path or config_module.default_path()
    if path is None:
        raise config_module.ConfigError(
            "no system config found. Create one with:\n  exp-monitor system discover --write")
    return config_module.load(path)


def cmd_check(args) -> None:
    try:
        config = load_config(args.config)
    except config_module.ConfigError as e:
        sys.exit(f"Config error: {e}")
    monitor = Monitor(config)
    monitor.sample()
    time.sleep(1)  # CPU load and disk speed are measured between two samples
    snapshot = monitor.sample()

    print(f"config: {config.path}")
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
    if any(a["level"] == "crit" for a in snapshot["alerts"]):
        sys.exit(1)


def cmd_discover(args) -> None:
    found, nvidia_problem, nvidia_gpu = discover.scan()
    print(discover.report(found, nvidia_problem, args.all))
    if nvidia_gpu and nvidia_problem:
        print("An NVIDIA GPU is installed, so its sensors are included in starter configs anyway.")
    if args.write:
        if args.write.exists():
            sys.exit(f"\n{args.write} already exists; not overwriting it.")
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(discover.starter_config(found, nvidia_gpu, args.machine))
        print(f"\nWrote {args.write}. Review the names and limits; the dashboard uses it after "
              f"`systemctl --user restart exp-monitor`.")
