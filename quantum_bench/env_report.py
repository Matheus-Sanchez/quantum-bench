from __future__ import annotations

import os
import platform
import subprocess
import sys
from typing import Any

from quantum_bench.capability import gpu_info, system_memory_bytes
from quantum_bench.utils import is_wsl, package_version, utc_now_iso


def _cpu_info() -> dict[str, Any]:
    info = {
        "processor": platform.processor() or None,
        "machine": platform.machine(),
        "cpu_count_logical": os.cpu_count(),
    }
    try:
        import psutil  # type: ignore

        info["cpu_count_physical"] = psutil.cpu_count(logical=False)
        freq = psutil.cpu_freq()
        if freq:
            info["cpu_freq_mhz_max"] = freq.max
    except Exception:
        pass
    return info


def _cuda_version() -> str | None:
    try:
        completed = subprocess.run(["nvidia-smi"], text=True, capture_output=True, timeout=10, check=False)
        if completed.returncode != 0:
            return None
        for line in completed.stdout.splitlines():
            if "CUDA Version" in line:
                marker = "CUDA Version:"
                fragment = line.split(marker, 1)[1]
                return fragment.split("|", 1)[0].strip()
    except Exception:
        return None
    return None


def build_env_report() -> dict[str, Any]:
    qiskit_aer_version = package_version("qiskit-aer") or package_version("qiskit-aer-gpu")
    total_ram, available_ram = system_memory_bytes()
    packages = {
        "quantum-bench": package_version("quantum-bench"),
        "qiskit": package_version("qiskit"),
        "qiskit-aer": qiskit_aer_version,
        "qiskit-aer-gpu": package_version("qiskit-aer-gpu"),
        "qulacs": package_version("qulacs"),
        "pennylane": package_version("pennylane"),
        "pennylane-lightning": package_version("pennylane-lightning"),
        "matplotlib": package_version("matplotlib"),
        "psutil": package_version("psutil"),
        "pynvml": package_version("pynvml"),
    }
    return {
        "timestamp_utc": utc_now_iso(),
        "host": {
            "hostname": platform.node(),
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "python_executable": sys.executable,
            "python_version": sys.version,
            "is_wsl": is_wsl(),
        },
        "cpu": _cpu_info(),
        "memory": {
            "total_bytes": total_ram,
            "available_bytes": available_ram,
        },
        "gpu": gpu_info(),
        "cuda_version": _cuda_version(),
        "package_versions": packages,
        "environment": {
            "OMP_NUM_THREADS": os.environ.get("OMP_NUM_THREADS"),
            "QULACS_NUM_THREADS": os.environ.get("QULACS_NUM_THREADS"),
            "CUDA_VISIBLE_DEVICES": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "CUQUANTUM_SDK": os.environ.get("CUQUANTUM_SDK"),
        },
    }
