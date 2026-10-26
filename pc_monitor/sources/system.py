"""Operating-system counters from /proc and /sys: CPU load and clocks, RAM, disks.

Paths:
    cpu/utilization       % across all threads (per-thread values in ``series``)
    cpu/frequency         average clock of all threads, MHz
    cpu/frequency_peak    fastest thread, MHz
    memory/used           GiB, memory/percent
    swap/used             GiB, swap/percent
    load/1m               load average (max = thread count)
    mount[/]/percent      disk space for each configured mount, also mount[/]/used in GiB
    block[nvme0n1]/read   MB/s for each configured block device, also .../write
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import time
from pathlib import Path

from .base import Reading, Source

GIB = 2**30
CPU_ROOT = Path("/sys/devices/system/cpu")


class SystemSource(Source):
    name = "system"

    def __init__(self, mounts: list[str] = ("/",), block_devices: list[str] = ()):
        self.mounts = list(mounts)
        self.block_devices = list(block_devices)
        self._cpu_dirs = sorted(CPU_ROOT.glob("cpu[0-9]*/cpufreq"), key=lambda p: int(p.parent.name[3:]))
        self._max_mhz = _read_int(self._cpu_dirs[0] / "cpuinfo_max_freq") / 1000 if self._cpu_dirs else None
        self._prev_cpu = _cpu_times()
        self._prev_io = (time.monotonic(), _disk_sectors())

    def info(self) -> dict[str, str]:
        return {
            "hostname": socket.gethostname(),
            "kernel": platform.release(),
            "cpu": _cpu_model(),
            "uptime": _uptime(),
        }

    def sample(self) -> dict[str, Reading]:
        out = {}
        out.update(self._cpu())
        out.update(_memory())
        out["load/1m"] = Reading(os.getloadavg()[0], "", {"max": os.cpu_count()})
        for mount in self.mounts:
            try:
                usage = shutil.disk_usage(mount)
            except OSError:
                continue
            out[f"mount[{mount}]/percent"] = Reading(100 * usage.used / usage.total, "%")
            out[f"mount[{mount}]/used"] = Reading(usage.used / GIB, "GiB", {"max": usage.total / GIB})
        out.update(self._disk_io())
        return out

    def _cpu(self) -> dict[str, Reading]:
        now = _cpu_times()
        busy = {}
        for name, (idle, total) in now.items():
            prev_idle, prev_total = self._prev_cpu.get(name, (idle, total))
            elapsed = total - prev_total
            busy[name] = 100 * (1 - (idle - prev_idle) / elapsed) if elapsed > 0 else 0.0
        self._prev_cpu = now
        threads = [busy[n] for n in sorted((n for n in busy if n != "cpu"), key=lambda n: int(n[3:]))]

        clocks = [mhz for d in self._cpu_dirs if (mhz := _read_int(d / "scaling_cur_freq")) is not None]
        clocks = [c / 1000 for c in clocks]
        limits = {"max": self._max_mhz} if self._max_mhz else {}
        return {
            "cpu/utilization": Reading(busy.get("cpu"), "%", series=threads),
            "cpu/frequency": Reading(sum(clocks) / len(clocks) if clocks else None, "MHz", limits),
            "cpu/frequency_peak": Reading(max(clocks) if clocks else None, "MHz", limits),
        }

    def _disk_io(self) -> dict[str, Reading]:
        now, sectors = time.monotonic(), _disk_sectors()
        then, prev = self._prev_io
        self._prev_io = (now, sectors)
        out = {}
        for dev in self.block_devices:
            if dev not in sectors or dev not in prev or now <= then:
                continue
            for i, direction in enumerate(("read", "write")):
                rate = (sectors[dev][i] - prev[dev][i]) * 512 / (now - then) / 1e6
                out[f"block[{dev}]/{direction}"] = Reading(rate, "MB/s")
        return out


def _cpu_times() -> dict[str, tuple[int, int]]:
    """(idle, total) jiffies for the whole CPU ("cpu") and each thread ("cpu0"...)."""
    times = {}
    for line in Path("/proc/stat").read_text().splitlines():
        if not line.startswith("cpu"):
            break
        name, *fields = line.split()
        values = [int(v) for v in fields[:8]]  # guest time is already counted in user
        times[name] = (values[3] + values[4], sum(values))
    return times


def _memory() -> dict[str, Reading]:
    info = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        info[key] = int(value.split()[0]) * 1024
    total, used = info["MemTotal"], info["MemTotal"] - info["MemAvailable"]
    swap_total, swap_used = info["SwapTotal"], info["SwapTotal"] - info["SwapFree"]
    return {
        "memory/used": Reading(used / GIB, "GiB", {"max": total / GIB}),
        "memory/percent": Reading(100 * used / total, "%"),
        "swap/used": Reading(swap_used / GIB, "GiB", {"max": swap_total / GIB}),
        "swap/percent": Reading(100 * swap_used / swap_total if swap_total else 0.0, "%"),
    }


def _disk_sectors() -> dict[str, tuple[int, int]]:
    sectors = {}
    for line in Path("/proc/diskstats").read_text().splitlines():
        fields = line.split()
        sectors[fields[2]] = (int(fields[5]), int(fields[9]))
    return sectors


def _cpu_model() -> str:
    for line in Path("/proc/cpuinfo").read_text().splitlines():
        if line.startswith("model name"):
            return line.split(":", 1)[1].strip()
    return platform.processor()


def _uptime() -> str:
    seconds = int(float(Path("/proc/uptime").read_text().split()[0]))
    days, rest = divmod(seconds, 86400)
    return f"{days}d {rest // 3600}h {rest % 3600 // 60}m"


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text())
    except (OSError, ValueError):
        return None
