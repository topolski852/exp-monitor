"""Linux hwmon sensors (/sys/class/hwmon): CPU, RAM, SSD, motherboard and fan chips.

Paths look like ``<chip>[<n>]/<label>``, for example ``k10temp/Tctl`` or
``spd5118[1]/temp1``. ``[n]`` picks between chips with the same name (one per RAM
stick, one per SSD) and defaults to ``[0]``. Chips are numbered by their device
path, not by the hwmonN number, which can change between boots.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .base import Reading, Source

HWMON_ROOT = Path("/sys/class/hwmon")

# hwmon file prefix -> (unit, divisor that turns the raw integer into that unit)
UNITS = {
    "temp": ("°C", 1000),
    "fan": ("RPM", 1),
    "in": ("V", 1000),
    "power": ("W", 1_000_000),
    "curr": ("A", 1000),
    "freq": ("MHz", 1_000_000),
}
_INPUT = re.compile(r"^(temp|fan|in|power|curr|freq)(\d+)_input$")

# Unused limit registers hold junk such as 65261.8 °C or -273.1 °C.
_SANE_TEMP = (-50.0, 200.0)


@dataclass
class Channel:
    chip: str
    prefix: str
    label: str | None
    input: Path
    unit: str
    divisor: int
    limits: dict[str, float] = field(default_factory=dict)

    @property
    def keys(self) -> list[str]:
        keys = [f"{self.chip}/{self.prefix}"]
        if self.label and self.label != self.prefix:
            keys.insert(0, f"{self.chip}/{self.label}")
        return keys


class HwmonSource(Source):
    name = "hwmon"

    def __init__(self, root: str | Path = HWMON_ROOT):
        self.root = Path(root)
        self.channels = self._scan()

    def canonical(self, path: str) -> str:
        chip, sep, rest = path.partition("/")
        if "[" not in chip:
            chip += "[0]"
        return chip + sep + rest

    def sample(self) -> dict[str, Reading]:
        out = {}
        for ch in self.channels:
            raw = _read_int(ch.input)
            reading = Reading(None if raw is None else raw / ch.divisor, ch.unit, ch.limits)
            for key in ch.keys:
                out[key] = reading
        return out

    def _scan(self) -> list[Channel]:
        chips = []
        for d in self.root.glob("hwmon*"):
            name = _read_text(d / "name")
            if name:
                device = d / "device"
                chips.append((name, os.path.realpath(device if device.exists() else d), d))
        chips.sort()

        channels = []
        counts: dict[str, int] = {}
        for name, _, d in chips:
            index = counts.get(name, 0)
            counts[name] = index + 1
            chip = f"{name}[{index}]"
            inputs = [m for f in d.iterdir() if (m := _INPUT.match(f.name))]
            for m in sorted(inputs, key=lambda m: (m.group(1), int(m.group(2)))):
                kind, prefix = m.group(1), m.group(1) + m.group(2)
                unit, divisor = UNITS[kind]
                channels.append(Channel(
                    chip=chip,
                    prefix=prefix,
                    label=_read_text(d / f"{prefix}_label"),
                    input=d / m.group(0),
                    unit=unit,
                    divisor=divisor,
                    limits=_temp_limits(d, prefix) if kind == "temp" else {},
                ))
        return channels


def _temp_limits(d: Path, prefix: str) -> dict[str, float]:
    limits = {}
    for suffix, meaning in (("max", "warn"), ("crit", "crit")):
        raw = _read_int(d / f"{prefix}_{suffix}")
        if raw is not None and _SANE_TEMP[0] < raw / 1000 < _SANE_TEMP[1]:
            limits[meaning] = raw / 1000
    return limits


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text().strip()
    except OSError:
        return None


def _read_int(path: Path) -> int | None:
    text = _read_text(path)
    try:
        return int(text) if text is not None else None
    except ValueError:
        return None
