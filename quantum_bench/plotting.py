from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Any

from quantum_bench.utils import ensure_directory, iqr, median, safe_float


def _load_rows(input_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for csv_path in input_dir.rglob("results.csv"):
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            rows.extend(reader)
    return rows


def _success_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("success") in ("True", "true", True) and row.get("warmup") not in ("True", "true", True)]


def _group_metric(rows: list[dict[str, Any]], metric: str) -> dict[tuple[str, str, str, str], list[tuple[int, float]]]:
    grouped: dict[tuple[str, str, str, str], list[tuple[int, float]]] = defaultdict(list)
    buckets: dict[tuple[str, str, str, str, int], list[float]] = defaultdict(list)
    for row in rows:
        value = safe_float(row.get(metric))
        qubits = row.get("qubits")
        if value is None or qubits is None:
            continue
        key = (row["library"], row["family"], row["device"], row["precision"], int(qubits))
        buckets[key].append(value)

    for (library, family, device, precision, qubits), values in sorted(buckets.items()):
        group_key = (library, family, device, precision)
        med = median(values)
        if med is not None:
            grouped[group_key].append((qubits, med))
    return grouped


def _plot_lines(grouped: dict[tuple[str, str, str, str], list[tuple[int, float]]], title: str, ylabel: str, output_path: Path) -> None:
    import matplotlib.pyplot as plt  # type: ignore

    plt.figure(figsize=(12, 7))
    for (library, family, device, precision), points in grouped.items():
        points = sorted(points, key=lambda item: item[0])
        xs = [item[0] for item in points]
        ys = [item[1] for item in points]
        label = f"{library}:{family}:{device}:{precision}"
        plt.plot(xs, ys, marker="o", label=label)
    plt.title(title)
    plt.xlabel("Qubits")
    plt.ylabel(ylabel)
    plt.grid(True, alpha=0.3)
    if grouped:
        plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()


def _plot_speedup(rows: list[dict[str, Any]], output_path: Path) -> None:
    import matplotlib.pyplot as plt  # type: ignore

    cpu_buckets: dict[tuple[str, str, str, int], list[float]] = defaultdict(list)
    gpu_buckets: dict[tuple[str, str, str, int], list[float]] = defaultdict(list)
    for row in rows:
        value = safe_float(row.get("wall_s"))
        if value is None:
            continue
        key = (row["library"], row["family"], row["precision"], int(row["qubits"]))
        if row["device"] == "CPU":
            cpu_buckets[key].append(value)
        elif row["device"] == "GPU":
            gpu_buckets[key].append(value)

    series: dict[tuple[str, str, str], list[tuple[int, float]]] = defaultdict(list)
    for key, cpu_values in cpu_buckets.items():
        gpu_values = gpu_buckets.get(key)
        if not gpu_values:
            continue
        cpu_med = median(cpu_values)
        gpu_med = median(gpu_values)
        if cpu_med is None or gpu_med in (None, 0):
            continue
        library, family, precision, qubits = key
        series[(library, family, precision)].append((qubits, cpu_med / gpu_med))

    plt.figure(figsize=(12, 7))
    for (library, family, precision), points in series.items():
        points = sorted(points, key=lambda item: item[0])
        plt.plot(
            [item[0] for item in points],
            [item[1] for item in points],
            marker="o",
            label=f"{library}:{family}:{precision}",
        )
    plt.axhline(1.0, linestyle="--", color="gray", linewidth=1)
    plt.title("GPU / CPU Speedup (median wall time)")
    plt.xlabel("Qubits")
    plt.ylabel("Speedup")
    plt.grid(True, alpha=0.3)
    if series:
        plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close()


def _write_summary(rows: list[dict[str, Any]], output_path: Path) -> None:
    summary: dict[str, Any] = {"groups": []}
    buckets: dict[tuple[str, str, str, str], list[float]] = defaultdict(list)
    for row in rows:
        value = safe_float(row.get("wall_s"))
        if value is None:
            continue
        key = (row["library"], row["family"], row["device"], row["precision"])
        buckets[key].append(value)
    for key, values in sorted(buckets.items()):
        summary["groups"].append(
            {
                "library": key[0],
                "family": key[1],
                "device": key[2],
                "precision": key[3],
                "median_wall_s": median(values),
                "iqr_wall_s": iqr(values),
                "samples": len(values),
            }
        )
    output_path.write_text(__import__("json").dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


def generate_plots(input_dir: Path, output_dir: Path) -> dict[str, Any]:
    ensure_directory(output_dir)
    all_rows = _load_rows(input_dir)
    rows = _success_rows(all_rows)
    summary_path = output_dir / "summary.json"
    _write_summary(rows, summary_path)

    try:
        import matplotlib.pyplot  # type: ignore  # noqa: F401
    except ImportError:
        status = {
            "input_dir": str(input_dir),
            "output_dir": str(output_dir),
            "row_count": len(all_rows),
            "successful_non_warmup_rows": len(rows),
            "plots_generated": False,
            "reason": "matplotlib_not_installed",
            "summary_path": str(summary_path),
        }
        (output_dir / "plot-status.json").write_text(
            __import__("json").dumps(status, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return status

    time_grouped = _group_metric(rows, "wall_s")
    rss_grouped = _group_metric(rows, "peak_rss_mb")
    vram_grouped = _group_metric(rows, "gpu_peak_mem_mb")
    fidelity_grouped = _group_metric(rows, "state_fidelity_ref")

    _plot_lines(time_grouped, "Wall Time vs Qubits", "Wall time (s)", output_dir / "time_vs_qubits.png")
    _plot_lines(rss_grouped, "Peak RAM vs Qubits", "Peak RSS (MB)", output_dir / "ram_vs_qubits.png")
    _plot_lines(vram_grouped, "Peak GPU Memory vs Qubits", "Peak GPU memory (MB)", output_dir / "vram_vs_qubits.png")
    _plot_speedup(rows, output_dir / "gpu_cpu_speedup.png")
    _plot_lines(fidelity_grouped, "Fidelity vs Qubits", "State fidelity", output_dir / "fidelity_vs_qubits.png")
    return {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "row_count": len(all_rows),
        "successful_non_warmup_rows": len(rows),
        "plots_generated": True,
        "summary_path": str(summary_path),
    }
