"""The TOML config: what to monitor on this PC, what to call it, and how to show it.

See configs/ for complete examples. The short version:

    [app]                 machine name, refresh rate
    [sources.<name>]      options for a source, e.g. which disks to watch
    [[sensor]]            one entry per reading, with a readable name and its limits
    [layout]              which sensors become big gauges and which go in panels
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

KINDS = {"temperature", "percent", "power", "clock", "memory", "rpm", "throughput", "voltage", "other"}
WIDGETS = {"rows", "threads"}
SOURCE_NAMES = {"hwmon", "nvidia", "system"}

# A limit is a number, "auto" (use what the hardware reports about itself),
# "NN%" (a share of the gauge maximum), or unset.
Limit = float | str | None
_PERCENT = re.compile(r"^\d+(\.\d+)?%$")


class ConfigError(ValueError):
    pass


@dataclass
class Sensor:
    id: str
    name: str
    key: str
    kind: str = "other"
    about: str = ""   # plain-English explanation shown in "explain" mode
    spec: str = ""    # where the limits come from, e.g. "AMD Tjmax 95 °C"
    unit: str | None = None
    decimals: int = 0
    min: float = 0
    max: Limit = None
    warn: Limit = None
    crit: Limit = None
    direction: str = "high"  # "low" raises the alarm when the value drops, e.g. a stopped fan

    @property
    def source(self) -> str:
        return self.key.partition(":")[0]

    @property
    def path(self) -> str:
        return self.key.partition(":")[2]


@dataclass
class Panel:
    title: str
    sensors: list[str]
    widget: str = "rows"


@dataclass
class Layout:
    gauges: list[str] = field(default_factory=list)
    panels: list[Panel] = field(default_factory=list)


@dataclass
class AppSettings:
    # title, host, port and open_browser are from standalone PC Monitor; still accepted, but exp-monitor's
    # own options decide the server address and window.
    title: str = "PC MONITOR"
    machine: str = ""
    refresh_ms: int = 1000
    history_seconds: int = 300
    host: str = "127.0.0.1"
    port: int = 8765
    open_browser: bool = True


@dataclass
class Config:
    app: AppSettings
    sensors: list[Sensor]
    layout: Layout
    sources: dict[str, dict] = field(default_factory=dict)
    path: Path | None = None

    def sensor(self, sensor_id: str) -> Sensor:
        return next(s for s in self.sensors if s.id == sensor_id)


# The bundled config for this rig (repo-root configs/), used when there is no personal one.
BUNDLED = Path(__file__).resolve().parents[3] / "configs" / "isaac-sim-rig.toml"
USER_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "exp-monitor" / "system.toml"


def default_path() -> Path | None:
    """The first config that exists: $EXP_MONITOR_SYSTEM_CONFIG, ~/.config/exp-monitor/system.toml, the bundled one."""
    for candidate in (os.environ.get("EXP_MONITOR_SYSTEM_CONFIG"), USER_PATH, BUNDLED):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def load(path: str | Path) -> Config:
    path = Path(path)
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None
    config = parse(data)
    config.path = path
    return config


def parse(data: dict) -> Config:
    unknown = set(data) - {"app", "sources", "sensor", "layout"}
    if unknown:
        raise ConfigError(f"unknown section(s): {', '.join(sorted(unknown))}")
    layout = data.get("layout", {})
    config = Config(
        app=_build(AppSettings, data.get("app", {}), "[app]"),
        sensors=[_build(Sensor, s, f"[[sensor]] {s.get('id', '#' + str(i + 1))}")
                 for i, s in enumerate(data.get("sensor", []))],
        layout=Layout(
            gauges=layout.get("gauges", []),
            panels=[_build(Panel, p, f"[[layout.panels]] {p.get('title', '')}") for p in layout.get("panels", [])],
        ),
        sources=data.get("sources", {}),
    )
    _validate(config)
    return config


def _build(cls, table: dict, where: str):
    allowed = {f.name for f in fields(cls)}
    unknown = set(table) - allowed
    if unknown:
        raise ConfigError(f"{where}: unknown setting(s) {', '.join(sorted(unknown))}")
    try:
        return cls(**table)
    except TypeError as e:
        raise ConfigError(f"{where}: {e}") from None


def _validate(config: Config) -> None:
    ids = set()
    for s in config.sensors:
        where = f"[[sensor]] {s.id}"
        if s.id in ids:
            raise ConfigError(f"{where}: duplicate id")
        ids.add(s.id)
        if s.source not in SOURCE_NAMES or not s.path:
            raise ConfigError(f"{where}: key {s.key!r} must look like '<source>:<path>' "
                              f"with source one of {', '.join(sorted(SOURCE_NAMES))}")
        if s.kind not in KINDS:
            raise ConfigError(f"{where}: kind {s.kind!r} is not one of {', '.join(sorted(KINDS))}")
        if s.direction not in ("high", "low"):
            raise ConfigError(f"{where}: direction must be 'high' or 'low'")
        for name in ("max", "warn", "crit"):
            value = getattr(s, name)
            if not (value is None or isinstance(value, (int, float)) or value == "auto"
                    or (isinstance(value, str) and _PERCENT.match(value) and name != "max")):
                raise ConfigError(f"{where}: {name} = {value!r} must be a number, \"auto\" or \"NN%\"")

    for name in config.sources:
        if name not in SOURCE_NAMES:
            raise ConfigError(f"[sources.{name}]: unknown source")
    for gauge in config.layout.gauges:
        if gauge not in ids:
            raise ConfigError(f"[layout] gauges: no sensor with id {gauge!r}")
    for panel in config.layout.panels:
        if panel.widget not in WIDGETS:
            raise ConfigError(f"[[layout.panels]] {panel.title}: widget must be one of {', '.join(sorted(WIDGETS))}")
        for sensor_id in panel.sensors:
            if sensor_id not in ids:
                raise ConfigError(f"[[layout.panels]] {panel.title}: no sensor with id {sensor_id!r}")
