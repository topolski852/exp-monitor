# exp-monitor

A local live dashboard for long experiment runs, with the rig's hardware sensors alongside.
It watches `~/.local/state/exp-monitor/runs/*.json` (written by e.g.
`stampede-actuator/src/analysis/progress.py`) and serves a page at
<http://127.0.0.1:8787/> that updates live over Server-Sent Events.

The page, top to bottom:

1. Hardware alerts, such as the GPU driver not being loaded or a part past its limit.
2. A strip of six gauges: GPU temperature, load and VRAM; CPU temperature and load; RAM.
3. Active runs, the main focus, with finished runs in a history column on the right.
4. The System section: every configured sensor, per-thread CPU load, storage and fans.

The hardware half was [PC Monitor](#system-sensors), merged in with its git history. The
backend is the Python stdlib plus `nvidia-ml-py`; the page is plain HTML, CSS and JS with no
build step. Its fonts are bundled, so it works offline.

## Start

```bash
uv run exp-monitor                 # open the dashboard, starting the server if it isn't running
uv run exp-monitor install-service # always on: systemd user service, starts with your session
```

Other commands:

| command | |
|---|---|
| `exp-monitor serve [--open]` | run the server in the foreground (what the service runs) |
| `exp-monitor open` | open the window for the running server |
| `exp-monitor status` | active runs as text, handy over ssh |
| `exp-monitor install-service [--print]` | write `~/.config/systemd/user/exp-monitor.service`, enable + start it |
| `exp-monitor uninstall-service` | stop + remove it |
| `exp-monitor system check` | print every configured sensor once, coloured by status |
| `exp-monitor system discover [--all] [--write]` | list and explain every sensor on this machine; `--write` makes a starter config |

Options for any command: `--port` (env `EXP_MONITOR_PORT`, default 8787), `--runs-dir`
(env `EXP_MONITOR_DIR`, the same variable the writers honour), `--no-auto-open`,
`--system-config PATH`, `--no-system`.
Options passed to `install-service` are baked into the unit.

Service logs: `journalctl --user -u exp-monitor -f`.

## Try it without experiments

```bash
uv run exp-monitor-fake            # demo: 5 concurrent runs (ok, chained 12/13, failing, stalling, quiet) + history
uv run exp-monitor-fake ok -d 30   # one run, ~30 s; also: chain, fail, stall, quiet
uv run exp-monitor-fake --clean    # delete all fake run files (project "fake-*")
```

Ctrl-C on a fake run leaves its file as a crashed run would. It shows as stalled after 60 s.

## What it shows

- **Active runs**: one card each, with title, experiment, project, commit (`+dirty`) and pid.
  Each card has a bar per phase with done/total, plus elapsed time, ETA, rate, worker count,
  time since the last update, chain position and the log tail.
- **ETA**: the time left in the current phase at the current phase's rate over the last 60 s.
  When later phases are listed, a whole-run ETA also appears (remaining items ÷ current rate,
  so it is rough when phases differ in cost). Until there are about 3 s of samples, for
  example just after the server starts, the rate is the whole-run average, marked `avg`.
- **Stalled**: the status is still `running`, `updated` is more than 60 s old, *and* the pid
  is gone (or is a zombie). If the pid is still alive, the card says *quiet* instead, because
  the writer only rewrites on progress and a slow step can be silent for a while. Stalled
  runs stay on the active grid for 15 min, then move to history.
- **History**: done, failed and long-stalled runs, newest first, up to 100. Click a row for
  its phases, error and log tail.

## Desktop behaviour

- **Auto-open**: when a new run file appears and no dashboard is connected, the server opens
  one. It uses a Chromium-family browser as an app window (`--app=`; chromium, chrome, brave,
  edge, vivaldi, or their snap/flatpak builds) and falls back to `xdg-open`. A launch is
  followed by a 30 s cooldown so a slow-starting browser doesn't get a second window. Under
  systemd the browser gets its own scope (`systemd-run --scope`), so restarting the service
  doesn't close it.

## Input contract

`<run_id>.json` is rewritten atomically about once a second:

```json
{"run_id": "20261026-191500-v2_slide", "project": "stampede-actuator",
 "experiment": "v2_slide", "title": "Exp 13: spring-loaded eccentric slide",
 "commit": "38d0dd9", "dirty": false, "pid": 574392, "workers": 30,
 "started": "2026-10-26T19:15:00", "updated": "2026-10-26T19:20:03",
 "status": "running", "phase": "statics",
 "phases": [{"name": "kinematics", "done": 96, "total": 96},
            {"name": "statics", "done": 412, "total": 828}],
 "chain": {"name": "rerun after disc_profile fix", "index": 12, "total": 13},
 "log_tail": ["last few stdout lines"], "error": null}
```

`status` is `running`, `done` or `failed`. `chain` and `error` may be null. Only `*.json` is
read, so the writer's `<run_id>.tmp` is never picked up half-written. A file that fails to
parse keeps its last good copy on screen. Deleting a file removes the run.

## System sensors

Gauges and bars are scaled to the manufacturer's limits. Every temperature is green in the
part's comfortable range, amber as it nears its rated maximum, and red past it. Cryptic labels
get plain names (`Tctl` becomes *CPU Temperature*), and **EXPLAIN** in the header shows what
each reading means. Click any row to expand just that one.

The sensors, their names, limits and layout come from one TOML file. The first one that
exists wins:

1. `--system-config PATH` or `$EXP_MONITOR_SYSTEM_CONFIG`;
2. `~/.config/exp-monitor/system.toml`, your own;
3. [`configs/isaac-sim-rig.toml`](configs/isaac-sim-rig.toml), the bundled config for this
   Ryzen 9 9950X + RTX 5080 rig.

After editing it, run `systemctl --user restart exp-monitor`. A broken config doesn't stop
the dashboard: the runs still show, and the footer says why the sensors are off.

```toml
[app]
machine = "SIMULATION RIG"
refresh_ms = 1000
history_seconds = 300       # sparkline length

[sources.nvidia]
required = true             # red alert if the driver isn't loaded

[layout]
gauges = ["gpu_temp", "cpu_temp"]   # the strip at the top

[[layout.panels]]                   # the System section
title = "CPU"
sensors = ["cpu_temp"]

[[sensor]]
id = "cpu_temp"
name = "CPU Temperature"
key = "hwmon:k10temp/Tctl"  # <source>:<path>, as listed by `exp-monitor system discover`
kind = "temperature"
max = 100                   # gauge range
warn = 85                   # amber from here
crit = 95                   # red from here (AMD Tjmax)
spec = "AMD Ryzen 9 9950X Tjmax: 95 °C"
about = "The CPU's control temperature."
```

**Limits** (`warn`, `crit`, `max`) can be:

- a number;
- `"auto"`, which uses the limit the hardware reports about itself (RAM sticks, NVMe drives,
  NVIDIA GPUs);
- for `warn` and `crit` only, a percentage such as `"90%"`, meaning that share of `max`
  (handy for VRAM and RAM).

Set `direction = "low"` for readings that are bad when they *drop*, like a fan that stops. A
panel with `widget = "threads"` shows per-thread CPU load.

**Sources:**

| Source | Paths | Examples |
|---|---|---|
| `hwmon` | `<chip>[n]/<label>` from `/sys/class/hwmon` | `k10temp/Tctl`, `nvme/Composite`, `spd5118[1]/temp1`, `nct6799/fan2` |
| `nvidia` | `<gpu>/<field>` | `0/temperature`, `0/utilization`, `0/memory_used`, `0/power`, `0/fan`, `0/clock_graphics` |
| `system` | OS counters | `cpu/utilization`, `cpu/frequency`, `memory/used`, `swap/percent`, `mount[/]/percent`, `block[nvme0n1]/read` |

`[n]` picks between chips with the same name (one per RAM stick, for example) and defaults to
`[0]`. Requirements: `lm-sensors` kernel drivers for the motherboard (most distros load them
automatically), and the NVIDIA driver for NVML.

## Tests

```bash
uv run pytest
```

## License

MIT (see [LICENSE](LICENSE)). The bundled fonts, Orbitron, Rajdhani and JetBrains Mono, are
under the SIL Open Font License; see `src/exp_monitor/static/fonts/`.
