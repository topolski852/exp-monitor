"""Shared types for sensor sources."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Reading:
    """One sensor value, already converted to a human unit."""

    value: float | None
    unit: str = ""
    # Limits the hardware reports about itself. Keys are normalized across
    # sources so a config can say "auto": "warn", "crit" and "max".
    limits: dict[str, float] = field(default_factory=dict)
    # Per-item values for multi-value sensors (e.g. utilization of each CPU thread).
    series: list[float] | None = None


@dataclass
class Health:
    ok: bool
    message: str = ""
    title: str = ""  # short headline for the alert banner


class Source:
    """A family of sensors (hwmon chips, NVIDIA GPUs, /proc counters...).

    Each sensor is addressed by a path that is unique within its source, e.g.
    ``k10temp/Tctl`` for hwmon or ``0/temperature`` for the first NVIDIA GPU.
    """

    name = ""

    def sample(self) -> dict[str, Reading]:
        raise NotImplementedError

    def canonical(self, path: str) -> str:
        """Turn a path written in a config into the form ``sample()`` uses."""
        return path

    def health(self) -> Health:
        return Health(True)

    def info(self) -> dict[str, str]:
        """Static facts for the dashboard header (model names, driver versions)."""
        return {}
