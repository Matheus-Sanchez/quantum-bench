from __future__ import annotations

import argparse
from pathlib import Path

from quantum_bench.capability import build_capability_report
from quantum_bench.config import expand_cases, load_profile, profile_requires_capabilities
from quantum_bench.env_report import build_env_report
from quantum_bench.plotting import generate_plots
from quantum_bench.runner import build_result_dir, run_profile, write_child_output
from quantum_bench.utils import ensure_directory, load_json, write_json


def _command_env_report(args: argparse.Namespace) -> int:
    report = build_env_report()
    output = Path(args.output)
    ensure_directory(output.parent)
    write_json(output, report)
    print(f"Wrote environment report to {output}")
    return 0


def _command_capability_probe(args: argparse.Namespace) -> int:
    report = build_capability_report(
        ram_fraction=args.ram_fraction,
        vram_fraction=args.vram_fraction,
        overhead_factor=args.overhead_factor,
    )
    output = Path(args.output)
    ensure_directory(output.parent)
    write_json(output, report)
    print(f"Wrote capability report to {output}")
    return 0


def _command_run(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    capability_report = None
    if args.capabilities:
        capability_report = load_json(Path(args.capabilities))
    elif profile_requires_capabilities(profile):
        capability_report = build_capability_report()

    cases = expand_cases(profile, capability_report=capability_report, limit=args.limit)
    if args.dry_run:
        print(f"Expanded {len(cases)} cases")
        for case in cases[: min(10, len(cases))]:
            print(
                f"{case.library} {case.family} {case.qubits}q {case.device} {case.precision} "
                f"{case.thread_mode} {'warmup' if case.warmup else f'rep{case.repeat_index}'}"
            )
        return 0

    result_dir = build_result_dir(
        args.results_dir or profile.get("results_dir", "results"),
        str(profile.get("profile_name", "default")),
        str(profile.get("execution_env", "default")),
    )
    outcome = run_profile(profile, cases, result_dir, capability_report=capability_report)
    print(f"Run completed in {outcome['result_dir']}")
    return 0


def _command_plot(args: argparse.Namespace) -> int:
    report = generate_plots(Path(args.input_dir), Path(args.output_dir))
    if report.get("plots_generated"):
        print(f"Generated plots in {report['output_dir']}")
    else:
        print(
            "Generated summary only in "
            f"{report['output_dir']} because matplotlib is not installed"
        )
    return 0


def _command_internal_run_case(args: argparse.Namespace) -> int:
    write_child_output(args.payload, args.output)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quantum-bench")
    subparsers = parser.add_subparsers(dest="command", required=True)

    env_report = subparsers.add_parser("env-report", help="Write a machine and package report")
    env_report.add_argument("--output", default="artifacts/env-report.json")
    env_report.set_defaults(func=_command_env_report)

    capability = subparsers.add_parser("capability-probe", help="Estimate safe RAM/VRAM operating envelopes")
    capability.add_argument("--output", default="artifacts/capabilities.json")
    capability.add_argument("--ram-fraction", type=float, default=0.75)
    capability.add_argument("--vram-fraction", type=float, default=0.80)
    capability.add_argument("--overhead-factor", type=float, default=1.5)
    capability.set_defaults(func=_command_capability_probe)

    run = subparsers.add_parser("run", help="Run a benchmark profile")
    run.add_argument("--profile", required=True)
    run.add_argument("--capabilities")
    run.add_argument("--results-dir")
    run.add_argument("--limit", type=int)
    run.add_argument("--dry-run", action="store_true")
    run.set_defaults(func=_command_run)

    plot = subparsers.add_parser("plot", help="Generate plots from result directories")
    plot.add_argument("--input-dir", required=True)
    plot.add_argument("--output-dir", required=True)
    plot.set_defaults(func=_command_plot)

    internal = subparsers.add_parser("_internal-run-case")
    internal.add_argument("--payload", required=True)
    internal.add_argument("--output", required=True)
    internal.set_defaults(func=_command_internal_run_case)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))
