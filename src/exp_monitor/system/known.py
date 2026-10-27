"""Plain-English names for the cryptic labels Linux sensor chips use.

Used by ``exp-monitor system discover`` to explain every sensor on a machine and to
write a starter config that keeps only the ones worth watching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

KEY, USEFUL, MINOR, IGNORE = "key", "useful", "minor", "ignore"


@dataclass(frozen=True)
class Known:
    name: str
    kind: str
    relevance: str
    about: str
    warn: float | str | None = None
    crit: float | str | None = None


# (chip regex, label regex, info). Names may use {1}, {2}... for label regex groups.
_SUPERIO = r"nct6\d+|it8\d+|w83\d+"
RULES: list[tuple[str, str, Known]] = [
    # AMD CPUs
    ("k10temp", r"Tctl", Known(
        "CPU Temperature", "temperature", KEY,
        "The CPU's control temperature. Boost clocks and fan curves react to this one. "
        "Most Ryzen CPUs are rated for 95 °C (Tjmax), but check your model's spec sheet.", 85, 95)),
    ("k10temp", r"Tdie", Known("CPU Die Temperature", "temperature", USEFUL,
                               "Actual die temperature, without the offset some older Ryzen chips add to Tctl.", 85, 95)),
    ("k10temp", r"Tccd(\d+)", Known(
        "CPU Core Die {1}", "temperature", USEFUL,
        "One of the CPU's core chiplets (CCDs). Each holds a group of cores; a big gap between dies "
        "can point to uneven cooler contact.", 85, 95)),
    # Intel CPUs
    ("coretemp", r"Package id (\d+)", Known("CPU Package Temperature", "temperature", KEY,
                                            "Hottest point of the whole CPU package.", "auto", "auto")),
    ("coretemp", r"Core (\d+)", Known("CPU Core {1}", "temperature", MINOR, "A single core.", "auto", "auto")),
    # Storage
    ("nvme", r"Composite", Known(
        "SSD Temperature", "temperature", KEY,
        "The NVMe drive's overall temperature. Drives slow themselves down (throttle) near the drive's "
        "own warning limit. Most consumer drives are rated to run at 0-70 °C.", 70, "auto")),
    ("nvme", r"Sensor (\d+)", Known("SSD Internal Sensor {1}", "temperature", IGNORE,
                                    "One of the drive's internal sensors. 'Composite' already summarizes them.")),
    ("drivetemp", r"temp1", Known("Drive Temperature", "temperature", USEFUL,
                                  "A SATA drive's temperature.", 50, 60)),
    # RAM
    ("spd5118|jc42", r"temp1", Known(
        "RAM Stick Temperature", "temperature", USEFUL,
        "Temperature reported by a memory stick's own sensor. Overclocked (XMP/EXPO) memory gets less stable "
        "as it heats up; the limits come from the stick itself.", "auto", "auto")),
    # GPUs that use hwmon (AMD). NVIDIA GPUs come through the nvidia source instead.
    ("amdgpu", r"edge", Known("AMD GPU Temperature", "temperature", USEFUL,
                              "AMD graphics edge temperature. On CPUs with built-in graphics this is the iGPU.")),
    ("amdgpu", r"junction", Known("AMD GPU Hotspot", "temperature", USEFUL, "Hottest point on an AMD GPU die.")),
    ("amdgpu", r"mem", Known("AMD GPU Memory Temperature", "temperature", USEFUL, "AMD GPU VRAM temperature.")),
    ("amdgpu", r"sclk", Known("AMD GPU Clock", "clock", MINOR, "AMD graphics core clock.")),
    ("amdgpu", r"PPT", Known("AMD GPU Power", "power", MINOR, "AMD GPU package power.")),
    ("amdgpu", r"vdd\w+", Known("AMD GPU Voltage", "voltage", IGNORE, "Raw AMD GPU rail voltage.")),
    # ASUS embedded controller (supported boards only)
    ("asusec", r"VRM", Known("VRM Temperature", "temperature", KEY,
                                      "The motherboard's CPU power stage. It works hard under all-core loads.", 90, 110)),
    ("asusec", r"Chipset", Known("Chipset Temperature", "temperature", USEFUL,
                                          "The motherboard chipset, which runs the USB, SATA and extra PCIe lanes.", 75, 90)),
    ("asusec", r"T_Sensor", Known("T_SENSOR Probe", "temperature", USEFUL,
                                           "Optional thermistor probe plugged into the T_SENSOR header.")),
    ("asusec", r"Water_(In|Out)", Known("Coolant {1}", "temperature", USEFUL, "Liquid loop coolant temperature.")),
    # Motherboard Super I/O chips (Nuvoton / ITE / Winbond)
    (_SUPERIO, r"SYSTIN", Known("Motherboard Temperature", "temperature", USEFUL,
                                "A thermistor on the motherboard. It roughly tracks the air inside the case.", 45, 60)),
    (_SUPERIO, r"CPUTIN", Known(
        "CPU Socket Area", "temperature", USEFUL,
        "A thermistor on the motherboard near the CPU socket and its power delivery. Not the CPU itself.", 70, 85)),
    (_SUPERIO, r"AUXTIN(\d+)", Known(
        "Spare Sensor Input {1}", "temperature", IGNORE,
        "An unlabeled extra sensor input on the motherboard. Usually nothing is connected, so it reads "
        "nonsense, unless a probe is plugged into a T_SENSOR header.")),
    (_SUPERIO, r"PCH_.*|PECI.*|TSI\d+_TEMP|SMBUSMASTER.*", Known(
        "Internal / Duplicate Reading", "temperature", IGNORE,
        "Either not wired on this board, or a copy of the CPU temperature read through the motherboard.")),
    (_SUPERIO, r"fan(\d+)", Known("Fan Header {1}", "rpm", USEFUL,
                                  "A motherboard fan header. Daisy-chained fans only report the first fan's speed.")),
    (_SUPERIO, r"in(\d+)", Known(
        "Raw Voltage Input {1}", "voltage", IGNORE,
        "A raw voltage input. Without the board's scaling it can't be read as a real voltage.")),
    (_SUPERIO, r"intrusion\d+", Known("Case Open Switch", "other", IGNORE, "The chassis-intrusion header.")),
    # Wireless
    (r"mt79\w+|iwlwifi\w*|ath\d+k\w*", r"temp1", Known("Wi-Fi Card Temperature", "temperature", MINOR,
                                                       "The wireless chip. 60-80 °C is normal for these.")),
    ("acpitz", r"temp\d+", Known("ACPI Thermal Zone", "temperature", IGNORE,
                                 "A firmware-defined zone. Often fixed or stale on desktops.")),
]


def identify(chip: str, label: str, value: float | None = None) -> Known | None:
    """Look up a sensor by chip name (without the [n] suffix) and label."""
    for chip_pattern, label_pattern, known in RULES:
        if not re.fullmatch(chip_pattern, chip):
            continue
        match = re.fullmatch(label_pattern, label)
        if not match:
            continue
        name = known.name.format(*("", *match.groups()))
        if known.kind == "rpm" and not value:
            return Known(name, "rpm", IGNORE, "Fan header reading 0 RPM: nothing is connected, or the fan is stopped.")
        return Known(name, known.kind, known.relevance, known.about, known.warn, known.crit)
    return None
