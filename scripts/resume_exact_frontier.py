from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from quantum_bench.artifacts import enrich_error_accumulation, enrich_speedups, write_run_artifacts
from quantum_bench.config import expand_cases, load_profile
from quantum_bench.env_report import build_env_report
from quantum_bench.models import BenchmarkCase, json_default
from quantum_bench.runner import (
    CSV_FIELDS,
    _csv_safe_row,
    _frontier_group_key,
    _iter_case_units,
    _normalize_unit_rows,
    _row_status_label,
    _should_stop_frontier_after_failure,
    build_error_row,
    build_result_dir,
    invoke_case_group_subprocess,
    invoke_case_subprocess,
)
from quantum_bench.utils import ensure_directory, load_json, utc_now_iso, write_json


KEY_FIELDS = (
    "library",
    "backend",
    "variant",
    "sim_method",
    "execution_mode",
    "executor",
    "output_mode",
    "noise_profile",
    "device",
    "precision",
    "family",
    "qubits",
    "depth",
    "seed",
    "repeat_index",
    "warmup",
    "thread_mode",
)


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"true", "false"}:
        return text.lower()
    return text


def _case_key(case: BenchmarkCase) -> tuple[str, ...]:
    payload = case.to_dict()
    return tuple(_stringify(payload.get(field)) for field in KEY_FIELDS)


def _row_key(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(_stringify(row.get(field)) for field in KEY_FIELDS)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _append_checkpoint(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    ensure_directory(path.parent)
    exists = path.exists()
    with path.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=CSV_FIELDS,
            extrasaction="ignore",
            quoting=csv.QUOTE_MINIMAL,
            escapechar="\\",
        )
        if not exists:
            writer.writeheader()
        for row in rows:
            writer.writerow(_csv_safe_row(row))
        handle.flush()


def _write_results(path: Path, rows: list[dict[str, Any]]) -> None:
    csv_path = path / "results.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=CSV_FIELDS,
            extrasaction="ignore",
            quoting=csv.QUOTE_MINIMAL,
            escapechar="\\",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(_csv_safe_row(row))
    (path / "results.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False, default=json_default),
        encoding="utf-8",
    )


def _load_existing_rows(results_root: Path) -> dict[tuple[str, ...], dict[str, Any]]:
    rows_by_key: dict[tuple[str, ...], dict[str, Any]] = {}
    for csv_path in sorted(results_root.glob("*/results.csv")):
        for row in _read_rows(csv_path):
            rows_by_key[_row_key(row)] = row
    return rows_by_key


def _write_manifest(
    result_dir: Path,
    profile: dict[str, Any],
    cases: list[BenchmarkCase],
    *,
    completed_seed_rows: int,
) -> None:
    manifest = {
        "schema_version": 3,
        "timestamp_utc": utc_now_iso(),
        "profile_name": profile.get("profile_name"),
        "execution_env": profile.get("execution_env"),
        "preliminary": profile.get("preliminary"),
        "result_directory": str(result_dir),
        "case_count": len(cases),
        "completed_seed_rows": completed_seed_rows,
        "resume_checkpoint": str(result_dir / "resume-checkpoint.csv"),
        "execution_modes": sorted({case.execution_mode for case in cases}),
        "executors": sorted({case.executor for case in cases}),
        "output_modes": sorted({case.output_mode for case in cases}),
        "noise_profiles": sorted({case.noise_profile for case in cases}),
        "variants": sorted({case.variant for case in cases}),
    }
    write_json(result_dir / "manifest.json", manifest)


def resume(profile_path: Path, capabilities_path: Path, result_dir: Path | None) -> dict[str, Any]:
    profile = load_profile(profile_path)
    capability_report = load_json(capabilities_path)
    cases = expand_cases(profile, capability_report=capability_report)
    results_root = Path(profile.get("results_dir", "results"))
    result_dir = result_dir or build_result_dir(
        results_root,
        str(profile.get("profile_name", "default")),
        str(profile.get("execution_env", "default")),
    )
    ensure_directory(result_dir)

    rows_by_key = _load_existing_rows(results_root)
    checkpoint_path = result_dir / "resume-checkpoint.csv"
    for row in _read_rows(checkpoint_path):
        rows_by_key[_row_key(row)] = row

    env_report = build_env_report()
    write_json(result_dir / "env-report.json", env_report)
    write_json(result_dir / "capability-report.json", capability_report)
    _write_manifest(result_dir, profile, cases, completed_seed_rows=len(rows_by_key))

    total = len(cases)
    frontier_stop_on_failure = bool(profile.get("frontier_stop_on_failure", False))
    frontier_blocked_qubits: dict[tuple[str, str, str, str, str, str, str, str], int] = {}
    processed = 0
    skipped = 0
    ran = 0

    for unit in _iter_case_units(cases):
        first_case = unit[0]
        missing = [case for case in unit if _case_key(case) not in rows_by_key]

        if not missing:
            skipped += len(unit)
            processed += len(unit)
            for case in unit:
                row = rows_by_key[_case_key(case)]
                if frontier_stop_on_failure and _should_stop_frontier_after_failure(row):
                    group_key = _frontier_group_key(case)
                    previous = frontier_blocked_qubits.get(group_key)
                    frontier_blocked_qubits[group_key] = case.qubits if previous is None else min(previous, case.qubits)
            continue

        blocked_qubits = frontier_blocked_qubits.get(_frontier_group_key(first_case))
        if blocked_qubits is not None and first_case.qubits >= blocked_qubits:
            unit_rows = [
                build_error_row(case, error_type="frontier_pruned", error=f"skipped_after_failure_at_{blocked_qubits}q")
                for case in unit
            ]
        else:
            raw_rows = (
                invoke_case_group_subprocess(unit)
                if first_case.execution_mode == "persistent_group"
                else [invoke_case_subprocess(first_case)]
            )
            unit_rows = _normalize_unit_rows(unit, raw_rows)
            if frontier_stop_on_failure and any(_should_stop_frontier_after_failure(row) for row in unit_rows):
                group_key = _frontier_group_key(first_case)
                previous = frontier_blocked_qubits.get(group_key)
                frontier_blocked_qubits[group_key] = first_case.qubits if previous is None else min(previous, first_case.qubits)

        safe_rows = [_csv_safe_row(row) for row in unit_rows]
        for case, row in zip(unit, safe_rows):
            rows_by_key[_case_key(case)] = row
            processed += 1
            ran += 1
            print(
                f"[{processed}/{total}] {case.variant} {case.family} {case.qubits}q "
                f"{case.device} {case.precision} {case.thread_mode} "
                f"{'warmup' if case.warmup else f'rep{case.repeat_index}'} -> "
                f"{_row_status_label(row)}",
                flush=True,
            )
        _append_checkpoint(checkpoint_path, safe_rows)

    ordered_rows = [rows_by_key[_case_key(case)] for case in cases if _case_key(case) in rows_by_key]
    enrich_speedups(ordered_rows)
    accumulation = enrich_error_accumulation(ordered_rows)
    artifact_status = write_run_artifacts(ordered_rows, result_dir, accumulation)
    public_rows = [{key: value for key, value in row.items() if not key.startswith("_")} for row in ordered_rows]
    _write_results(result_dir, public_rows)
    write_json(
        result_dir / "resume-status.json",
        {
            "total_cases": total,
            "completed_rows": len(public_rows),
            "skipped_existing_rows": skipped,
            "new_rows": ran,
            "artifact_status": artifact_status,
        },
    )
    return {"result_dir": str(result_dir), "total_cases": total, "completed_rows": len(public_rows), "new_rows": ran}


def main() -> int:
    parser = argparse.ArgumentParser(description="Resume the cuQuantum exact frontier campaign with per-case checkpoints.")
    parser.add_argument("--profile", default="profiles/cuquantum-exact-frontier-wsl.json")
    parser.add_argument("--capabilities", default="artifacts/capabilities-wsl-frontier.json")
    parser.add_argument("--result-dir")
    args = parser.parse_args()

    outcome = resume(
        Path(args.profile),
        Path(args.capabilities),
        Path(args.result_dir) if args.result_dir else None,
    )
    print(f"Resume completed in {outcome['result_dir']}")
    print(f"Rows: {outcome['completed_rows']}/{outcome['total_cases']} (new: {outcome['new_rows']})")
    return 0 if outcome["completed_rows"] == outcome["total_cases"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
