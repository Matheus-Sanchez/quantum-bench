from __future__ import annotations

from quantum_bench.models import AdapterResult, BenchmarkCase, MissingDependencyError, UnsupportedCaseError


def run_pennylane_case(case: BenchmarkCase, ops: list[tuple]) -> AdapterResult:
    try:
        import pennylane as qml
    except ImportError as exc:  # pragma: no cover
        raise MissingDependencyError("PennyLane is not installed") from exc

    if case.precision == "single":
        raise UnsupportedCaseError("PennyLane Lightning adapter currently supports only double precision")

    device_name = "lightning.gpu" if case.device == "GPU" else "lightning.qubit"
    try:
        device = qml.device(device_name, wires=case.qubits)
    except Exception as exc:
        raise UnsupportedCaseError(f"PennyLane device {device_name!r} is not available: {exc}") from exc

    @qml.qnode(device)
    def circuit():
        for op in ops:
            gate = op[0]
            if gate == "h":
                qml.Hadamard(wires=op[1])
            elif gate == "rx":
                qml.RX(op[2], wires=op[1])
            elif gate == "ry":
                qml.RY(op[2], wires=op[1])
            elif gate == "rz":
                qml.RZ(op[2], wires=op[1])
            elif gate == "cx":
                qml.CNOT(wires=[op[1], op[2]])
            elif gate == "cp":
                qml.ControlledPhaseShift(op[3], wires=[op[1], op[2]])
            elif gate == "swap":
                qml.SWAP(wires=[op[1], op[2]])
            else:  # pragma: no cover
                raise ValueError(f"Unsupported PennyLane gate {gate!r}")
        return qml.state()

    state = circuit()
    try:
        statevector = [complex(value) for value in state.tolist()]
    except AttributeError:
        statevector = [complex(value) for value in state]
    return AdapterResult(backend_name=device_name, statevector=statevector, extra={})
