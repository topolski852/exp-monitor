"""Operating-system counters on Windows: the same paths as system.py, read through PDH (performance counters).

Paths:
    cpu/utilization            % across all threads (per-thread values in ``series``)
    cpu/frequency              average clock of all threads, MHz
    cpu/frequency_peak         fastest thread, MHz
    memory/used                GiB, memory/percent
    swap/used                  GiB of the page file, swap/percent
    swap/activity              MB/s of paging right now, also swap/in and swap/out. Includes memory-mapped files,
                               so programs starting up show here too
    mount[C:/]/percent         disk space for each configured mount, also mount[C:/]/used in GiB
    block[0 C:]/read           MB/s for each configured physical disk, also .../write
    thermal[cpuz]/temperature  °C from each ACPI thermal zone the firmware exposes

Part temperatures beyond the thermal zones need a kernel driver and admin rights, so they aren't here.
"""

from __future__ import annotations

import ctypes
import platform
import shutil
import socket
import winreg
from ctypes import wintypes

from .base import Reading, Source

GIB = 2**30
PAGE = 4096
KELVIN = 273.15
PDH_MORE_DATA = 0x800007D2
PDH_FMT_DOUBLE_NOCAP100 = 0x00000200 | 0x00008000  # % Processor Performance goes over 100 under turbo
PDH_VALID_STATUS = (0, 1)  # PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA

COUNTERS = {
    "thread_time": r"\Processor(*)\% Processor Time",
    "performance": r"\Processor Information(*)\% Processor Performance",
    "base_clock": r"\Processor Information(*)\Processor Frequency",
    "page_file": r"\Paging File(_Total)\% Usage",
    "pages_in": r"\Memory\Pages Input/sec",
    "pages_out": r"\Memory\Pages Output/sec",
    "disk_read": r"\PhysicalDisk(*)\Disk Read Bytes/sec",
    "disk_write": r"\PhysicalDisk(*)\Disk Write Bytes/sec",
    "thermal": r"\Thermal Zone Information(*)\Temperature",
}

_pdh = ctypes.WinDLL("pdh")
_pdh.PdhOpenQueryW.argtypes = (wintypes.LPCWSTR, ctypes.c_size_t, ctypes.POINTER(ctypes.c_void_p))
_pdh.PdhOpenQueryW.restype = wintypes.DWORD
_pdh.PdhAddEnglishCounterW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_size_t,
                                       ctypes.POINTER(ctypes.c_void_p))
_pdh.PdhAddEnglishCounterW.restype = wintypes.DWORD
_pdh.PdhCollectQueryData.argtypes = (ctypes.c_void_p,)
_pdh.PdhCollectQueryData.restype = wintypes.DWORD
_pdh.PdhGetFormattedCounterArrayW.argtypes = (ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                              ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
_pdh.PdhGetFormattedCounterArrayW.restype = wintypes.DWORD
_kernel32 = ctypes.WinDLL("kernel32")
_kernel32.GetTickCount64.restype = ctypes.c_ulonglong


class _CounterValue(ctypes.Structure):
    _fields_ = [("status", wintypes.DWORD), ("value", ctypes.c_double)]


class _CounterItem(ctypes.Structure):
    _fields_ = [("name", wintypes.LPWSTR), ("counter", _CounterValue)]


class _MemoryStatus(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("load", wintypes.DWORD), ("total_phys", ctypes.c_ulonglong),
                ("avail_phys", ctypes.c_ulonglong), ("total_page", ctypes.c_ulonglong),
                ("avail_page", ctypes.c_ulonglong), ("total_virtual", ctypes.c_ulonglong),
                ("avail_virtual", ctypes.c_ulonglong), ("avail_extended", ctypes.c_ulonglong)]


class WindowsSystemSource(Source):
    name = "system"

    def __init__(self, mounts: list[str] = ("C:/",), block_devices: list[str] = ()):
        self.mounts = list(mounts)
        self.block_devices = list(block_devices)
        self._query = ctypes.c_void_p()
        status = _pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._query))
        if status:
            raise OSError(f"cannot open the Windows performance counters (PDH error {status:#x})")
        self._counters = {}
        for name, path in COUNTERS.items():
            counter = ctypes.c_void_p()
            if _pdh.PdhAddEnglishCounterW(self._query, path, 0, ctypes.byref(counter)) == 0:  # not every PC has all
                self._counters[name] = counter
        _pdh.PdhCollectQueryData(self._query)  # rates are measured between two collections

    def info(self) -> dict[str, str]:
        return {
            "hostname": socket.gethostname(),
            "kernel": platform.version(),
            "cpu": _cpu_model(),
            "uptime": _uptime(),
        }

    def sample(self) -> dict[str, Reading]:
        _pdh.PdhCollectQueryData(self._query)
        values = {name: _read_counter(counter) for name, counter in self._counters.items()}
        out = {}
        out.update(_cpu(values))
        out.update(_memory(values))
        for mount in self.mounts:
            try:
                usage = shutil.disk_usage(mount)
            except OSError:
                continue
            out[f"mount[{mount}]/percent"] = Reading(100 * usage.used / usage.total, "%")
            out[f"mount[{mount}]/used"] = Reading(usage.used / GIB, "GiB", {"max": usage.total / GIB})
        for dev in self.block_devices:
            for direction in ("read", "write"):
                rate = values.get(f"disk_{direction}", {}).get(dev)
                if rate is not None:
                    out[f"block[{dev}]/{direction}"] = Reading(rate / 1e6, "MB/s")
        for zone, kelvin in values.get("thermal", {}).items():
            out[f"thermal[{zone.rsplit('.', 1)[-1].lower()}]/temperature"] = Reading(kelvin - KELVIN, "°C")
        return out


def _read_counter(counter) -> dict[str, float]:
    """{instance name: value} for one counter; empty if it has nothing valid yet."""
    size, count = wintypes.DWORD(0), wintypes.DWORD(0)
    status = _pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_DOUBLE_NOCAP100, ctypes.byref(size),
                                               ctypes.byref(count), None)
    if status != PDH_MORE_DATA:
        return {}
    buffer = ctypes.create_string_buffer(size.value)
    status = _pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_DOUBLE_NOCAP100, ctypes.byref(size),
                                               ctypes.byref(count), buffer)
    if status:
        return {}
    items = ctypes.cast(buffer, ctypes.POINTER(_CounterItem))
    return {items[i].name: items[i].counter.value for i in range(count.value)
            if items[i].counter.status in PDH_VALID_STATUS}


def _cpu(values: dict[str, dict[str, float]]) -> dict[str, Reading]:
    times = values.get("thread_time", {})
    threads = [min(times[n], 100.0) for n in sorted((n for n in times if n != "_Total"), key=int)]
    total = times.get("_Total")
    # Each thread's clock is its rated clock scaled by how far above or below it the thread is running.
    performance, base = values.get("performance", {}), values.get("base_clock", {})
    clocks = [performance[n] / 100 * base[n] for n in performance if n in base and "_Total" not in n]
    return {
        "cpu/utilization": Reading(None if total is None else min(total, 100.0), "%", series=threads),
        "cpu/frequency": Reading(sum(clocks) / len(clocks) if clocks else None, "MHz"),
        "cpu/frequency_peak": Reading(max(clocks) if clocks else None, "MHz"),
    }


def _memory(values: dict[str, dict[str, float]]) -> dict[str, Reading]:
    status = _MemoryStatus(length=ctypes.sizeof(_MemoryStatus))
    if not _kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return {}
    used = status.total_phys - status.avail_phys
    page_file = max(status.total_page - status.total_phys, 0)  # total_page counts RAM as well
    swap_percent = values.get("page_file", {}).get("_Total", 0.0)
    out = {
        "memory/used": Reading(used / GIB, "GiB", {"max": status.total_phys / GIB}),
        "memory/percent": Reading(100 * used / status.total_phys, "%"),
        "swap/used": Reading(page_file * swap_percent / 100 / GIB, "GiB", {"max": page_file / GIB}),
        "swap/percent": Reading(swap_percent, "%"),
    }
    # Single-instance counters are reported under an empty instance name, so take whatever value is there.
    pages_in = next(iter(values.get("pages_in", {}).values()), None)
    pages_out = next(iter(values.get("pages_out", {}).values()), None)
    if pages_in is not None and pages_out is not None:
        rates = [pages * PAGE / 1e6 for pages in (pages_in, pages_out)]
        out.update({"swap/in": Reading(rates[0], "MB/s"), "swap/out": Reading(rates[1], "MB/s"),
                    "swap/activity": Reading(sum(rates), "MB/s")})
    return out


def _cpu_model() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            return winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
    except OSError:
        return platform.processor()


def _uptime() -> str:
    seconds = _kernel32.GetTickCount64() // 1000
    days, rest = divmod(seconds, 86400)
    return f"{days}d {rest // 3600}h {rest % 3600 // 60}m"
