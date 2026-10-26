# exp-monitor

A local live dashboard for long experiment runs. It watches
`~/.local/state/exp-monitor/runs/*.json` (written by e.g.
`stampede-actuator/src/analysis/progress.py`) and serves a page at
<http://127.0.0.1:8787/> that updates live over Server-Sent Events. It uses only the Python
stdlib, with no dependencies.

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

Options for any command: `--port` (env `EXP_MONITOR_PORT`, default 8787), `--runs-dir`
(env `EXP_MONITOR_DIR`, the same variable the writers honour), `--no-auto-open`, `--no-notify`.
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
- **Notifications** (`notify-send`): on done, failed (critical, with the last error line)
  and stalled. Turn them off with `--no-notify`.

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

## Tests

```bash
uv run python -m unittest discover -s tests
```
