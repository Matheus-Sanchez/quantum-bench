from __future__ import annotations

from quantum_bench.adapters.pennylane_lightning import run_pennylane_case
from quantum_bench.adapters.qiskit_aer import run_qiskit_case
from quantum_bench.adapters.qulacs_adapter import run_qulacs_case
from quantum_bench.models import AdapterResult, BenchmarkCase


def run_backend_case(case: BenchmarkCase, ops: list[tuple]) -> AdapterResult:
    if case.library == "qiskit_aer":
        return run_qiskit_case(case, ops)
    if case.library == "qulacs":
        return run_qulacs_case(case, ops)
    if case.library == "pennylane":
        return run_pennylane_case(case, ops)
    raise ValueError(f"Unsupported library: {case.library}")
