from __future__ import annotations

import cmath
import math


def _apply_1q(state: list[complex], qubit: int, matrix: tuple[tuple[complex, complex], tuple[complex, complex]]) -> None:
    step = 1 << qubit
    size = len(state)
    for start in range(0, size, 2 * step):
        for offset in range(step):
            i0 = start + offset
            i1 = i0 + step
            a = state[i0]
            b = state[i1]
            state[i0] = matrix[0][0] * a + matrix[0][1] * b
            state[i1] = matrix[1][0] * a + matrix[1][1] * b


def _apply_cx(state: list[complex], control: int, target: int) -> None:
    size = len(state)
    for index in range(size):
        if ((index >> control) & 1) == 1 and ((index >> target) & 1) == 0:
            partner = index | (1 << target)
            state[index], state[partner] = state[partner], state[index]


def _apply_cp(state: list[complex], control: int, target: int, theta: float) -> None:
    phase = cmath.exp(1j * theta)
    for index in range(len(state)):
        if ((index >> control) & 1) == 1 and ((index >> target) & 1) == 1:
            state[index] *= phase


def _apply_swap(state: list[complex], a: int, b: int) -> None:
    if a == b:
        return
    for index in range(len(state)):
        abit = (index >> a) & 1
        bbit = (index >> b) & 1
        if abit != bbit and abit == 0:
            partner = index ^ ((1 << a) | (1 << b))
            state[index], state[partner] = state[partner], state[index]


def reference_statevector(qubits: int, ops: list[tuple]) -> list[complex]:
    state = [0j] * (2 ** qubits)
    state[0] = 1.0 + 0j

    for op in ops:
        gate = op[0]
        if gate == "h":
            s = 1.0 / math.sqrt(2.0)
            _apply_1q(state, op[1], ((s, s), (s, -s)))
        elif gate == "rx":
            theta = op[2]
            c = math.cos(theta / 2.0)
            s = math.sin(theta / 2.0)
            _apply_1q(state, op[1], ((c, -1j * s), (-1j * s, c)))
        elif gate == "ry":
            theta = op[2]
            c = math.cos(theta / 2.0)
            s = math.sin(theta / 2.0)
            _apply_1q(state, op[1], ((c, -s), (s, c)))
        elif gate == "rz":
            theta = op[2]
            _apply_1q(
                state,
                op[1],
                (
                    (cmath.exp(-1j * theta / 2.0), 0j),
                    (0j, cmath.exp(1j * theta / 2.0)),
                ),
            )
        elif gate == "cx":
            _apply_cx(state, op[1], op[2])
        elif gate == "cp":
            _apply_cp(state, op[1], op[2], op[3])
        elif gate == "swap":
            _apply_swap(state, op[1], op[2])
        else:  # pragma: no cover
            raise ValueError(f"Unsupported gate in reference simulator: {gate}")
    return state


def _norm(state: list[complex]) -> float:
    return math.sqrt(sum((amp.real * amp.real + amp.imag * amp.imag) for amp in state))


def normalize_statevector(state: list[complex]) -> list[complex]:
    magnitude = _norm(state)
    if magnitude == 0:
        return state[:]
    return [amp / magnitude for amp in state]


def state_fidelity(reference: list[complex], measured: list[complex]) -> float:
    if len(reference) != len(measured):
        raise ValueError("Statevector sizes do not match")
    ref = normalize_statevector(list(reference))
    got = normalize_statevector(list(measured))
    overlap = sum(a.conjugate() * b for a, b in zip(ref, got))
    fidelity = overlap.real * overlap.real + overlap.imag * overlap.imag
    return max(0.0, min(1.0, fidelity))


def trace_distance_pure(reference: list[complex], measured: list[complex]) -> float:
    fidelity = state_fidelity(reference, measured)
    return math.sqrt(max(0.0, 1.0 - fidelity))
