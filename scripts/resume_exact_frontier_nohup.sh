#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

mkdir -p logs

tag="resume-exact-frontier-continue-$(date -u +%Y%m%dT%H%M%SZ)"
result="results/cuquantum-exact-frontier-wsl/resume-exact-frontier-20260428T010950Z"
log="logs/${tag}.log"
pidfile="logs/${tag}.pid"

printf "\n=== restart %s tag=%s ===\n" "$(date -u +%FT%TZ)" "$tag" >> "$log"

nohup env PYTHONUNBUFFERED=1 .venv-wsl/bin/python scripts/resume_exact_frontier.py \
  --profile profiles/cuquantum-exact-frontier-wsl.json \
  --capabilities artifacts/capabilities-wsl-frontier.json \
  --result-dir "$result" \
  >> "$log" 2>&1 &

pid=$!
echo "$pid" > "$pidfile"

echo "PID: $pid"
echo "LOG: $log"
