#!/usr/bin/env bash
# =====================================================================
#   run_dstar_study.sh — paired randomized schedule, Modules A and B
#
#   Experimental identity (ANALYSIS_PLAN.md §1):
#     - frozen snapshot pinned INSIDE run_paired_study.py (exact match)
#     - PROVIDER_ORDER is REQUIRED (exactly one upstream provider)
#     - preflight runs with the exact production payload and writes an
#       artifact into the runs dir; the analysis gate requires it.
#       SKIP_PREFLIGHT=1 leaves no artifact -> analysis is refused as
#       confirmatory (non-confirmatory only via --force).
#
#   Usage:
#     PROVIDER_ORDER=openai bash scripts/run_dstar_study.sh          # Module A
#     MODULE=B PROVIDER_ORDER=openai bash scripts/run_dstar_study.sh # Module B
#     DRY_RUN=1 bash scripts/run_dstar_study.sh
# =====================================================================

set -eo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-$(which python3 2>/dev/null || which python)}"
DRY_RUN="${DRY_RUN:-0}"
MODULE="${MODULE:-A}"

if [[ "$MODULE" == "B" ]]; then
  MANIFEST="${MANIFEST:-$REPO_ROOT/instances/rubiks_nominal_manifest_v1.json}"
  SCHEDULE="${SCHEDULE:-$REPO_ROOT/instances/rubiks_nominal_schedule_v1.json}"
  RUNS_DIR="${RUNS_DIR:-$REPO_ROOT/logs/nominal_runs}"
else
  MANIFEST="${MANIFEST:-$REPO_ROOT/instances/rubiks_dstar_manifest_v1.json}"
  SCHEDULE="${SCHEDULE:-$REPO_ROOT/instances/rubiks_schedule_v1.json}"
  RUNS_DIR="${RUNS_DIR:-$REPO_ROOT/logs/dstar_runs}"
fi

if [[ "$DRY_RUN" != "1" && -z "${PROVIDER_ORDER:-}" ]]; then
  printf '[FATAL] PROVIDER_ORDER is required (exactly one upstream\n'
  printf '        provider, e.g. PROVIDER_ORDER=openai). Routing must be\n'
  printf '        pinned for the confirmatory contrast (plan §1).\n'
  exit 1
fi
PROVIDER_ORDER="${PROVIDER_ORDER:-openai}"   # dry-run placeholder

if [[ ! -f "$SCHEDULE" ]]; then
  printf '[setup] generating paired schedule (seeded, hashed)\n'
  "$PYTHON" "$REPO_ROOT/scripts/gen_schedule.py" \
    --manifest "$MANIFEST" --out "$SCHEDULE"
fi

mkdir -p "$RUNS_DIR"
if [[ "$DRY_RUN" != "1" && "${SKIP_PREFLIGHT:-0}" != "1" ]]; then
  printf '[setup] preflight with the exact production payload\n'
  "$PYTHON" "$REPO_ROOT/scripts/preflight_reasoning_check.py" \
    --model "$("$PYTHON" -c 'import sys; sys.path.insert(0,"'"$REPO_ROOT"'/scripts/.."); from scripts.run_paired_study import EXPECTED_MODEL; print(EXPECTED_MODEL)' 2>/dev/null || echo openai/gpt-5.5-20260423)" \
    --provider-order "$PROVIDER_ORDER" \
    --out "$RUNS_DIR/preflight.json" || {
      printf '[FATAL] preflight failed — do not start collection.\n'
      exit 1
    }
elif [[ "${SKIP_PREFLIGHT:-0}" == "1" ]]; then
  printf '[WARN] SKIP_PREFLIGHT=1: no preflight artifact will exist —\n'
  printf '       the driver runs as an explicit non-confirmatory smoke\n'
  printf '       test and the analysis gate will refuse such data.\n'
fi

EXTRA=()
if [[ "$DRY_RUN" == "1" ]]; then EXTRA+=(--dry-run); fi
if [[ "${SKIP_PREFLIGHT:-0}" == "1" ]]; then
  EXTRA+=(--non-confirmatory-smoke-test)
fi
if [[ -n "${GPT_SNAPSHOT:-}" ]]; then EXTRA+=(--model "$GPT_SNAPSHOT"); fi

"$PYTHON" "$REPO_ROOT/scripts/run_paired_study.py" \
  --provider-order "$PROVIDER_ORDER" \
  --manifest "$MANIFEST" \
  --schedule "$SCHEDULE" \
  --runs-dir "$RUNS_DIR" \
  "${EXTRA[@]}"
