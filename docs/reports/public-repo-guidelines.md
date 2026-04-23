# Public Repository Guidelines

This document describes what should and should not be committed from local benchmark campaigns when the goal is a clean public GitHub repository.

## Safe To Commit

- Source code under `quantum_bench/`, `profiles/`, and `scripts/`
- Curated documentation under `docs/`
- Curated plots copied into `docs/assets/`
- Sanitized JSON summaries under `docs/data/`
- Human-written benchmark reports under `docs/reports/`
- Benchmark methodology descriptions, profile definitions, and commit-safe environment summaries

## Keep Local Only

- Raw timestamped directories under `results/`
- Raw generated directories under `plots/`
- Machine snapshots under `artifacts/`
- Virtual environments such as `.venv/` and `.venv-wsl/`
- Temporary orchestration output such as `.campaign-temp/`
- Any file that exposes usernames, full local paths, temporary directories, hostnames, or raw environment-variable dumps

## Why The Separation Matters

- Raw outputs are large, noisy, and machine-specific.
- Raw environment reports can expose local identifiers that are not useful in a public repository.
- Curated reports are easier to review, safer to publish, and much easier to keep stable over time.

## Recommended Public Layout

- `README.md` for the high-level story
- `docs/reports/current-machine-frontier.md` for the canonical benchmark narrative
- `docs/reports/public-repo-guidelines.md` for the publication policy
- `docs/data/current-machine-frontier.json` for the public structured summary
- `docs/data/current-machine-hardware.json` for the public-safe hardware specification
- `docs/assets/` for the small set of committed figures that support the report
