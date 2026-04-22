from __future__ import annotations

import csv
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from quantum_bench.adapters import run_backend_case
from quantum_bench.env_report import build_env_report
from quantum_bench.models import BenchmarkCase, MissingDependencyError, UnsupportedCaseError, json_default
from quantum_bench.recipes import build_recipe, logical_depth, recipe_counts
from quantum_bench.reference import reference_statevector, state_fidelity, trace_distance_pure
from quantum_bench.telemetry import ProcessTelemetrySampler
from quantum_bench.utils import ensure_directory, load_json, sanitize_token, utc_now_iso, write_json


CSV_FIELDS = [
    "timestamp_utc",
    "profile_name",
    "manifest_id",
    "execution_env",
    "preliminary",
    "host",
    "platform",
    "library",
    "backend",
    "backend_name",
    "device",
    "precision",
    "family",
    "qubits",
    "depth",
    "seed",
    "repeat_index",
    "warmup",
    "thread_mode",
    "timeout_s",
    "wall_s",
    "cpu_s",
    "peak_rss_mb",
    "gpu_peak_mem_mb",
    "gpu_peak_util_pct",
    "estimated_statevector_mb",
    "state_fidelity_ref",
    "trace_distance_ref",
    "success",
    "error",
    "error_type",
    "python_executable",
    "python_version",
    "driver_version",
    "cuda_version",
    "qiskit_version",
    "qiskit_aer_version",
    "qulacs_version",
    "pennylane_version",
    "pennylane_lightning_version",
    "op_total",
    "op_h",
    "op_rx",
    "op_ry",
    "op_rz",
    "op_cx",
    "op_cp",
    "op_swap",
    "logical_depth",
]


def _bytes_to_mb(value: int | None) -> float | None:
    if value is None:
        return None
    return round(value / (1024 * 1024), 3)


def _estimate_statevector_bytes(case: BenchmarkCase) -> int:
    bytes_per_amplitude = 8 if case.precision == "single" else 16
    return int((2 ** case.qubits) * bytes_per_amplitude * case.overhead_factor)


def _resource_limit_error(case: BenchmarkCase) -> str | None:
    estimate = _estimate_statevector_bytes(case)
    if case.device == "GPU" and case.vram_limit_bytes and estimate > case.vram_limit_bytes:
        return f"estimated_statevector_bytes_exceeds_vram_limit:{estimate}>{case.vram_limit_bytes}"
    if case.device == "CPU" and case.ram_limit_bytes and estimate > case.ram_limit_bytes:
        return f"estimated_statevector_bytes_exceeds_ram_limit:{estimate}>{case.ram_limit_bytes}"
    return None


def _thread_env(case: BenchmarkCase) -> dict[str, str]:
    env = os.environ.copy()
    cpu_total = os.cpu_count() or 1
    target_threads = 1 if case.thread_mode == "single" else cpu_total
    env["OMP_NUM_THREADS"] = str(target_threads)
    env["QULACS_NUM_THREADS"] = str(target_threads)
    return env


def run_child_case(case: BenchmarkCase) -> dict[str, Any]:
    env_report = build_env_report()
    ops = build_recipe(case.family, case.qubits, case.depth, case.seed)
    op_counts = recipe_counts(ops)
    depth = logical_depth(case.qubits, ops)
    sampler = ProcessTelemetrySampler(sample_interval_s=case.sample_interval_s)

    start_wall = time.perf_counter()
    start_cpu = time.process_time()
    sampler.start()
    backend_name = None
    statevector = None
    success = False
    error = None
    error_type = None
    extra: dict[str, Any] = {}

    try:
        result = run_backend_case(case, ops)
        backend_name = result.backend_name
        statevector = result.statevector
        extra = result.extra
        success = True
    except UnsupportedCaseError as exc:
        error = str(exc)
        error_type = "unsupported_case"
    except MissingDependencyError as exc:
        error = str(exc)
        error_type = "missing_dependency"
    except Exception as exc:  # pragma: no cover
        error = str(exc)
        error_type = type(exc).__name__

    telemetry = sampler.stop()
    wall_s = time.perf_counter() - start_wall
    cpu_s = time.process_time() - start_cpu

    fidelity = None
    trace_distance = None
    if success and statevector is not None and case.qubits <= case.max_reference_qubits:
        reference = reference_statevector(case.qubits, ops)
        fidelity = round(state_fidelity(reference, statevector), 10)
        trace_distance = round(trace_distance_pure(reference, statevector), 10)

    return {
        "timestamp_utc": utc_now_iso(),
        "profile_name": case.profile_name,
        "manifest_id": case.manifest_id,
        "execution_env": case.execution_env,
        "preliminary": case.preliminary,
        "host": env_report["host"]["hostname"],
        "platform": env_report["host"]["platform"],
        "library": case.library,
        "backend": case.backend,
        "backend_name": backend_name,
        "device": case.device,
        "precision": case.precision,
        "family": case.family,
        "qubits": case.qubits,
        "depth": case.depth,
        "seed": case.seed,
        "repeat_index": case.repeat_index,
        "warmup": case.warmup,
        "thread_mode": case.thread_mode,
        "timeout_s": case.timeout_s,
        "wall_s": round(wall_s, 10),
        "cpu_s": round(cpu_s, 10),
        "peak_rss_mb": _bytes_to_mb(telemetry["peak_rss_bytes"]),
        "gpu_peak_mem_mb": _bytes_to_mb(telemetry["gpu_peak_mem_bytes"]),
        "gpu_peak_util_pct": telemetry["gpu_peak_util_percent"],
        "estimated_statevector_mb": _bytes_to_mb(_estimate_statevector_bytes(case)),
        "state_fidelity_ref": fidelity,
        "trace_distance_ref": trace_distance,
        "success": success,
        "error": error,
        "error_type": error_type,
        "python_executable": sys.executable,
        "python_version": platform.python_version(),
        "driver_version": env_report["gpu"].get("driver_version"),
        "cuda_version": env_report.get("cuda_version"),
        "qiskit_version": env_report["package_versions"].get("qiskit"),
        "qiskit_aer_version": env_report["package_versions"].get("qiskit-aer"),
        "qulacs_version": env_report["package_versions"].get("qulacs"),
        "pennylane_version": env_report["package_versions"].get("pennylane"),
        "pennylane_lightning_version": env_report["package_versions"].get("pennylane-lightning"),
        "logical_depth": depth,
        **op_counts,
        **extra,
    }


def invoke_case_subprocess(case: BenchmarkCase) -> dict[str, Any]:
    limit_error = _resource_limit_error(case)
    if limit_error:
        return {
            "timestamp_utc": utc_now_iso(),
            "profile_name": case.profile_name,
            "manifest_id": case.manifest_id,
            "execution_env": case.execution_env,
            "preliminary": case.preliminary,
            "host": platform.node(),
            "platform": platform.platform(),
            "library": case.library,
            "backend": case.backend,
            "backend_name": None,
            "device": case.device,
            "precision": case.precision,
            "family": case.family,
            "qubits": case.qubits,
            "depth": case.depth,
            "seed": case.seed,
            "repeat_index": case.repeat_index,
            "warmup": case.warmup,
            "thread_mode": case.thread_mode,
            "timeout_s": case.timeout_s,
            "wall_s": None,
            "cpu_s": None,
            "peak_rss_mb": None,
            "gpu_peak_mem_mb": None,
            "gpu_peak_util_pct": None,
            "estimated_statevector_mb": _bytes_to_mb(_estimate_statevector_bytes(case)),
            "state_fidelity_ref": None,
            "trace_distance_ref": None,
            "success": False,
            "error": limit_error,
            "error_type": "estimated_limit",
            "python_executable": sys.executable,
            "python_version": platform.python_version(),
            "driver_version": None,
            "cuda_version": None,
            "qiskit_version": None,
            "qiskit_aer_version": None,
            "qulacs_version": None,
            "pennylane_version": None,
            "pennylane_lightning_version": None,
            "op_total": None,
            "op_h": None,
            "op_rx": None,
            "op_ry": None,
            "op_rz": None,
            "op_cx": None,
            "op_cp": None,
            "op_swap": None,
            "logical_depth": None,
        }

    with tempfile.TemporaryDirectory(prefix="quantum-bench-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        payload_path = temp_dir / "case.json"
        output_path = temp_dir / "row.json"
        write_json(payload_path, case.to_dict())

        command = [
            sys.executable,
            "-m",
            "quantum_bench",
            "_internal-run-case",
            "--payload",
            str(payload_path),
            "--output",
            str(output_path),
        ]

        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=case.timeout_s,
                env=_thread_env(case),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return {
                "timestamp_utc": utc_now_iso(),
                "profile_name": case.profile_name,
                "manifest_id": case.manifest_id,
                "execution_env": case.execution_env,
                "preliminary": case.preliminary,
                "host": platform.node(),
                "platform": platform.platform(),
                "library": case.library,
                "backend": case.backend,
                "backend_name": None,
                "device": case.device,
                "precision": case.precision,
                "family": case.family,
                "qubits": case.qubits,
                "depth": case.depth,
                "seed": case.seed,
                "repeat_index": case.repeat_index,
                "warmup": case.warmup,
                "thread_mode": case.thread_mode,
                "timeout_s": case.timeout_s,
                "wall_s": round(time.perf_counter() - started, 10),
                "cpu_s": None,
                "peak_rss_mb": None,
                "gpu_peak_mem_mb": None,
                "gpu_peak_util_pct": None,
                "estimated_statevector_mb": _bytes_to_mb(_estimate_statevector_bytes(case)),
                "state_fidelity_ref": None,
                "trace_distance_ref": None,
                "success": False,
                "error": f"timeout_after_{case.timeout_s}s:{exc}",
                "error_type": "timeout",
                "python_executable": sys.executable,
                "python_version": platform.python_version(),
                "driver_version": None,
                "cuda_version": None,
                "qiskit_version": None,
                "qiskit_aer_version": None,
                "qulacs_version": None,
                "pennylane_version": None,
                "pennylane_lightning_version": None,
                "op_total": None,
                "op_h": None,
                "op_rx": None,
                "op_ry": None,
                "op_rz": None,
                "op_cx": None,
                "op_cp": None,
                "op_swap": None,
                "logical_depth": None,
            }

        if completed.returncode != 0 and not output_path.exists():
            return {
                "timestamp_utc": utc_now_iso(),
                "profile_name": case.profile_name,
                "manifest_id": case.manifest_id,
                "execution_env": case.execution_env,
                "preliminary": case.preliminary,
                "host": platform.node(),
                "platform": platform.platform(),
                "library": case.library,
                "backend": case.backend,
                "backend_name": None,
                "device": case.device,
                "precision": case.precision,
                "family": case.family,
                "qubits": case.qubits,
                "depth": case.depth,
                "seed": case.seed,
                "repeat_index": case.repeat_index,
                "warmup": case.warmup,
                "thread_mode": case.thread_mode,
                "timeout_s": case.timeout_s,
                "wall_s": round(time.perf_counter() - started, 10),
                "cpu_s": None,
                "peak_rss_mb": None,
                "gpu_peak_mem_mb": None,
                "gpu_peak_util_pct": None,
                "estimated_statevector_mb": _bytes_to_mb(_estimate_statevector_bytes(case)),
                "state_fidelity_ref": None,
                "trace_distance_ref": None,
                "success": False,
                "error": (completed.stderr or completed.stdout or "child_case_failed").strip(),
                "error_type": f"subprocess_exit_{completed.returncode}",
                "python_executable": sys.executable,
                "python_version": platform.python_version(),
                "driver_version": None,
                "cuda_version": None,
                "qiskit_version": None,
                "qiskit_aer_version": None,
                "qulacs_version": None,
                "pennylane_version": None,
                "pennylane_lightning_version": None,
                "op_total": None,
                "op_h": None,
                "op_rx": None,
                "op_ry": None,
                "op_rz": None,
                "op_cx": None,
                "op_cp": None,
                "op_swap": None,
                "logical_depth": None,
            }

        return load_json(output_path)


def run_profile(
    profile: dict[str, Any],
    cases: list[BenchmarkCase],
    destination_dir: Path,
    capability_report: dict[str, Any] | None,
) -> dict[str, Any]:
    ensure_directory(destination_dir)
    env_report = build_env_report()
    write_json(destination_dir / "env-report.json", env_report)
    if capability_report is not None:
        write_json(destination_dir / "capability-report.json", capability_report)

    manifest = {
        "timestamp_utc": utc_now_iso(),
        "profile_name": profile.get("profile_name"),
        "execution_env": profile.get("execution_env"),
        "preliminary": profile.get("preliminary"),
        "result_directory": str(destination_dir),
        "case_count": len(cases),
    }
    write_json(destination_dir / "manifest.json", manifest)

    rows = []
    csv_path = destination_dir / "results.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for index, case in enumerate(cases, start=1):
            row = invoke_case_subprocess(case)
            rows.append(row)
            writer.writerow(row)
            print(
                f"[{index}/{len(cases)}] {case.library} {case.family} {case.qubits}q "
                f"{case.device} {case.precision} {case.thread_mode} "
                f"{'warmup' if case.warmup else f'rep{case.repeat_index}'} -> "
                f"{'ok' if row.get('success') else 'fail'}"
            )

    json_path = destination_dir / "results.json"
    json_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False, default=json_default), encoding="utf-8")

    return {
        "manifest": manifest,
        "env_report": env_report,
        "result_dir": str(destination_dir),
        "rows": rows,
    }


def build_result_dir(base_dir: str | Path, profile_name: str, execution_env: str) -> Path:
    timestamp = utc_now_iso().replace(":", "").replace("+00:00", "Z")
    name = f"{sanitize_token(profile_name)}-{sanitize_token(execution_env)}-{timestamp}"
    return ensure_directory(Path(base_dir) / name)


def write_child_output(payload_path: str | Path, output_path: str | Path) -> None:
    case = BenchmarkCase(**load_json(Path(payload_path)))
    row = run_child_case(case)
    write_json(Path(output_path), row)
