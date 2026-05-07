from __future__ import annotations

import math
import unittest

from quantum_bench.probability import (
    basis_states,
    distribution_from_counts,
    distribution_metrics,
    distribution_shape_metrics,
    hellinger_distance,
    jensen_shannon_divergence,
    normalize_distribution,
    total_variation_distance,
)


class ProbabilityMetricsTests(unittest.TestCase):
    def test_basis_order_and_normalization(self) -> None:
        self.assertEqual(basis_states(3), ["000", "001", "010", "011", "100", "101", "110", "111"])
        self.assertEqual(normalize_distribution([2, 2]), [0.5, 0.5])
        self.assertEqual(normalize_distribution([1], width=2), [1.0, 0.0, 0.0, 0.0])

    def test_divergences_for_known_distributions(self) -> None:
        left = [1.0, 0.0]
        right = [0.0, 1.0]
        self.assertAlmostEqual(total_variation_distance(left, right), 1.0)
        self.assertAlmostEqual(hellinger_distance(left, right), 1.0)
        self.assertAlmostEqual(jensen_shannon_divergence(left, right), 1.0)
        metrics = distribution_metrics(left, right)
        self.assertEqual(metrics["prob_l1"], 2.0)
        self.assertEqual(metrics["tvd"], 1.0)

    def test_counts_marginalization_uses_qiskit_bit_order(self) -> None:
        counts = {"000": 2, "101": 2}
        probabilities = distribution_from_counts(counts, total_qubits=3, output_qubits=[0, 2])
        self.assertEqual(probabilities, [0.5, 0.0, 0.0, 0.5])

    def test_shape_metrics(self) -> None:
        metrics = distribution_shape_metrics([0.1, 0.6, 0.2, 0.1], top_k=2)
        self.assertTrue(math.isclose(metrics["dominant_state_mass"] or 0.0, 0.6))
        self.assertTrue(math.isclose(metrics["topk_probability_mass"] or 0.0, 0.8))


if __name__ == "__main__":
    unittest.main()
