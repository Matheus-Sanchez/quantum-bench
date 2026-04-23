from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from quantum_bench.config import expand_cases, profile_requires_capabilities
from quantum_bench.models import BenchmarkCase
from quantum_bench.runner import _resource_limit_error, build_error_row, build_result_dir, run_profile
from quantum_bench.utils import load_json, windows_to_wsl_path


def _wsl_base_command(distro: str | None) -> list[str]:
    command = ["wsl"]
    if distro:
        command.extend(["-d", distro])
    return command


def _run_wsl_bash(
    repo_root: Path,
    bash_script: str,
    *,
    distro: str | None,
    timeout_s: int,
) -> subprocess.CompletedProcess[str]:
    repo_root_wsl = windows_to_wsl_path(repo_root.resolve())
    wrapped = f"set -euo pipefail && cd {shlex.quote(repo_root_wsl)} && {bash_script}"
    command = [*_wsl_base_command(distro), "bash", "-lc", wrapped]
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=timeout_s,
        check=False,
    )


def _run_wsl_python_json(
    repo_root: Path,
    python_path: str,
    python_code: str,
    *,
    distro: str | None,
    timeout_s: int,
) -> dict[str, Any]:
    bash_script = f"{shlex.quote(python_path)} -c {shlex.quote(python_code)}"
    completed = _run_wsl_bash(repo_root, bash_script, distro=distro, timeout_s=timeout_s)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "wsl_python_failed").strip())
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError("wsl_python_produced_no_stdout")
    return json.loads(lines[-1])


def build_wsl_env_report(
    repo_root: Path,
    *,
    python_path: str = ".venv-wsl/bin/python",
    distro: str | None = None,
    timeout_s: int = 120,
) -> dict[str, Any]:
    return _run_wsl_python_json(
        repo_root,
        python_path,
        "import json; from quantum_bench.env_report import build_env_report; print(json.dumps(build_env_report(), ensure_ascii=False))",
        distro=distro,
        timeout_s=timeout_s,
    )


def build_wsl_capability_report(
    repo_root: Path,
    *,
    python_path: str = ".venv-wsl/bin/python",
    distro: str | None = None,
    timeout_s: int = 120,
    ram_fraction: float = 0.75,
    vram_fraction: float = 0.80,
    overhead_factor: float = 1.5,
) -> dict[str, Any]:
    return _run_wsl_python_json(
        repo_root,
        python_path,
        (
            "import json; "
            "from quantum_bench.capability import build_capability_report; "
            f"print(json.dumps(build_capability_report(ram_fraction={ram_fraction}, "
            f"vram_fraction={vram_fraction}, overhead_factor={overhead_factor}), ensure_ascii=False))"
        ),
        distro=distro,
        timeout_s=timeout_s,
    )


def _is_wsl_transport_error(output: str) -> bool:
    lowered = output.lower()
    markers = [
        "wsl/service/",
        "e_unexpected",
        "catastrophic failure",
        "pipe is being closed",
        "there is no distribution",
        "wsl.exe",
    ]
    return any(marker in lowered for marker in markers)


def invoke_case_wsl(
    case: BenchmarkCase,
    *,
    repo_root: Path,
    python_path: str = ".venv-wsl/bin/python",
    distro: str | None = None,
    transport_retries: int = 1,
    startup_buffer_s: int = 30,
) -> dict[str, Any]:
    limit_error = _resource_limit_error(case)
    if limit_error:
        return build_error_row(case, error_type="estimated_limit", error=limit_error)

    with tempfile.TemporaryDirectory(prefix="quantum-bench-wsl-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        payload_path = temp_dir / "case.json"
        output_path = temp_dir / "row.json"
        payload_path.write_text(json.dumps(case.to_dict(), ensure_ascii=False), encoding="utf-8")

        payload_wsl = windows_to_wsl_path(payload_path)
        output_wsl = windows_to_wsl_path(output_path)
        thread_count = "1" if case.thread_mode == "single" else str(os.cpu_count() or 1)
        bash_script = (
            f"OMP_NUM_THREADS={thread_count} "
            f"QULACS_NUM_THREADS={thread_count} "
            f"{shlex.quote(python_path)} -m quantum_bench _internal-run-case "
            f"--payload {shlex.quote(payload_wsl)} --output {shlex.quote(output_wsl)}"
        )

        attempts = 0
        while True:
            started = time.perf_counter()
            try:
                completed = _run_wsl_bash(
                    repo_root,
                    bash_script,
                    distro=distro,
                    timeout_s=case.timeout_s + startup_buffer_s,
                )
            except subprocess.TimeoutExpired as exc:
                return build_error_row(
                    case,
                    error_type="timeout",
                    error=f"timeout_after_{case.timeout_s}s:{exc}",
                    wall_s=time.perf_counter() - started,
                )

            if output_path.exists():
                return load_json(output_path)

            combined = (completed.stderr or "") + "\n" + (completed.stdout or "")
            if attempts < transport_retries and _is_wsl_transport_error(combined):
                attempts += 1
                time.sleep(3)
                continue

            return build_error_row(
                case,
                error_type=f"wsl_exit_{completed.returncode}",
                error=(combined.strip() or "wsl_case_failed"),
                wall_s=time.perf_counter() - started,
            )


def run_profile_wsl(
    profile: dict[str, Any],
    *,
    repo_root: Path,
    capabilities_path: Path | None = None,
    env_report_path: Path | None = None,
    results_dir: str | None = None,
    limit: int | None = None,
    python_path: str = ".venv-wsl/bin/python",
    distro: str | None = None,
    transport_retries: int = 1,
) -> dict[str, Any]:
    capability_report = None
    if capabilities_path:
        capability_report = load_json(capabilities_path)
    elif profile_requires_capabilities(profile):
        defaults = profile.get("defaults", {}).get("memory_limits", {})
        capability_report = build_wsl_capability_report(
            repo_root,
            python_path=python_path,
            distro=distro,
            ram_fraction=float(defaults.get("ram_fraction", 0.75)),
            vram_fraction=float(defaults.get("vram_fraction", 0.80)),
            overhead_factor=float(defaults.get("overhead_factor", 1.5)),
        )

    cases = expand_cases(profile, capability_report=capability_report, limit=limit)
    result_dir = build_result_dir(
        results_dir or profile.get("results_dir", "results"),
        str(profile.get("profile_name", "default")),
        str(profile.get("execution_env", "default")),
    )
    if env_report_path:
        env_report = load_json(env_report_path)
    else:
        env_report = build_wsl_env_report(repo_root, python_path=python_path, distro=distro)
    return run_profile(
        profile,
        cases,
        result_dir,
        capability_report=capability_report,
        case_invoker=lambda case: invoke_case_wsl(
            case,
            repo_root=repo_root,
            python_path=python_path,
            distro=distro,
            transport_retries=transport_retries,
        ),
        env_report_override=env_report,
    )
