import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from exp_monitor import store
from exp_monitor.store import Store


def iso(dt_):
    return dt_.isoformat(timespec="seconds")


def dead_pid():
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.events = []
        self.store = Store(self.dir, on_event=lambda kind, run: self.events.append((kind, run["key"])))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, run_id, **over):
        t = datetime.now()
        d = dict(run_id=run_id, project="p", experiment="e", title="T", commit="abc", dirty=False, pid=os.getpid(),
                 workers=4, started=iso(t - timedelta(minutes=5)), updated=iso(t), status="running", phase="b",
                 phases=[dict(name="a", done=10, total=10), dict(name="b", done=5, total=100)], chain=None,
                 log_tail=["x"], error=None)
        d.update(over)
        tmp = self.dir / f"{run_id}.tmp"
        tmp.write_text(json.dumps(d))
        tmp.replace(self.dir / f"{run_id}.json")

    def snap(self):
        self.store.scan()
        return json.loads(self.store.snapshot)

    def test_running_done_failed(self):
        self.write("r1")
        self.write("r2", status="done")
        self.write("r3", status="failed", error="boom")
        s = self.snap()
        self.assertEqual([r["key"] for r in s["active"]], ["r1"])
        self.assertEqual({r["key"]: r["state"] for r in s["history"]}, {"r2": "done", "r3": "failed"})

    def test_stalled_needs_old_update_and_dead_pid(self):
        old = iso(datetime.now() - timedelta(seconds=90))
        self.write("dead", pid=dead_pid(), updated=old)
        self.write("alive", updated=old)
        self.write("fresh_dead", pid=dead_pid())
        by = {r["key"]: r for r in self.snap()["active"]}
        self.assertEqual(by["dead"]["state"], "stalled")
        self.assertEqual(by["alive"]["state"], "running")
        self.assertTrue(by["alive"]["quiet"])
        self.assertEqual(by["fresh_dead"]["state"], "running")

    def test_long_stalled_moves_to_history(self):
        self.write("old", pid=dead_pid(), updated=iso(datetime.now() - timedelta(hours=1)))
        s = self.snap()
        self.assertEqual(s["active"], [])
        self.assertEqual(s["history"][0]["state"], "stalled")

    def test_events_after_first_scan_only(self):
        self.write("pre")
        self.store.scan()
        self.assertEqual(self.events, [])
        self.write("new")
        self.store.scan()
        self.assertEqual(self.events, [("new", "new")])
        self.write("new", status="done")
        self.store.scan()
        self.assertEqual(self.events[-1], ("done", "new"))

    def test_tmp_and_bad_json_ignored(self):
        (self.dir / "half.tmp").write_text("{")
        self.write("r1")
        self.store.scan()
        (self.dir / "r1.json").write_text("{not json")  # keeps the last good copy
        s = self.snap()
        self.assertEqual([r["key"] for r in s["active"]], ["r1"])
        (self.dir / "r1.json").unlink()
        self.assertEqual(self.snap()["active"], [])

    def test_recent_rate_and_eta(self):
        t0 = datetime.now().replace(microsecond=0) - timedelta(seconds=20)
        run = store.Run("r", {}, None)
        run.samples.clear()
        for i in range(0, 21, 2):  # phase b: 5 -> 55 over 20 s = 2.5/s
            data = dict(started=iso(t0 - timedelta(minutes=5)), updated=iso(t0 + timedelta(seconds=i)),
                        status="running", phase="b", pid=os.getpid(),
                        phases=[dict(name="a", done=10, total=10), dict(name="b", done=5 + int(2.5 * i), total=100),
                                dict(name="c", done=0, total=50)])
            run.update(data, None, 0)
        now = (t0 + timedelta(seconds=20)).timestamp()
        v = run.view(now)
        self.assertAlmostEqual(v["rate"], 2.5, places=6)
        self.assertFalse(v["rate_approx"])
        self.assertAlmostEqual(v["eta_phase_at"] - now, 45 / 2.5, places=6)
        self.assertAlmostEqual(v["eta_run_at"] - now, 95 / 2.5, places=6)

    def test_rate_falls_back_to_average(self):
        self.write("r1")
        v = self.snap()["active"][0]
        self.assertTrue(v["rate_approx"])
        self.assertAlmostEqual(v["rate"], 15 / 300, places=3)

    def test_zombie_counts_as_dead(self):
        with mock.patch("builtins.open", mock.mock_open(read_data="1 (x) Z 0")):
            self.assertFalse(store.pid_alive(os.getpid()))
        self.assertFalse(store.pid_alive(None))
        self.assertFalse(store.pid_alive(True))


if __name__ == "__main__":
    unittest.main()
