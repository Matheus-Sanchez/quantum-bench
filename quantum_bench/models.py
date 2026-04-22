from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class QuantumBenchError(Exception):
    """Base error for benchmark failures."""


class MissingDependencyError(QuantumBenchError):
    """Raised when a runtime dependency is not installed."""


class UnsupportedCaseError(QuantumBenchError):
    """Raised when a benchmark case is not supported by a backend."""


@dataclass
class BenchmarkCase:
    profile_name: str
    execution_env: str
    preliminary: bool
    library: str
    backend: str
    device: str
    precision: str
    family: str
    qubits: int
    depth: int
    seed: int
    repeat_index: int
    warmup: bool
    thread_mode: str
    timeout_s: int
    ram_limit_bytes: int | None
    vram_limit_bytes: int | None
    overhead_factor: float
    max_reference_qubits: int
    manifest_id: str
    sample_interval_s: float = 0.05
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AdapterResult:
    backend_name: str
    statevector: list[complex] | None
    extra: dict[str, Any] = field(default_factory=dict)


def json_default(value: Any) -> Any:
    if isinstance(value, complex):
        return {"real": value.real, "imag": value.imag}
    if hasattr(value, "to_dict"):
        return value.to_dict()
    raise TypeError(f"Object of type {type(value)!r} is not JSON serializable")
