#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

session="${1:-missing-cuquantum-benchmarks}"
tag="resume-missing-cuquantum-$(date -u +%Y%m%dT%H%M%SZ)"

if tmux has-session -t "$session" 2>/dev/null; then
  echo "Session already running: $session"
  echo "Attach with: tmux attach -t $session"
  exit 0
fi

tmux new-session -d -s "$session" "bash scripts/resume_missing_cuquantum_foreground.sh $tag"

echo "SESSION: $session"
echo "LOG: logs/${tag}.log"
echo "ATTACH: tmux attach -t $session"
