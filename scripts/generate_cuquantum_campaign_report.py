from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DATA = REPO_ROOT / "docs" / "data"
DOCS_ASSETS = REPO_ROOT / "docs" / "assets"
DOCS_REPORTS = REPO_ROOT / "docs" / "reports"

CAMPAIGNS = [
    {
        "id": "speed-sweep",
        "name": "cuquantum-speed-sweep-wsl",
        "title": "Speed sweep",
        "profile": "profiles/cuquantum-speed-sweep-wsl.json",
        "result_dir": "results/cuquantum-speed-sweep-wsl/resume-cuquantum-speed-sweep-wsl",
        "intent": "Baseline speed and memory sweep for CPU, GPU thrust, and GPU cuStateVec at 20q to 28q.",
    },
    {
        "id": "exact-frontier",
        "name": "cuquantum-exact-frontier-wsl",
        "title": "Exact statevector frontier",
        "profile": "profiles/cuquantum-exact-frontier-wsl.json",
        "result_dir": "results/cuquantum-exact-frontier-wsl/resume-exact-frontier-20260428T010950Z",
        "intent": "Exact statevector frontier attempt using isolated per-case execution and frontier pruning.",
    },
    {
        "id": "observable-frontier",
        "name": "cuquantum-observable-frontier-wsl",
        "title": "Observable frontier",
        "profile": "profiles/cuquantum-observable-frontier-wsl.json",
        "result_dir": "results/cuquantum-observable-frontier-wsl/resume-cuquantum-observable-frontier-wsl",
        "intent": "Marginal-probability frontier attempt from 24q upward.",
    },
    {
        "id": "ideal-depth",
        "name": "cuquantum-ideal-depth-sweep-wsl",
        "title": "Ideal depth sweep",
        "profile": "profiles/cuquantum-ideal-depth-sweep-wsl.json",
        "result_dir": "results/cuquantum-ideal-depth-sweep-wsl/resume-cuquantum-ideal-depth-sweep-wsl",
        "intent": "Depth sweep without synthetic noise across ansatz, random, and trotter families.",
    },
    {
        "id": "noisy-depth",
        "name": "cuquantum-noisy-depth-sweep-wsl",
        "title": "Noisy depth sweep",
        "profile": "profiles/cuquantum-noisy-depth-sweep-wsl.json",
        "result_dir": "results/cuquantum-noisy-depth-sweep-wsl/resume-cuquantum-noisy-depth-sweep-wsl",
        "intent": "Synthetic-noise counts sweep across CPU, GPU thrust, GPU cuStateVec, and GPU tensor-network paths.",
    },
]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _safe_float(value: Any) -> float | None:
    if value in ("", None, "None"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value in ("", None, "None"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_true(value: Any) -> bool:
    return value in (True, "True", "true", "1", 1)


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def _campaign_paths(campaign: dict[str, str]) -> tuple[Path, Path, Path]:
    result_dir = REPO_ROOT / campaign["result_dir"]
    return result_dir, result_dir / "analysis-summary.json", result_dir / "results.csv"


def _group_key(row: dict[str, str]) -> tuple[str, str, str, str]:
    return (
        row.get("variant") or "unknown",
        row.get("device") or "unknown",
        row.get("precision") or "unknown",
        row.get("family") or "unknown",
    )


def _non_warmup(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows if not _is_true(row.get("warmup"))]


def _successful(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in rows if _is_true(row.get("success"))]


def _tested_qubit_stats(rows: list[dict[str, str]], key: tuple[str, str, str, str]) -> dict[str, Any]:
    matching = [row for row in rows if _group_key(row) == key]
    qubits = sorted({q for row in matching if (q := _safe_int(row.get("qubits"))) is not None})
    success_qubits = sorted({q for row in matching if _is_true(row.get("success")) and (q := _safe_int(row.get("qubits"))) is not None})
    failure_qubits = sorted({q for row in matching if not _is_true(row.get("success")) and (q := _safe_int(row.get("qubits"))) is not None})
    return {
        "tested_qubits": " ".join(str(item) for item in qubits),
        "highest_tested_qubits": max(qubits) if qubits else None,
        "highest_success_qubits": max(success_qubits) if success_qubits else None,
        "first_failure_qubits": min(failure_qubits) if failure_qubits else None,
    }


def _profile_rows(campaign_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in campaign_data:
        summary = item["summary"]
        counts = summary["counts"]
        matrix = summary["matrix"]
        status = item.get("status") or {}
        rows.append(
            {
                "campaign_id": item["id"],
                "profile_name": item["name"],
                "title": item["title"],
                "intent": item["intent"],
                "profile": item["profile"],
                "total_rows": counts["rows"],
                "non_warmup_rows": counts["non_warmup_rows"],
                "successful_non_warmup_rows": counts["successful_non_warmup_rows"],
                "failed_non_warmup_rows": counts["failed_non_warmup_rows"],
                "success_rate_non_warmup_pct": counts["success_rate_non_warmup_pct"],
                "completed_rows": status.get("completed_rows", counts["rows"]),
                "output_modes": " ".join(matrix.get("output_modes", [])),
                "noise_profiles": " ".join(matrix.get("noise_profiles", [])),
                "variants": " ".join(matrix.get("variants", [])),
                "families": " ".join(matrix.get("families", [])),
                "qubit_min": matrix.get("qubit_min"),
                "qubit_max": matrix.get("qubit_max"),
            }
        )
    return rows


def _slice_rows(campaign_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in campaign_data:
        non_warmup = _non_warmup(item["rows"])
        for group in item["summary"].get("group_summaries", []):
            key = (group["variant"], group["device"], group["precision"], group["family"])
            tested = _tested_qubit_stats(non_warmup, key)
            rows.append(
                {
                    "campaign_id": item["id"],
                    "profile_name": item["name"],
                    "variant": group["variant"],
                    "device": group["device"],
                    "precision": group["precision"],
                    "family": group["family"],
                    "rows": group["rows"],
                    "success_rows": group["success_rows"],
                    "success_rate_pct": _round((group["success_rate"] or 0) * 100, 2),
                    "stable_max_qubits": group.get("stable_max_qubits"),
                    "highest_success_qubits": tested["highest_success_qubits"],
                    "first_failure_qubits": tested["first_failure_qubits"],
                    "highest_tested_qubits": tested["highest_tested_qubits"],
                    "tested_qubits": tested["tested_qubits"],
                    "median_simulate_s": group.get("median_simulate_s"),
                    "median_wall_s": group.get("median_wall_s"),
                    "median_speedup_vs_cpu": group.get("median_speedup_vs_cpu"),
                    "median_tvd": group.get("median_tvd"),
                    "peak_rss_mb": group.get("peak_rss_mb"),
                    "peak_gpu_mem_mb": group.get("peak_gpu_mem_mb"),
                }
            )
    return rows


def _frontier_rows(campaign_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in campaign_data:
        non_warmup = _non_warmup(item["rows"])
        for frontier in item["summary"].get("stable_frontier", []):
            key = (frontier["variant"], frontier["device"], frontier["precision"], frontier["family"])
            tested = _tested_qubit_stats(non_warmup, key)
            rows.append(
                {
                    "campaign_id": item["id"],
                    "profile_name": item["name"],
                    "variant": frontier["variant"],
                    "device": frontier["device"],
                    "precision": frontier["precision"],
                    "family": frontier["family"],
                    "stable_max_qubits": frontier.get("stable_max_qubits"),
                    "stable_qubits": " ".join(str(q) for q in frontier.get("stable_qubits", [])),
                    "highest_success_qubits": tested["highest_success_qubits"],
                    "first_failure_qubits": tested["first_failure_qubits"],
                    "highest_tested_qubits": tested["highest_tested_qubits"],
                    "tested_qubits": tested["tested_qubits"],
                }
            )
    return rows


def _error_rows(campaign_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in campaign_data:
        failed_rows = [row for row in _non_warmup(item["rows"]) if not _is_true(row.get("success"))]
        failed = len(failed_rows)
        error_counts: dict[str, int] = defaultdict(int)
        for row in failed_rows:
            error_counts[row.get("error_type") or "unknown"] += 1
        for error_type, count in sorted(error_counts.items(), key=lambda item: (-item[1], item[0])):
            rows.append(
                {
                    "campaign_id": item["id"],
                    "profile_name": item["name"],
                    "error_type": error_type,
                    "count": count,
                    "pct_of_failed_non_warmup": _round(100 * count / failed, 2) if failed else None,
                }
            )
    return rows


def _curve_rows(campaign_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in campaign_data:
        buckets: dict[tuple[str, str, int, int], list[dict[str, str]]] = defaultdict(list)
        for row in _non_warmup(item["rows"]):
            depth = _safe_int(row.get("depth"))
            qubits = _safe_int(row.get("qubits"))
            if depth is None or qubits is None:
                continue
            buckets[(row.get("variant") or "unknown", row.get("family") or "unknown", qubits, depth)].append(row)
        for key, items in sorted(buckets.items()):
            successes = _successful(items)
            tvd_values = [value for r in successes if (value := _safe_float(r.get("tvd_noisy_vs_ideal") or r.get("tvd_ref"))) is not None]
            jsd_values = [value for r in successes if (value := _safe_float(r.get("jsd_noisy_vs_ideal") or r.get("jsd_ref"))) is not None]
            wall_values = [value for r in successes if (value := _safe_float(r.get("wall_s"))) is not None]
            simulate_values = [value for r in successes if (value := _safe_float(r.get("simulate_s"))) is not None]
            rows.append(
                {
                    "campaign_id": item["id"],
                    "variant": key[0],
                    "family": key[1],
                    "qubits": key[2],
                    "depth": key[3],
                    "rows": len(items),
                    "success_rows": len(successes),
                    "success_rate_pct": _round(100 * len(successes) / len(items), 2) if items else None,
                    "median_tvd": _round(median(tvd_values), 6) if tvd_values else None,
                    "median_jsd": _round(median(jsd_values), 6) if jsd_values else None,
                    "median_wall_s": _round(median(wall_values), 4) if wall_values else None,
                    "median_simulate_s": _round(median(simulate_values), 4) if simulate_values else None,
                }
            )
    return rows


def _try_import_matplotlib():
    try:
        import matplotlib.pyplot as plt  # type: ignore

        return plt
    except Exception:
        return None


def _bar_chart(plt: Any, path: Path, labels: list[str], values: list[float], title: str, ylabel: str, color: str) -> None:
    plt.figure(figsize=(11, 6))
    bars = plt.bar(range(len(labels)), values, color=color, edgecolor="#111827", linewidth=0.6)
    plt.title(title, fontsize=15, weight="bold")
    plt.ylabel(ylabel)
    plt.xticks(range(len(labels)), labels, rotation=25, ha="right")
    plt.grid(axis="y", linestyle="--", alpha=0.25)
    for bar, value in zip(bars, values):
        plt.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{value:g}", ha="center", va="bottom", fontsize=9)
    plt.tight_layout()
    plt.savefig(path, dpi=170)
    plt.close()


def _generate_plots(profile_rows: list[dict[str, Any]], slice_rows: list[dict[str, Any]], curve_rows: list[dict[str, Any]]) -> list[str]:
    plt = _try_import_matplotlib()
    if plt is None:
        return []

    DOCS_ASSETS.mkdir(parents=True, exist_ok=True)
    generated: list[str] = []

    labels = [row["campaign_id"] for row in profile_rows]
    total_values = [float(row["non_warmup_rows"]) for row in profile_rows]
    success_values = [float(row["successful_non_warmup_rows"]) for row in profile_rows]
    fail_values = [float(row["failed_non_warmup_rows"]) for row in profile_rows]

    path = DOCS_ASSETS / "cuquantum_campaign_rows_by_profile.png"
    plt.figure(figsize=(12, 6.5))
    plt.bar(labels, success_values, label="successful non-warmup", color="#0f766e")
    plt.bar(labels, fail_values, bottom=success_values, label="failed/pruned non-warmup", color="#b91c1c")
    plt.title("cuQuantum Campaign Rows By Profile", fontsize=15, weight="bold")
    plt.ylabel("Non-warmup rows")
    plt.xticks(rotation=25, ha="right")
    plt.grid(axis="y", linestyle="--", alpha=0.25)
    plt.legend()
    for idx, total in enumerate(total_values):
        plt.text(idx, total, f"{int(total)}", ha="center", va="bottom", fontsize=9)
    plt.tight_layout()
    plt.savefig(path, dpi=170)
    plt.close()
    generated.append(str(path.relative_to(REPO_ROOT)))

    path = DOCS_ASSETS / "cuquantum_campaign_success_rate.png"
    _bar_chart(
        plt,
        path,
        labels,
        [float(row["success_rate_non_warmup_pct"]) for row in profile_rows],
        "Non-Warmup Success Rate By Campaign",
        "Success rate (%)",
        "#2563eb",
    )
    generated.append(str(path.relative_to(REPO_ROOT)))

    noisy_frontier = [
        row
        for row in slice_rows
        if row["campaign_id"] == "noisy-depth" and row.get("stable_max_qubits") not in (None, "")
    ]
    path = DOCS_ASSETS / "cuquantum_noisy_stable_frontier.png"
    plt.figure(figsize=(12, 6.5))
    labels_nf = [f"{row['variant']}\n{row['family']}" for row in noisy_frontier]
    values_nf = [float(row["stable_max_qubits"]) for row in noisy_frontier]
    colors = ["#1d4ed8" if row["device"] == "CPU" else "#047857" for row in noisy_frontier]
    plt.bar(range(len(labels_nf)), values_nf, color=colors, edgecolor="#111827", linewidth=0.6)
    plt.title("Noisy Depth Sweep Stable Frontier", fontsize=15, weight="bold")
    plt.ylabel("Stable max qubits")
    plt.xticks(range(len(labels_nf)), labels_nf, rotation=35, ha="right")
    plt.ylim(0, max(values_nf or [1]) + 4)
    plt.grid(axis="y", linestyle="--", alpha=0.25)
    for idx, value in enumerate(values_nf):
        plt.text(idx, value + 0.2, str(int(value)), ha="center", va="bottom", fontsize=9)
    plt.tight_layout()
    plt.savefig(path, dpi=170)
    plt.close()
    generated.append(str(path.relative_to(REPO_ROOT)))

    speed_28 = [
        row
        for row in curve_rows
        if row["campaign_id"] == "speed-sweep" and row["qubits"] == 28 and row.get("median_simulate_s") not in (None, "")
    ]
    path = DOCS_ASSETS / "cuquantum_speed_sweep_28q_simulate_time.png"
    plt.figure(figsize=(13, 7))
    variants = ["cpu_statevector", "gpu_thrust", "gpu_custatevec"]
    families = sorted({row["family"] for row in speed_28})
    width = 0.24
    x_values = list(range(len(families)))
    for offset, variant in enumerate(variants):
        values = []
        for family in families:
            match = next((row for row in speed_28 if row["variant"] == variant and row["family"] == family), None)
            values.append(float(match["median_simulate_s"]) if match else 0.0)
        shifted = [x + (offset - 1) * width for x in x_values]
        plt.bar(shifted, values, width=width, label=variant)
    plt.title("Speed Sweep Median Simulation Time At 28q", fontsize=15, weight="bold")
    plt.ylabel("Median simulate_s")
    plt.xticks(x_values, families)
    plt.grid(axis="y", linestyle="--", alpha=0.25)
    plt.legend()
    plt.tight_layout()
    plt.savefig(path, dpi=170)
    plt.close()
    generated.append(str(path.relative_to(REPO_ROOT)))

    noisy_tvd = [
        row
        for row in curve_rows
        if row["campaign_id"] == "noisy-depth"
        and row["qubits"] == 12
        and row["variant"] in {"cpu_statevector", "gpu_thrust"}
        and row.get("median_tvd") not in (None, "")
    ]
    path = DOCS_ASSETS / "cuquantum_noisy_tvd_by_depth_12q.png"
    plt.figure(figsize=(12, 7))
    for variant in ["cpu_statevector", "gpu_thrust"]:
        for family in sorted({row["family"] for row in noisy_tvd}):
            points = sorted(
                (row["depth"], float(row["median_tvd"]))
                for row in noisy_tvd
                if row["variant"] == variant and row["family"] == family
            )
            if not points:
                continue
            plt.plot([p[0] for p in points], [p[1] for p in points], marker="o", label=f"{variant}:{family}")
    plt.title("Noisy 12q Median TVD By Depth", fontsize=15, weight="bold")
    plt.xlabel("Depth")
    plt.ylabel("Median TVD vs ideal/reference")
    plt.grid(True, alpha=0.25)
    plt.legend(fontsize=8, ncol=2)
    plt.tight_layout()
    plt.savefig(path, dpi=170)
    plt.close()
    generated.append(str(path.relative_to(REPO_ROOT)))

    return generated


def _markdown_table(rows: list[dict[str, Any]], fields: list[str], limit: int | None = None) -> str:
    selected = rows[:limit] if limit is not None else rows
    lines = ["| " + " | ".join(fields) + " |", "| " + " | ".join("---" for _ in fields) + " |"]
    for row in selected:
        lines.append("| " + " | ".join(str(row.get(field, "")) for field in fields) + " |")
    return "\n".join(lines)


def _write_report(
    campaign_data: list[dict[str, Any]],
    profile_rows: list[dict[str, Any]],
    slice_rows: list[dict[str, Any]],
    frontier_rows: list[dict[str, Any]],
    error_rows: list[dict[str, Any]],
    generated_plots: list[str],
) -> None:
    totals = {
        "rows": sum(row["total_rows"] for row in profile_rows),
        "non_warmup": sum(row["non_warmup_rows"] for row in profile_rows),
        "success": sum(row["successful_non_warmup_rows"] for row in profile_rows),
        "failed": sum(row["failed_non_warmup_rows"] for row in profile_rows),
    }
    totals["success_pct"] = _round(100 * totals["success"] / totals["non_warmup"], 2) if totals["non_warmup"] else None

    successful_slices = [row for row in slice_rows if int(row.get("success_rows") or 0) > 0]
    noisy_frontier = [
        row
        for row in slice_rows
        if row["campaign_id"] == "noisy-depth" and row.get("stable_max_qubits") not in (None, "")
    ]
    speed_top = [
        row
        for row in slice_rows
        if row["campaign_id"] == "speed-sweep" and row.get("stable_max_qubits") == 28
    ]

    lines = [
        "# cuQuantum Benchmark Campaign Report",
        "",
        "This report is the public, GitHub-safe synthesis of the cuQuantum/Qiskit Aer benchmark campaign run on the current workstation. It intentionally summarizes the raw local `results/` and `logs/` directories instead of committing those machine-specific artifacts.",
        "",
        "## Executive Summary",
        "",
        f"- Campaign profiles consolidated: `{len(campaign_data)}`.",
        f"- Total rows recorded, including warmups: `{totals['rows']}`.",
        f"- Non-warmup rows: `{totals['non_warmup']}`; successful non-warmup rows: `{totals['success']}`; failed/pruned non-warmup rows: `{totals['failed']}`.",
        f"- Overall non-warmup success rate across all campaigns: `{totals['success_pct']}%`.",
        "- The speed sweep was the cleanest positive result: CPU statevector, GPU thrust, and GPU cuStateVec all completed through `28q` for GHZ, random, ansatz, and trotter.",
        "- The exact frontier and observable/ideal frontier-style runs mostly served as failure-boundary probes. Their failures are kept as data because the point of these profiles is to push until the local WSL/GPU/hardware envelope breaks.",
        "- The noisy depth sweep produced the most useful new science-facing data: CPU and GPU thrust remained stable through `20q` for GHZ and through `16q` for ansatz/trotter under the synthetic canonical noise profile.",
        "- The appliance profiles were not included in the measured result set because Docker/NVIDIA Container Toolkit was not available on this machine at run time.",
        "",
        "## How The Campaign Was Run",
        "",
        "- Environment: WSL2 Ubuntu on Windows, Python `3.12.3`, Qiskit `1.4.5`, Qiskit Aer `0.15.1`, NVIDIA driver `581.42`, CUDA `13.0`, GPU `NVIDIA RTX A2000 12GB`.",
        "- Execution model: the long campaign was launched from the Windows host and invoked WSL one case or persistent group at a time. This kept checkpointing alive even when individual WSL/GPU executions failed.",
        "- Checkpointing: each completed row was appended to `resume-checkpoint.csv`; after each profile completed, the runner wrote final `results.csv`, `results.json`, `analysis-summary.json`, and `analysis-report.md` locally.",
        "- Success accounting: warmups are recorded but not counted in headline success rates. Frontier pruning and WSL/timeouts are not discarded; they describe where the machine stopped being able to complete the requested workload.",
        "- Public reporting: raw run directories stay local. This report publishes aggregate CSV/JSON summaries and selected PNG charts under `docs/`.",
        "",
        "## Campaign Summary",
        "",
        _markdown_table(
            profile_rows,
            [
                "campaign_id",
                "total_rows",
                "non_warmup_rows",
                "successful_non_warmup_rows",
                "failed_non_warmup_rows",
                "success_rate_non_warmup_pct",
                "qubit_min",
                "qubit_max",
            ],
        ),
        "",
        "![Rows by campaign](../assets/cuquantum_campaign_rows_by_profile.png)",
        "",
        "![Success rate by campaign](../assets/cuquantum_campaign_success_rate.png)",
        "",
        "## Main Positive Results",
        "",
        "### Speed Sweep",
        "",
        "The speed sweep completed all non-warmup rows successfully. It is the strongest apples-to-apples timing comparison in this campaign because CPU, GPU thrust, and GPU cuStateVec all reached the top of the configured grid.",
        "",
        _markdown_table(
            speed_top,
            [
                "variant",
                "device",
                "family",
                "stable_max_qubits",
                "median_simulate_s",
                "median_speedup_vs_cpu",
                "peak_rss_mb",
                "peak_gpu_mem_mb",
            ],
        ),
        "",
        "![Speed sweep 28q median simulation time](../assets/cuquantum_speed_sweep_28q_simulate_time.png)",
        "",
        "### Noisy Depth Sweep",
        "",
        "The noisy sweep used `synthetic_canonical_v1` and `counts` output. It shows a practical noisy frontier that is lower than the exact speed sweep frontier, as expected: noise, sampling, and deeper circuits make the workload materially harder.",
        "",
        _markdown_table(
            noisy_frontier,
            [
                "variant",
                "device",
                "family",
                "stable_max_qubits",
                "highest_success_qubits",
                "first_failure_qubits",
                "median_tvd",
                "median_simulate_s",
                "median_speedup_vs_cpu",
            ],
        ),
        "",
        "![Noisy stable frontier](../assets/cuquantum_noisy_stable_frontier.png)",
        "",
        "![Noisy TVD by depth](../assets/cuquantum_noisy_tvd_by_depth_12q.png)",
        "",
        "## Failure-Boundary Results",
        "",
        "The exact frontier, observable frontier, and ideal depth campaigns intentionally pushed beyond stable operation. In these profiles, a failure row is still a useful result: it identifies a boundary condition for the local machine and software stack.",
        "",
        _markdown_table(
            [row for row in profile_rows if row["campaign_id"] in {"exact-frontier", "observable-frontier", "ideal-depth"}],
            [
                "campaign_id",
                "non_warmup_rows",
                "successful_non_warmup_rows",
                "failed_non_warmup_rows",
                "success_rate_non_warmup_pct",
                "output_modes",
                "qubit_min",
                "qubit_max",
            ],
        ),
        "",
        "The observable frontier and ideal depth profiles completed as recorded campaigns but did not produce successful non-warmup measurements in this run. The dominant errors were WSL transport exits and grouped aborts after warmup failure, followed by frontier pruning where configured.",
        "",
        "## Error Summary",
        "",
        _markdown_table(error_rows, ["campaign_id", "error_type", "count", "pct_of_failed_non_warmup"], limit=20),
        "",
        "## Published Data Files",
        "",
        "- `docs/data/cuquantum-campaign-summary.json`: structured public summary.",
        "- `docs/data/cuquantum-profile-summary.csv`: spreadsheet of profile-level results.",
        "- `docs/data/cuquantum-slice-summary.csv`: spreadsheet of variant/device/family slices.",
        "- `docs/data/cuquantum-frontier-summary.csv`: spreadsheet of stable frontier and failure qubit levels.",
        "- `docs/data/cuquantum-error-summary.csv`: spreadsheet of error types by campaign.",
        "- `docs/data/cuquantum-depth-curves.csv`: spreadsheet of depth-curve medians by campaign, variant, family, qubits, and depth.",
        "",
        "## Published Figures",
        "",
        *[f"- `{plot}`" for plot in generated_plots],
        "",
        "## Interpretation",
        "",
        "- The current workstation can run sustained `28q` statevector speed sweeps on both CPU and GPU paths when the profile is controlled and the workload is exact/ideal.",
        "- Noisy sampled circuits are harder: the stable noisy frontier is `20q` for GHZ and `16q` for ansatz/trotter on both CPU statevector and GPU thrust.",
        "- cuStateVec and tensor-network paths should not be treated as automatically superior on this hardware. In the successful noisy rows, GPU thrust was often slower than CPU, and tensor-network success was sparse.",
        "- WSL stability is a real operational variable in marathon campaigns. The repo now records WSL exits/timeouts instead of hiding them, because they define reproducibility boundaries on this machine.",
        "",
        "## Reproduction Notes",
        "",
        "- Raw local inputs were read from the gitignored `results/cuquantum-*` directories.",
        "- Public assets were generated with `python scripts/generate_cuquantum_campaign_report.py` using only aggregate/sanitized fields.",
        "- To rerun the public synthesis after new local results are produced, run the same generator from the repository root.",
    ]
    report = "\n".join(lines) + "\n"
    DOCS_REPORTS.mkdir(parents=True, exist_ok=True)
    (DOCS_REPORTS / "cuquantum-benchmark-campaign.md").write_text(report, encoding="utf-8")


def main() -> int:
    DOCS_DATA.mkdir(parents=True, exist_ok=True)
    DOCS_ASSETS.mkdir(parents=True, exist_ok=True)
    DOCS_REPORTS.mkdir(parents=True, exist_ok=True)

    campaign_data: list[dict[str, Any]] = []
    for campaign in CAMPAIGNS:
        result_dir, summary_path, results_path = _campaign_paths(campaign)
        if not summary_path.exists() or not results_path.exists():
            raise FileNotFoundError(f"Missing completed campaign artifacts for {campaign['id']}: {result_dir}")
        status_path = result_dir / "resume-status.json"
        campaign_data.append(
            {
                **campaign,
                "summary": _load_json(summary_path),
                "status": _load_json(status_path) if status_path.exists() else None,
                "rows": _read_csv(results_path),
            }
        )

    profile_rows = _profile_rows(campaign_data)
    slice_rows = _slice_rows(campaign_data)
    frontier_rows = _frontier_rows(campaign_data)
    error_rows = _error_rows(campaign_data)
    curve_rows = _curve_rows(campaign_data)
    generated_plots = _generate_plots(profile_rows, slice_rows, curve_rows)

    _write_csv(
        DOCS_DATA / "cuquantum-profile-summary.csv",
        profile_rows,
        [
            "campaign_id",
            "profile_name",
            "title",
            "intent",
            "profile",
            "total_rows",
            "non_warmup_rows",
            "successful_non_warmup_rows",
            "failed_non_warmup_rows",
            "success_rate_non_warmup_pct",
            "completed_rows",
            "output_modes",
            "noise_profiles",
            "variants",
            "families",
            "qubit_min",
            "qubit_max",
        ],
    )
    _write_csv(
        DOCS_DATA / "cuquantum-slice-summary.csv",
        slice_rows,
        [
            "campaign_id",
            "profile_name",
            "variant",
            "device",
            "precision",
            "family",
            "rows",
            "success_rows",
            "success_rate_pct",
            "stable_max_qubits",
            "highest_success_qubits",
            "first_failure_qubits",
            "highest_tested_qubits",
            "tested_qubits",
            "median_simulate_s",
            "median_wall_s",
            "median_speedup_vs_cpu",
            "median_tvd",
            "peak_rss_mb",
            "peak_gpu_mem_mb",
        ],
    )
    _write_csv(
        DOCS_DATA / "cuquantum-frontier-summary.csv",
        frontier_rows,
        [
            "campaign_id",
            "profile_name",
            "variant",
            "device",
            "precision",
            "family",
            "stable_max_qubits",
            "stable_qubits",
            "highest_success_qubits",
            "first_failure_qubits",
            "highest_tested_qubits",
            "tested_qubits",
        ],
    )
    _write_csv(
        DOCS_DATA / "cuquantum-error-summary.csv",
        error_rows,
        ["campaign_id", "profile_name", "error_type", "count", "pct_of_failed_non_warmup"],
    )
    _write_csv(
        DOCS_DATA / "cuquantum-depth-curves.csv",
        curve_rows,
        [
            "campaign_id",
            "variant",
            "family",
            "qubits",
            "depth",
            "rows",
            "success_rows",
            "success_rate_pct",
            "median_tvd",
            "median_jsd",
            "median_wall_s",
            "median_simulate_s",
        ],
    )

    summary = {
        "schema_version": 1,
        "source": "local gitignored results/cuquantum-* directories",
        "campaigns": profile_rows,
        "headline": {
            "campaign_count": len(profile_rows),
            "total_rows": sum(row["total_rows"] for row in profile_rows),
            "non_warmup_rows": sum(row["non_warmup_rows"] for row in profile_rows),
            "successful_non_warmup_rows": sum(row["successful_non_warmup_rows"] for row in profile_rows),
            "failed_non_warmup_rows": sum(row["failed_non_warmup_rows"] for row in profile_rows),
            "noisy_stable_frontier": [
                row
                for row in slice_rows
                if row["campaign_id"] == "noisy-depth" and row.get("stable_max_qubits") not in (None, "")
            ],
            "speed_sweep_stable_28q_slices": [
                row
                for row in slice_rows
                if row["campaign_id"] == "speed-sweep" and row.get("stable_max_qubits") == 28
            ],
        },
        "published_csv": [
            "docs/data/cuquantum-profile-summary.csv",
            "docs/data/cuquantum-slice-summary.csv",
            "docs/data/cuquantum-frontier-summary.csv",
            "docs/data/cuquantum-error-summary.csv",
            "docs/data/cuquantum-depth-curves.csv",
        ],
        "published_plots": generated_plots,
    }
    (DOCS_DATA / "cuquantum-campaign-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_report(campaign_data, profile_rows, slice_rows, frontier_rows, error_rows, generated_plots)
    print("Wrote cuQuantum campaign report assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
