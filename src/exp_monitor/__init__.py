"""exp-monitor: a local live dashboard for experiment run files."""

import os
from pathlib import Path

# Same override the writers use (stampede-actuator/src/analysis/progress.py).
RUNS_DIR = Path(os.environ.get("EXP_MONITOR_DIR", Path.home() / ".local/state/exp-monitor/runs"))
HOST = "127.0.0.1"
PORT = int(os.environ.get("EXP_MONITOR_PORT", 8787))
