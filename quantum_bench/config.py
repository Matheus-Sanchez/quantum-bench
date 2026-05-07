from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from quantum_bench.capability import build_capability_report
from quantum_bench.models import BenchmarkCase
from quantum_bench.utils import load_json


def load_profile(path: str | Path) -> dict[str, Any]:
    return load_json(Path(path))


def _as_list(value: Any, default: list[Any] | None = None) -> list[Any]:
    if value is None:
        return list(default or [])
    if isinstance(value, list):
        return value
    return [value]


def _qubit_grid_from_capability(entry: dict[str, Any], capability_report: dict[str, Any]) -> list[int]:
    resource = entry["resource"]
    start = int(entry.get("start", 4))
    step = int(entry.get("step", 2))
    cap = entry.get("cap")
    recommended = capability_report["resources"].get(resource, {}).get("recommended_max_qubits")
    if recommended is None:
        return []
    stop = min(int(cap), int(recommended)) if cap is not None else int(recommended)
    if stop < start:
        return []
    return list(range(start, stop + 1, step))


def resolve_qubit_grid(
    qubit_grid: dict[str, Any],
    family: str,
    capability_report: dict[str, Any] | None,
) -> list[int]:
    entry = qubit_grid.get(family)
    if entry is None:
        return []
    if isinstance(entry, list):
        return [int(value) for value in entry]
    if isinstance(entry, dict) and entry.get("source") == "capability_probe":
        report = capability_report or build_capability_report()
        return _qubit_grid_from_capability(entry, report)
    raise ValueError(f"Unsupported qubit grid entry for family {family!r}: {entry!r}")


def profile_requires_capabilities(profile: dict[str, Any]) -> bool:
    for benchmark in profile.get("benchmarks", []):
        qubit_grid = benchmark.get("qubit_grid", {})
        for entry in qubit_grid.values():
            if isinstance(entry, dict) and entry.get("source") == "capability_probe":
                return True
    return False


def _memory_limits(defaults: dict[str, Any], benchmark: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(defaults.get("memory_limits", {}))
    merged.update(benchmark.get("memory_limits", {}))
    return merged


def _timeout_seconds(defaults: dict[str, Any], benchmark: dict[str, Any]) -> int:
    merged = deepcopy(defaults.get("timeouts", {}))
    merged.update(benchmark.get("timeouts", {}))
    return int(merged.get("case_s", 300))


def _memory_source_value(report: dict[str, Any] | None, *, kind: str, source: str) -> int | None:
    report = report or {}
    source_name = source.lower()
    if kind == "ram":
        memory = report.get("system_memory", {})
        if source_name == "total":
            value = memory.get("total_bytes")
        else:
            value = memory.get("available_bytes")
            if value is None:
                value = memory.get("total_bytes")
        return int(value) if value else None

    gpu = report.get("gpu", {})
    if source_name == "total":
        value = gpu.get("total_bytes")
    else:
        value = gpu.get("free_bytes")
        if value is None:
            value = gpu.get("total_bytes")
    return int(value) if value else None


def _backend_options(defaults: dict[str, Any], benchmark: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(defaults.get("backend_options", {}))
    merged.update(benchmark.get("backend_options", {}))
    return merged


def _depth_values(benchmark: dict[str, Any], family: str) -> list[int]:
    raw_depths = benchmark.get("depths", {})
    if isinstance(raw_depths, dict):
        value = raw_depths.get(family, 1)
    else:
        value = raw_depths
    return [int(depth) for depth in _as_list(value, [1])]


def _default_variant(
    *,
    library: str,
    device: str,
    sim_method: str,
    backend_options: dict[str, Any],
) -> str:
    normalized_device = str(device).upper()
    if library == "qiskit_aer" and backend_options.get("cusvaer_enable") is True:
        return "appliance_cusvaer"
    if library == "qiskit_aer" and sim_method == "tensor_network":
        if normalized_device == "GPU":
            return "gpu_tensornetwork"
        return "cpu_tensornetwork"
    if library == "qiskit_aer" and sim_method == "statevector":
        if normalized_device == "CPU":
            return "cpu_statevector"
        if backend_options.get("cuStateVec_enable") is True:
            return "gpu_custatevec"
        return "gpu_thrust"
    return f"{library}_{normalized_device.lower()}_{sim_method}"


def expand_cases(
    profile: dict[str, Any],
    capability_report: dict[str, Any] | None = None,
    limit: int | None = None,
) -> list[BenchmarkCase]:
    defaults = profile.get("defaults", {})
    max_reference_qubits = int(profile.get("max_reference_qubits", defaults.get("max_reference_qubits", 12)))
    cases: list[BenchmarkCase] = []
    generated = 0

    for spec_index, benchmark in enumerate(profile.get("benchmarks", [])):
        library = benchmark["library"]
        backend = benchmark["backend"]
        devices = _as_list(benchmark.get("devices", benchmark.get("device", "CPU")))
        precisions = _as_list(benchmark.get("precisions", benchmark.get("precision", "double")))
        families = _as_list(benchmark.get("families", []))
        warmups = int(benchmark.get("warmups", defaults.get("warmups", 2)))
        repeats = int(benchmark.get("repeats", defaults.get("repeats", 3)))
        thread_modes = _as_list(benchmark.get("thread_modes", defaults.get("thread_modes", ["single", "all"])))
        timeout_s = _timeout_seconds(defaults, benchmark)
        limits = _memory_limits(defaults, benchmark)
        backend_options = _backend_options(defaults, benchmark)
        ram_fraction = float(limits.get("ram_fraction", 0.75))
        vram_fraction = float(limits.get("vram_fraction", 0.80))
        overhead_factor = float(limits.get("overhead_factor", 1.5))
        ram_source = str(limits.get("ram_source", "available"))
        vram_source = str(limits.get("vram_source", "free"))
        seeds = benchmark.get("seeds", {})
        sim_method = str(benchmark.get("sim_method", backend_options.get("method", defaults.get("sim_method", "statevector"))))
        execution_mode = str(benchmark.get("execution_mode", defaults.get("execution_mode", "isolated_frontier")))
        executor = str(benchmark.get("executor", defaults.get("executor", profile.get("executor", "wsl_python"))))
        output_mode = str(benchmark.get("output_mode", defaults.get("output_mode", "statevector")))
        raw_output_qubits = benchmark.get("output_qubits", defaults.get("output_qubits", list(range(8))))
        output_qubits = [int(value) for value in _as_list(raw_output_qubits)] if raw_output_qubits is not None else None
        shots_value = benchmark.get("shots", defaults.get("shots", 4096))
        shots = int(shots_value) if shots_value is not None else None
        noise_profile = str(benchmark.get("noise_profile", defaults.get("noise_profile", "none")))

        ram_total = _memory_source_value(capability_report, kind="ram", source=ram_source)
        ram_limit_bytes = int(ram_total * ram_fraction) if ram_total else None
        gpu_total = _memory_source_value(capability_report, kind="gpu", source=vram_source)
        vram_limit_bytes = int(gpu_total * vram_fraction) if gpu_total else None

        for family in families:
            qubit_values = resolve_qubit_grid(benchmark["qubit_grid"], family, capability_report)
            depth_values = _depth_values(benchmark, family)
            family_seeds = _as_list(seeds.get(family, seeds.get("default", [0])))

            for device in devices:
                active_thread_modes = ["all"] if str(device).upper() == "GPU" else thread_modes
                for precision in precisions:
                    variant = str(
                        benchmark.get(
                            "variant",
                            _default_variant(
                                library=str(library),
                                device=str(device),
                                sim_method=sim_method,
                                backend_options=backend_options,
                            ),
                        )
                    )
                    for qubits in qubit_values:
                        for depth in depth_values:
                            for seed in family_seeds:
                                for thread_mode in active_thread_modes:
                                    total_loops = warmups + repeats
                                    for offset in range(total_loops):
                                        warmup = offset < warmups
                                        repeat_index = offset if warmup else offset - warmups
                                        manifest_id = (
                                            f"{library}-{spec_index}-{family}-{device}-{precision}-{qubits}q-depth{depth}-"
                                            f"seed{seed}-{thread_mode}-{'warmup' if warmup else f'rep{repeat_index}'}"
                                        )
                                        cases.append(
                                            BenchmarkCase(
                                                profile_name=str(profile.get("profile_name", "default")),
                                                execution_env=str(profile["execution_env"]),
                                                preliminary=bool(profile.get("preliminary", False)),
                                                library=str(library),
                                                backend=str(backend),
                                                device=str(device).upper(),
                                                precision=str(precision).lower(),
                                                family=str(family),
                                                qubits=int(qubits),
                                                depth=depth,
                                                seed=int(seed),
                                                repeat_index=repeat_index,
                                                warmup=warmup,
                                                thread_mode=str(thread_mode),
                                                timeout_s=timeout_s,
                                                ram_limit_bytes=ram_limit_bytes,
                                                vram_limit_bytes=vram_limit_bytes,
                                                overhead_factor=overhead_factor,
                                                max_reference_qubits=max_reference_qubits,
                                                manifest_id=manifest_id,
                                                variant=variant,
                                                sim_method=sim_method,
                                                execution_mode=execution_mode,
                                                executor=executor,
                                                backend_options=deepcopy(backend_options),
                                                output_mode=output_mode,
                                                output_qubits=output_qubits[:] if output_qubits is not None else None,
                                                shots=shots,
                                                noise_profile=noise_profile,
                                                metadata={
                                                    "spec_index": spec_index,
                                                    "ram_fraction": ram_fraction,
                                                    "vram_fraction": vram_fraction,
                                                    "ram_source": ram_source,
                                                    "vram_source": vram_source,
                                                },
                                            )
                                        )
                                        generated += 1
                                        if limit is not None and generated >= limit:
                                            return cases
    return cases
