from __future__ import annotations

import cmath

from quantum_bench.models import AdapterResult, BenchmarkCase, MissingDependencyError, UnsupportedCaseError


def _controlled_phase(control: int, target: int, theta: float):
    from qulacs.gate import DenseMatrix

    matrix = [[1.0 + 0.0j, 0.0 + 0.0j], [0.0 + 0.0j, cmath.exp(1j * theta)]]
    gate = DenseMatrix(target, matrix)
    gate.add_control_qubit(control, 1)
    return gate


def run_qulacs_case(case: BenchmarkCase, ops: list[tuple]) -> AdapterResult:
    if case.precision != "double":
        raise UnsupportedCaseError("Qulacs adapter currently supports only double precision")

    try:
        from qulacs import QuantumCircuit, QuantumState
        try:
            from qulacs import QuantumStateGpu  # type: ignore
        except ImportError:
            QuantumStateGpu = None  # type: ignore
        from qulacs.gate import SWAP
    except ImportError as exc:  # pragma: no cover
        raise MissingDependencyError("Qulacs is not installed") from exc

    if case.device == "GPU":
        if QuantumStateGpu is None:
            raise UnsupportedCaseError("Qulacs GPU backend is not available in this environment")
        state = QuantumStateGpu(case.qubits)
        backend_name = "Qulacs QuantumStateGpu"
    else:
        state = QuantumState(case.qubits)
        backend_name = "Qulacs QuantumState"

    state.set_zero_state()
    circuit = QuantumCircuit(case.qubits)

    for op in ops:
        gate = op[0]
        if gate == "h":
            circuit.add_H_gate(op[1])
        elif gate == "rx":
            circuit.add_RX_gate(op[1], op[2])
        elif gate == "ry":
            circuit.add_RY_gate(op[1], op[2])
        elif gate == "rz":
            circuit.add_RZ_gate(op[1], op[2])
        elif gate == "cx":
            circuit.add_CNOT_gate(op[1], op[2])
        elif gate == "cp":
            circuit.add_gate(_controlled_phase(op[1], op[2], op[3]))
        elif gate == "swap":
            circuit.add_gate(SWAP(op[1], op[2]))
        else:  # pragma: no cover
            raise ValueError(f"Unsupported Qulacs gate {gate!r}")

    circuit.update_quantum_state(state)
    vector = state.get_vector()
    statevector = [complex(value) for value in vector]
    return AdapterResult(backend_name=backend_name, statevector=statevector, extra={})
