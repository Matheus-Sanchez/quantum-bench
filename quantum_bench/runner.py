from __future__ import annotations

import csv
import json
import math
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from quantum_bench.adapters import create_backend_group_runner, run_backend_case
from quantum_bench.artifacts import enrich_error_accumulation, enrich_speedups, write_run_artifacts
from quantum_bench.env_report import build_env_report
from quantum_bench.models import BenchmarkCase, MissingDependencyError, UnsupportedCaseError, json_default
from quantum_bench.probability import (
    distribution_from_counts,
    distribution_from_statevector,
    distribution_metrics,
    distribution_shape_metrics,
)
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
    "variant",
    "backend_name",
    "sim_method",
    "execution_mode",
    "executor",
    "output_mode",
    "output_qubits",
    "effective_output_qubits",
    "shots",
    "noise_profile",
    "backend_options",
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
    "backend_init_s",
    "transpile_s",
    "simulate_s",
    "extract_s",
    "wall_s",
    "cpu_s",
    "peak_rss_mb",
    "gpu_peak_mem_mb",
    "gpu_peak_util_pct",
    "estimated_statevector_mb",
    "speedup_vs_cpu",
    "speedup_vs_gpu_thrust",
    "state_fidelity_ref",
    "trace_distance_ref",
    "prob_l1_ref",
    "tvd_ref",
    "hellinger_ref",
    "jsd_ref",
    "tvd_noisy_vs_ideal",
    "hellinger_noisy_vs_ideal",
    "jsd_noisy_vs_ideal",
    "dominant_state_mass",
    "topk_probability_mass",
    "error_slope_vs_depth",
    "depth_at_tvd_1pct",
    "depth_at_tvd_5pct",
    "depth_at_jsd_threshold",
    "success",
    "error",
    "error_type",
    "cuStateVec_requested",
    "cuStateVec_effective",
    "cuStateVec_effective_source",
    "tensor_network_requested",
    "tensor_network_effective",
    "tensor_network_effective_source",
    "cusvaer_requested",
    "cusvaer_effective",
    "cusvaer_effective_source",
    "container_image",
    "qiskit_aer_available_methods",
    "qiskit_aer_available_devices",
    "cuquantum_version",
    "backend_metadata_json",
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
    "one_qubit_gate_count",
    "two_qubit_gate_count",
    "entangling_gate_count",
    "two_qubit_gate_density",
    "entangling_depth_estimate",
    "logical_depth",
]


def _bytes_to_mb(value: int | None) -> float | None:
    if value is None:
        return None
    return round(value / (1024 * 1024), 3)


def _sanitize_text(value: str) -> str:
    return value.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")


def _csv_safe_row(row: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, str):
            safe[key] = _sanitize_text(value)
        else:
            safe[key] = value
    return safe


def _json_cell(value: Any) -> str | None:
    if value in (None, [], {}):
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=json_default)


def _estimate_statevector_bytes(case: BenchmarkCase) -> int:
    bytes_per_amplitude = 8 if case.precision == "single" else 16
    return int((2 ** case.qubits) * bytes_per_amplitude * case.overhead_factor)


def _resource_limit_error(case: BenchmarkCase) -> str | None:
    if case.sim_method == "tensor_network":
        return None
    estimate = _estimate_statevector_bytes(case)
    if case.device == "GPU" and case.vram_limit_bytes and estimate > case.vram_limit_bytes:
        return f"estimated_statevector_bytes_exceeds_vram_limit:{estimate}>{case.vram_limit_bytes}"
    if case.device == "CPU" and case.ram_limit_bytes and estimate > case.ram_limit_bytes:
        return f"estimated_statevector_bytes_exceeds_ram_limit:{estimate}>{case.ram_limit_bytes}"
    return None


def _executor_setup_error(case: BenchmarkCase) -> str | None:
    if case.executor != "docker_wsl":
        return None
    if shutil.which("docker") is None:
        return "docker_wsl_missing_dependency:docker executable not found"
    if shutil.which("nvidia-ctk") is None and shutil.which("nvidia-container-cli") is None:
        return "docker_wsl_missing_dependency:NVIDIA Container Toolkit executable not found"
    if not case.backend_options.get("container_image"):
        return "docker_wsl_missing_configuration:backend_options.container_image is required"
    return None


def _case_identity(case: BenchmarkCase) -> dict[str, Any]:
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
        "variant": case.variant,
        "backend_name": None,
        "sim_method": case.sim_method,
        "execution_mode": case.execution_mode,
        "executor": case.executor,
        "output_mode": case.output_mode,
        "output_qubits": _json_cell(case.output_qubits),
        "effective_output_qubits": None,
        "shots": case.shots,
        "noise_profile": case.noise_profile,
        "backend_options": _json_cell(case.backend_options),
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
        "backend_init_s": None,
        "transpile_s": None,
        "simulate_s": None,
        "extract_s": None,
        "wall_s": None,
        "cpu_s": None,
        "peak_rss_mb": None,
        "gpu_peak_mem_mb": None,
        "gpu_peak_util_pct": None,
        "estimated_statevector_mb": _bytes_to_mb(_estimate_statevector_bytes(case)),
        "speedup_vs_cpu": None,
        "speedup_vs_gpu_thrust": None,
        "state_fidelity_ref": None,
        "trace_distance_ref": None,
        "prob_l1_ref": None,
        "tvd_ref": None,
        "hellinger_ref": None,
        "jsd_ref": None,
        "tvd_noisy_vs_ideal": None,
        "hellinger_noisy_vs_ideal": None,
        "jsd_noisy_vs_ideal": None,
        "dominant_state_mass": None,
        "topk_probability_mass": None,
        "error_slope_vs_depth": None,
        "depth_at_tvd_1pct": None,
        "depth_at_tvd_5pct": None,
        "depth_at_jsd_threshold": None,
        "success": False,
        "error": None,
        "error_type": None,
        "cuStateVec_requested": bool(case.backend_options.get("cuStateVec_enable") is True or case.variant == "gpu_custatevec"),
        "cuStateVec_effective": None,
        "cuStateVec_effective_source": None,
        "tensor_network_requested": bool(case.sim_method == "tensor_network" or case.variant in {"gpu_tensornetwork", "appliance_tensornetwork"}),
        "tensor_network_effective": None,
        "tensor_network_effective_source": None,
        "cusvaer_requested": bool(case.backend_options.get("cusvaer_enable") is True or case.variant == "appliance_cusvaer"),
        "cusvaer_effective": None,
        "cusvaer_effective_source": None,
        "container_image": case.backend_options.get("container_image"),
        "qiskit_aer_available_methods": None,
        "qiskit_aer_available_devices": None,
        "cuquantum_version": None,
        "backend_metadata_json": None,
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
        "one_qubit_gate_count": None,
        "two_qubit_gate_count": None,
        "entangling_gate_count": None,
        "two_qubit_gate_density": None,
        "entangling_depth_estimate": None,
        "logical_depth": None,
    }


def build_error_row(
    case: BenchmarkCase,
    *,
    error_type: str,
    error: str,
    wall_s: float | None = None,
) -> dict[str, Any]:
    row = _case_identity(case)
    row["error_type"] = error_type
    row["error"] = _sanitize_text(error)
    row["wall_s"] = round(wall_s, 10) if wall_s is not None else None
    return row


def _frontier_group_key(case: BenchmarkCase) -> tuple[str, str, str, str, str, str, str, str]:
    return (case.variant, case.library, case.backend, case.device, case.precision, case.family, case.output_mode, case.noise_profile)


def _persistent_group_key(case: BenchmarkCase) -> tuple[str, str, int, int, int, str, str, str, str, str, str]:
    return (
        case.variant,
        case.family,
        case.qubits,
        case.depth,
        case.seed,
        case.precision,
        case.thread_mode,
        case.output_mode,
        case.noise_profile,
        case.sim_method,
        case.executor,
    )


def _should_stop_frontier_after_failure(row: dict[str, Any]) -> bool:
    if row.get("success"):
        return False
    error_type = str(row.get("error_type") or "")
    return error_type not in {"frontier_pruned"}


def _row_status_label(row: dict[str, Any]) -> str:
    if row.get("success"):
        return "ok"
    error_type = str(row.get("error_type") or "")
    if error_type == "frontier_pruned":
        return "pruned"
    if error_type == "estimated_limit":
        return "skip"
    return "fail"


def _thread_env(case: BenchmarkCase) -> dict[str, str]:
    env = os.environ.copy()
    cpu_total = os.cpu_count() or 1
    target_threads = 1 if case.thread_mode == "single" else cpu_total
    env["OMP_NUM_THREADS"] = str(target_threads)
    env["QULACS_NUM_THREADS"] = str(target_threads)
    return env


def _prepare_case_context(case: BenchmarkCase) -> tuple[list[tuple], dict[str, int | float], int]:
    ops = build_recipe(case.family, case.qubits, case.depth, case.seed)
    op_counts = recipe_counts(ops)
    depth = logical_depth(case.qubits, ops)
    return ops, op_counts, depth


def _json_list_cell(value: Any) -> list[int] | None:
    if value in (None, "", "None"):
        return None
    if isinstance(value, list):
        return [int(item) for item in value]
    try:
        loaded = json.loads(str(value))
    except (TypeError, ValueError):
        return None
    if isinstance(loaded, list):
        return [int(item) for item in loaded]
    return None


def _effective_output_qubits(case: BenchmarkCase, result: Any) -> list[int] | None:
    raw = result.extra.get("effective_output_qubits") if result.extra else None
    parsed = _json_list_cell(raw)
    if parsed is not None:
        return parsed
    if case.output_qubits is not None:
        return [qubit for qubit in case.output_qubits if 0 <= qubit < case.qubits]
    return None


def _case_probability_distribution(case: BenchmarkCase, result: Any, output_qubits: list[int] | None) -> list[float] | None:
    if result.probabilities is not None:
        return [float(value) for value in result.probabilities]
    if result.counts is not None:
        return distribution_from_counts(result.counts, total_qubits=case.qubits, output_qubits=output_qubits)
    if result.statevector is not None and case.qubits <= case.max_reference_qubits:
        selected = None if case.output_mode == "statevector" else output_qubits
        return distribution_from_statevector(result.statevector, total_qubits=case.qubits, output_qubits=selected)
    return None


def _reference_distribution(case: BenchmarkCase, ops: list[tuple], output_qubits: list[int] | None) -> tuple[list[complex] | None, list[float] | None]:
    if case.qubits > case.max_reference_qubits:
        return None, None
    reference = reference_statevector(case.qubits, ops)
    selected = None if case.output_mode == "statevector" else output_qubits
    probabilities = distribution_from_statevector(reference, total_qubits=case.qubits, output_qubits=selected)
    return reference, probabilities


def _build_success_row(
    case: BenchmarkCase,
    env_report: dict[str, Any],
    op_counts: dict[str, Any],
    depth: int,
    telemetry: dict[str, Any],
    wall_s: float,
    cpu_s: float,
    result: Any,
) -> dict[str, Any]:
    fidelity = None
    trace_distance = None
    ops = build_recipe(case.family, case.qubits, case.depth, case.seed)
    output_qubits = _effective_output_qubits(case, result)
    probabilities = _case_probability_distribution(case, result, output_qubits)
    reference, reference_probabilities = _reference_distribution(case, ops, output_qubits)
    if result.statevector is not None and reference is not None:
        fidelity = round(state_fidelity(reference, result.statevector), 10)
        trace_distance = round(trace_distance_pure(reference, result.statevector), 10)
    ref_metrics = distribution_metrics(probabilities, reference_probabilities)
    shape_metrics = distribution_shape_metrics(probabilities)
    noisy_metrics = {"tvd": None, "hellinger": None, "jsd": None}
    if case.noise_profile not in ("", None, "none"):
        noisy_metrics = ref_metrics

    metadata_json = result.extra.get("backend_metadata_json") if result.extra else None
    if metadata_json is None and getattr(result, "metadata", None):
        metadata_json = _json_cell(result.metadata)

    row = {
        "timestamp_utc": utc_now_iso(),
        "profile_name": case.profile_name,
        "manifest_id": case.manifest_id,
        "execution_env": case.execution_env,
        "preliminary": case.preliminary,
        "host": env_report["host"]["hostname"],
        "platform": env_report["host"]["platform"],
        "library": case.library,
        "backend": case.backend,
        "variant": case.variant,
        "backend_name": result.backend_name,
        "sim_method": case.sim_method,
        "execution_mode": case.execution_mode,
        "executor": case.executor,
        "output_mode": case.output_mode,
        "output_qubits": _json_cell(case.output_qubits),
        "effective_output_qubits": result.extra.get("effective_output_qubits") if result.extra else _json_cell(output_qubits),
        "shots": case.shots,
        "noise_profile": case.noise_profile,
        "backend_options": _json_cell(case.backend_options),
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
        "backend_init_s": result.timings.get("backend_init_s") if result.timings else None,
        "transpile_s": result.timings.get("transpile_s") if result.timings else None,
        "simulate_s": result.timings.get("simulate_s") if result.timings else None,
        "extract_s": result.timings.get("extract_s") if result.timings else None,
        "wall_s": round(wall_s, 10),
        "cpu_s": round(cpu_s, 10),
        "peak_rss_mb": _bytes_to_mb(telemetry["peak_rss_bytes"]),
        "gpu_peak_mem_mb": _bytes_to_mb(telemetry["gpu_peak_mem_bytes"]),
        "gpu_peak_util_pct": telemetry["gpu_peak_util_percent"],
        "estimated_statevector_mb": _bytes_to_mb(_estimate_statevector_bytes(case)),
        "speedup_vs_cpu": None,
        "speedup_vs_gpu_thrust": None,
        "state_fidelity_ref": fidelity,
        "trace_distance_ref": trace_distance,
        "prob_l1_ref": ref_metrics["prob_l1"],
        "tvd_ref": ref_metrics["tvd"],
        "hellinger_ref": ref_metrics["hellinger"],
        "jsd_ref": ref_metrics["jsd"],
        "tvd_noisy_vs_ideal": noisy_metrics["tvd"],
        "hellinger_noisy_vs_ideal": noisy_metrics["hellinger"],
        "jsd_noisy_vs_ideal": noisy_metrics["jsd"],
        "dominant_state_mass": shape_metrics["dominant_state_mass"],
        "topk_probability_mass": shape_metrics["topk_probability_mass"],
        "error_slope_vs_depth": None,
        "depth_at_tvd_1pct": None,
        "depth_at_tvd_5pct": None,
        "depth_at_jsd_threshold": None,
        "success": True,
        "error": None,
        "error_type": None,
        "cuStateVec_requested": result.extra.get("cuStateVec_requested") if result.extra else None,
        "cuStateVec_effective": result.extra.get("cuStateVec_effective") if result.extra else None,
        "cuStateVec_effective_source": result.extra.get("cuStateVec_effective_source") if result.extra else None,
        "tensor_network_requested": result.extra.get("tensor_network_requested") if result.extra else None,
        "tensor_network_effective": result.extra.get("tensor_network_effective") if result.extra else None,
        "tensor_network_effective_source": result.extra.get("tensor_network_effective_source") if result.extra else None,
        "cusvaer_requested": result.extra.get("cusvaer_requested") if result.extra else None,
        "cusvaer_effective": result.extra.get("cusvaer_effective") if result.extra else None,
        "cusvaer_effective_source": result.extra.get("cusvaer_effective_source") if result.extra else None,
        "container_image": result.extra.get("container_image") if result.extra else case.backend_options.get("container_image"),
        "qiskit_aer_available_methods": result.extra.get("qiskit_aer_available_methods") if result.extra else None,
        "qiskit_aer_available_devices": result.extra.get("qiskit_aer_available_devices") if result.extra else None,
        "cuquantum_version": result.extra.get("cuquantum_version") if result.extra else env_report["package_versions"].get("cuquantum"),
        "backend_metadata_json": metadata_json,
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
        "entangling_depth_estimate": depth,
        **op_counts,
        **(result.extra or {}),
    }
    if probabilities is not None and len(probabilities) <= 4096:
        row["_probabilities"] = probabilities
        row["_probability_width"] = int(math.log2(len(probabilities))) if probabilities else 0
    return row


def _materialize_aborted_rows(cases: list[BenchmarkCase], *, error_type: str, error: str) -> list[dict[str, Any]]:
    return [build_error_row(case, error_type=error_type, error=error) for case in cases]


def _execute_single_case(
    case: BenchmarkCase,
    *,
    env_report: dict[str, Any],
    backend_runner: Any = None,
) -> dict[str, Any]:
    executor_error = _executor_setup_error(case)
    if executor_error:
        return build_error_row(case, error_type="unsupported_case", error=executor_error)
    ops, op_counts, depth = _prepare_case_context(case)
    sampler = ProcessTelemetrySampler(sample_interval_s=case.sample_interval_s)
    started_wall = time.perf_counter()
    started_cpu = time.process_time()
    sampler.start()

    try:
        result = backend_runner.run_case(case, ops) if backend_runner is not None else run_backend_case(case, ops)
    except UnsupportedCaseError as exc:
        telemetry = sampler.stop()
        row = build_error_row(case, error_type="unsupported_case", error=str(exc), wall_s=time.perf_counter() - started_wall)
        row["cpu_s"] = round(time.process_time() - started_cpu, 10)
        row["peak_rss_mb"] = _bytes_to_mb(telemetry["peak_rss_bytes"])
        row["gpu_peak_mem_mb"] = _bytes_to_mb(telemetry["gpu_peak_mem_bytes"])
        row["gpu_peak_util_pct"] = telemetry["gpu_peak_util_percent"]
        return row
    except MissingDependencyError as exc:
        telemetry = sampler.stop()
        row = build_error_row(case, error_type="missing_dependency", error=str(exc), wall_s=time.perf_counter() - started_wall)
        row["cpu_s"] = round(time.process_time() - started_cpu, 10)
        row["peak_rss_mb"] = _bytes_to_mb(telemetry["peak_rss_bytes"])
        row["gpu_peak_mem_mb"] = _bytes_to_mb(telemetry["gpu_peak_mem_bytes"])
        row["gpu_peak_util_pct"] = telemetry["gpu_peak_util_percent"]
        return row
    except Exception as exc:  # pragma: no cover
        telemetry = sampler.stop()
        row = build_error_row(
            case,
            error_type=type(exc).__name__,
            error=str(exc),
            wall_s=time.perf_counter() - started_wall,
        )
        row["cpu_s"] = round(time.process_time() - started_cpu, 10)
        row["peak_rss_mb"] = _bytes_to_mb(telemetry["peak_rss_bytes"])
        row["gpu_peak_mem_mb"] = _bytes_to_mb(telemetry["gpu_peak_mem_bytes"])
        row["gpu_peak_util_pct"] = telemetry["gpu_peak_util_percent"]
        return row

    telemetry = sampler.stop()
    wall_s = time.perf_counter() - started_wall
    cpu_s = time.process_time() - started_cpu
    return _build_success_row(case, env_report, op_counts, depth, telemetry, wall_s, cpu_s, result)


def run_child_cases(cases: list[BenchmarkCase]) -> list[dict[str, Any]]:
    if not cases:
        return []

    if cases[0].executor == "docker_wsl":
        return [invoke_case_docker(case) for case in cases]

    env_report = build_env_report()
    if any(_executor_setup_error(case) for case in cases):
        return [
            build_error_row(case, error_type="unsupported_case", error=_executor_setup_error(case) or "unsupported_executor")
            for case in cases
        ]
    if len(cases) == 1:
        limit_error = _resource_limit_error(cases[0])
        if limit_error:
            return [build_error_row(cases[0], error_type="estimated_limit", error=limit_error)]
        return [_execute_single_case(cases[0], env_report=env_report)]

    if any(_resource_limit_error(case) for case in cases):
        rows: list[dict[str, Any]] = []
        for case in cases:
            limit_error = _resource_limit_error(case)
            if limit_error:
                rows.append(build_error_row(case, error_type="estimated_limit", error=limit_error))
            else:
                rows.append(build_error_row(case, error_type="group_aborted_after_warmup_failure", error="group_aborted_after_estimated_limit"))
        return rows

    backend_runner = None
    try:
        backend_runner = create_backend_group_runner(cases[0])
    except UnsupportedCaseError as exc:
        return [build_error_row(cases[0], error_type="unsupported_case", error=str(exc)), *_materialize_aborted_rows(cases[1:], error_type="group_aborted_after_warmup_failure", error="group_aborted_after_runner_setup_failure")]
    except MissingDependencyError as exc:
        return [build_error_row(cases[0], error_type="missing_dependency", error=str(exc)), *_materialize_aborted_rows(cases[1:], error_type="group_aborted_after_warmup_failure", error="group_aborted_after_runner_setup_failure")]
    except Exception as exc:  # pragma: no cover
        return [build_error_row(cases[0], error_type=type(exc).__name__, error=str(exc)), *_materialize_aborted_rows(cases[1:], error_type="group_aborted_after_warmup_failure", error="group_aborted_after_runner_setup_failure")]

    rows: list[dict[str, Any]] = []
    for index, case in enumerate(cases):
        row = _execute_single_case(case, env_report=env_report, backend_runner=backend_runner)
        rows.append(row)
        if not row.get("success"):
            aborted_error_type = "group_aborted_after_warmup_failure" if case.warmup else "group_aborted_after_repeat_failure"
            aborted_error = f"group_aborted_after_failure_at_{case.manifest_id}"
            rows.extend(_materialize_aborted_rows(cases[index + 1 :], error_type=aborted_error_type, error=aborted_error))
            break

    return rows


def run_child_case(case: BenchmarkCase) -> dict[str, Any]:
    return run_child_cases([case])[0]


def invoke_case_subprocess(case: BenchmarkCase) -> dict[str, Any]:
    if case.executor == "docker_wsl":
        return invoke_case_docker(case)
    executor_error = _executor_setup_error(case)
    if executor_error:
        return build_error_row(case, error_type="unsupported_case", error=executor_error)
    limit_error = _resource_limit_error(case)
    if limit_error:
        return build_error_row(case, error_type="estimated_limit", error=limit_error)

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
            return build_error_row(
                case,
                error_type="timeout",
                error=f"timeout_after_{case.timeout_s}s:{exc}",
                wall_s=time.perf_counter() - started,
            )

        if completed.returncode != 0 and not output_path.exists():
            return build_error_row(
                case,
                error_type=f"subprocess_exit_{completed.returncode}",
                error=(completed.stderr or completed.stdout or "child_case_failed").strip(),
                wall_s=time.perf_counter() - started,
            )

        return load_json(output_path)


def invoke_case_docker(case: BenchmarkCase) -> dict[str, Any]:
    executor_error = _executor_setup_error(case)
    if executor_error:
        return build_error_row(case, error_type="unsupported_case", error=executor_error)

    image = str(case.backend_options.get("container_image"))
    repo_root = Path.cwd().resolve()
    with tempfile.TemporaryDirectory(prefix="quantum-bench-docker-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        payload_path = temp_dir / "case.json"
        output_path = temp_dir / "row.json"
        payload = case.to_dict()
        payload["executor"] = "wsl_python"
        write_json(payload_path, payload)
        command = [
            "docker",
            "run",
            "--rm",
            "--gpus",
            "all",
            "-v",
            f"{repo_root}:/workspace",
            "-v",
            f"{temp_dir.resolve()}:/payload",
            "-w",
            "/workspace",
            image,
            "python",
            "-m",
            "quantum_bench",
            "_internal-run-case",
            "--payload",
            "/payload/case.json",
            "--output",
            "/payload/row.json",
        ]
        started = time.perf_counter()
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=case.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return build_error_row(
                case,
                error_type="timeout",
                error=f"docker_timeout_after_{case.timeout_s}s:{exc}",
                wall_s=time.perf_counter() - started,
            )
        if output_path.exists():
            row = load_json(output_path)
            row["executor"] = "docker_wsl"
            row["container_image"] = image
            return row
        return build_error_row(
            case,
            error_type=f"docker_exit_{completed.returncode}",
            error=(completed.stderr or completed.stdout or "docker_case_failed").strip(),
            wall_s=time.perf_counter() - started,
        )


def invoke_case_group_subprocess(cases: list[BenchmarkCase]) -> list[dict[str, Any]]:
    if not cases:
        return []

    if cases[0].executor == "docker_wsl":
        return [invoke_case_docker(case) for case in cases]

    if any(_executor_setup_error(case) for case in cases):
        return [
            build_error_row(case, error_type="unsupported_case", error=_executor_setup_error(case) or "unsupported_executor")
            for case in cases
        ]

    if any(_resource_limit_error(case) for case in cases):
        return [
            build_error_row(case, error_type="estimated_limit", error=_resource_limit_error(case) or "estimated_limit")
            for case in cases
        ]

    with tempfile.TemporaryDirectory(prefix="quantum-bench-group-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        payload_path = temp_dir / "cases.json"
        output_path = temp_dir / "rows.json"
        write_json(payload_path, [case.to_dict() for case in cases])

        command = [
            sys.executable,
            "-m",
            "quantum_bench",
            "_internal-run-group",
            "--payload",
            str(payload_path),
            "--output",
            str(output_path),
        ]

        started = time.perf_counter()
        timeout_s = max(1, sum(case.timeout_s for case in cases))
        try:
            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=timeout_s,
                env=_thread_env(cases[0]),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return [
                build_error_row(cases[0], error_type="timeout", error=f"timeout_after_{timeout_s}s:{exc}", wall_s=time.perf_counter() - started),
                *_materialize_aborted_rows(cases[1:], error_type="group_aborted_after_warmup_failure", error="group_aborted_after_timeout"),
            ]

        if completed.returncode != 0 and not output_path.exists():
            return [
                build_error_row(
                    cases[0],
                    error_type=f"subprocess_exit_{completed.returncode}",
                    error=(completed.stderr or completed.stdout or "child_group_failed").strip(),
                    wall_s=time.perf_counter() - started,
                ),
                *_materialize_aborted_rows(cases[1:], error_type="group_aborted_after_warmup_failure", error="group_aborted_after_subprocess_failure"),
            ]

        payload = load_json(output_path)
        return payload if isinstance(payload, list) else [payload]


def _iter_case_units(cases: list[BenchmarkCase]) -> list[list[BenchmarkCase]]:
    units: list[list[BenchmarkCase]] = []
    index = 0
    while index < len(cases):
        case = cases[index]
        if case.execution_mode == "persistent_group":
            group = [case]
            index += 1
            while index < len(cases) and cases[index].execution_mode == "persistent_group" and _persistent_group_key(cases[index]) == _persistent_group_key(case):
                group.append(cases[index])
                index += 1
            units.append(group)
            continue
        units.append([case])
        index += 1
    return units


def _normalize_unit_rows(unit: list[BenchmarkCase], payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        rows = [payload]
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []

    if len(rows) < len(unit):
        missing_cases = unit[len(rows) :]
        rows.extend(_materialize_aborted_rows(missing_cases, error_type="group_invoker_mismatch", error="group_invoker_returned_too_few_rows"))
    if len(rows) > len(unit):
        rows = rows[: len(unit)]
    return rows


def run_profile(
    profile: dict[str, Any],
    cases: list[BenchmarkCase],
    destination_dir: Path,
    capability_report: dict[str, Any] | None,
    *,
    case_invoker: Callable[[BenchmarkCase], dict[str, Any]] | None = None,
    group_invoker: Callable[[list[BenchmarkCase]], list[dict[str, Any]]] | None = None,
    env_report_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_directory(destination_dir)
    env_report = env_report_override or build_env_report()
    write_json(destination_dir / "env-report.json", env_report)
    if capability_report is not None:
        write_json(destination_dir / "capability-report.json", capability_report)

    manifest = {
        "schema_version": 3,
        "timestamp_utc": utc_now_iso(),
        "profile_name": profile.get("profile_name"),
        "execution_env": profile.get("execution_env"),
        "preliminary": profile.get("preliminary"),
        "result_directory": str(destination_dir),
        "case_count": len(cases),
        "execution_modes": sorted({case.execution_mode for case in cases}),
        "executors": sorted({case.executor for case in cases}),
        "output_modes": sorted({case.output_mode for case in cases}),
        "noise_profiles": sorted({case.noise_profile for case in cases}),
        "variants": sorted({case.variant for case in cases}),
    }
    write_json(destination_dir / "manifest.json", manifest)

    rows: list[dict[str, Any]] = []
    frontier_stop_on_failure = bool(profile.get("frontier_stop_on_failure", False))
    frontier_blocked_qubits: dict[tuple[str, str, str, str, str, str, str, str], int] = {}
    single_invoker = case_invoker or invoke_case_subprocess
    grouped_invoker = group_invoker or invoke_case_group_subprocess
    case_units = _iter_case_units(cases)
    processed = 0
    for unit in case_units:
        first_case = unit[0]
        blocked_qubits = frontier_blocked_qubits.get(_frontier_group_key(first_case))
        if blocked_qubits is not None and first_case.qubits >= blocked_qubits:
            unit_rows = [
                build_error_row(case, error_type="frontier_pruned", error=f"skipped_after_failure_at_{blocked_qubits}q")
                for case in unit
            ]
        else:
            raw_rows = grouped_invoker(unit) if first_case.execution_mode == "persistent_group" else [single_invoker(first_case)]
            unit_rows = _normalize_unit_rows(unit, raw_rows)
            if frontier_stop_on_failure and any(_should_stop_frontier_after_failure(row) for row in unit_rows):
                group_key = _frontier_group_key(first_case)
                previous = frontier_blocked_qubits.get(group_key)
                frontier_blocked_qubits[group_key] = first_case.qubits if previous is None else min(previous, first_case.qubits)

        for case, row in zip(unit, unit_rows):
            processed += 1
            safe_row = _csv_safe_row(row)
            rows.append(safe_row)
            print(
                f"[{processed}/{len(cases)}] {case.variant} {case.family} {case.qubits}q "
                f"{case.device} {case.precision} {case.thread_mode} "
                f"{'warmup' if case.warmup else f'rep{case.repeat_index}'} -> "
                f"{_row_status_label(safe_row)}"
            )

    enrich_speedups(rows)
    accumulation = enrich_error_accumulation(rows)
    artifact_status = write_run_artifacts(rows, destination_dir, accumulation)
    public_rows = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in rows
    ]
    csv_path = destination_dir / "results.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=CSV_FIELDS,
            extrasaction="ignore",
            quoting=csv.QUOTE_MINIMAL,
            escapechar="\\",
        )
        writer.writeheader()
        for row in public_rows:
            writer.writerow(_csv_safe_row(row))
    json_path = destination_dir / "results.json"
    json_path.write_text(json.dumps(public_rows, indent=2, ensure_ascii=False, default=json_default), encoding="utf-8")

    return {
        "manifest": manifest,
        "env_report": env_report,
        "result_dir": str(destination_dir),
        "rows": public_rows,
        "artifact_status": artifact_status,
    }


def build_result_dir(base_dir: str | Path, profile_name: str, execution_env: str) -> Path:
    timestamp = utc_now_iso().replace(":", "").replace("+00:00", "Z")
    name = f"{sanitize_token(profile_name)}-{sanitize_token(execution_env)}-{timestamp}"
    return ensure_directory(Path(base_dir) / name)


def write_child_output(payload_path: str | Path, output_path: str | Path) -> None:
    case = BenchmarkCase(**load_json(Path(payload_path)))
    row = run_child_case(case)
    write_json(Path(output_path), row)


def write_group_output(payload_path: str | Path, output_path: str | Path) -> None:
    cases = [BenchmarkCase(**payload) for payload in load_json(Path(payload_path))]
    rows = run_child_cases(cases)
    write_json(Path(output_path), rows)
