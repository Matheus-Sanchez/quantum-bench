from __future__ import annotations

import math
from typing import Any


def basis_states(width: int) -> list[str]:
    return [format(index, f"0{width}b") for index in range(2**width)]


def normalize_distribution(values: list[float], *, width: int | None = None) -> list[float]:
    if width is not None:
        target = 2**width
        values = values[:target] + [0.0] * max(0, target - len(values))
    clipped = [max(0.0, float(value)) for value in values]
    total = sum(clipped)
    if total <= 0:
        return clipped
    return [value / total for value in clipped]


def distribution_from_statevector(
    statevector: list[complex],
    *,
    total_qubits: int,
    output_qubits: list[int] | None = None,
) -> list[float]:
    selected = output_qubits if output_qubits is not None else list(range(total_qubits))
    width = len(selected)
    probabilities = [0.0] * (2**width)
    for index, amplitude in enumerate(statevector):
        out_index = 0
        for position, qubit in enumerate(selected):
            if (index >> qubit) & 1:
                out_index |= 1 << position
        probabilities[out_index] += amplitude.real * amplitude.real + amplitude.imag * amplitude.imag
    return normalize_distribution(probabilities, width=width)


def distribution_from_counts(
    counts: dict[str, int],
    *,
    total_qubits: int,
    output_qubits: list[int] | None = None,
) -> list[float]:
    selected = output_qubits if output_qubits is not None else list(range(total_qubits))
    width = len(selected)
    probabilities = [0.0] * (2**width)
    total = sum(max(0, int(value)) for value in counts.values())
    if total <= 0:
        return probabilities
    for bitstring, count in counts.items():
        clean = str(bitstring).replace(" ", "")
        if len(clean) < total_qubits:
            clean = clean.zfill(total_qubits)
        out_index = 0
        for position, qubit in enumerate(selected):
            source_index = len(clean) - 1 - qubit
            if 0 <= source_index < len(clean) and clean[source_index] == "1":
                out_index |= 1 << position
        probabilities[out_index] += max(0, int(count)) / total
    return normalize_distribution(probabilities, width=width)


def distribution_from_probabilities(values: Any, *, width: int) -> list[float]:
    if isinstance(values, dict):
        probabilities = [0.0] * (2**width)
        for key, value in values.items():
            if isinstance(key, str):
                index = int(key.replace(" ", ""), 2)
            else:
                index = int(key)
            if 0 <= index < len(probabilities):
                probabilities[index] = float(value)
        return normalize_distribution(probabilities, width=width)
    return normalize_distribution([float(value) for value in values], width=width)


def l1_distance(left: list[float], right: list[float]) -> float:
    width = max(len(left), len(right))
    a = left + [0.0] * (width - len(left))
    b = right + [0.0] * (width - len(right))
    return sum(abs(x - y) for x, y in zip(a, b))


def total_variation_distance(left: list[float], right: list[float]) -> float:
    return 0.5 * l1_distance(left, right)


def hellinger_distance(left: list[float], right: list[float]) -> float:
    width = max(len(left), len(right))
    a = normalize_distribution(left + [0.0] * (width - len(left)))
    b = normalize_distribution(right + [0.0] * (width - len(right)))
    return math.sqrt(sum((math.sqrt(x) - math.sqrt(y)) ** 2 for x, y in zip(a, b)) / 2.0)


def _kl_divergence(left: list[float], right: list[float]) -> float:
    total = 0.0
    for x, y in zip(left, right):
        if x > 0 and y > 0:
            total += x * math.log2(x / y)
    return total


def jensen_shannon_divergence(left: list[float], right: list[float]) -> float:
    width = max(len(left), len(right))
    a = normalize_distribution(left + [0.0] * (width - len(left)))
    b = normalize_distribution(right + [0.0] * (width - len(right)))
    middle = [(x + y) / 2.0 for x, y in zip(a, b)]
    return 0.5 * _kl_divergence(a, middle) + 0.5 * _kl_divergence(b, middle)


def distribution_metrics(left: list[float] | None, right: list[float] | None) -> dict[str, float | None]:
    if left is None or right is None:
        return {"prob_l1": None, "tvd": None, "hellinger": None, "jsd": None}
    return {
        "prob_l1": round(l1_distance(left, right), 10),
        "tvd": round(total_variation_distance(left, right), 10),
        "hellinger": round(hellinger_distance(left, right), 10),
        "jsd": round(jensen_shannon_divergence(left, right), 10),
    }


def distribution_shape_metrics(probabilities: list[float] | None, *, top_k: int = 8) -> dict[str, float | None]:
    if probabilities is None:
        return {"dominant_state_mass": None, "topk_probability_mass": None}
    normalized = normalize_distribution(probabilities)
    if not normalized:
        return {"dominant_state_mass": None, "topk_probability_mass": None}
    ordered = sorted(normalized, reverse=True)
    return {
        "dominant_state_mass": round(ordered[0], 10),
        "topk_probability_mass": round(sum(ordered[:top_k]), 10),
    }


def probability_payload(probabilities: list[float], *, width: int) -> dict[str, float]:
    normalized = normalize_distribution(probabilities, width=width)
    return {state: normalized[index] for index, state in enumerate(basis_states(width))}
