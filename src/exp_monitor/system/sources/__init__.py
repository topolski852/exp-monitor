"""Sensor sources. A config sensor key is ``<source>:<path>``, e.g. ``hwmon:k10temp/Tctl``."""

import sys

from .base import Health, Reading, Source
from .hwmon import HwmonSource
from .nvidia import NvidiaSource

if sys.platform == "win32":
    from .windows import WindowsSystemSource as SystemSource
else:
    from .system import SystemSource

SOURCES: dict[str, type[Source]] = {
    HwmonSource.name: HwmonSource,
    NvidiaSource.name: NvidiaSource,
    SystemSource.name: SystemSource,
}

__all__ = ["SOURCES", "Health", "Reading", "Source", "HwmonSource", "NvidiaSource", "SystemSource"]
