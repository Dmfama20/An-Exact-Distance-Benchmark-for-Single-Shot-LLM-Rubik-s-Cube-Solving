# Trial-level audit package

## Contents (present in WITH_DATA release builds)
- `logs/dstar_runs/`, `logs/nominal_runs/` — the primary per-trial
  metrics CSVs (one row per scored trial; API-error sidecars,
  `run_config.json`, and the bound `preflight.json` included), exactly
  as consumed by the analysis pipelines.
- `audit_logs/moduleA/`, `audit_logs/moduleB/` — one detailed log per
  trial, named `<arm>__<instance_id>.log`, containing: the exact
  initial cube state (JSON, replayable), the VISIBLE model response
  verbatim, the parsed move sequence, parse mode, verifier result, and
  wall time. 1731 of 1736 trials are present; the 5 absent trials are
  the 600 s global-deadline timeouts (3 in Module A, 2 in Module B),
  for which no response ever existed (their metrics rows carry the
  timeout error and are scored failures).
- `results_dstar/`, `results_nominal/` — every aggregate reported in
  the paper, regenerable via:

      python scripts/analyse_dstar.py --runs-dir logs/dstar_runs \
          --manifest instances/rubiks_dstar_manifest_v1.json \
          --schedule instances/rubiks_schedule_v1.json
      python scripts/analyse_nominal.py --runs-dir logs/nominal_runs

- `ANALYSIS_PLAN_FROZEN.md` — the immutable pre-collection protocol
  (verbatim freeze-commit file; sha256 27cce48b…79c9).
  `ANALYSIS_PLAN.md` is the annotated working copy; `DEVIATIONS.md`
  is the post-collection record.

## Log-to-row mapping
Detailed logs were written by the runner per trial; the mapping to
metrics rows (`scripts/collect_audit_logs.py`) uses the instance id
embedded in each log plus the trial end timestamp (log file mtime vs.
row timestamp, one-to-one greedy assignment, max 90 s discrepancy).
A 40-trial random consistency sample (log verdict vs. row success)
matched 40/40.

## Re-checking without the pinned dependency
`scripts/verify_solution_lite.py` (standard library only) checks
state integrity (scramble replay, state hash) and witness validity
(the witness solves the state in the claimed number of moves) for
all 868 instances, and can replay any candidate move sequence
(`--instance … --sequence …`). Note its scope: a valid witness is an
UPPER bound on the optimal distance; optimality certification comes
from the exact solver (`benchmarks/rubiks/distance.py`, validated in
`tests/test_rubiks_distance.py`).

## Distance-label certification artifacts
- `scripts/independent_distance_check.py` — re-certifies EVERY
  distance label of both manifests (868/868 confirmed) using a
  third-party engine (Kociemba's RubikOptimal cubie coordinates) and
  deliberately pruning-free search; the independent BFS layers are
  checked against the published HTM ball sizes before any instance is
  examined. Runtime ~1 minute (pure Python). The third-party
  engine is pinned in requirements-audit.txt (RubikOptimal
  1.1.0), its version is recorded in the certification output,
  and the check runs as a CI step.
- `scripts/emit_depth_table.py` — deterministically rebuilds the
  solver's depth-5 lookup cache and prints its checksum; two builds
  are byte-identical. Expected:
  `data/rubiks_depth_table_d5.bin  sha256
  83722ad307e38ec52fb17dceb05e4d03b483abf0b9bc2211ab9db46520425959`
- The completeness of the benchmark solver's two pruning rules is
  proved in the paper's appendix; the independent check above does
  not rely on it.
