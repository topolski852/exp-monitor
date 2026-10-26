"""Write fake run files so the dashboard can be checked without real experiments.

    exp-monitor-fake                  # demo: several runs at once (ok, chained, failing, stalling) + some history
    exp-monitor-fake ok -d 60         # one run that finishes in ~60 s
    exp-monitor-fake fail | stall | quiet
    exp-monitor-fake --clean          # delete every fake run file

Files are written exactly like the real writer (stampede-actuator/src/analysis/progress.py): <run_id>.tmp, then
rename, at most every 0.5 s. Fake runs have a project starting with "fake-", which is what --clean matches.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

from exp_monitor import RUNS_DIR

SCENARIOS = {
    # name: (project, experiment, title, phases {name: total})
    "ok": ("fake-actuator", "v2_slide", "Exp 13: spring-loaded eccentric slide",
           {"kinematics": 96, "statics": 828}),
    "chain": ("fake-actuator", "v2_discs", "Exp 12: thin-disc stack, profile sweep",
              {"kinematics": 108, "statics": 640, "fatigue": 220}),
    "fail": ("fake-actuator", "v2_precision", "Exp 14: tolerance stack-up Monte Carlo",
             {"sample": 400, "assemble": 1200}),
    "stall": ("fake-policy", "biped_sweep", "Reward sweep: torso pitch penalty",
              {"rollouts": 600}),
    "quiet": ("fake-policy", "biped_long_eval", "Long-horizon eval, slow episodes",
              {"episodes": 40}),
}


def now_iso(offset: float = 0.0) -> str:
    return (dt.datetime.now() + dt.timedelta(seconds=offset)).isoformat(timespec="seconds")


def dead_pid() -> int:
    p = subprocess.Popen(["true"])
    p.wait()
    return p.pid


class FakeRun:
    def __init__(self, runs_dir: Path, scenario: str, workers: int, chain=None, run_id_time=None):
        project, experiment, title, phases = SCENARIOS[scenario]
        start = run_id_time or dt.datetime.now()
        self.path = runs_dir / f"{start:%Y%m%d-%H%M%S}-{experiment}.json"
        self.s = dict(run_id=self.path.stem, project=project, experiment=experiment, title=title,
                      commit=f"{random.getrandbits(28):07x}", dirty=random.random() < 0.3, pid=os.getpid(),
                      workers=workers, started=start.isoformat(timespec="seconds"), updated=now_iso(),
                      status="running", phase=next(iter(phases)),
                      phases=[dict(name=k, done=0, total=v) for k, v in phases.items()],
                      chain=chain, log_tail=[], error=None)
        self.last = 0.0

    def write(self, force=False, updated_offset=0.0):
        if not force and time.time() - self.last < 0.5:
            return
        self.last = time.time()
        self.s["updated"] = now_iso(updated_offset)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.s, indent=1))
        tmp.replace(self.path)

    def log(self, line: str):
        self.s["log_tail"] = (self.s["log_tail"] + [line])[-12:]
        self.write(force=True)

    def finish(self, error=None):
        self.s["status"] = "failed" if error else "done"
        self.s["error"] = error
        self.write(force=True)


def run(args) -> None:
    r = FakeRun(args.runs_dir, args.scenario, args.workers, chain=chain_arg(args))
    phases = r.s["phases"]
    total = sum(p["total"] for p in phases)
    per_item = args.duration / total
    r.log(f"start {r.s['experiment']} on {args.workers} workers, commit {r.s['commit']}")
    went_quiet = False
    for i, p in enumerate(phases):
        r.s["phase"] = p["name"]
        r.log(f"[{p['name']}] {p['total']} cases")
        speed = random.uniform(0.6, 1.6)  # phases differ in cost, like real ones
        while p["done"] < p["total"]:
            if args.scenario == "fail" and i == 1 and p["done"] >= p["total"] * 0.55:
                r.log("Traceback (most recent call last):")
                r.log('  File "src/analysis/v2_precision.py", line 214, in assemble')
                r.finish(f"ValueError: negative clearance at case {p['done']} (disc_profile r2=11.98 < r1=12.00)")
                return
            if args.scenario == "stall" and p["done"] >= p["total"] * 0.4:
                # Last words of a crashed process: backdate so it reads as stalled at once rather than in 60 s.
                started = dt.datetime.fromisoformat(r.s["started"]) - dt.timedelta(seconds=90)
                r.s["started"] = started.isoformat(timespec="seconds")
                r.write(force=True, updated_offset=-90)
                print(f"stall: {r.path.name} left running with a dead pid", flush=True)
                os._exit(1)
            if args.scenario == "quiet" and not went_quiet and p["done"] >= p["total"] // 3:
                went_quiet = True
                r.log(f"[{p['name']}] episode {p['done']} is a long one…")
                time.sleep(args.quiet_for)  # alive but silent: the dashboard should say "quiet", not stalled
            step = random.randint(1, 4)
            time.sleep(per_item * step * speed * random.uniform(0.5, 1.5))
            p["done"] = min(p["done"] + step, p["total"])
            if random.random() < 0.08:
                r.log(f"[{p['name']}] case {p['done']}/{p['total']}  max_stress={random.uniform(120, 260):.1f} MPa  "
                      f"margin={random.uniform(1.1, 2.6):.2f}")
            r.write(force=p["done"] == p["total"])
    r.log(f"wrote results/{r.s['experiment']}.json")
    r.finish()


def chain_arg(args):
    if not args.chain:
        return None
    index, _, total = args.chain.partition("/")
    return dict(name=args.chain_name, index=int(index), total=int(total or index))


def seed_history(runs_dir: Path) -> None:
    """A few finished runs from earlier today, so the history list has something in it."""
    for scenario, hours_ago, minutes, error in [("ok", 5, 42, None), ("fail", 3.5, 7, "RuntimeError: worker 17 "
                                                "died (MemoryError while building stiffness matrix)"),
                                                ("chain", 1.2, 23, None)]:
        start = dt.datetime.now() - dt.timedelta(hours=hours_ago)
        r = FakeRun(runs_dir, scenario, 30, run_id_time=start)
        r.s["pid"] = dead_pid()
        frac = 0.5 if error else 1.0
        for p in r.s["phases"]:
            p["done"] = int(p["total"] * frac) if p is r.s["phases"][-1] else p["total"]
        r.s["phase"] = r.s["phases"][-1]["name"]
        r.s["log_tail"] = ["start", f"[{r.s['phase']}] {r.s['phases'][-1]['total']} cases",
                           *(["Traceback (most recent call last):", "  ..."] if error else ["wrote results"])]
        r.s["status"], r.s["error"] = ("failed", error) if error else ("done", None)
        r.s["updated"] = (start + dt.timedelta(minutes=minutes)).isoformat(timespec="seconds")
        r.path.parent.mkdir(parents=True, exist_ok=True)
        r.path.write_text(json.dumps(r.s, indent=1))


def demo(args) -> None:
    seed_history(args.runs_dir)
    me = [sys.executable, "-m", "exp_monitor.fake", "--runs-dir", str(args.runs_dir)]
    procs = [
        subprocess.Popen(me + ["chain", "-d", str(args.duration * 1.5), "-w", "30", "--chain", "12/13",
                               "--chain-name", "rerun after disc_profile fix"]),
        subprocess.Popen(me + ["ok", "-d", str(args.duration), "-w", "24"]),
    ]
    time.sleep(4)
    procs.append(subprocess.Popen(me + ["fail", "-d", str(args.duration * 0.8), "-w", "16"]))
    time.sleep(3)
    procs.append(subprocess.Popen(me + ["stall", "-d", str(args.duration), "-w", "8"]))
    procs.append(subprocess.Popen(me + ["quiet", "-d", str(args.duration), "-w", "4"]))
    print(f"demo: {len(procs)} fake runs writing to {args.runs_dir} (Ctrl-C to stop, --clean to remove)", flush=True)
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        pass


def clean(runs_dir: Path) -> None:
    n = 0
    for f in list(runs_dir.glob("*.json")) + list(runs_dir.glob("*.tmp")):
        try:
            if str(json.loads(f.read_text()).get("project", "")).startswith("fake-"):
                f.unlink()
                n += 1
        except (OSError, ValueError, AttributeError):
            pass
    print(f"removed {n} fake run file(s) from {runs_dir}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="exp-monitor-fake", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario", nargs="?", default="demo", choices=["demo", *SCENARIOS])
    ap.add_argument("-d", "--duration", type=float, default=90, help="approximate seconds per run (default 90)")
    ap.add_argument("-w", "--workers", type=int, default=30)
    ap.add_argument("--chain", metavar="I/N", help="chain position, e.g. 12/13")
    ap.add_argument("--chain-name", default="fake chain")
    ap.add_argument("--quiet-for", type=float, default=75, help="'quiet' scenario: seconds of silence (default 75)")
    ap.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    ap.add_argument("--clean", action="store_true", help="delete all fake run files and exit")
    args = ap.parse_args(argv)
    if args.clean:
        return clean(args.runs_dir)
    try:
        demo(args) if args.scenario == "demo" else run(args)
    except KeyboardInterrupt:
        pass  # leave the file as a crashed run would: it turns stalled after 60 s


if __name__ == "__main__":
    main()
