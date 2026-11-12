import json
import threading

import pytest

from exp_monitor import timeclock
from exp_monitor.timeclock import TimeClock


@pytest.fixture
def clock(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(timeclock.time, "time", lambda: now[0])
    c = TimeClock(tmp_path / "clock.json", threading.Condition())
    c.advance = lambda s: now.__setitem__(0, now[0] + s)
    c.totals = lambda: [t["acc"] + (now[0] - t["since"] if t["since"] is not None else 0) for t in c.timers]
    return c


def test_pause_holds_only_timer_1(clock):
    assert clock.press("in") and clock.state == "on"
    clock.advance(60)
    assert clock.press("toggle") and clock.state == "paused"
    clock.advance(30)
    assert clock.totals() == [60, 90]
    assert clock.press("toggle") and clock.state == "on"
    clock.advance(10)
    assert clock.totals() == [70, 100]


def test_clock_in_only_resets_when_both_are_stopped(clock):
    clock.press("in")
    clock.advance(60)
    assert not clock.press("in")                     # running: nothing
    clock.press("toggle")
    assert not clock.press("in")                     # timer 2 still running: nothing
    assert clock.totals() == [60, 60]
    assert clock.press("out") and clock.state == "off"
    assert not clock.press("out")
    clock.advance(500)
    assert clock.totals() == [60, 60]                # stopped
    assert clock.press("in") and clock.totals() == [0, 0]


def test_play_after_clock_out_resumes_both_without_reset(clock):
    clock.press("in")
    clock.advance(60)
    clock.press("toggle")                            # pause timer 1
    clock.advance(20)
    clock.press("out")
    clock.advance(1000)
    assert clock.press("toggle") and clock.state == "on"
    clock.advance(10)
    assert clock.totals() == [70, 90]


def test_state_survives_restart(clock, tmp_path):
    clock.press("in")
    clock.advance(60)
    clock.press("toggle")
    again = TimeClock(tmp_path / "clock.json", threading.Condition())
    assert again.state == "paused" and again.timers == clock.timers
    assert json.loads(again.view())["state"] == "paused"


def test_unreadable_file_starts_off(tmp_path):
    (tmp_path / "clock.json").write_text("{not json")
    assert TimeClock(tmp_path / "clock.json", threading.Condition()).state == "off"
