from __future__ import annotations

import json
import time
from typing import Any

from quantum_bench.models import AdapterResult, BenchmarkCase, MissingDependencyError, UnsupportedCaseError
from quantum_bench.probability import distribution_from_probabilities
from quantum_bench.utils import package_version


CONTROL_BACKEND_OPTIONS = {
    "container_image",
    "container_python",
    "container_workdir",
}


def _load_qiskit() -> tuple[Any, Any, Any, Any, Any, Any, Any]:
    try:
        from qiskit import ClassicalRegister, QuantumCircuit, transpile
        from qiskit_aer import AerSimulator
        from qiskit_aer.noise import NoiseModel, ReadoutError, depolarizing_error
    except ImportError as exc:  # pragma: no cover
        raise MissingDependencyError("Qiskit Aer is not installed") from exc
    return ClassicalRegister, QuantumCircuit, transpile, AerSimulator, NoiseModel, ReadoutError, depolarizing_error


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, complex):
        return {"real": value.real, "imag": value.imag}
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _bool_or_none(value: Any) -> bool | None:
    if value is True or value is False:
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    if isinstance(value, (int, float)):
        if value == 1:
            return True
        if value == 0:
            return False
    return None


def _metadata_items(payload: Any, prefix: str = "") -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            items.extend(_metadata_items(value, path))
        return items
    if isinstance(payload, list):
        for index, value in enumerate(payload):
            path = f"{prefix}[{index}]"
            items.extend(_metadata_items(value, path))
        return items
    items.append((prefix, payload))
    return items


def _requested_custatevec(case: BenchmarkCase) -> bool:
    if case.backend_options.get("cuStateVec_enable") is True:
        return True
    return case.variant == "gpu_custatevec"


def _requested_tensor_network(case: BenchmarkCase) -> bool:
    if case.backend_options.get("method") == "tensor_network":
        return True
    return case.sim_method == "tensor_network" or case.variant in {"gpu_tensornetwork", "appliance_tensornetwork"}


def _requested_cusvaer(case: BenchmarkCase) -> bool:
    if case.backend_options.get("cusvaer_enable") is True:
        return True
    return case.variant == "appliance_cusvaer"


def _detect_effective(metadata: dict[str, Any], marker: str) -> tuple[bool | None, str | None]:
    for path, value in _metadata_items(metadata):
        canonical = "".join(ch.lower() for ch in path if ch.isalnum())
        if marker not in canonical:
            continue
        parsed = _bool_or_none(value)
        if parsed is not None:
            return parsed, path
    return None, None


def _effective_output_qubits(case: BenchmarkCase) -> list[int]:
    if case.output_qubits:
        return [qubit for qubit in case.output_qubits if 0 <= qubit < case.qubits]
    return list(range(case.qubits))


def _build_noise_model(case: BenchmarkCase, NoiseModel: Any, ReadoutError: Any, depolarizing_error: Any) -> Any | None:
    if case.noise_profile in ("", None, "none"):
        return None
    if case.noise_profile != "synthetic_canonical_v1":
        raise UnsupportedCaseError(f"Unsupported noise_profile for Qiskit Aer: {case.noise_profile!r}")
    if _requested_custatevec(case):
        raise UnsupportedCaseError("gpu_custatevec does not support synthetic noise in this harness; use counts on cpu_statevector, gpu_thrust, or gpu_tensornetwork")
    if _requested_cusvaer(case):
        raise UnsupportedCaseError("appliance_cusvaer is treated as ideal statevector only and is excluded from noise campaigns")
    if case.output_mode != "counts":
        raise UnsupportedCaseError("synthetic_canonical_v1 requires output_mode='counts'")

    noise_model = NoiseModel()
    one_qubit_error = depolarizing_error(1e-4, 1)
    two_qubit_error = depolarizing_error(1e-3, 2)
    readout_error = ReadoutError([[0.998, 0.002], [0.002, 0.998]])
    noise_model.add_all_qubit_quantum_error(one_qubit_error, ["h", "rx", "ry", "rz"])
    noise_model.add_all_qubit_quantum_error(two_qubit_error, ["cx", "cp", "swap"])
    noise_model.add_all_qubit_readout_error(readout_error)
    return noise_model


def _build_circuit(case: BenchmarkCase, ops: list[tuple], QuantumCircuit: Any, ClassicalRegister: Any) -> Any:
    if case.output_mode == "counts":
        circuit = QuantumCircuit(case.qubits, case.qubits)
    else:
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

    if case.output_mode == "statevector":
        circuit.save_statevector()
    elif case.output_mode == "marginal_probabilities":
        output_qubits = _effective_output_qubits(case)
        if not output_qubits:
            raise UnsupportedCaseError("output_qubits must be provided for marginal_probabilities mode")
        circuit.save_probabilities(qubits=output_qubits, label="probabilities")
    elif case.output_mode == "counts":
        circuit.measure(range(case.qubits), range(case.qubits))
    else:
        raise UnsupportedCaseError(f"Unsupported output_mode for Qiskit Aer: {case.output_mode!r}")

    return circuit


class QiskitAerRunner:
    def __init__(self, case: BenchmarkCase) -> None:
        (
            ClassicalRegister,
            QuantumCircuit,
            transpile,
            AerSimulator,
            NoiseModel,
            ReadoutError,
            depolarizing_error,
        ) = _load_qiskit()
        self._ClassicalRegister = ClassicalRegister
        self._QuantumCircuit = QuantumCircuit
        self._transpile = transpile

        noise_model = _build_noise_model(case, NoiseModel, ReadoutError, depolarizing_error)
        simulator_kwargs = {
            "method": case.sim_method,
            "device": case.device,
            "precision": case.precision,
        }
        simulator_kwargs.update(
            {key: value for key, value in case.backend_options.items() if key not in CONTROL_BACKEND_OPTIONS}
        )
        if noise_model is not None:
            simulator_kwargs["noise_model"] = noise_model

        started = time.perf_counter()
        try:
            self._simulator = AerSimulator(**simulator_kwargs)
        except Exception as exc:
            raise UnsupportedCaseError(f"Qiskit Aer could not initialize this case: {exc}") from exc
        self.backend_init_s = round(time.perf_counter() - started, 10)
        self.available_methods = list(getattr(self._simulator, "available_methods", lambda: [])())
        self.available_devices = list(getattr(self._simulator, "available_devices", lambda: [])())
        if case.sim_method not in self.available_methods:
            raise UnsupportedCaseError(
                f"Qiskit Aer method {case.sim_method!r} is not available; available_methods={self.available_methods}"
            )
        if case.device not in self.available_devices:
            raise UnsupportedCaseError(
                f"Qiskit Aer device {case.device!r} is not available; available_devices={self.available_devices}"
            )
        self.backend_options = simulator_kwargs

    def run_case(self, case: BenchmarkCase, ops: list[tuple]) -> AdapterResult:
        transpile_started = time.perf_counter()
        circuit = _build_circuit(case, ops, self._QuantumCircuit, self._ClassicalRegister)
        transpiled = self._transpile(circuit, self._simulator)
        transpile_s = round(time.perf_counter() - transpile_started, 10)

        run_kwargs: dict[str, Any] = {}
        if case.output_mode == "counts":
            run_kwargs["shots"] = int(case.shots or 1024)

        simulate_started = time.perf_counter()
        result = self._simulator.run(transpiled, **run_kwargs).result()
        simulate_s = round(time.perf_counter() - simulate_started, 10)

        extract_started = time.perf_counter()
        data = result.data(0) if hasattr(result, "data") else {}
        backend_metadata: dict[str, Any] = {}
        result_items = getattr(result, "results", None) or []
        if result_items:
            backend_metadata = _json_safe(getattr(result_items[0], "metadata", {}) or {})

        statevector = None
        probabilities = None
        counts = None
        if case.output_mode == "statevector":
            state = data.get("statevector")
            if state is None and hasattr(result, "get_statevector"):
                state = result.get_statevector(transpiled)
            statevector = [complex(value) for value in state] if state is not None else None
        elif case.output_mode == "marginal_probabilities":
            raw = data.get("probabilities")
            if raw is not None:
                probabilities = distribution_from_probabilities(raw, width=len(_effective_output_qubits(case)))
        elif case.output_mode == "counts":
            raw_counts = result.get_counts(transpiled)
            counts = {str(key): int(value) for key, value in dict(raw_counts).items()}
        extract_s = round(time.perf_counter() - extract_started, 10)

        backend_name = getattr(result, "backend_name", None)
        if not backend_name:
            simulator_name = getattr(self._simulator, "name", None)
            backend_name = simulator_name() if callable(simulator_name) else simulator_name
        backend_name = str(backend_name or "AerSimulator")

        cu_requested = _requested_custatevec(case)
        cu_effective, cu_source = _detect_effective(backend_metadata, "custatevec")
        tensor_requested = _requested_tensor_network(case)
        tensor_effective, tensor_source = _detect_effective(backend_metadata, "tensornetwork")
        if tensor_requested and tensor_effective is None:
            tensor_effective = case.sim_method == "tensor_network"
            tensor_source = "case.sim_method"
        cusvaer_requested = _requested_cusvaer(case)
        cusvaer_effective, cusvaer_source = _detect_effective(backend_metadata, "cusvaer")
        metadata_json = json.dumps(backend_metadata, ensure_ascii=False, sort_keys=True)
        output_qubits = _effective_output_qubits(case)

        return AdapterResult(
            backend_name=backend_name,
            statevector=statevector,
            probabilities=probabilities,
            counts=counts,
            metadata=backend_metadata,
            timings={
                "backend_init_s": self.backend_init_s,
                "transpile_s": transpile_s,
                "simulate_s": simulate_s,
                "extract_s": extract_s,
            },
            extra={
                "transpiled_depth": getattr(transpiled, "depth", lambda: None)(),
                "backend_metadata_json": metadata_json,
                "cuStateVec_requested": cu_requested,
                "cuStateVec_effective": cu_effective,
                "cuStateVec_effective_source": cu_source,
                "tensor_network_requested": tensor_requested,
                "tensor_network_effective": tensor_effective,
                "tensor_network_effective_source": tensor_source,
                "cusvaer_requested": cusvaer_requested,
                "cusvaer_effective": cusvaer_effective,
                "cusvaer_effective_source": cusvaer_source,
                "effective_output_qubits": json.dumps(output_qubits, ensure_ascii=False),
                "container_image": case.backend_options.get("container_image"),
                "cuquantum_version": package_version("cuquantum-python") or package_version("cuquantum"),
                "qiskit_aer_available_methods": json.dumps(self.available_methods, ensure_ascii=False),
                "qiskit_aer_available_devices": json.dumps(self.available_devices, ensure_ascii=False),
            },
        )


def create_qiskit_group_runner(case: BenchmarkCase) -> QiskitAerRunner:
    return QiskitAerRunner(case)


def run_qiskit_case(case: BenchmarkCase, ops: list[tuple]) -> AdapterResult:
    runner = create_qiskit_group_runner(case)
    return runner.run_case(case, ops)
