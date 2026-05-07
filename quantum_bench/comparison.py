from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from quantum_bench.utils import ensure_directory, median, safe_float, safe_int, utc_now_iso


def _is_true(value: Any) -> bool:
    return value in (True, "True", "true", "1", 1)


def _variant(row: dict[str, Any]) -> str:
    variant = str(row.get("variant") or "").strip()
    if variant:
        return variant
    library = str(row.get("library") or "unknown")
    device = str(row.get("device") or "unknown").lower()
    return f"{library}_{device}"


def _load_rows(input_dir: Path) -> list[dict[str, Any]]:
    direct = input_dir / "results.csv"
    csv_paths = [direct] if direct.exists() else sorted(input_dir.rglob("results.csv"))
    rows: list[dict[str, Any]] = []
    for csv_path in csv_paths:
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows.extend(reader)
    return rows


def _load_snapshots(input_dir: Path) -> list[dict[str, Any]]:
    paths = sorted(input_dir.rglob("distribution-snapshots.jsonl"))
    snapshots: list[dict[str, Any]] = []
    for path in paths:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    snapshots.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return snapshots


def _non_warmup_success_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if not _is_true(row.get("warmup")) and _is_true(row.get("success"))]


def _median_metric(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [value for value in (safe_float(row.get(field)) for row in rows) if value is not None]
    return median(values)


def _comparison_key(row: dict[str, Any]) -> tuple[str, int | None, int | None, str, str, str]:
    return (
        str(row.get("family") or "unknown"),
        safe_int(row.get("qubits")),
        safe_int(row.get("depth")),
        str(row.get("precision") or "unknown"),
        str(row.get("noise_profile") or "none"),
        str(row.get("output_mode") or "statevector"),
    )


def _variant_medians(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_variant(row)].append(row)

    summary: list[dict[str, Any]] = []
    for variant, items in sorted(grouped.items()):
        summary.append(
            {
                "variant": variant,
                "simulate_s_median": _median_metric(items, "simulate_s"),
                "wall_s_median": _median_metric(items, "wall_s"),
                "peak_rss_mb": max((value for value in (safe_float(row.get("peak_rss_mb")) for row in items) if value is not None), default=None),
                "peak_gpu_mem_mb": max((value for value in (safe_float(row.get("gpu_peak_mem_mb")) for row in items) if value is not None), default=None),
                "samples": len(items),
            }
        )
    return summary


def _pointwise_speedup(rows: list[dict[str, Any]], baseline_variant: str) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, int | None, int | None, str, str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        metric = safe_float(row.get("simulate_s"))
        if metric is None:
            metric = safe_float(row.get("wall_s"))
        if metric is None:
            continue
        buckets[_comparison_key(row)][_variant(row)].append(metric)

    per_variant: dict[str, list[float]] = defaultdict(list)
    for variants in buckets.values():
        baseline_values = variants.get(baseline_variant)
        if not baseline_values:
            continue
        baseline_median = median(baseline_values)
        if baseline_median in (None, 0):
            continue
        for variant, values in variants.items():
            current_median = median(values)
            if variant == baseline_variant or current_median in (None, 0):
                continue
            per_variant[variant].append(baseline_median / current_median)

    return [
        {
            "variant": variant,
            "median_speedup": median(values),
            "matched_points": len(values),
        }
        for variant, values in sorted(per_variant.items())
    ]


def _stable_frontier(rows: list[dict[str, Any]], *, output_mode: str | None = None) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if output_mode is not None and str(row.get("output_mode") or "statevector") != output_mode:
            continue
        grouped[
            (
                _variant(row),
                str(row.get("precision") or "unknown"),
                str(row.get("family") or "unknown"),
                str(row.get("noise_profile") or "none"),
            )
        ].append(row)

    frontiers: list[dict[str, Any]] = []
    for (variant, precision, family, noise_profile), items in sorted(grouped.items()):
        qubit_groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in items:
            qubits = safe_int(row.get("qubits"))
            if qubits is not None:
                qubit_groups[qubits].append(row)

        stable_qubits: list[int] = []
        expected_seed_count = 0
        for qubit_rows in qubit_groups.values():
            expected_seed_count = max(expected_seed_count, len({safe_int(row.get("seed")) for row in qubit_rows}))

        for qubits, qubit_rows in sorted(qubit_groups.items()):
            seed_groups: dict[int | None, list[dict[str, Any]]] = defaultdict(list)
            for row in qubit_rows:
                seed_groups[safe_int(row.get("seed"))].append(row)
            expected_repeats = 0
            for seed_rows in seed_groups.values():
                expected_repeats = max(expected_repeats, len({safe_int(row.get("repeat_index")) for row in seed_rows}))
            stable = (
                len(seed_groups) == expected_seed_count
                and expected_repeats > 0
                and all(
                    len({safe_int(row.get("repeat_index")) for row in seed_rows}) == expected_repeats
                    and all(_is_true(row.get("success")) for row in seed_rows)
                    for seed_rows in seed_groups.values()
                )
            )
            if stable:
                stable_qubits.append(qubits)

        frontiers.append(
            {
                "variant": variant,
                "precision": precision,
                "family": family,
                "noise_profile": noise_profile,
                "stable_max_qubits": max(stable_qubits) if stable_qubits else None,
                "stable_qubits": stable_qubits,
            }
        )
    return frontiers


def _error_curves(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, int | None, str], dict[int, dict[str, list[float]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for row in rows:
        depth = safe_int(row.get("depth"))
        if depth is None:
            continue
        key = (_variant(row), str(row.get("family") or "unknown"), safe_int(row.get("qubits")), str(row.get("noise_profile") or "none"))
        for source, target in [
            ("tvd_noisy_vs_ideal", "tvd"),
            ("tvd_ref", "tvd"),
            ("jsd_noisy_vs_ideal", "jsd"),
            ("jsd_ref", "jsd"),
            ("hellinger_noisy_vs_ideal", "hellinger"),
            ("hellinger_ref", "hellinger"),
        ]:
            value = safe_float(row.get(source))
            if value is not None:
                groups[key][depth][target].append(value)

    curves: list[dict[str, Any]] = []
    for key, by_depth in sorted(groups.items()):
        points = []
        for depth, metrics in sorted(by_depth.items()):
            points.append(
                {
                    "depth": depth,
                    "tvd": median(metrics.get("tvd", [])),
                    "jsd": median(metrics.get("jsd", [])),
                    "hellinger": median(metrics.get("hellinger", [])),
                }
            )
        curves.append(
            {
                "variant": key[0],
                "family": key[1],
                "qubits": key[2],
                "noise_profile": key[3],
                "points": points,
            }
        )
    return curves


def _custatevec_status(rows: list[dict[str, Any]]) -> dict[str, Any]:
    requested_rows = [row for row in rows if _is_true(row.get("cuStateVec_requested"))]
    buckets = {"confirmed": [], "inconclusive": [], "fallback_explicit": []}
    for row in requested_rows:
        effective = row.get("cuStateVec_effective")
        if effective in (True, "True", "true", "1", 1):
            buckets["confirmed"].append(row)
        elif effective in (False, "False", "false", "0", 0):
            buckets["fallback_explicit"].append(row)
        else:
            buckets["inconclusive"].append(row)

    def sample(rows_for_status: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in rows_for_status[:5]:
            result.append(
                {
                    "variant": _variant(row),
                    "family": row.get("family"),
                    "qubits": safe_int(row.get("qubits")),
                    "precision": row.get("precision"),
                    "source": row.get("cuStateVec_effective_source"),
                }
            )
        return result

    return {
        "confirmed": {"count": len(buckets["confirmed"]), "samples": sample(buckets["confirmed"])},
        "inconclusive": {"count": len(buckets["inconclusive"]), "samples": sample(buckets["inconclusive"])},
        "fallback_explicit": {"count": len(buckets["fallback_explicit"]), "samples": sample(buckets["fallback_explicit"])},
    }


def _render_report(summary: dict[str, Any]) -> str:
    lines = [
        "# Quantum Bench Comparison Report",
        "",
        f"Generated at: `{summary['generated_at_utc']}`",
        f"Input dirs: `{', '.join(summary['input_dirs'])}`",
        "",
        "## Speed",
        "| Variant | Median simulate_s | Median wall_s | Speedup vs CPU | Speedup vs gpu_thrust | Samples |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    speed_cpu = {item["variant"]: item for item in summary["speedup_vs_cpu_statevector"]}
    speed_gpu = {item["variant"]: item for item in summary["speedup_vs_gpu_thrust"]}
    for item in summary["variant_medians"]:
        variant = item["variant"]
        lines.append(
            f"| {variant} | {item['simulate_s_median'] if item['simulate_s_median'] is not None else '-'} | "
            f"{item['wall_s_median'] if item['wall_s_median'] is not None else '-'} | "
            f"{speed_cpu.get(variant, {}).get('median_speedup', '-')} | "
            f"{speed_gpu.get(variant, {}).get('median_speedup', '-')} | {item['samples']} |"
        )
    if not summary["variant_medians"]:
        lines.append("| - | - | - | - | - | - |")

    for title, key in [
        ("Exact Frontier By Variant", "exact_frontier_by_variant"),
        ("Observable Frontier By Variant", "observable_frontier_by_variant"),
    ]:
        lines.extend(["", f"## {title}", "| Variant | Precision | Family | Noise | Stable max qubits |", "|---|---|---|---|---:|"])
        for item in summary[key]:
            lines.append(
                f"| {item['variant']} | {item['precision']} | {item['family']} | {item['noise_profile']} | "
                f"{item['stable_max_qubits'] if item['stable_max_qubits'] is not None else '-'} |"
            )
        if not summary[key]:
            lines.append("| - | - | - | - | - |")

    lines.extend(["", "## Memory", "| Variant | Peak RSS MB | Peak GPU MB |", "|---|---:|---:|"])
    for item in summary["variant_medians"]:
        lines.append(f"| {item['variant']} | {item['peak_rss_mb'] if item['peak_rss_mb'] is not None else '-'} | {item['peak_gpu_mem_mb'] if item['peak_gpu_mem_mb'] is not None else '-'} |")

    lines.extend(["", "## Error Accumulation", "| Variant | Family | Qubits | Noise | Depth points |", "|---|---|---:|---|---:|"])
    for curve in summary["error_accumulation"]:
        lines.append(f"| {curve['variant']} | {curve['family']} | {curve['qubits']} | {curve['noise_profile']} | {len(curve['points'])} |")
    if not summary["error_accumulation"]:
        lines.append("| - | - | - | - | - |")

    status = summary["cuStateVec_status"]
    lines.extend(
        [
            "",
            "## cuStateVec Status",
            f"- Confirmed: `{status['confirmed']['count']}`",
            f"- Inconclusive: `{status['inconclusive']['count']}`",
            f"- Explicit fallback: `{status['fallback_explicit']['count']}`",
            "",
            "## Generated Plots",
        ]
    )
    for plot in summary.get("plots", []):
        lines.append(f"- `{plot}`")
    if not summary.get("plots"):
        lines.append("- none")
    return "\n".join(lines) + "\n"


def _generate_plots(summary: dict[str, Any], output_dir: Path, snapshots: list[dict[str, Any]]) -> list[str]:
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except ImportError:
        return []
    plots: list[str] = []

    medians = summary["variant_medians"]
    if medians:
        plt.figure(figsize=(10, 6))
        plt.bar([item["variant"] for item in medians], [item["simulate_s_median"] or 0.0 for item in medians])
        plt.ylabel("Median simulate_s")
        plt.xticks(rotation=30, ha="right")
        plt.tight_layout()
        path = output_dir / "speed_by_variant.png"
        plt.savefig(path)
        plt.close()
        plots.append(str(path))

        plt.figure(figsize=(10, 6))
        plt.bar([item["variant"] for item in medians], [item["peak_gpu_mem_mb"] or item["peak_rss_mb"] or 0.0 for item in medians])
        plt.ylabel("Peak memory MB")
        plt.xticks(rotation=30, ha="right")
        plt.tight_layout()
        path = output_dir / "memory_by_variant.png"
        plt.savefig(path)
        plt.close()
        plots.append(str(path))

    for key, filename, title in [
        ("exact_frontier_by_variant", "exact_frontier_by_variant.png", "Exact frontier"),
        ("observable_frontier_by_variant", "observable_frontier_by_variant.png", "Observable frontier"),
    ]:
        data = [item for item in summary[key] if item["stable_max_qubits"] is not None]
        plt.figure(figsize=(10, 6))
        plt.bar([f"{item['variant']}:{item['family']}" for item in data], [item["stable_max_qubits"] for item in data])
        plt.ylabel("Stable max qubits")
        plt.title(title)
        plt.xticks(rotation=30, ha="right")
        plt.tight_layout()
        path = output_dir / filename
        plt.savefig(path)
        plt.close()
        plots.append(str(path))

    plt.figure(figsize=(10, 6))
    any_error = False
    for curve in summary["error_accumulation"]:
        points = [(item["depth"], item["tvd"]) for item in curve["points"] if item.get("tvd") is not None]
        if not points:
            continue
        any_error = True
        plt.plot([item[0] for item in points], [item[1] for item in points], marker="o", label=f"{curve['variant']}:{curve['family']}:{curve['qubits']}q")
    plt.xlabel("Depth")
    plt.ylabel("TVD")
    if any_error:
        plt.legend(fontsize=8)
    plt.tight_layout()
    path = output_dir / "error_accumulation_tvd.png"
    plt.savefig(path)
    plt.close()
    plots.append(str(path))

    for snapshot in snapshots[:12]:
        values = list((snapshot.get("probabilities") or {}).values())
        if not values:
            continue
        plt.figure(figsize=(max(8, min(18, len(values) / 8)), 3))
        plt.imshow([values], aspect="auto", cmap="viridis")
        plt.yticks([])
        plt.xticks([])
        plt.colorbar(label="Probability")
        plt.title(str(snapshot.get("manifest_id") or "probability snapshot"))
        safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(snapshot.get("manifest_id") or len(plots))).strip("-")
        path = output_dir / f"probability_heatmap_{safe_name}.png"
        plt.tight_layout()
        plt.savefig(path)
        plt.close()
        plots.append(str(path))
    return plots


def build_comparison_report(input_dirs: list[Path], output_dir: Path | None = None) -> dict[str, Any]:
    resolved_inputs = [Path(path) for path in input_dirs]
    destination = ensure_directory(output_dir or resolved_inputs[0])
    rows: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    for input_dir in resolved_inputs:
        rows.extend(_load_rows(input_dir))
        snapshots.extend(_load_snapshots(input_dir))

    successful = _non_warmup_success_rows(rows)
    non_warmup = [row for row in rows if not _is_true(row.get("warmup"))]
    summary = {
        "generated_at_utc": utc_now_iso(),
        "input_dirs": [str(path) for path in resolved_inputs],
        "output_dir": str(destination),
        "row_count": len(rows),
        "successful_non_warmup_rows": len(successful),
        "comparison_key": ["family", "qubits", "depth", "precision", "noise_profile", "output_mode"],
        "variant_medians": _variant_medians(successful),
        "speedup_vs_cpu_statevector": _pointwise_speedup(successful, "cpu_statevector"),
        "speedup_vs_gpu_thrust": _pointwise_speedup(successful, "gpu_thrust"),
        "exact_frontier_by_variant": _stable_frontier(non_warmup, output_mode="statevector"),
        "observable_frontier_by_variant": _stable_frontier(non_warmup, output_mode="marginal_probabilities"),
        "error_accumulation": _error_curves(successful),
        "cuStateVec_status": _custatevec_status(non_warmup),
        "probability_snapshot_count": len(snapshots),
    }
    summary["plots"] = _generate_plots(summary, destination, snapshots)

    summary_path = destination / "comparison-summary.json"
    report_path = destination / "comparison-report.md"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(_render_report(summary), encoding="utf-8")

    return {
        "output_dir": str(destination),
        "summary_path": str(summary_path),
        "report_path": str(report_path),
        "row_count": len(rows),
    }
