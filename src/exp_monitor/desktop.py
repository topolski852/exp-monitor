"""Desktop side effect: open the dashboard as an app window."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# Chromium-family browsers that understand --app=URL, in order of preference.
APP_BROWSERS = ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "brave-browser",
                "microsoft-edge", "vivaldi"]
FLATPAK_APPS = ["org.chromium.Chromium", "com.google.Chrome", "com.brave.Browser"]
SESSION_VARS = ["DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR",
                "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE"]


def session_env() -> dict[str, str]:
    """os.environ, plus the graphical-session variables from the systemd user manager if they are missing.

    A user service can start before the desktop imports DISPLAY/WAYLAND_DISPLAY, so look them up at use time.
    """
    env = dict(os.environ)
    if env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"):
        return env
    try:
        out = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True,
                             timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return env
    for line in out.splitlines():
        k, _, v = line.partition("=")
        if k in SESSION_VARS and not env.get(k):
            env[k] = v
    return env


def _spawn(cmd: list[str], env: dict[str, str]) -> bool:
    # Under systemd, give the browser its own scope so restarting the service does not kill the window.
    if os.environ.get("INVOCATION_ID") and shutil.which("systemd-run"):
        cmd = ["systemd-run", "--user", "--scope", "--quiet", "--collect", "--"] + cmd
    try:
        subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def app_command(url: str) -> list[str] | None:
    for name in APP_BROWSERS:
        exe = shutil.which(name) or (f"/snap/bin/{name}" if os.path.exists(f"/snap/bin/{name}") else None)
        if exe:
            return [exe, f"--app={url}", "--window-size=1360,900"]
    if shutil.which("flatpak"):
        try:
            installed = subprocess.run(["flatpak", "list", "--app", "--columns=application"], capture_output=True,
                                       text=True, timeout=5).stdout.split()
        except (OSError, subprocess.SubprocessError):
            installed = []
        for app in FLATPAK_APPS:
            if app in installed:
                return ["flatpak", "run", app, f"--app={url}", "--window-size=1360,900"]
    return None


def open_dashboard(url: str) -> str:
    """Open `url` as an app window if a Chromium-family browser exists, else via xdg-open. Returns what ran."""
    env = session_env()
    cmd = app_command(url)
    if cmd and _spawn(cmd, env):
        return Path(cmd[0]).name if cmd[0] != "flatpak" else f"flatpak {cmd[2]}"
    if shutil.which("xdg-open") and _spawn(["xdg-open", url], env):
        return "xdg-open"
    return "nothing (no browser found)"
