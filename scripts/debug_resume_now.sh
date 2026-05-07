#!/usr/bin/env bash
set -o pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

mkdir -p logs

export OMP_NUM_THREADS=8
export MKL_NUM_THREADS=8
export OPENBLAS_NUM_THREADS=8
export NUMEXPR_NUM_THREADS=8
export PYTHONUNBUFFERED=1

LOG="logs/debug-resume-now-$(date -u +%Y%m%dT%H%M%SZ).log"

{
  echo "=== DEBUG RESUME START ==="
  date -u
  echo "PWD=$(pwd)"
  echo

  echo "=== Python check ==="
  .venv-wsl/bin/python --version
  .venv-wsl/bin/python - <<'PY'
import sys
print("executable:", sys.executable)
print("version:", sys.version)
PY

  echo
  echo "=== Import check ==="
  .venv-wsl/bin/python - <<'PY'
import sys
print("python ok")
import numpy
print("numpy ok")
import qiskit
print("qiskit ok")
import quantum_bench
print("quantum_bench ok")
PY

  echo
  echo "=== Check files ==="
  ls -lh profiles/cuquantum-exact-frontier-wsl.json
  ls -lh artifacts/capabilities-wsl-frontier.json
  ls -lh results/cuquantum-exact-frontier-wsl/resume-exact-frontier-20260428T010950Z/resume-checkpoint.csv

  echo
  echo "=== Checkpoint lines before ==="
  wc -l results/cuquantum-exact-frontier-wsl/resume-exact-frontier-20260428T010950Z/resume-checkpoint.csv

  echo
  echo "=== Running resume_exact_frontier.py ==="
} 2>&1 | tee "$LOG"

.venv-wsl/bin/python -X faulthandler -u scripts/resume_exact_frontier.py \
  --profile profiles/cuquantum-exact-frontier-wsl.json \
  --capabilities artifacts/capabilities-wsl-frontier.json \
  --result-dir results/cuquantum-exact-frontier-wsl/resume-exact-frontier-20260428T010950Z \
  2>&1 | tee -a "$LOG"

RC=${PIPESTATUS[0]}

{
  echo
  echo "=== EXIT CODE: $RC ==="
  echo
  echo "=== Checkpoint lines after ==="
  wc -l results/cuquantum-exact-frontier-wsl/resume-exact-frontier-20260428T010950Z/resume-checkpoint.csv
  echo
  echo "LOG=$LOG"
} 2>&1 | tee -a "$LOG"

exit "$RC"
