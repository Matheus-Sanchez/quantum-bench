from __future__ import annotations

import os
import threading
import time
from typing import Any


class ProcessTelemetrySampler:
    def __init__(self, sample_interval_s: float = 0.05) -> None:
        self.sample_interval_s = sample_interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peak_rss_bytes = 0
        self.gpu_peak_mem_bytes = 0
        self.gpu_peak_util_percent = 0.0
        self._pid = os.getpid()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.sample_interval_s * 4))
        return {
            "peak_rss_bytes": self.peak_rss_bytes or None,
            "gpu_peak_mem_bytes": self.gpu_peak_mem_bytes or None,
            "gpu_peak_util_percent": self.gpu_peak_util_percent or None,
        }

    def _loop(self) -> None:
        process = None
        nvml = None
        handle = None

        try:
            import psutil  # type: ignore

            process = psutil.Process(self._pid)
        except Exception:
            process = None

        try:
            import pynvml  # type: ignore

            pynvml.nvmlInit()
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            nvml = pynvml
        except Exception:
            nvml = None
            handle = None

        while not self._stop.is_set():
            if process is not None:
                try:
                    rss = int(process.memory_info().rss)
                    self.peak_rss_bytes = max(self.peak_rss_bytes, rss)
                except Exception:
                    process = None

            if nvml is not None and handle is not None:
                try:
                    memory = nvml.nvmlDeviceGetMemoryInfo(handle)
                    util = nvml.nvmlDeviceGetUtilizationRates(handle)
                    self.gpu_peak_mem_bytes = max(self.gpu_peak_mem_bytes, int(memory.used))
                    self.gpu_peak_util_percent = max(self.gpu_peak_util_percent, float(util.gpu))
                except Exception:
                    nvml = None
                    handle = None

            time.sleep(self.sample_interval_s)
