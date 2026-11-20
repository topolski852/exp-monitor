"""The Stampede Queue (~/humanoid/stampede-queue), read from the files it writes; it owns their format.

Each queue run is a systemd user unit named <prefix>-<id> (default sq-848), so a process's cgroup says which queue
run it belongs to. That is how a run file's pid is matched to its queue run without changing the run-file contract.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

SNAPSHOT = Path(os.environ.get("STAMPEDE_QUEUE_SNAPSHOT", Path(os.environ.get(
    "XDG_STATE_HOME", Path.home() / ".local/state")) / "stampede-queue" / "snapshot.json"))
LOG_DIR = Path(os.environ.get("STAMPEDE_QUEUE_LOGS", Path.home() / "humanoid" / "stampede-data" / "queue"))
UNIT = re.compile(rf"/{re.escape(os.environ.get('STAMPEDE_QUEUE_UNIT_PREFIX', 'sq'))}-(\d+)\.service$", re.M)
STALE_AFTER = 30.0  # s; the daemon rewrites the snapshot every pass, so an older one means it stopped
LOG_BYTES = 16384   # read this much from the end of a run's log for its tail


def snapshot() -> dict:
    """The queue's own snapshot, plus enabled (the file exists) and stale (the daemon stopped)."""
    try:
        snap = json.loads(SNAPSHOT.read_text())
    except (OSError, ValueError):
        return {"enabled": False}
    snap["enabled"] = True
    snap["stale"] = time.time() - snap.get("t", 0) > STALE_AFTER
    return snap


def job_of(pid) -> int | None:
    """The queue run a process belongs to, or None when it wasn't started by the queue."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    try:
        m = UNIT.search(Path(f"/proc/{pid}/cgroup").read_text())
    except OSError:
        return None
    return int(m.group(1)) if m else None


def log_tail(job_id: int, lines: int = 40) -> list[str]:
    """The last lines of a queue run's log (stampede-queue names it <id:06d>_<name>.log)."""
    for path in LOG_DIR.glob(f"{job_id:06d}_*.log"):
        try:
            with open(path, "rb") as f:
                start = max(0, os.fstat(f.fileno()).st_size - LOG_BYTES)
                f.seek(start)
                text = f.read().decode("utf-8", "replace")
        except OSError:
            return []
        rows = text.rstrip("\n").split("\n")[1 if start else 0:]  # a mid-file start cuts the first line
        # progress bars redraw one line with \r; keep what the last redraw left
        return [r.rstrip("\r").rsplit("\r", 1)[-1] for r in rows if r.strip()][-lines:]
    return []
