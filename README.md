# Quantum Bench

Portable CPU/GPU benchmark harness for quantum circuit simulation with an NVIDIA-first workflow.

The project is designed for two modes of use:

- `dev`: installation validation, smoke tests, portability checks, and preliminary numbers on the current machine.
- `full`: heavier benchmark campaigns on a stronger workstation, preferably Ubuntu native with one NVIDIA GPU.

## Current MVP scope

- Libraries: `Qiskit Aer`, `Qulacs`, `PennyLane Lightning`
- Circuits: `ghz`, `qft`, `random`, `ansatz`, `trotter`
- Commands:
  - `run`
  - `plot`
  - `env-report`
  - `capability-probe`

Phase 2 items such as `qsim/Cirq`, `ProjectQ`, noise, density matrices, `W`, `HHL`, and `SupermarQ` are intentionally left out of this version.

## Quick start

### 1. Create an environment

Windows PowerShell:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e .
```

Ubuntu / WSL2:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e .
```

Optional runtime dependencies for the full benchmark pipeline:

```bash
python -m pip install psutil matplotlib pynvml
python -m pip install qiskit qiskit-aer
python -m pip install qulacs
python -m pip install pennylane pennylane-lightning
```

GPU-specific packages depend on the target machine and CUDA stack. For the strongest `cuQuantum` path, prefer Ubuntu native or WSL2 Ubuntu.

## Commands

Generate a machine report:

```bash
python -m quantum_bench env-report --output artifacts/env-report.json
```

Estimate safe CPU/GPU limits:

```bash
python -m quantum_bench capability-probe --output artifacts/capabilities.json
```

Run the development profile:

```bash
python -m quantum_bench run --profile profiles/dev.json
```

Run the full profile using a capability report:

```bash
python -m quantum_bench run --profile profiles/full.json --capabilities artifacts/capabilities.json
```

Generate plots from a result directory:

```bash
python -m quantum_bench plot --input-dir results/dev --output-dir plots/dev
```

## Profile format

Profiles are JSON files. Top-level fields:

- `profile_name`
- `execution_env`
- `preliminary`
- `results_dir`
- `max_reference_qubits`
- `defaults`
- `benchmarks`

Each benchmark entry defines:

- `library`
- `backend`
- `devices`
- `precisions`
- `families`
- `qubit_grid`
- `depths`
- `seeds`
- `repeats`
- `warmups`
- `thread_modes`
- `timeouts`
- `memory_limits`

`qubit_grid` accepts either explicit lists or auto-generated grids driven by `capability-probe`.

## Output layout

Each `run` invocation creates a timestamped directory under the configured `results_dir` containing:

- `env-report.json`
- `capability-report.json` when available
- `manifest.json`
- `results.csv`
- `results.json`

Every row records machine metadata, benchmark metadata, telemetry, success state, and optional fidelity metrics.

## Notes

- This project avoids importing quantum frameworks until a specific case is executed.
- Missing dependencies or unsupported GPU/precision combinations are recorded as failed or skipped rows instead of aborting the whole campaign.
- The implementation is intentionally subprocess-based so per-case timeouts and failures are isolated.
