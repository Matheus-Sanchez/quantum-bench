from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DATA = REPO_ROOT / "docs" / "data"
DOCS_ASSETS = REPO_ROOT / "docs" / "assets"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _ensure_output_dir() -> None:
    DOCS_ASSETS.mkdir(parents=True, exist_ok=True)


def _gib(value_bytes: int) -> float:
    return value_bytes / (1024 ** 3)


def build_frontier_chart(frontier_data: dict) -> Path:
    entries = frontier_data["frontier"]
    labels = [
        f"{entry['device']}/{entry['precision']}\n{entry['family']}"
        for entry in entries
    ]
    values = [entry["stable_max_qubits_measured"] for entry in entries]
    colors = []
    for entry in entries:
        if entry["device"] == "CPU":
            colors.append("#1f6feb")
        elif entry["precision"] == "double":
            colors.append("#d97706")
        else:
            colors.append("#059669")

    fig, ax = plt.subplots(figsize=(12, 6.5), constrained_layout=True)
    bars = ax.bar(range(len(entries)), values, color=colors, edgecolor="#0f172a", linewidth=0.8)
    ax.set_title("Stable Frontier By Slice On The Current Machine", fontsize=16, weight="bold")
    ax.set_ylabel("Stable max qubits")
    ax.set_xticks(range(len(entries)))
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_ylim(0, max(values) + 4)
    ax.grid(axis="y", linestyle="--", alpha=0.25)

    for bar, value in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + 0.3,
            str(value),
            ha="center",
            va="bottom",
            fontsize=9,
            weight="bold",
        )

    cpu_patch = plt.Line2D([0], [0], color="#1f6feb", lw=8, label="CPU double")
    gpu_double_patch = plt.Line2D([0], [0], color="#d97706", lw=8, label="GPU double")
    gpu_single_patch = plt.Line2D([0], [0], color="#059669", lw=8, label="GPU single")
    ax.legend(handles=[cpu_patch, gpu_double_patch, gpu_single_patch], loc="upper right")

    output = DOCS_ASSETS / "current_machine_frontier_overview.png"
    fig.savefig(output, dpi=180)
    plt.close(fig)
    return output


def build_hardware_chart(frontier_data: dict, hw_data: dict) -> Path:
    fig = plt.figure(figsize=(12, 7), constrained_layout=True)
    grid = fig.add_gridspec(2, 2, height_ratios=[1.05, 1], width_ratios=[1.3, 1])
    ax_text = fig.add_subplot(grid[:, 0])
    ax_mem = fig.add_subplot(grid[0, 1])
    ax_qubits = fig.add_subplot(grid[1, 1])

    ax_text.axis("off")

    cpu = hw_data["hardware"]["cpu"]
    memory = hw_data["hardware"]["memory"]
    gpu = hw_data["hardware"]["gpu"]
    capability = hw_data["hardware"]["capability_probe"]

    lines = [
        "Current Machine Hardware",
        "",
        f"CPU: {cpu['model']}",
        f"Cores / threads: {cpu['physical_cores']} / {cpu['logical_threads']}",
        f"WSL-visible cores: {cpu['wsl_logical_threads']}",
        f"L3 cache: {cpu['l3_cache']}",
        "",
        f"Host OS: {hw_data['hardware']['host_os']}",
        f"Guest OS: {hw_data['hardware']['guest_os']}",
        "",
        f"System RAM: {memory['system_ram_gib']:.1f} GiB",
        f"WSL safe RAM budget: {memory['safe_ram_budget_gib']:.1f} GiB",
        "",
        f"GPU: {gpu['model']}",
        f"VRAM: {gpu['vram_gib']:.1f} GiB",
        f"Driver / CUDA: {gpu['driver']} / {gpu['cuda']}",
        "",
        f"Python: {hw_data['software']['python']}",
        f"Qiskit / Aer: {hw_data['software']['qiskit']} / {hw_data['software']['qiskit_aer']}",
    ]
    ax_text.text(
        0.0,
        1.0,
        "\n".join(lines),
        ha="left",
        va="top",
        fontsize=11.5,
        family="monospace",
    )

    mem_labels = ["System RAM", "Safe RAM", "GPU VRAM", "Safe VRAM"]
    mem_values = [
        memory["system_ram_gib"],
        memory["safe_ram_budget_gib"],
        gpu["vram_gib"],
        gpu["safe_vram_budget_gib"],
    ]
    mem_colors = ["#1f6feb", "#60a5fa", "#d97706", "#f59e0b"]
    ax_mem.barh(mem_labels, mem_values, color=mem_colors)
    ax_mem.set_title("Memory Envelope", fontsize=13, weight="bold")
    ax_mem.set_xlabel("GiB")
    ax_mem.grid(axis="x", linestyle="--", alpha=0.25)
    for idx, value in enumerate(mem_values):
        ax_mem.text(value + 0.15, idx, f"{value:.1f}", va="center", fontsize=9)

    qubit_labels = [
        "CPU double",
        "CPU single",
        "GPU double",
        "GPU single",
    ]
    qubit_values = [
        capability["cpu_statevector_double_recommended_max_qubits"],
        capability["cpu_statevector_single_recommended_max_qubits"],
        capability["gpu_statevector_double_recommended_max_qubits"],
        capability["gpu_statevector_single_recommended_max_qubits"],
    ]
    qubit_colors = ["#1f6feb", "#60a5fa", "#d97706", "#059669"]
    ax_qubits.bar(qubit_labels, qubit_values, color=qubit_colors)
    ax_qubits.set_title("Capability Probe Envelope", fontsize=13, weight="bold")
    ax_qubits.set_ylabel("Recommended max qubits")
    ax_qubits.set_ylim(0, max(qubit_values) + 4)
    ax_qubits.grid(axis="y", linestyle="--", alpha=0.25)
    for idx, value in enumerate(qubit_values):
        ax_qubits.text(idx, value + 0.2, str(value), ha="center", va="bottom", fontsize=9)

    output = DOCS_ASSETS / "current_machine_hardware_overview.png"
    fig.savefig(output, dpi=180)
    plt.close(fig)
    return output


def main() -> int:
    _ensure_output_dir()
    frontier_data = _load_json(DOCS_DATA / "current-machine-frontier.json")
    hw_data = _load_json(DOCS_DATA / "current-machine-hardware.json")
    build_frontier_chart(frontier_data)
    build_hardware_chart(frontier_data, hw_data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
