from __future__ import annotations

import math
import os
from typing import Any

from quantum_bench.utils import utc_now_iso


def _psutil_memory() -> tuple[int | None, int | None]:
    try:
        import psutil  # type: ignore
    except ImportError:
        return None, None
    vm = psutil.virtual_memory()
    return int(vm.total), int(vm.available)


def _windows_memory() -> tuple[int | None, int | None]:
    try:
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("memory_load", ctypes.c_ulong),
                ("total_phys", ctypes.c_ulonglong),
                ("avail_phys", ctypes.c_ulonglong),
                ("total_page_file", ctypes.c_ulonglong),
                ("avail_page_file", ctypes.c_ulonglong),
                ("total_virtual", ctypes.c_ulonglong),
                ("avail_virtual", ctypes.c_ulonglong),
                ("avail_extended_virtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
        return int(status.total_phys), int(status.avail_phys)
    except Exception:
        return None, None


def system_memory_bytes() -> tuple[int | None, int | None]:
    total, available = _psutil_memory()
    if total is not None:
        return total, available
    if os.name == "nt":
        return _windows_memory()
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        available = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_AVPHYS_PAGES")
        return int(total), int(available)
    except (AttributeError, ValueError, OSError):
        return None, None


def gpu_info() -> dict[str, Any]:
    try:
        import pynvml  # type: ignore

        pynvml.nvmlInit()
        handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        memory = pynvml.nvmlDeviceGetMemoryInfo(handle)
        return {
            "name": pynvml.nvmlDeviceGetName(handle).decode("utf-8") if isinstance(pynvml.nvmlDeviceGetName(handle), bytes) else pynvml.nvmlDeviceGetName(handle),
            "total_bytes": int(memory.total),
            "free_bytes": int(memory.free),
            "used_bytes": int(memory.used),
            "driver_version": pynvml.nvmlSystemGetDriverVersion().decode("utf-8")
            if isinstance(pynvml.nvmlSystemGetDriverVersion(), bytes)
            else pynvml.nvmlSystemGetDriverVersion(),
            "source": "pynvml",
        }
    except Exception:
        pass

    try:
        import subprocess

        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.free,memory.used,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        if completed.returncode == 0 and completed.stdout.strip():
            name, total_mb, free_mb, used_mb, driver = [item.strip() for item in completed.stdout.strip().split(",")]
            return {
                "name": name,
                "total_bytes": int(total_mb) * 1024 * 1024,
                "free_bytes": int(free_mb) * 1024 * 1024,
                "used_bytes": int(used_mb) * 1024 * 1024,
                "driver_version": driver,
                "source": "nvidia-smi",
            }
    except Exception:
        pass

    return {"source": "unavailable"}


def estimate_safe_qubits(usable_bytes: int | None, bytes_per_amplitude: int, overhead_factor: float) -> int | None:
    if usable_bytes is None or usable_bytes <= 0:
        return None
    effective = usable_bytes / max(overhead_factor, 1.0)
    if effective <= bytes_per_amplitude:
        return 0
    return int(math.floor(math.log2(effective / bytes_per_amplitude)))


def build_capability_report(ram_fraction: float = 0.75, vram_fraction: float = 0.80, overhead_factor: float = 1.5) -> dict[str, Any]:
    total_ram, available_ram = system_memory_bytes()
    gpu = gpu_info()

    ram_budget = int((available_ram or total_ram or 0) * ram_fraction) if (available_ram or total_ram) else None
    gpu_available = gpu.get("free_bytes") or gpu.get("total_bytes")
    vram_budget = int(gpu_available * vram_fraction) if gpu_available else None

    resources = {
        "cpu_statevector_double": {
            "bytes_per_amplitude": 16,
            "safe_bytes": ram_budget,
            "recommended_max_qubits": estimate_safe_qubits(ram_budget, 16, overhead_factor),
        },
        "cpu_statevector_single": {
            "bytes_per_amplitude": 8,
            "safe_bytes": ram_budget,
            "recommended_max_qubits": estimate_safe_qubits(ram_budget, 8, overhead_factor),
        },
        "gpu_statevector_double": {
            "bytes_per_amplitude": 16,
            "safe_bytes": vram_budget,
            "recommended_max_qubits": estimate_safe_qubits(vram_budget, 16, overhead_factor),
        },
        "gpu_statevector_single": {
            "bytes_per_amplitude": 8,
            "safe_bytes": vram_budget,
            "recommended_max_qubits": estimate_safe_qubits(vram_budget, 8, overhead_factor),
        },
    }

    return {
        "timestamp_utc": utc_now_iso(),
        "assumptions": {
            "ram_fraction": ram_fraction,
            "vram_fraction": vram_fraction,
            "overhead_factor": overhead_factor,
        },
        "system_memory": {
            "total_bytes": total_ram,
            "available_bytes": available_ram,
            "budget_bytes": ram_budget,
        },
        "gpu": gpu,
        "resources": resources,
    }
