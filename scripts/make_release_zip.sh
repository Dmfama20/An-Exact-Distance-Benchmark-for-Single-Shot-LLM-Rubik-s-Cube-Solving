#!/usr/bin/env bash
# Build a clean release/review archive:
# no caches, no bytecode, no macOS metadata, paper INCLUDING its
# bibliography, and a LICENSE check.

set -eo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$REPO_ROOT/dist/rubiks-planning-bench.zip}"
mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"

if [[ ! -f "$REPO_ROOT/LICENSE" ]]; then
  printf '[FATAL] LICENSE missing — choose a license for code, manifest,\n'
  printf '        and result data before building a release archive.\n'
  exit 1
fi
if [[ ! -f "$REPO_ROOT/paper/literatur.bib" ]]; then
  printf '[FATAL] paper/literatur.bib missing — the manuscript must ship\n'
  printf '        with its bibliography.\n'
  exit 1
fi

cd "$REPO_ROOT"
# WITH_DATA=1 additionally packages everything needed to audit the
# reported results end to end: raw per-trial CSVs (incl. preflight and
# run_config), per-trial visible-response logs, and analysis outputs.
DATA_PATHS=()
if [[ "${WITH_DATA:-0}" == "1" ]]; then
  DATA_PATHS=(DEVIATIONS.md AUDIT_README.md
              logs/dstar_runs logs/nominal_runs
              results_dstar results_nominal)
  [[ -d audit_logs ]] && DATA_PATHS+=(audit_logs)
fi
zip -r "$OUT" \
  ANALYSIS_PLAN.md ANALYSIS_PLAN_FROZEN.md DEVIATIONS.md \
  DATASHEET.md \
  README.md LICENSE LICENSE-DATA requirements.txt \
  requirements-audit.txt \
  requirements-lock.txt .python-version .github \
  run_benchmark.py scaffold benchmarks scripts tests \
  instances data/rubiks_move_perms.json paper \
  results_power "${DATA_PATHS[@]}" \
  -x "*__pycache__*" -x "*.pyc" -x "*.pytest_cache*" \
  -x "*.DS_Store" -x "*__MACOSX*" \
  -x "data/rubiks_depth_table_d*" \
  $(if [[ "${WITH_DATA:-0}" != "1" ]]; then
      printf -- '-x logs/* -x results_dstar/* -x results_nominal/*'
    fi) \
  -x "paper/*.aux" -x "paper/*.log" -x "paper/*.out" -x "paper/*.toc" \
  -x "paper/*.fls" -x "*.fdb_latexmk" -x "*.bcf" -x "*.run.xml" \
  -x "*.bbl" -x "*.blg" -x "*.synctex.gz"

printf '\nArchive contents (top level):\n'
# Read the listing completely before truncating: "unzip -l | head" would
# SIGPIPE unzip after 25 lines and, under pipefail, fail the script with
# exit code 141 even though the archive is fine.
LISTING="$(unzip -l "$OUT")"
printf '%s\n' "$LISTING" | sed -n '1,25p'
printf '\nWrote %s\n' "$OUT"
printf 'sha256: '
if command -v shasum >/dev/null 2>&1; then
  shasum -a 256 "$OUT" | cut -d' ' -f1
else
  sha256sum "$OUT" | cut -d' ' -f1
fi
