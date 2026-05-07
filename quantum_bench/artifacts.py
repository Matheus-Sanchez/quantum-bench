from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from quantum_bench.probability import basis_states, probability_payload
from quantum_bench.utils import ensure_directory, median, safe_float, safe_int, sanitize_token, write_json


def _is_true(value: Any) -> bool:
    return value in (True, "True", "true", "1", 1)


def _variant(row: dict[str, Any]) -> str:
    return str(row.get("variant") or "unknown")


def _comparison_key(row: dict[str, Any]) -> tuple[str, int | None, int | None, str, str, str]:
    return (
        str(row.get("family") or "unknown"),
        safe_int(row.get("qubits")),
        safe_int(row.get("depth")),
        str(row.get("precision") or "unknown"),
        str(row.get("noise_profile") or "none"),
        str(row.get("output_mode") or "statevector"),
    )


def enrich_speedups(rows: list[dict[str, Any]]) -> None:
    buckets: dict[tuple[str, int | None, int | None, str, str, str], dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        if _is_true(row.get("warmup")) or not _is_true(row.get("success")):
            continue
        metric = safe_float(row.get("simulate_s"))
        if metric is None:
            metric = safe_float(row.get("wall_s"))
        if metric is None:
            continue
        buckets[_comparison_key(row)][_variant(row)].append(metric)

    medians: dict[tuple[str, int | None, int | None, str, str, str], dict[str, float]] = {}
    for key, variants in buckets.items():
        medians[key] = {
            variant: value
            for variant, values in variants.items()
            if (value := median(values)) is not None
        }

    for row in rows:
        row["speedup_vs_cpu"] = None
        row["speedup_vs_gpu_thrust"] = None
        if _is_true(row.get("warmup")) or not _is_true(row.get("success")):
            continue
        metric = safe_float(row.get("simulate_s"))
        if metric is None:
            metric = safe_float(row.get("wall_s"))
        if metric in (None, 0):
            continue
        variants = medians.get(_comparison_key(row), {})
        cpu = variants.get("cpu_statevector")
        thrust = variants.get("gpu_thrust")
        if cpu is not None:
            row["speedup_vs_cpu"] = round(cpu / metric, 10)
        if thrust is not None:
            row["speedup_vs_gpu_thrust"] = round(thrust / metric, 10)


def _threshold_depth(points: list[tuple[int, float]], threshold: float) -> int | None:
    for depth, value in sorted(points):
        if value >= threshold:
            return depth
    return None


def enrich_error_accumulation(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[str, str, int | None, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if _is_true(row.get("warmup")) or not _is_true(row.get("success")):
            continue
        key = (
            _variant(row),
            str(row.get("family") or "unknown"),
            safe_int(row.get("qubits")),
            str(row.get("precision") or "unknown"),
            str(row.get("noise_profile") or "none"),
        )
        groups[key].append(row)

    summary: dict[str, Any] = {"curves": []}
    group_metrics: dict[tuple[str, str, int | None, str, str], dict[str, Any]] = {}
    for key, items in sorted(groups.items()):
        depth_buckets: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        for row in items:
            depth = safe_int(row.get("depth"))
            if depth is None:
                continue
            tvd = safe_float(row.get("tvd_noisy_vs_ideal"))
            if tvd is None:
                tvd = safe_float(row.get("tvd_ref"))
            jsd = safe_float(row.get("jsd_noisy_vs_ideal"))
            if jsd is None:
                jsd = safe_float(row.get("jsd_ref"))
            if tvd is not None:
                depth_buckets[depth]["tvd"].append(tvd)
            if jsd is not None:
                depth_buckets[depth]["jsd"].append(jsd)
        points = []
        for depth, metrics in sorted(depth_buckets.items()):
            points.append(
                {
                    "depth": depth,
                    "tvd": median(metrics.get("tvd", [])),
                    "jsd": median(metrics.get("jsd", [])),
                }
            )
        tvd_points = [(item["depth"], item["tvd"]) for item in points if item["tvd"] is not None]
        jsd_points = [(item["depth"], item["jsd"]) for item in points if item["jsd"] is not None]
        slope = None
        if len(tvd_points) >= 2:
            first_depth, first_value = tvd_points[0]
            last_depth, last_value = tvd_points[-1]
            if last_depth != first_depth:
                slope = round((last_value - first_value) / (last_depth - first_depth), 10)
        metrics = {
            "error_slope_vs_depth": slope,
            "depth_at_tvd_1pct": _threshold_depth(tvd_points, 0.01),
            "depth_at_tvd_5pct": _threshold_depth(tvd_points, 0.05),
            "depth_at_jsd_threshold": _threshold_depth(jsd_points, 0.01),
        }
        group_metrics[key] = metrics
        summary["curves"].append(
            {
                "variant": key[0],
                "family": key[1],
                "qubits": key[2],
                "precision": key[3],
                "noise_profile": key[4],
                "points": points,
                **metrics,
            }
        )

    for row in rows:
        key = (
            _variant(row),
            str(row.get("family") or "unknown"),
            safe_int(row.get("qubits")),
            str(row.get("precision") or "unknown"),
            str(row.get("noise_profile") or "none"),
        )
        metrics = group_metrics.get(key, {})
        row["error_slope_vs_depth"] = metrics.get("error_slope_vs_depth")
        row["depth_at_tvd_1pct"] = metrics.get("depth_at_tvd_1pct")
        row["depth_at_tvd_5pct"] = metrics.get("depth_at_tvd_5pct")
        row["depth_at_jsd_threshold"] = metrics.get("depth_at_jsd_threshold")
    return summary


def _snapshot_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for row in rows:
        probabilities = row.get("_probabilities")
        width = safe_int(row.get("_probability_width"))
        if not probabilities or width is None:
            continue
        if len(probabilities) > 4096:
            continue
        snapshots.append(
            {
                "manifest_id": row.get("manifest_id"),
                "variant": _variant(row),
                "family": row.get("family"),
                "qubits": safe_int(row.get("qubits")),
                "depth": safe_int(row.get("depth")),
                "precision": row.get("precision"),
                "noise_profile": row.get("noise_profile"),
                "output_mode": row.get("output_mode"),
                "output_qubits": row.get("effective_output_qubits") or row.get("output_qubits"),
                "basis_order": basis_states(width),
                "probabilities": probability_payload(probabilities, width=width),
            }
        )
    return snapshots


def _probability_matrix(snapshots: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, float]]] = defaultdict(list)
    for snapshot in snapshots:
        key = "|".join(
            str(snapshot.get(name))
            for name in ("variant", "family", "qubits", "depth", "noise_profile")
        )
        buckets[key].append(snapshot["probabilities"])

    matrix: dict[str, Any] = {"groups": []}
    for key, probability_items in sorted(buckets.items()):
        first = probability_items[0]
        averaged = {}
        for state in first:
            averaged[state] = sum(item.get(state, 0.0) for item in probability_items) / len(probability_items)
        variant, family, qubits, depth, noise_profile = key.split("|")
        matrix["groups"].append(
            {
                "variant": variant,
                "family": family,
                "qubits": int(qubits) if qubits not in {"None", ""} else None,
                "depth": int(depth) if depth not in {"None", ""} else None,
                "noise_profile": noise_profile,
                "samples": len(probability_items),
                "probabilities": averaged,
            }
        )
    return matrix


def _try_generate_plots(destination: Path, rows: list[dict[str, Any]], snapshots: list[dict[str, Any]], accumulation: dict[str, Any]) -> list[str]:
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except ImportError:
        return []

    generated: list[str] = []

    for snapshot in snapshots[:12]:
        values = list(snapshot["probabilities"].values())
        if not values:
            continue
        fig_width = max(8, min(18, len(values) / 8))
        plt.figure(figsize=(fig_width, 3))
        plt.imshow([values], aspect="auto", cmap="viridis")
        plt.yticks([])
        plt.xticks([])
        plt.colorbar(label="Probability")
        plt.title(str(snapshot["manifest_id"]))
        path = destination / f"probability_heatmap_{sanitize_token(str(snapshot['manifest_id']))}.png"
        plt.tight_layout()
        plt.savefig(path)
        plt.close()
        generated.append(str(path))

    def line_plot(metric: str, filename: str, ylabel: str) -> None:
        plt.figure(figsize=(12, 7))
        any_points = False
        for curve in accumulation.get("curves", []):
            points = [
                (item["depth"], item.get(metric))
                for item in curve.get("points", [])
                if item.get(metric) is not None
            ]
            if not points:
                continue
            any_points = True
            label = f"{curve['variant']}:{curve['family']}:{curve['qubits']}q:{curve['noise_profile']}"
            plt.plot([item[0] for item in points], [item[1] for item in points], marker="o", label=label)
        plt.xlabel("Depth")
        plt.ylabel(ylabel)
        plt.grid(True, alpha=0.3)
        if any_points:
            plt.legend(fontsize=8)
        plt.tight_layout()
        path = destination / filename
        plt.savefig(path)
        plt.close()
        generated.append(str(path))

    line_plot("tvd", "tvd_vs_depth.png", "TVD")
    line_plot("jsd", "jsd_vs_depth.png", "JSD")

    successful = [row for row in rows if _is_true(row.get("success")) and not _is_true(row.get("warmup"))]
    timing_fields = ["backend_init_s", "transpile_s", "simulate_s", "extract_s"]
    timing_values = [median([value for value in (safe_float(row.get(field)) for row in successful) if value is not None]) or 0.0 for field in timing_fields]
    plt.figure(figsize=(8, 5))
    plt.bar(timing_fields, timing_values)
    plt.ylabel("Median seconds")
    plt.tight_layout()
    path = destination / "time_breakdown_stacked.png"
    plt.savefig(path)
    plt.close()
    generated.append(str(path))

    def scatter(source_rows: list[dict[str, Any]], metric: str, filename: str, ylabel: str) -> None:
        plt.figure(figsize=(10, 6))
        buckets: dict[tuple[str, int], list[float]] = defaultdict(list)
        for row in source_rows:
            qubits = safe_int(row.get("qubits"))
            value = safe_float(row.get(metric))
            if qubits is None or value is None:
                continue
            buckets[(_variant(row), qubits)].append(value)
        series: dict[str, list[tuple[int, float]]] = defaultdict(list)
        for (variant, qubits), values in buckets.items():
            med = median(values)
            if med is not None:
                series[variant].append((qubits, med))
        for variant, points in sorted(series.items()):
            points = sorted(points)
            plt.plot([item[0] for item in points], [item[1] for item in points], marker="o", label=variant)
        plt.xlabel("Qubits")
        plt.ylabel(ylabel)
        plt.grid(True, alpha=0.3)
        if series:
            plt.legend(fontsize=8)
        plt.tight_layout()
        path = destination / filename
        plt.savefig(path)
        plt.close()
        generated.append(str(path))

    scatter(successful, "peak_rss_mb", "memory_vs_qubits.png", "Peak RSS MB")
    scatter(successful, "qubits", "exact_frontier_by_variant.png", "Stable exact frontier proxy")
    observable_rows = [row for row in successful if row.get("output_mode") == "marginal_probabilities"]
    scatter(observable_rows, "qubits", "observable_frontier_by_variant.png", "Stable observable frontier proxy")

    return generated


def write_run_artifacts(rows: list[dict[str, Any]], destination: Path, accumulation: dict[str, Any]) -> dict[str, Any]:
    ensure_directory(destination)
    snapshots = _snapshot_rows(rows)
    snapshot_path = destination / "distribution-snapshots.jsonl"
    with snapshot_path.open("w", encoding="utf-8") as handle:
        for snapshot in snapshots:
            handle.write(json.dumps(snapshot, ensure_ascii=False, sort_keys=True) + "\n")

    matrix = _probability_matrix(snapshots)
    matrix_path = destination / "probability-matrix.json"
    write_json(matrix_path, matrix)

    accumulation_path = destination / "error-accumulation-summary.json"
    write_json(accumulation_path, accumulation)

    generated_plots = _try_generate_plots(destination, rows, snapshots, accumulation)
    status = {
        "distribution_snapshots": str(snapshot_path),
        "probability_matrix": str(matrix_path),
        "error_accumulation_summary": str(accumulation_path),
        "snapshot_count": len(snapshots),
        "plots": generated_plots,
    }
    write_json(destination / "artifact-status.json", status)
    return status
