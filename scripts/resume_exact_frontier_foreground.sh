#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

mkdir -p logs

tag="${1:-resume-exact-frontier-tmux-$(date -u +%Y%m%dT%H%M%SZ)}"
result="results/cuquantum-exact-frontier-wsl/resume-exact-frontier-20260428T010950Z"
log="logs/${tag}.log"

printf "\n=== restart %s tag=%s ===\n" "$(date -u +%FT%TZ)" "$tag" | tee -a "$log"

set +e
env PYTHONUNBUFFERED=1 .venv-wsl/bin/python scripts/resume_exact_frontier.py \
  --profile profiles/cuquantum-exact-frontier-wsl.json \
  --capabilities artifacts/capabilities-wsl-frontier.json \
  --result-dir "$result" \
  2>&1 | tee -a "$log"
status=${PIPESTATUS[0]}
set -e

printf "\n=== exit %s status=%s ===\n" "$(date -u +%FT%TZ)" "$status" | tee -a "$log"
exit "$status"
