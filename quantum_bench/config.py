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


def expand_cases(
    profile: dict[str, Any],
    capability_report: dict[str, Any] | None = None,
    limit: int | None = None,
) -> list[BenchmarkCase]:
    defaults = profile.get("defaults", {})
    max_reference_qubits = int(profile.get("max_reference_qubits", 12))
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
        ram_fraction = float(limits.get("ram_fraction", 0.75))
        vram_fraction = float(limits.get("vram_fraction", 0.80))
        overhead_factor = float(limits.get("overhead_factor", 1.5))
        ram_source = str(limits.get("ram_source", "available"))
        vram_source = str(limits.get("vram_source", "free"))
        seeds = benchmark.get("seeds", {})

        ram_total = _memory_source_value(capability_report, kind="ram", source=ram_source)
        ram_limit_bytes = int(ram_total * ram_fraction) if ram_total else None
        gpu_total = _memory_source_value(capability_report, kind="gpu", source=vram_source)
        vram_limit_bytes = int(gpu_total * vram_fraction) if gpu_total else None

        for family in families:
            qubit_values = resolve_qubit_grid(benchmark["qubit_grid"], family, capability_report)
            depth = int(benchmark.get("depths", {}).get(family, 1))
            family_seeds = _as_list(seeds.get(family, seeds.get("default", [0])))

            for device in devices:
                active_thread_modes = ["all"] if str(device).upper() == "GPU" else thread_modes
                for precision in precisions:
                    for qubits in qubit_values:
                        for seed in family_seeds:
                            for thread_mode in active_thread_modes:
                                total_loops = warmups + repeats
                                for offset in range(total_loops):
                                    warmup = offset < warmups
                                    repeat_index = offset if warmup else offset - warmups
                                    manifest_id = (
                                        f"{library}-{spec_index}-{family}-{device}-{precision}-{qubits}q-"
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
