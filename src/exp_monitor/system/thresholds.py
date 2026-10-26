"""Turn a reading plus its limits into a gauge range and a status color.

Zones follow the manufacturer's numbers: below ``warn`` is the comfortable range
(green), ``warn`` up to ``crit`` is getting close (amber), ``crit`` and above is
over the limit (red).
"""

from __future__ import annotations

from .config import Limit, Sensor
from .sources import Reading

OK, WARN, CRIT, MISSING = "ok", "warn", "crit", "missing"

# Gauge maximum when the config doesn't give one.
DEFAULT_MAX = {"percent": 100, "temperature": 100}


def resolve(limit: Limit, auto: float | None, scale_max: float | None = None) -> float | None:
    if limit is None:
        return None
    if limit == "auto":
        return auto
    if isinstance(limit, str):  # "NN%" of the gauge maximum
        return scale_max * float(limit[:-1]) / 100 if scale_max else None
    return float(limit)


def evaluate(sensor: Sensor, reading: Reading | None) -> dict:
    limits = reading.limits if reading else {}
    scale_max = resolve(sensor.max, limits.get("max"))
    warn = resolve(sensor.warn, limits.get("warn"), scale_max)
    crit = resolve(sensor.crit, limits.get("crit"), scale_max)
    if scale_max is None:
        scale_max = DEFAULT_MAX.get(sensor.kind) or crit or warn
    if sensor.kind == "temperature" and crit and scale_max < crit:
        scale_max = crit + 10

    value = reading.value if reading else None
    return {
        "value": value,
        "unit": sensor.unit or (reading.unit if reading else ""),
        "status": status(value, warn, crit, sensor.direction),
        "min": sensor.min,
        "max": scale_max,
        "warn": warn,
        "crit": crit,
    }


def status(value: float | None, warn: float | None, crit: float | None, direction: str = "high") -> str:
    if value is None:
        return MISSING

    def past(limit: float) -> bool:
        return value >= limit if direction == "high" else value <= limit

    if crit is not None and past(crit):
        return CRIT
    if warn is not None and past(warn):
        return WARN
    return OK
