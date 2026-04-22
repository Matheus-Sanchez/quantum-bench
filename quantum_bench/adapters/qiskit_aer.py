from __future__ import annotations

from quantum_bench.models import AdapterResult, BenchmarkCase, MissingDependencyError


def run_qiskit_case(case: BenchmarkCase, ops: list[tuple]) -> AdapterResult:
    try:
        from qiskit import QuantumCircuit, transpile
        from qiskit_aer import AerSimulator
    except ImportError as exc:  # pragma: no cover
        raise MissingDependencyError("Qiskit Aer is not installed") from exc

    simulator_kwargs = {
        "method": "statevector",
        "device": case.device,
        "precision": case.precision,
    }
    simulator = AerSimulator(**simulator_kwargs)
    circuit = QuantumCircuit(case.qubits)

    for op in ops:
        gate = op[0]
        if gate == "h":
            circuit.h(op[1])
        elif gate == "rx":
            circuit.rx(op[2], op[1])
        elif gate == "ry":
            circuit.ry(op[2], op[1])
        elif gate == "rz":
            circuit.rz(op[2], op[1])
        elif gate == "cx":
            circuit.cx(op[1], op[2])
        elif gate == "cp":
            circuit.cp(op[3], op[1], op[2])
        elif gate == "swap":
            circuit.swap(op[1], op[2])
        else:  # pragma: no cover
            raise ValueError(f"Unsupported Qiskit gate {gate!r}")

    circuit.save_statevector()
    transpiled = transpile(circuit, simulator)
    result = simulator.run(transpiled).result()
    data = result.data(0)
    state = data.get("statevector")
    if state is None and hasattr(result, "get_statevector"):
        state = result.get_statevector(transpiled)
    statevector = [complex(value) for value in state] if state is not None else None
    return AdapterResult(
        backend_name=f"AerSimulator(method=statevector,device={case.device},precision={case.precision})",
        statevector=statevector,
        extra={"transpiled_depth": getattr(transpiled, "depth", lambda: None)()},
    )
