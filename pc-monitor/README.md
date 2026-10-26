# PC Monitor

A hardware monitor dashboard for Linux that tells you what your sensors actually mean.

- **Gauges scaled to the manufacturer's limits.** Every temperature is green in the part's comfortable range, amber as it nears its rated maximum, and red past it. The limits come from spec sheets, or from the part itself when it reports them (RAM sticks, NVMe drives, NVIDIA GPUs).
- **Plain-English names.** `Tctl` becomes *CPU Temperature*, `Tccd2` becomes *CPU Die 2*, and noise like `AUXTIN1` or `PCH_CHIP_TEMP` is hidden. Press **EXPLAIN** to see what each reading is and why it matters.
- **One config file per PC.** Which sensors to show, what to call them, their limits and the layout all live in a TOML file, so the same app fits any machine.
- **Built for compute workloads.** VRAM and RAM headroom, per-thread CPU load, swap, disk space, and a loud alert when the GPU driver isn't loaded.

The dashboard is a local web page, so you can also open it from another device on your network while a long job runs.

## Quick start

```bash
git clone https://github.com/topolski852/pc-monitor.git
cd pc-monitor
python3 -m venv .venv
.venv/bin/pip install -e .

# See every sensor on this machine, with plain names and what's worth watching
.venv/bin/pc-monitor discover

# Write a starter config, then edit names and limits to match your parts
.venv/bin/pc-monitor discover --write ~/.config/pc-monitor/config.toml --machine "MY RIG"

# Start the dashboard (opens http://localhost:8765)
.venv/bin/pc-monitor
```

Other commands:

| Command | What it does |
|---|---|
| `pc-monitor check -c FILE` | Print every configured sensor once, colored by status, then exit |
| `pc-monitor run --host 0.0.0.0` | Let other devices on your network open the dashboard |
| `pc-monitor discover --all` | Also list the sensors that are hidden as noise |

Requirements: Linux, Python 3.11+, and `lm-sensors` kernel drivers for your motherboard (most distros load them automatically). NVIDIA GPUs are read through NVML, which ships with the NVIDIA driver.

## Config

A config file has four parts. [`configs/isaac-sim-rig.toml`](configs/isaac-sim-rig.toml) is a complete example for a Ryzen 9 9950X + RTX 5080 workstation.

```toml
[app]
machine = "ISAAC SIM RIG"
refresh_ms = 1000

[sources.nvidia]
required = true             # red alert if the driver isn't loaded

[layout]
gauges = ["gpu_temp", "cpu_temp"]

[[layout.panels]]
title = "CPU"
sensors = ["cpu_temp"]

[[sensor]]
id = "cpu_temp"
name = "CPU Temperature"
key = "hwmon:k10temp/Tctl"  # <source>:<path>, as listed by `pc-monitor discover`
kind = "temperature"
max = 100                   # gauge range
warn = 85                   # amber from here
crit = 95                   # red from here (AMD Tjmax)
spec = "AMD Ryzen 9 9950X Tjmax: 95 °C"
about = "The CPU's control temperature."
```

**Limits** (`warn`, `crit`, `max`) can be:

- a number;
- `"auto"`, which uses the limit the hardware reports about itself;
- for `warn` and `crit` only, a percentage such as `"90%"`, meaning that share of `max` (handy for VRAM and RAM).

Set `direction = "low"` for readings that are bad when they *drop*, like a fan that stops.

**Sources:**

| Source | Paths | Examples |
|---|---|---|
| `hwmon` | `<chip>[n]/<label>` from `/sys/class/hwmon` | `k10temp/Tctl`, `nvme/Composite`, `spd5118[1]/temp1`, `nct6799/fan2` |
| `nvidia` | `<gpu>/<field>` | `0/temperature`, `0/utilization`, `0/memory_used`, `0/power`, `0/fan`, `0/clock_graphics` |
| `system` | OS counters | `cpu/utilization`, `cpu/frequency`, `memory/used`, `swap/percent`, `mount[/]/percent`, `block[nvme0n1]/read` |

`[n]` picks between chips with the same name (one per RAM stick, for example) and defaults to `[0]`.

## Development

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

The backend uses only the Python standard library plus `nvidia-ml-py`. The dashboard (`pc_monitor/web/`) is plain HTML, CSS and JavaScript with no build step.

## Roadmap

- 3D model of the PC with live temperatures mapped onto the parts
- Native app window and a desktop launcher
- CPU package power, which needs read access to the RAPL energy counter
- Logging runs to disk to compare thermals between training jobs

## License

MIT
