"""Find every sensor on this machine, explain it, and write a starter config."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import known
from .sources import HwmonSource, NvidiaSource, SystemSource

PCI_ROOT = Path("/sys/bus/pci/devices")

# Panel for each hwmon chip family, in display order.
PANELS = [
    ("GPU", r"amdgpu"),
    ("CPU", r"k10temp|coretemp"),
    ("Memory", r"spd5118|jc42"),
    ("Storage", r"nvme|drivetemp"),
    ("Motherboard", known._SUPERIO + r"|asusec"),
    ("Other", r".*"),
]
NVIDIA_FIELDS = [
    ("temperature", "GPU Temperature", "temperature", "auto", "auto",
     "The GPU core temperature. The limits come from the driver: amber at the GPU's rated maximum "
     "operating temperature, red where it starts slowing itself down."),
    ("utilization", "GPU Load", "percent", None, None, "How busy the GPU's compute cores are."),
    ("memory_used", "VRAM Used", "memory", "90%", "97%",
     "Video memory in use. Training and simulation fail with out-of-memory errors when this fills up."),
    ("power", "GPU Power", "power", None, None, "Power the graphics card is drawing. The gauge maximum is its power limit."),
    ("clock_graphics", "GPU Clock", "clock", None, None, "Current core clock."),
    ("fan", "GPU Fan", "percent", None, None,
     "GPU fan speed. Many cards stop their fans completely at low temperatures, so 0% can be normal."),
]
SYSTEM_SENSORS = [
    ("cpu_load", "CPU Load", "system:cpu/utilization", "percent", None, None, "CPU", 0,
     "How busy the CPU is across all threads."),
    ("cpu_clock", "CPU Clock (avg)", "system:cpu/frequency", "clock", None, None, "CPU", 0,
     "Average clock speed across all threads."),
    ("ram_used", "RAM Used", "system:memory/used", "memory", "85%", "95%", "Memory", 1,
     "System memory in use. When it fills up, the system starts swapping and everything slows down."),
    ("swap_used", "Swap Used", "system:swap/percent", "percent", 10, 50, "Memory", 0,
     "Memory pushed out to disk. Any real swap use during training means RAM is too small for the job."),
    ("disk_root", "System Disk Space", "system:mount[/]/percent", "percent", 85, 95, "Storage", 0,
     "How full the drive holding the operating system is."),
]


@dataclass
class Found:
    key: str
    value: float | None
    unit: str
    info: known.Known | None
    panel: str


def scan() -> tuple[list[Found], str | None, bool]:
    """Returns (hwmon sensors, NVIDIA problem if any, whether an NVIDIA GPU is installed)."""
    found = []
    hwmon = HwmonSource()
    readings = hwmon.sample()
    for ch in hwmon.channels:
        chip_name = ch.chip.split("[")[0]
        label = ch.label or ch.prefix
        chip = ch.chip.removesuffix("[0]")
        reading = readings[ch.keys[0]]
        panel = next(p for p, pattern in PANELS if re.fullmatch(pattern, chip_name))
        info = known.identify(chip_name, label, reading.value)
        found.append(Found(f"hwmon:{chip}/{label}", reading.value, ch.unit, info, panel))
    nvidia = NvidiaSource()
    return found, nvidia.health().message or None, _has_nvidia_gpu()


def _has_nvidia_gpu() -> bool:
    for device in PCI_ROOT.glob("*"):
        try:
            if (device / "vendor").read_text().strip() == "0x10de" and \
                    (device / "class").read_text().startswith("0x03"):
                return True
        except OSError:
            continue
    return False


def report(found: list[Found], nvidia_problem: str | None, show_all: bool = False) -> str:
    order = [known.KEY, known.USEFUL, known.MINOR, known.IGNORE, None]
    lines = []
    for f in sorted(found, key=lambda f: order.index(f.info.relevance if f.info else None)):
        relevance = f.info.relevance if f.info else "unknown"
        if relevance == known.IGNORE and not show_all:
            continue
        value = "--" if f.value is None else f"{f.value:.1f} {f.unit}"
        name = f.info.name if f.info else "?"
        lines.append(f"{relevance:8} {f.key:42} {value:>12}  {name}")
    hidden = sum(1 for f in found if f.info and f.info.relevance == known.IGNORE)
    if hidden and not show_all:
        lines.append(f"\n{hidden} sensors hidden as noise (duplicates, unconnected inputs). Use --all to list them.")
    lines.append("\nNVIDIA: " + (nvidia_problem or "OK"))
    return "\n".join(lines)


def starter_config(found: list[Found], nvidia_gpu: bool, machine: str = "") -> str:
    """A config that monitors the sensors worth watching, grouped into panels."""
    sensors: list[dict] = []
    if nvidia_gpu:
        for field, name, kind, warn, crit, about in NVIDIA_FIELDS:
            sensors.append(dict(id=_slug("gpu_" + field), name=name, key=f"nvidia:0/{field}", kind=kind,
                                warn=warn, crit=crit, about=about, panel="GPU",
                                max="auto" if kind in ("memory", "power", "clock") else None,
                                decimals=1 if kind == "memory" else 0))
    for sid, name, key, kind, warn, crit, panel, decimals, about in SYSTEM_SENSORS:
        sensors.append(dict(id=sid, name=name, key=key, kind=kind, warn=warn, crit=crit, about=about,
                            panel=panel, max="auto" if kind == "memory" else None, decimals=decimals))
    used_ids = {s["id"] for s in sensors}
    for f in found:
        if not f.info or f.info.relevance not in (known.KEY, known.USEFUL):
            continue
        sid = _unique(_slug(f.info.name), used_ids)
        extra = {"direction": "low", "crit": 200} if f.info.kind == "rpm" else {}
        sensors.append(dict(id=sid, name=f.info.name, key=f.key, kind=f.info.kind, warn=f.info.warn,
                            crit=f.info.crit, about=f.info.about, panel="Cooling" if f.info.kind == "rpm" else f.panel,
                            decimals=0) | extra)

    ids = {s["id"] for s in sensors}
    gauges = [g for g in ("gpu_temperature", "gpu_utilization", "gpu_memory_used",
                          "cpu_temperature", "cpu_load", "ram_used") if g in ids]
    if "cpu_temperature" not in ids:
        gauges += [s["id"] for s in sensors if s["panel"] == "CPU" and s["kind"] == "temperature"][:1]

    out = ["# exp-monitor system config, generated by `exp-monitor system discover --write`.",
           "# Check the warn/crit limits against your hardware's spec sheets.", "",
           "[app]", f"machine = {_toml(machine)}", "refresh_ms = 1000", "",
           "[sources.system]", 'mounts = ["/"]', ""]
    if nvidia_gpu:
        out += ["[sources.nvidia]", "required = true  # show a red alert when the driver isn't loaded", ""]
    out += ["[layout]", f"gauges = {_toml(gauges)}", ""]
    for panel in ("GPU", "CPU", "Memory", "Storage", "Cooling", "Motherboard", "Other"):
        members = [s["id"] for s in sensors if s["panel"] == panel]
        if members:
            out += ["[[layout.panels]]", f"title = {_toml(panel)}", f"sensors = {_toml(members)}", ""]
            if panel == "CPU":
                out += ["[[layout.panels]]", 'title = "CPU Threads"', 'widget = "threads"',
                        'sensors = ["cpu_load"]', ""]
    for s in sensors:
        out.append("[[sensor]]")
        for field in ("id", "name", "key", "kind", "max", "warn", "crit", "direction", "decimals", "about"):
            if s.get(field) not in (None, 0) or (field == "decimals" and s.get(field)):
                out.append(f"{field} = {_toml(s[field])}")
        out.append("")
    return "\n".join(out)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _unique(sid: str, used: set[str]) -> str:
    candidate, n = sid, 2
    while candidate in used:
        candidate, n = f"{sid}_{n}", n + 1
    used.add(candidate)
    return candidate


def _toml(value) -> str:
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    return str(value)
