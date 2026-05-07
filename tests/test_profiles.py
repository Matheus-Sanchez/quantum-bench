from __future__ import annotations

from pathlib import Path
import unittest

from quantum_bench.config import expand_cases, load_profile


CANONICAL_PROFILES = [
    "cuquantum-exact-frontier-wsl.json",
    "cuquantum-observable-frontier-wsl.json",
    "cuquantum-ideal-depth-sweep-wsl.json",
    "cuquantum-noisy-depth-sweep-wsl.json",
    "cuquantum-exact-frontier-appliance.json",
    "cuquantum-observable-frontier-appliance.json",
]


class ProfileExpansionTests(unittest.TestCase):
    def test_canonical_profiles_expand_with_required_fields(self) -> None:
        profile_dir = Path("profiles")
        for profile_name in CANONICAL_PROFILES:
            with self.subTest(profile=profile_name):
                profile = load_profile(profile_dir / profile_name)
                cases = expand_cases(profile, limit=5)
                self.assertTrue(cases)
                for case in cases:
                    self.assertIn(case.variant, {
                        "cpu_statevector",
                        "gpu_thrust",
                        "gpu_custatevec",
                        "gpu_tensornetwork",
                        "appliance_cusvaer",
                        "appliance_tensornetwork",
                    })
                    self.assertIn(case.sim_method, {"statevector", "tensor_network"})
                    self.assertIn(case.execution_mode, {"isolated_frontier", "persistent_group"})
                    self.assertIn(case.executor, {"wsl_python", "docker_wsl"})
                    self.assertIn(case.output_mode, {"statevector", "marginal_probabilities", "counts"})
                    self.assertIsNotNone(case.shots)
                    self.assertIsNotNone(case.output_qubits)

    def test_depth_lists_expand(self) -> None:
        profile = load_profile(Path("profiles/cuquantum-ideal-depth-sweep-wsl.json"))
        cases = expand_cases(profile, limit=30)
        depths = {case.depth for case in cases}
        self.assertTrue({2, 4}.issubset(depths))


if __name__ == "__main__":
    unittest.main()
