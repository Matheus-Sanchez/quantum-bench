from __future__ import annotations

import math
import random
from collections import Counter


Operation = tuple


def build_recipe(family: str, qubits: int, depth: int, seed: int) -> list[Operation]:
    rng = random.Random(seed)
    ops: list[Operation] = []

    if family == "ghz":
        ops.append(("h", 0))
        for index in range(qubits - 1):
            ops.append(("cx", index, index + 1))
        return ops

    if family == "qft":
        basis = seed % max(1, 2 ** min(qubits, 10))
        for qubit in range(qubits):
            if (basis >> qubit) & 1:
                ops.append(("rx", qubit, math.pi))
        for target in range(qubits):
            ops.append(("h", target))
            for control in range(target + 1, qubits):
                theta = math.pi / (2 ** (control - target))
                ops.append(("cp", control, target, theta))
        for index in range(qubits // 2):
            ops.append(("swap", index, qubits - index - 1))
        return ops

    if family == "random":
        one_qubit = ("rx", "ry", "rz")
        for _layer in range(depth):
            for qubit in range(qubits):
                gate = rng.choice(one_qubit)
                theta = rng.uniform(-math.pi, math.pi)
                ops.append((gate, qubit, theta))
            perm = list(range(qubits))
            rng.shuffle(perm)
            for start in range(0, qubits - 1, 2):
                a = perm[start]
                b = perm[start + 1]
                if a != b:
                    ops.append(("cx", a, b))
        return ops

    if family == "ansatz":
        for layer in range(depth):
            for qubit in range(qubits):
                ops.append(("ry", qubit, (layer + 1) * 0.1 + qubit * 0.03))
                ops.append(("rz", qubit, (layer + 1) * 0.2 + qubit * 0.05))
            for qubit in range(qubits - 1):
                ops.append(("cx", qubit, qubit + 1))
            if qubits > 2:
                ops.append(("cx", qubits - 1, 0))
        return ops

    if family == "trotter":
        coupling = 1.0
        field = 0.7
        dt = 1.0 / max(1, depth)
        for _step in range(depth):
            for qubit in range(qubits - 1):
                ops.append(("cx", qubit, qubit + 1))
                ops.append(("rz", qubit + 1, 2.0 * coupling * dt))
                ops.append(("cx", qubit, qubit + 1))
            for qubit in range(qubits):
                ops.append(("rx", qubit, 2.0 * field * dt))
        return ops

    raise ValueError(f"Unsupported family: {family}")


def recipe_counts(ops: list[Operation]) -> dict[str, int]:
    counter = Counter(op[0] for op in ops)
    return {
        "op_total": len(ops),
        "op_h": counter["h"],
        "op_rx": counter["rx"],
        "op_ry": counter["ry"],
        "op_rz": counter["rz"],
        "op_cx": counter["cx"],
        "op_cp": counter["cp"],
        "op_swap": counter["swap"],
    }


def logical_depth(qubits: int, ops: list[Operation]) -> int:
    occupancy = [0] * qubits
    for op in ops:
        if op[0] in {"h", "rx", "ry", "rz"}:
            qubit = op[1]
            occupancy[qubit] += 1
        else:
            a = op[1]
            b = op[2]
            depth = max(occupancy[a], occupancy[b]) + 1
            occupancy[a] = depth
            occupancy[b] = depth
    return max(occupancy, default=0)
