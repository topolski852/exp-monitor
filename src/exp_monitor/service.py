"""systemd user service: always-on server, started with the user session."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

NAME = "exp-monitor.service"
UNIT = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "systemd/user" / NAME
ROOT = Path(__file__).resolve().parents[2]  # project dir (src/exp_monitor/service.py)


def unit_text(args) -> str:
    uv = shutil.which("uv")
    if uv and (ROOT / "pyproject.toml").exists():
        exec_start = f"{uv} run --project {ROOT} --quiet exp-monitor serve"
    else:
        exec_start = f"{sys.executable} -m exp_monitor serve"
    flags = [f"--port {args.port}", f"--runs-dir {args.runs_dir}"]
    flags += [] if args.auto_open else ["--no-auto-open"]
    flags += [] if args.notify else ["--no-notify"]
    flags += [f"--system-config {args.system_config.resolve()}"] if args.system_config else []
    flags += [] if args.system else ["--no-system"]
    return f"""[Unit]
Description=exp-monitor live experiment dashboard (http://127.0.0.1:{args.port}/)

[Service]
ExecStart={exec_start} {' '.join(flags)}
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


def _systemctl(*a: str) -> bool:
    r = subprocess.run(["systemctl", "--user", *a])
    return r.returncode == 0


def install(args):
    text = unit_text(args)
    if args.print:
        print(text, end="")
        return
    UNIT.parent.mkdir(parents=True, exist_ok=True)
    UNIT.write_text(text)
    print(f"wrote {UNIT}")
    _systemctl("daemon-reload")
    if _systemctl("enable", NAME) and _systemctl("restart", NAME):
        print(f"exp-monitor is running: http://127.0.0.1:{args.port}/  (logs: journalctl --user -u exp-monitor -f)")


def uninstall(args):
    _systemctl("disable", "--now", NAME)
    if UNIT.exists():
        UNIT.unlink()
        print(f"removed {UNIT}")
    _systemctl("daemon-reload")
