import tomllib
from pathlib import Path

import pytest

from pc_monitor import config, known, thresholds
from pc_monitor.discover import starter_config, Found
from pc_monitor.monitor import Monitor
from pc_monitor.sources import Health, Reading, Source
from pc_monitor.sources.hwmon import HwmonSource

ROOT = Path(__file__).parent.parent


def sensor(**kw):
    defaults = dict(id="s", name="S", key="hwmon:chip/x", kind="temperature")
    return config.Sensor(**(defaults | kw))


# ------------------------------------------------------------------ thresholds

@pytest.mark.parametrize("value, expected", [(None, "missing"), (60, "ok"), (85, "warn"), (94.9, "warn"), (95, "crit")])
def test_status_high(value, expected):
    assert thresholds.status(value, 85, 95) == expected


@pytest.mark.parametrize("value, expected", [(0, "crit"), (200, "crit"), (450, "warn"), (1200, "ok")])
def test_status_low_for_fans(value, expected):
    assert thresholds.status(value, 500, 200, direction="low") == expected


def test_auto_limits_come_from_hardware():
    entry = thresholds.evaluate(sensor(warn="auto", crit="auto", max=90),
                                Reading(45.0, "°C", {"warn": 55.0, "crit": 85.0}))
    assert (entry["warn"], entry["crit"], entry["status"]) == (55.0, 85.0, "ok")


def test_percent_limits_scale_with_auto_max():
    entry = thresholds.evaluate(sensor(kind="memory", max="auto", warn="90%", crit="97%"),
                                Reading(15.0, "GiB", {"max": 16.0}))
    assert entry["warn"] == pytest.approx(14.4)
    assert entry["status"] == "warn"


def test_missing_reading_keeps_configured_limits():
    entry = thresholds.evaluate(sensor(warn=78, crit=88, max=100), None)
    assert entry["status"] == "missing" and entry["crit"] == 88


# ------------------------------------------------------------------ config

@pytest.mark.parametrize("path", sorted((ROOT / "configs").glob("*.toml")))
def test_shipped_configs_load(path):
    cfg = config.load(path)
    assert cfg.sensors and cfg.layout.gauges


@pytest.mark.parametrize("data, message", [
    ({"sensor": [{"id": "a", "name": "A", "key": "nope"}]}, "must look like"),
    ({"sensor": [{"id": "a", "name": "A", "key": "hwmon:x", "colour": 1}]}, "unknown setting"),
    ({"sensor": [{"id": "a", "name": "A", "key": "hwmon:x", "warn": "hot"}]}, "must be a number"),
    ({"layout": {"gauges": ["ghost"]}}, "no sensor with id"),
    ({"widgets": {}}, "unknown section"),
])
def test_config_errors_are_readable(data, message):
    with pytest.raises(config.ConfigError, match=message):
        config.parse(data)


# ------------------------------------------------------------------ hwmon

def make_chip(root: Path, n: int, name: str, device: str, files: dict[str, str]):
    chip = root / f"hwmon{n}"
    (root / "devices" / device).mkdir(parents=True)
    chip.mkdir()
    (chip / "device").symlink_to(root / "devices" / device)
    (chip / "name").write_text(name + "\n")
    for file, value in files.items():
        (chip / file).write_text(value + "\n")


def test_hwmon_reads_labels_limits_and_numbers_duplicate_chips(tmp_path):
    make_chip(tmp_path, 0, "k10temp", "cpu", {"temp1_input": "74750", "temp1_label": "Tctl"})
    # hwmon numbers are reversed relative to device order, as can happen after a reboot
    make_chip(tmp_path, 2, "spd5118", "i2c-51", {"temp1_input": "45000", "temp1_max": "55000", "temp1_crit": "85000"})
    make_chip(tmp_path, 1, "spd5118", "i2c-53", {"temp1_input": "47000"})
    make_chip(tmp_path, 3, "nvme", "nvme0", {"temp2_input": "40850", "temp2_label": "Sensor 1",
                                             "temp2_max": "65261850"})

    source = HwmonSource(tmp_path)
    readings = source.sample()
    assert readings[source.canonical("k10temp/Tctl")].value == pytest.approx(74.75)
    assert readings["spd5118[0]/temp1"].value == 45.0  # i2c-51 sorts first
    assert readings["spd5118[0]/temp1"].limits == {"warn": 55.0, "crit": 85.0}
    assert readings["spd5118[1]/temp1"].value == 47.0
    assert readings["nvme[0]/Sensor 1"].limits == {}  # junk limit dropped


# ------------------------------------------------------------------ known names

def test_known_names():
    assert known.identify("k10temp", "Tccd2").name == "CPU Core Die 2"
    assert known.identify("nct6799", "AUXTIN1").relevance == known.IGNORE
    assert known.identify("nct6799", "fan3", 0).relevance == known.IGNORE
    assert known.identify("nct6799", "fan2", 2150).relevance == known.USEFUL
    assert known.identify("mystery", "temp9") is None


def test_starter_config_is_valid():
    found = [Found("hwmon:k10temp/Tctl", 70.0, "°C", known.identify("k10temp", "Tctl"), "CPU"),
             Found("hwmon:nct6799/fan2", 2000.0, "RPM", known.identify("nct6799", "fan2", 2000), "Motherboard")]
    cfg = config.parse(tomllib.loads(starter_config(found, nvidia_gpu=True, machine='Rig "1"')))
    assert "gpu_temperature" in cfg.layout.gauges and "cpu_temperature" in cfg.layout.gauges
    assert cfg.sensor("fan_header_2").direction == "low"


# ------------------------------------------------------------------ monitor

class FakeSource(Source):
    name = "hwmon"

    def __init__(self, readings, health=Health(True)):
        self.readings, self._health = readings, health

    def sample(self):
        return self.readings

    def canonical(self, path):
        return path

    def health(self):
        return self._health


def test_monitor_snapshot_alerts_and_history():
    cfg = config.parse({
        "app": {"refresh_ms": 1000, "history_seconds": 3},
        "sources": {"hwmon": {"required": True}},
        "sensor": [{"id": "cpu", "name": "CPU Temperature", "key": "hwmon:cpu", "warn": 85, "crit": 95},
                   {"id": "gone", "name": "Gone", "key": "hwmon:gone"}],
    })
    source = FakeSource({"cpu": Reading(96.0, "°C")}, Health(False, "chip vanished", "SENSOR CHIP LOST"))
    monitor = Monitor(cfg, {"hwmon": source})
    for _ in range(5):
        snap = monitor.sample()

    assert snap["sensors"]["cpu"]["status"] == "crit"
    assert snap["sensors"]["gone"]["status"] == "missing"
    titles = [(a["level"], a["title"]) for a in snap["alerts"]]
    assert ("crit", "SENSOR CHIP LOST") in titles and ("crit", "CPU TEMPERATURE CRITICAL") in titles
    assert monitor.history_data()["sensors"]["cpu"] == [96.0, 96.0, 96.0]
