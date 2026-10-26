"""NVIDIA GPUs through NVML (the library behind nvidia-smi).

Paths look like ``<gpu index>/<field>``, for example ``0/temperature``.
"""

from __future__ import annotations

import time

from .base import Health, Reading, Source

try:
    import pynvml
except ImportError:  # the source still loads and reports itself as unavailable
    pynvml = None

GIB = 2**30
RETRY_SECONDS = 30


class NvidiaSource(Source):
    name = "nvidia"

    def __init__(self):
        self._gpus: list[dict] = []
        self._error: str | None = None
        self._title = ""
        self._next_try = 0.0
        self._connect()

    def health(self) -> Health:
        return Health(self._error is None, self._error or "", self._title)

    def info(self) -> dict[str, str]:
        if not self._gpus:
            return {}
        info = {f"gpu{g['index']}": g["name"] for g in self._gpus}
        info["nvidia_driver"] = self._driver
        return info

    def sample(self) -> dict[str, Reading]:
        if not self._gpus:
            if time.monotonic() < self._next_try:
                return {}
            self._connect()
            if not self._gpus:
                return {}
        out = {}
        try:
            for gpu in self._gpus:
                out.update(self._sample_gpu(gpu))
        except (pynvml.NVMLError_GpuIsLost, pynvml.NVMLError_Uninitialized) as e:
            self._fail(f"Lost contact with the NVIDIA GPU ({e}).", "GPU LOST")
            return {}
        return out

    def _sample_gpu(self, gpu: dict) -> dict[str, Reading]:
        h, i = gpu["handle"], gpu["index"]
        temp = _try(pynvml.nvmlDeviceGetTemperature, h, pynvml.NVML_TEMPERATURE_GPU)
        util = _try(pynvml.nvmlDeviceGetUtilizationRates, h)
        mem = _try(pynvml.nvmlDeviceGetMemoryInfo, h)
        power = _try(pynvml.nvmlDeviceGetPowerUsage, h)
        return {
            f"{i}/temperature": Reading(temp, "°C", gpu["temp_limits"]),
            f"{i}/utilization": Reading(util.gpu if util else None, "%"),
            f"{i}/memory_controller": Reading(util.memory if util else None, "%"),
            f"{i}/memory_used": Reading(mem.used / GIB if mem else None, "GiB", gpu["memory_limits"]),
            f"{i}/power": Reading(power / 1000 if power is not None else None, "W", gpu["power_limits"]),
            f"{i}/fan": Reading(_try(pynvml.nvmlDeviceGetFanSpeed, h), "%"),
            f"{i}/clock_graphics": Reading(
                _try(pynvml.nvmlDeviceGetClockInfo, h, pynvml.NVML_CLOCK_GRAPHICS), "MHz", gpu["clock_limits"]),
            f"{i}/clock_memory": Reading(
                _try(pynvml.nvmlDeviceGetClockInfo, h, pynvml.NVML_CLOCK_MEM), "MHz", gpu["mem_clock_limits"]),
        }

    def _connect(self) -> None:
        self._next_try = time.monotonic() + RETRY_SECONDS
        if pynvml is None:
            return self._fail("The nvidia-ml-py package is not installed.", "GPU MONITORING UNAVAILABLE")
        try:
            pynvml.nvmlInit()
            self._driver = pynvml.nvmlSystemGetDriverVersion()
            count = pynvml.nvmlDeviceGetCount()
            self._gpus = [self._describe(i, pynvml.nvmlDeviceGetHandleByIndex(i)) for i in range(count)]
        except pynvml.NVMLError_DriverNotLoaded:
            return self._fail(
                "The NVIDIA kernel driver is not loaded, so CUDA, Isaac Sim and GPU monitoring "
                "cannot use the GPU. This usually means the driver module is missing for the "
                "running kernel.", "GPU DRIVER NOT LOADED")
        except pynvml.NVMLError_LibraryNotFound:
            return self._fail("The NVIDIA driver library (libnvidia-ml) is not installed.", "GPU DRIVER NOT INSTALLED")
        except pynvml.NVMLError as e:
            return self._fail(f"NVML error: {e}", "GPU MONITORING ERROR")
        if not self._gpus:
            self._fail("No NVIDIA GPU was found.", "NO NVIDIA GPU")
        else:
            self._error = None

    def _describe(self, index: int, h) -> dict:
        def threshold(kind):
            return _try(pynvml.nvmlDeviceGetTemperatureThreshold, h, kind)

        temp_limits = {"warn": threshold(pynvml.NVML_TEMPERATURE_THRESHOLD_GPU_MAX),
                       "crit": threshold(pynvml.NVML_TEMPERATURE_THRESHOLD_SLOWDOWN)}
        mem = _try(pynvml.nvmlDeviceGetMemoryInfo, h)
        power_limit = _try(pynvml.nvmlDeviceGetEnforcedPowerLimit, h)
        max_clock = _try(pynvml.nvmlDeviceGetMaxClockInfo, h, pynvml.NVML_CLOCK_GRAPHICS)
        max_mem_clock = _try(pynvml.nvmlDeviceGetMaxClockInfo, h, pynvml.NVML_CLOCK_MEM)
        return {
            "index": index,
            "handle": h,
            "name": pynvml.nvmlDeviceGetName(h),
            "temp_limits": {k: v for k, v in temp_limits.items() if v},
            "memory_limits": {"max": mem.total / GIB} if mem else {},
            "power_limits": {"max": power_limit / 1000} if power_limit else {},
            "clock_limits": {"max": max_clock} if max_clock else {},
            "mem_clock_limits": {"max": max_mem_clock} if max_mem_clock else {},
        }

    def _fail(self, message: str, title: str) -> None:
        self._gpus = []
        self._error = message
        self._title = title


def _try(fn, *args):
    """Call an NVML getter. Not every GPU supports every field, so failures give None."""
    try:
        return fn(*args)
    except pynvml.NVMLError_GpuIsLost:
        raise
    except pynvml.NVMLError:
        return None
