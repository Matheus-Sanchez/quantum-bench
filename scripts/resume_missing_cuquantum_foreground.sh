#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

mkdir -p logs

tag="${1:-resume-missing-cuquantum-$(date -u +%Y%m%dT%H%M%SZ)}"
log="logs/${tag}.log"
python_bin=".venv-wsl/bin/python"
capabilities="artifacts/capabilities-wsl-frontier.json"

run_profile() {
  local profile="$1"
  local result_dir="$2"

  printf "\n=== %s START %s ===\n" "$profile" "$(date -u +%FT%TZ)" | tee -a "$log"

  set +e
  env PYTHONUNBUFFERED=1 "$python_bin" scripts/resume_exact_frontier.py \
    --profile "$profile" \
    --capabilities "$capabilities" \
    --result-dir "$result_dir" \
    2>&1 | tee -a "$log"
  local status=${PIPESTATUS[0]}
  set -e

  printf "\n=== %s END status=%s %s ===\n" "$profile" "$status" "$(date -u +%FT%TZ)" | tee -a "$log"
  return "$status"
}

printf "\n=== missing cuQuantum campaign restart %s tag=%s ===\n" "$(date -u +%FT%TZ)" "$tag" | tee -a "$log"

run_profile \
  profiles/cuquantum-exact-frontier-wsl.json \
  results/cuquantum-exact-frontier-wsl/resume-exact-frontier-20260428T010950Z

run_profile \
  profiles/cuquantum-observable-frontier-wsl.json \
  results/cuquantum-observable-frontier-wsl/resume-cuquantum-observable-frontier-wsl

run_profile \
  profiles/cuquantum-ideal-depth-sweep-wsl.json \
  results/cuquantum-ideal-depth-sweep-wsl/resume-cuquantum-ideal-depth-sweep-wsl

run_profile \
  profiles/cuquantum-noisy-depth-sweep-wsl.json \
  results/cuquantum-noisy-depth-sweep-wsl/resume-cuquantum-noisy-depth-sweep-wsl

if command -v docker >/dev/null 2>&1 && { command -v nvidia-ctk >/dev/null 2>&1 || command -v nvidia-container-cli >/dev/null 2>&1; }; then
  run_profile \
    profiles/cuquantum-exact-frontier-appliance.json \
    results/cuquantum-exact-frontier-appliance/resume-cuquantum-exact-frontier-appliance

  run_profile \
    profiles/cuquantum-observable-frontier-appliance.json \
    results/cuquantum-observable-frontier-appliance/resume-cuquantum-observable-frontier-appliance
else
  printf "\n=== appliance profiles SKIPPED %s: docker or NVIDIA Container Toolkit not available ===\n" "$(date -u +%FT%TZ)" | tee -a "$log"
fi

printf "\n=== missing cuQuantum campaign complete %s tag=%s ===\n" "$(date -u +%FT%TZ)" "$tag" | tee -a "$log"
