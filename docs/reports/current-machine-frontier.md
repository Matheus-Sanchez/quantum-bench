# Current Machine Frontier Report

This is the commit-safe benchmark report for the current development machine. It consolidates the raw local runs into a single GitHub-friendly record and avoids depending on the timestamped `results/` and `plots/` directories, which remain intentionally gitignored.

## Executive Summary

- Canonical environment: `WSL2 Ubuntu 24.04` on Windows with `NVIDIA RTX A2000 12GB`
- Canonical stack: `Python 3.12.3`, `qiskit==1.4.5`, `qiskit-aer==0.15.1`
- Canonical methodology: clean-start frontier runs, one `(device, precision, family)` slice at a time
- Secondary methodology: sustained multi-slice batch run as an endurance signal, not as the final ceiling

The clean-start campaign established a practical `28q` stable frontier for the strongest slices on this machine, including `CPU/double/ghz`, `GPU/double/ghz`, and `GPU/single/ghz`. A dedicated `29q` probe on `GPU/double/ghz` failed during warmup, which confirms `28q` as the practical stable ceiling for that slice in the current setup.

![Current machine frontier overview](../assets/current_machine_frontier_overview.png)

## Hardware Specification

| Component | Public-safe value |
|---|---|
| Host OS | `Windows 11 Pro 10.0.26200` |
| Guest OS | `WSL2 Ubuntu 24.04` on `Linux 6.6.87.2-microsoft-standard-WSL2` |
| CPU | `Intel Core i7-14700K` |
| Physical cores / logical threads | `20 / 28` |
| WSL-visible threads | `28` |
| L3 cache | `33 MiB` |
| System RAM | `27.4 GiB` |
| Safe RAM budget used by capability probe | `20.0 GiB` |
| GPU | `NVIDIA RTX A2000 12GB` |
| VRAM | `12.0 GiB` |
| Safe VRAM budget used by capability probe | `9.1 GiB` |
| Driver / CUDA | `581.42 / 13.0` |
| Python / Qiskit / Aer | `3.12.3 / 1.4.5 / 0.15.1` |

![Current machine hardware overview](../assets/current_machine_hardware_overview.png)

## Stable Frontier

| Device | Precision | Family | Stable max measured | Highest tested | Reading |
|---|---|---|---:|---:|---|
| CPU | double | `ghz` | 28 | 29 | `29q` became unstable in the measured grid |
| CPU | double | `random` | 20 | 29 | stability dropped sharply after `20q` |
| CPU | double | `ansatz` | 28 | 29 | `29q` became unstable in the measured grid |
| CPU | double | `trotter` | 28 | 29 | `29q` became unstable in the measured grid |
| GPU | double | `ghz` | 28 | 29 | `28q` stable; dedicated `29q` probe failed |
| GPU | double | `random` | 28 | 28 | stable through the top of the tested grid |
| GPU | double | `ansatz` | 28 | 28 | stable through the top of the tested grid |
| GPU | double | `trotter` | 28 | 28 | stable through the top of the tested grid |
| GPU | single | `ghz` | 28 | 29 | `29q` became unstable in the measured grid |
| GPU | single | `random` | 28 | 29 | `29q` became unstable in the measured grid |
| GPU | single | `ansatz` | 28 | 29 | `29q` became unstable in the measured grid |
| GPU | single | `trotter` | 20 | 29 | stability dropped sharply after `20q` |

## What These Results Mean

- `WSL2 + Qiskit Aer GPU` is the validated path for this machine. Native Windows remained useful for pipeline work, but not for the final NVIDIA frontier study.
- `28q` is not a universal statement about every circuit family. The ceiling depends heavily on circuit structure.
- `random` on CPU/double and `trotter` on GPU/single are the best examples of that difference: both fell well below the `28q` ceiling reached by simpler or better-behaved slices.
- The long sustained batch run was still worth keeping because it revealed a real operational behavior: the WSL service becomes less trustworthy in marathon mode. That is why the repository now treats clean-start slice runs as the canonical frontier method.

## Selected Plots

### CPU double GHZ time scaling

![CPU double GHZ time vs qubits](../assets/frontier_cpu_double_ghz_time_vs_qubits.png)

### GPU double GHZ time scaling

![GPU double GHZ time vs qubits](../assets/frontier_gpu_double_ghz_time_vs_qubits.png)

### GPU double GHZ memory scaling

![GPU double GHZ RAM vs qubits](../assets/frontier_gpu_double_ghz_ram_vs_qubits.png)

![GPU double GHZ VRAM vs qubits](../assets/frontier_gpu_double_ghz_vram_vs_qubits.png)

### GPU single GHZ time scaling

![GPU single GHZ time vs qubits](../assets/frontier_gpu_single_ghz_time_vs_qubits.png)

## Committed Artifacts

- Structured summary: [docs/data/current-machine-frontier.json](../data/current-machine-frontier.json)
- Hardware specification: [docs/data/current-machine-hardware.json](../data/current-machine-hardware.json)
- Canonical report: [docs/reports/current-machine-frontier.md](./current-machine-frontier.md)
- Public repo policy: [docs/reports/public-repo-guidelines.md](./public-repo-guidelines.md)
- Frontier orchestration script: [scripts/run_wsl_frontier_precision.ps1](../../scripts/run_wsl_frontier_precision.ps1)
- Public asset generator: [scripts/generate_public_report_assets.py](../../scripts/generate_public_report_assets.py)

## Raw Local Artifacts

The raw run outputs that generated this report remain local under `results/`, `plots/`, and `artifacts/`. They are intentionally gitignored because they are machine-specific, timestamped, and bulky. The repository presentation for GitHub should treat this report and the assets under `docs/` as the canonical committed view.
