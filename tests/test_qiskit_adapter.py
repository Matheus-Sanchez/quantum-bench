from __future__ import annotations

import unittest

from quantum_bench.models import BenchmarkCase
from quantum_bench.runner import run_child_case


def _case(**overrides) -> BenchmarkCase:
    payload = {
        "profile_name": "test",
        "execution_env": "local",
        "preliminary": True,
        "library": "qiskit_aer",
        "backend": "aer_statevector",
        "device": "CPU",
        "precision": "double",
        "family": "ghz",
        "qubits": 3,
        "depth": 1,
        "seed": 101,
        "repeat_index": 0,
        "warmup": False,
        "thread_mode": "all",
        "timeout_s": 60,
        "ram_limit_bytes": None,
        "vram_limit_bytes": None,
        "overhead_factor": 1.5,
        "max_reference_qubits": 12,
        "manifest_id": "test-case",
        "variant": "cpu_statevector",
        "sim_method": "statevector",
        "execution_mode": "isolated_frontier",
        "executor": "wsl_python",
        "backend_options": {"method": "statevector"},
        "output_mode": "statevector",
        "output_qubits": [0, 1, 2],
        "shots": 256,
        "noise_profile": "none",
    }
    payload.update(overrides)
    return BenchmarkCase(**payload)


class QiskitAdapterSmokeTests(unittest.TestCase):
    def test_statevector(self) -> None:
        row = run_child_case(_case(output_mode="statevector"))
        if row.get("error_type") == "missing_dependency":
            self.skipTest(str(row.get("error")))
        self.assertTrue(row.get("success"), row.get("error"))
        self.assertEqual(row.get("prob_l1_ref"), 0.0)

    def test_marginal_probabilities(self) -> None:
        row = run_child_case(_case(output_mode="marginal_probabilities"))
        if row.get("error_type") == "missing_dependency":
            self.skipTest(str(row.get("error")))
        self.assertTrue(row.get("success"), row.get("error"))
        self.assertEqual(row.get("tvd_ref"), 0.0)

    def test_counts_with_synthetic_noise(self) -> None:
        row = run_child_case(_case(output_mode="counts", noise_profile="synthetic_canonical_v1", shots=256))
        if row.get("error_type") == "missing_dependency":
            self.skipTest(str(row.get("error")))
        self.assertTrue(row.get("success"), row.get("error"))
        self.assertIsNotNone(row.get("tvd_noisy_vs_ideal"))

    def test_tensor_network_reports_support_or_unsupported(self) -> None:
        row = run_child_case(
            _case(
                variant="gpu_tensornetwork",
                sim_method="tensor_network",
                device="GPU",
                backend_options={"method": "tensor_network"},
                output_mode="marginal_probabilities",
            )
        )
        self.assertIn(row.get("success"), {True, False})
        if not row.get("success"):
            self.assertIn(row.get("error_type"), {"unsupported_case", "estimated_limit", "missing_dependency"})


if __name__ == "__main__":
    unittest.main()
