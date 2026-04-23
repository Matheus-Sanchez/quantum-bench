from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from quantum_bench.utils import ensure_directory, load_json, median, safe_float, safe_int, utc_now_iso


def _is_true(value: Any) -> bool:
    return value in (True, "True", "true", "1", 1)


def _load_rows(input_dir: Path) -> list[dict[str, Any]]:
    direct = input_dir / "results.csv"
    csv_paths = [direct] if direct.exists() else sorted(input_dir.rglob("results.csv"))
    rows: list[dict[str, Any]] = []
    for csv_path in csv_paths:
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows.extend(reader)
    return rows


def _load_optional_json(input_dir: Path, name: str) -> dict[str, Any] | None:
    direct = input_dir / name
    if direct.exists():
        return load_json(direct)
    for path in sorted(input_dir.rglob(name)):
        return load_json(path)
    return None


def _non_warmup_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if not _is_true(row.get("warmup"))]


def _successful_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if _is_true(row.get("success"))]


def _round_or_none(value: float | None, digits: int = 3) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _group_key(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("library") or "unknown"),
        str(row.get("device") or "unknown"),
        str(row.get("precision") or "unknown"),
        str(row.get("family") or "unknown"),
    )


def _stable_frontier(non_warmup_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in non_warmup_rows:
        grouped[_group_key(row)].append(row)

    frontiers: list[dict[str, Any]] = []
    for key, rows in sorted(grouped.items()):
        qubit_groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
        expected_seed_count = 0
        for row in rows:
            qubits = safe_int(row.get("qubits"))
            if qubits is None:
                continue
            qubit_groups[qubits].append(row)
        if not qubit_groups:
            continue

        for qubit_rows in qubit_groups.values():
            expected_seed_count = max(expected_seed_count, len({safe_int(item.get("seed")) for item in qubit_rows}))

        stable_qubits: list[int] = []
        for qubits, qubit_rows in sorted(qubit_groups.items()):
            seed_groups: dict[int | None, list[dict[str, Any]]] = defaultdict(list)
            for row in qubit_rows:
                seed_groups[safe_int(row.get("seed"))].append(row)
            expected_repeats = 0
            for seed_rows in seed_groups.values():
                expected_repeats = max(
                    expected_repeats,
                    len({safe_int(item.get("repeat_index")) for item in seed_rows}),
                )
            qubit_is_stable = (
                len(seed_groups) == expected_seed_count
                and expected_repeats > 0
                and all(
                    len({safe_int(item.get("repeat_index")) for item in seed_rows}) == expected_repeats
                    and all(_is_true(item.get("success")) for item in seed_rows)
                    for seed_rows in seed_groups.values()
                )
            )
            if qubit_is_stable:
                stable_qubits.append(qubits)

        frontiers.append(
            {
                "library": key[0],
                "device": key[1],
                "precision": key[2],
                "family": key[3],
                "stable_max_qubits": max(stable_qubits) if stable_qubits else None,
                "stable_qubits": stable_qubits,
                "expected_seed_count": expected_seed_count,
            }
        )

    return frontiers


def _group_summaries(non_warmup_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in non_warmup_rows:
        grouped[_group_key(row)].append(row)

    frontiers = {
        (item["library"], item["device"], item["precision"], item["family"]): item
        for item in _stable_frontier(non_warmup_rows)
    }

    summaries: list[dict[str, Any]] = []
    for key, rows in sorted(grouped.items()):
        successes = _successful_rows(rows)
        wall_values = [value for value in (safe_float(row.get("wall_s")) for row in successes) if value is not None]
        rss_values = [value for value in (safe_float(row.get("peak_rss_mb")) for row in successes) if value is not None]
        gpu_values = [value for value in (safe_float(row.get("gpu_peak_mem_mb")) for row in successes) if value is not None]
        summary = {
            "library": key[0],
            "device": key[1],
            "precision": key[2],
            "family": key[3],
            "rows": len(rows),
            "success_rows": len(successes),
            "success_rate": _round_or_none(len(successes) / len(rows), 4) if rows else None,
            "median_wall_s": _round_or_none(median(wall_values), 4),
            "peak_rss_mb": _round_or_none(max(rss_values), 3) if rss_values else None,
            "peak_gpu_mem_mb": _round_or_none(max(gpu_values), 3) if gpu_values else None,
            "stable_max_qubits": frontiers.get(key, {}).get("stable_max_qubits"),
        }
        summaries.append(summary)
    return summaries


def _slowest_cases(successful_rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    ranked = []
    for row in successful_rows:
        wall_s = safe_float(row.get("wall_s"))
        if wall_s is None:
            continue
        ranked.append(
            {
                "library": row.get("library"),
                "device": row.get("device"),
                "precision": row.get("precision"),
                "family": row.get("family"),
                "qubits": safe_int(row.get("qubits")),
                "seed": safe_int(row.get("seed")),
                "wall_s": _round_or_none(wall_s, 4),
                "peak_rss_mb": _round_or_none(safe_float(row.get("peak_rss_mb")), 3),
                "gpu_peak_mem_mb": _round_or_none(safe_float(row.get("gpu_peak_mem_mb")), 3),
            }
        )
    return sorted(ranked, key=lambda item: item["wall_s"] or 0.0, reverse=True)[:limit]


def _failure_summary(non_warmup_rows: list[dict[str, Any]]) -> dict[str, Any]:
    failures = [row for row in non_warmup_rows if not _is_true(row.get("success"))]
    error_types = Counter(str(row.get("error_type") or "unknown") for row in failures)
    backends = Counter(
        f"{row.get('library') or 'unknown'}:{row.get('backend') or 'unknown'}:{row.get('device') or 'unknown'}:{row.get('precision') or 'unknown'}"
        for row in failures
    )

    notes: list[str] = []
    if any("Qulacs GPU backend is not available" in str(row.get("error") or "") for row in failures):
        notes.append("Qulacs GPU backend was not available in this environment.")
    if any("estimated_statevector_bytes_exceeds" in str(row.get("error") or "") for row in failures):
        notes.append("Some cases were skipped by the harness because the estimated statevector exceeded the configured RAM/VRAM envelope.")
    if any(str(row.get("error_type") or "") == "timeout" for row in failures):
        notes.append("At least one case reached the configured per-case timeout.")

    return {
        "error_types": [
            {"error_type": error_type, "count": count}
            for error_type, count in error_types.most_common()
        ],
        "failures_by_backend": [
            {"backend": backend, "count": count}
            for backend, count in backends.most_common()
        ],
        "notes": notes,
    }


def _resource_peaks(successful_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rss_ranked = []
    gpu_ranked = []
    for row in successful_rows:
        rss = safe_float(row.get("peak_rss_mb"))
        gpu = safe_float(row.get("gpu_peak_mem_mb"))
        base = {
            "library": row.get("library"),
            "device": row.get("device"),
            "precision": row.get("precision"),
            "family": row.get("family"),
            "qubits": safe_int(row.get("qubits")),
            "seed": safe_int(row.get("seed")),
        }
        if rss is not None:
            rss_ranked.append({**base, "peak_rss_mb": _round_or_none(rss, 3)})
        if gpu is not None:
            gpu_ranked.append({**base, "gpu_peak_mem_mb": _round_or_none(gpu, 3)})
    max_rss_case = max(rss_ranked, key=lambda item: item["peak_rss_mb"], default=None)
    max_gpu_case = max(gpu_ranked, key=lambda item: item["gpu_peak_mem_mb"], default=None)
    return {
        "max_rss_case": max_rss_case,
        "max_gpu_mem_case": max_gpu_case,
    }


def _matrix_summary(non_warmup_rows: list[dict[str, Any]]) -> dict[str, Any]:
    qubit_values = sorted({value for value in (safe_int(row.get("qubits")) for row in non_warmup_rows) if value is not None})
    return {
        "libraries": sorted({str(row.get("library") or "unknown") for row in non_warmup_rows}),
        "devices": sorted({str(row.get("device") or "unknown") for row in non_warmup_rows}),
        "precisions": sorted({str(row.get("precision") or "unknown") for row in non_warmup_rows}),
        "families": sorted({str(row.get("family") or "unknown") for row in non_warmup_rows}),
        "qubit_values": qubit_values,
        "qubit_min": min(qubit_values) if qubit_values else None,
        "qubit_max": max(qubit_values) if qubit_values else None,
    }


def _environment_summary(env_report: dict[str, Any] | None, manifest: dict[str, Any] | None) -> dict[str, Any]:
    env_report = env_report or {}
    manifest = manifest or {}
    host = env_report.get("host", {})
    gpu = env_report.get("gpu", {})
    packages = env_report.get("package_versions", {})
    return {
        "profile_name": manifest.get("profile_name"),
        "execution_env": manifest.get("execution_env"),
        "hostname": host.get("hostname"),
        "platform": host.get("platform"),
        "python_version": host.get("python_version"),
        "gpu_name": gpu.get("name"),
        "driver_version": gpu.get("driver_version"),
        "cuda_version": env_report.get("cuda_version"),
        "qiskit_version": packages.get("qiskit"),
        "qiskit_aer_version": packages.get("qiskit-aer"),
        "qulacs_version": packages.get("qulacs"),
    }


def _render_analysis_report(summary: dict[str, Any]) -> str:
    environment = summary["environment"]
    counts = summary["counts"]
    matrix = summary["matrix"]
    frontiers = summary["stable_frontier"]
    slowest = summary["slowest_successful_cases"]
    failure_summary = summary["failure_summary"]
    peaks = summary["resource_peaks"]

    lines = [
        "# Quantum Bench Analysis Report",
        "",
        f"Generated at: `{summary['generated_at_utc']}`",
        f"Input directory: `{summary['input_dir']}`",
        "",
        "## Environment",
        f"- Profile: `{environment.get('profile_name')}`",
        f"- Execution env: `{environment.get('execution_env')}`",
        f"- Host: `{environment.get('hostname')}`",
        f"- Platform: `{environment.get('platform')}`",
        f"- Python: `{environment.get('python_version')}`",
        f"- GPU: `{environment.get('gpu_name')}`",
        f"- Driver: `{environment.get('driver_version')}`",
        f"- CUDA: `{environment.get('cuda_version')}`",
        f"- Qiskit: `{environment.get('qiskit_version')}`",
        f"- Qiskit Aer: `{environment.get('qiskit_aer_version')}`",
        f"- Qulacs: `{environment.get('qulacs_version')}`",
        "",
        "## Matrix",
        f"- Non-warmup success rate: `{counts['success_rate_non_warmup_pct']:.2f}%` ({counts['successful_non_warmup_rows']}/{counts['non_warmup_rows']})" if counts["non_warmup_rows"] else "- Non-warmup success rate: `n/a`",
        f"- Libraries: `{', '.join(matrix['libraries']) or 'n/a'}`",
        f"- Devices: `{', '.join(matrix['devices']) or 'n/a'}`",
        f"- Precisions: `{', '.join(matrix['precisions']) or 'n/a'}`",
        f"- Families: `{', '.join(matrix['families']) or 'n/a'}`",
        f"- Qubit span: `{matrix['qubit_min']}..{matrix['qubit_max']}`" if matrix["qubit_min"] is not None else "- Qubit span: `n/a`",
        f"- Qubit values: `{', '.join(str(value) for value in matrix['qubit_values'])}`" if matrix["qubit_values"] else "- Qubit values: `n/a`",
        "",
        "## Stable Frontier",
        "| Library | Device | Precision | Family | Stable max qubits | Median wall_s | Peak RSS MB | Peak GPU MB |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ]

    summary_lookup = {
        (item["library"], item["device"], item["precision"], item["family"]): item
        for item in summary["group_summaries"]
    }
    for item in frontiers:
        key = (item["library"], item["device"], item["precision"], item["family"])
        group = summary_lookup.get(key, {})
        lines.append(
            "| "
            f"{item['library']} | {item['device']} | {item['precision']} | {item['family']} | "
            f"{item['stable_max_qubits'] if item['stable_max_qubits'] is not None else '-'} | "
            f"{group.get('median_wall_s') if group.get('median_wall_s') is not None else '-'} | "
            f"{group.get('peak_rss_mb') if group.get('peak_rss_mb') is not None else '-'} | "
            f"{group.get('peak_gpu_mem_mb') if group.get('peak_gpu_mem_mb') is not None else '-'} |"
        )

    lines.extend(
        [
            "",
            "## Slowest Successful Cases",
            "| Library | Device | Precision | Family | Qubits | Seed | Wall s | Peak RSS MB | Peak GPU MB |",
            "|---|---|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for item in slowest:
        lines.append(
            "| "
            f"{item['library']} | {item['device']} | {item['precision']} | {item['family']} | "
            f"{item['qubits']} | {item['seed']} | {item['wall_s']} | "
            f"{item['peak_rss_mb'] if item['peak_rss_mb'] is not None else '-'} | "
            f"{item['gpu_peak_mem_mb'] if item['gpu_peak_mem_mb'] is not None else '-'} |"
        )
    if not slowest:
        lines.append("| - | - | - | - | - | - | - | - | - |")

    lines.extend(
        [
            "",
            "## Resource Peaks",
            f"- Max RSS case: `{json.dumps(peaks['max_rss_case'], ensure_ascii=False)}`" if peaks["max_rss_case"] else "- Max RSS case: `n/a`",
            f"- Max GPU memory case: `{json.dumps(peaks['max_gpu_mem_case'], ensure_ascii=False)}`" if peaks["max_gpu_mem_case"] else "- Max GPU memory case: `n/a`",
            "",
            "## Failure Summary",
        ]
    )

    if failure_summary["error_types"]:
        top_errors = ", ".join(
            f"{item['error_type']} ({item['count']})" for item in failure_summary["error_types"][:5]
        )
        lines.append(f"- Top error types: `{top_errors}`")
    else:
        lines.append("- Top error types: `none`")

    if failure_summary["failures_by_backend"]:
        top_backends = ", ".join(
            f"{item['backend']} ({item['count']})" for item in failure_summary["failures_by_backend"][:5]
        )
        lines.append(f"- Failures by backend: `{top_backends}`")
    else:
        lines.append("- Failures by backend: `none`")

    if failure_summary["notes"]:
        for note in failure_summary["notes"]:
            lines.append(f"- {note}")
    else:
        lines.append("- No additional failure notes.")

    return "\n".join(lines) + "\n"


def build_analysis_report(input_dir: Path, output_dir: Path | None = None) -> dict[str, Any]:
    destination = ensure_directory(output_dir or input_dir)
    rows = _load_rows(input_dir)
    non_warmup_rows = _non_warmup_rows(rows)
    successful_rows = _successful_rows(non_warmup_rows)
    env_report = _load_optional_json(input_dir, "env-report.json")
    manifest = _load_optional_json(input_dir, "manifest.json")

    counts = {
        "rows": len(rows),
        "non_warmup_rows": len(non_warmup_rows),
        "successful_non_warmup_rows": len(successful_rows),
        "failed_non_warmup_rows": len(non_warmup_rows) - len(successful_rows),
        "success_rate_non_warmup_pct": round((len(successful_rows) / len(non_warmup_rows)) * 100.0, 2)
        if non_warmup_rows
        else 0.0,
    }

    summary = {
        "generated_at_utc": utc_now_iso(),
        "input_dir": str(input_dir),
        "output_dir": str(destination),
        "counts": counts,
        "manifest": manifest or {},
        "environment": _environment_summary(env_report, manifest),
        "matrix": _matrix_summary(non_warmup_rows),
        "stable_frontier": _stable_frontier(non_warmup_rows),
        "group_summaries": _group_summaries(non_warmup_rows),
        "slowest_successful_cases": _slowest_cases(successful_rows),
        "resource_peaks": _resource_peaks(successful_rows),
        "failure_summary": _failure_summary(rows),
    }

    summary_path = destination / "analysis-summary.json"
    report_path = destination / "analysis-report.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_analysis_report(summary), encoding="utf-8")

    return {
        "input_dir": str(input_dir),
        "output_dir": str(destination),
        "summary_path": str(summary_path),
        "report_path": str(report_path),
        "row_count": len(rows),
        "non_warmup_rows": len(non_warmup_rows),
        "successful_non_warmup_rows": len(successful_rows),
    }
