# Verified-Depth Rubik's Cube Benchmark for Single-Shot LLM Solution Generation

A benchmark for single-shot, full-sequence LLM solution generation on
the 3×3×3 Rubik's Cube along an **exact, verified difficulty axis**
(relevant to, but not by itself an identification of, planning): every
instance is
certified at its exact optimal solution distance **d\*** (half-turn
metric) by a solver whose BFS levels reproduce the published HTM position
counts. Instances are pre-generated into a hashed manifest and served
byte-identically to every configuration; the prompt discloses neither the
depth nor any difficulty label.

The repository is organised as a small **benchmark scaffold** plus
plug-in benchmarks: everything benchmark-agnostic (LLM client with
per-call audit logging, manifest-paired trial runner, metrics schema)
lives in `scaffold/`; the task itself (prompt, parsing, deterministic
verification, instance generation) lives in `benchmarks/rubiks/`.
Additional benchmarks can be added as further packages under
`benchmarks/` without touching the scaffold.

---

## Design (frozen before data collection — immutable protocol in
ANALYSIS_PLAN_FROZEN.md; ANALYSIS_PLAN.md is the annotated working
copy; post-collection record in DEVIATIONS.md)

- **Task:** one completed API call per trial, no tools, no simulator
  access; the returned Singmaster sequence is verified deterministically
  (magiccube). pass@1 per trial.
- **Depth:** verified optimal distance d\* ∈ {1..10}. d\* ≤ 5 cells are
  sampled uniformly from the exhaustively enumerated population; deeper
  cells use distance-certified rejection sampling (acceptance statistics
  in the manifest).
- **Instances:** `instances/rubiks_dstar_manifest_v1.json` — 468
  instances (18 at d\*=1, the full population; 50 per depth at d\*=2..10),
  each with a stable ID, SHA-256 state hash, private scramble, and an
  optimal witness solution. Trial i of every configuration uses the i-th
  instance of that depth: pairing holds by construction.
- **Blinding:** the prompt text is byte-identical across depths except
  for the cube state (enforced by test).
- **Scope (deliberate):** the demonstration study tests a SINGLE
  backbone (GPT-5.5) under one controlled lever — this repository's
  contribution is the benchmark instrument (verified depth axis,
  blinding, pairing, fail-closed gates), not a model survey;
  cross-model campaigns are what the released instrument is for.
- **Primary configurations (same lever):** the frozen snapshot
  `openai/gpt-5.5-20260423` (exact-match enforced by the driver;
  recorded in ANALYSIS_PLAN.md §1), reasoning effort `none` (base) vs.
  `medium` (reason) — identical 32 k ceiling, temperature unset (the
  GPT-5.5 endpoints support no sampling parameters; the request omits
  the field, plan item 54), n = 50
  per cell.
- **Experimental identity:** exactly ONE upstream provider is pinned
  (`--provider-order`, required; `require_parameters`, no fallbacks);
  the verbatim reasoning request is logged per trial; every row carries
  a `run_config_hash` over (model, provider, efforts, temperature,
  ceiling, timeout, manifest sha, schedule sha) and **resume accepts
  only rows with the identical hash** — runs from different
  configurations can never mix. A preflight with the exact production
  payload must confirm the contrast is real before collection and
  writes `preflight.json` into the runs dir (the analysis gate requires
  it; `SKIP_PREFLIGHT=1` therefore makes the run non-confirmatory).
  Execution follows a pre-generated, hashed pair-level random schedule
  (`instances/rubiks_schedule_v1.json`, structurally validated against
  the manifest before every run): depth and arm order are unconfounded
  with wall time, paired calls run back-to-back.
- **Module B (nominal-vs-verified, part of the main study):**
  `instances/rubiks_nominal_manifest_v1.json` — 400 instances indexed by
  nominal scramble length 1–8 with the verified d\* stored per instance
  (collapse fractions 10–14 % at lengths 6–8). Fully executable: own
  paired schedule (`instances/rubiks_nominal_schedule_v1.json`), driver
  mode `MODULE=B`, and a dedicated analysis
  (`scripts/analyse_nominal.py`) with the SAME confirmatory gate as
  Module A. Primary test: per arm, logistic success ~ collapsed +
  C(nominal length) over lengths 6–8, LR χ²(1) on the collapsed
  coefficient, ALWAYS one Holm family of fixed size 2 over the two arms
  (a not-estimable arm enters with p = 1, fail-closed), process-bootstrap
  OR CIs (instances resampled, stratified by length; ≥50 % convergence
  required); estimator and separation rule are shared verbatim with the
  precision simulation (`fit_collapsed_lr`). The collapsed share among successes is descriptive only
  and always reported next to the manifest base rate. Estimand: the
  scramble process (draws with replacement); a unique-state view is a
  labelled sensitivity analysis. Main-study size: 936 (A) + 800 (B) =
  1 736 trials; Module C (repeated sampling, ≈ 360 trials) follows as a
  non-confirmatory annex (plan §7).
- **Per-trial audit fields:** `finish_reason`, split
  prompt/completion/reasoning token counts, the actual
  `requested_max_tokens` of the call, retry count and kinds, echoed
  `response_model`, provider, `system_fingerprint`.
- **Action space = metric:** prompt, parser, and evaluator admit
  exactly the 18 HTM face turns in which d\* is defined; slice,
  rotation, and wide moves are rejected. The scored answer is the
  strict final line; a lenient fallback parse is logged (`parse_mode`,
  `lenient_rescue`) but never scored.
- **Random-sequence baseline:** success probability of a uniform random
  policy per depth at output lengths d\*, d\*+1, d\*+2
  (`instances/rubiks_random_baseline_v1.csv`; ~5.6 % at d\*=1/len 1,
  ≤0.36 % at d\*=2, ≈0 from d\*=4).
- **Confirmatory gate:** `scripts/analyse_dstar.py` refuses to emit
  confirmatory outputs on: broken pairing, short cells, missing depths,
  a missing `--manifest` (instance identity MUST be validated against
  the pinned manifest), harness-error sidecar files, mixed
  `run_config_hash` or schedule hashes, more than one upstream
  provider, snapshot drift within an arm (mixed `response_model`
  echoes), reasoning requests that differ from the frozen plan values
  (`{"effort": "none"}` / `{"effort": "medium"}`), a missing or failed
  `preflight.json`, or non-plan temperature/ceiling values. `--force`
  yields an explicitly non-confirmatory pass. Transport failures are
  retried in-trial (inside one global 600 s deadline per trial) and
  logged to a sidecar CSV, never scored; unexpected harness exceptions
  abort the run and never become trial rows.

## Workflow

```bash
pip install -r requirements.txt
export OPENROUTER_API_KEY=sk-or-...

# 0. validate the move engine and distance solver
python tests/test_rubiks_distance.py
# Reference runtimes (Apple M-class laptop): full Module B analysis
# ~8 s (older hardware: a few minutes; --smoke for a fast marked
# pass); test_analyse_nominal.py ~10 s; independent distance
# certification (scripts/independent_distance_check.py) ~1 min.
# Test files are runnable scripts; expensive work only runs under
# python tests/<file>.py, never at import time.

# 1. (already done, committed) generate + verify the instance manifest
python scripts/gen_rubiks_instances.py --depths 1,2,3,4,5,6,7,8,9,10 \
    --per-depth 50 --seed 42 --out instances/rubiks_dstar_manifest_v1.json

# 2. Module A (verified-d* study). PROVIDER_ORDER is REQUIRED and must
#    name exactly one upstream provider; the preflight runs with the
#    exact production payload and writes logs/dstar_runs/preflight.json.
PROVIDER_ORDER=openai bash scripts/run_dstar_study.sh
# DRY_RUN=1 to preview; exits non-zero unless every trial is complete

# 3. Module B (nominal-vs-verified contrast, same protocol)
MODULE=B PROVIDER_ORDER=openai bash scripts/run_dstar_study.sh

# 4. analyse exactly per ANALYSIS_PLAN.md (--manifest AND --schedule
#    are REQUIRED for confirmatory outputs; see the gate list above)
python scripts/analyse_dstar.py \
    --manifest instances/rubiks_dstar_manifest_v1.json \
    --schedule instances/rubiks_schedule_v1.json
python scripts/analyse_nominal.py           # -> results_nominal/
                                            # (defaults carry both files)
```

### Pre-collection checklist (must be complete BEFORE the first call)

1. Verify that the frozen snapshot slug `openai/gpt-5.5-20260423`
   exists on the gateway; if the canonical form differs, update
   `EXPECTED_MODEL` in `scripts/run_paired_study.py` **and**
   ANALYSIS_PLAN.md §1 together, before collection.
2. Choose the single upstream provider and set `PROVIDER_ORDER`.
3. Add a `LICENSE` file (code, manifests, result data) —
   `scripts/make_release_zip.sh` refuses to build an archive without it.
4. Commit the plan and deposit it in an immutable, timestamped archive
   (e.g. OSF/Zenodo); record the deposit in ANALYSIS_PLAN.md. Until
   then the plan is "prespecified", not "preregistered".
5. Archive one fully green CI run from a clean Python 3.12 environment
   immutably (e.g. keep the GitHub Actions run URL + log artifact with
   the deposit) — all six suites, including the solver validation.
6. Run the preflight (step 2 does this automatically) and check
   `preflight.json` says `"verdict": "ok"`. The driver itself refuses
   to start without a valid preflight artifact
   (`--non-confirmatory-smoke-test` is the explicit opt-out for smoke
   tests; such runs cannot pass the confirmatory gate).

A single manual cell:

```bash
python run_benchmark.py --benchmark rubiks --difficulty 5 --tests 50 \
    --manifest instances/rubiks_dstar_manifest_v1.json \
    --model openai/gpt-5.5-20260423 --reasoning-effort none \
    --require-parameters --no-fallbacks --provider-order openai
```

## Repository map

```
ANALYSIS_PLAN_FROZEN.md              IMMUTABLE pre-collection protocol
                                     (verbatim freeze-commit file)
ANALYSIS_PLAN.md                     annotated working copy of the plan
DEVIATIONS.md                        post-collection deviations record
run_benchmark.py                     CLI entry point
scaffold/
  llm.py                             LLM client with per-call audit logging
  runner.py                          manifest-paired single-shot trial loop
  metrics.py                         metrics schema + CSV logger
  registry.py                        benchmark plugin registry
benchmarks/rubiks/
  benchmark.py                       blinded prompt, parser, verifier
  distance.py                        move engine + exact d* solver
instances/rubiks_dstar_manifest_v1.json   pinned instance set (Module A)
instances/rubiks_schedule_v1.json         hashed pair-level schedule (A)
instances/rubiks_nominal_manifest_v1.json pinned instance set (Module B)
instances/rubiks_nominal_schedule_v1.json hashed pair-level schedule (B)
instances/MANIFEST_SHAS.json              additional pinned manifest hashes
instances/rubiks_random_baseline_v1.csv   random-sequence baseline
scripts/
  gen_rubiks_instances.py            manifest generator (verified d*)
  run_dstar_study.sh                 study wrapper (pinning + preflight)
  run_paired_study.py                paired-schedule driver (resumable)
  gen_schedule.py                    hashed pair-level randomization
  preflight_reasoning_check.py       interface check for the arm contrast
  make_release_zip.sh                clean review/release archive
  analyse_dstar.py                   frozen analysis pipeline (Module A)
  analyse_nominal.py                 Module B analysis (axes contrast)
  make_figures.py                    paper figures from results CSVs
  rubiks_random_baseline.py          random-policy baseline Monte-Carlo
  power_sim.py                       simulation power analysis (plan §5)
tests/
  test_rubiks_distance.py            solver validation (published counts,
                                     differential vs magiccube, witnesses)
  test_pipeline_stub.py              end-to-end with stubbed LLM (no API)
  test_analyse_dstar.py              Module A analysis vs known truth
  test_analyse_nominal.py            Module B analysis vs known truth
                                     (incl. fixed Holm family, shared
                                     estimator/separation rule)
  test_gate_adversarial.py           30 rejection cases: the gate must
                                     REFUSE manipulated data
  test_pair_resume.py                driver pair-resume quarantine rule
data/rubiks_move_perms.json          move permutations (derived, tracked)
```

## Metrics CSV schema

One row per trial: timestamp, benchmark, test_id, difficulty, success,
num_moves, tokens_used, time_seconds, error, instance_id, size,
complexity, similarity (sticker-match fraction), model_id, model_tag,
reasoning, temperature, backend, plus the per-call audit fields.

Axis semantics of `difficulty`/`complexity` (per manifest type):

| Module | difficulty | complexity |
| ------ | ---------- | ---------- |
| A (verified axis) | verified d\* | verified d\* (echo) |
| B (nominal axis) | `nominal_length` | `verified_d_star` |

`complexity` ALWAYS carries the verified exact distance; it never
repeats a nominal length.

| Column | Description |
|---|---|
| finish_reason | provider stop reason ("stop", "length", …) |
| prompt_tokens / completion_tokens / reasoning_tokens | split counts |
| requested_max_tokens | the actual limit sent with this request |
| requested_reasoning | verbatim reasoning request JSON of this call |
| retries / retry_errors | transport retry trail |
| response_model / provider / system_fingerprint | API snapshot echo |
| parse_mode / lenient_rescue | strict-vs-lenient parser diagnostics |
| block_index / arm_order / schedule_hash | paired-schedule provenance |
| run_config_hash | sha256 over the frozen run configuration |

Note on "single shot": each trial is one *completed* model call. There
is exactly ONE retry layer (plan v1.4): the client's internal retries
are disabled (`max_retries = 1`, i.e. a single request per attempt) and
the runner makes up to 3 fully-logged transport-level attempts per
trial, all inside one global 600 s deadline per trial (backoff sleeps
are capped by the remaining deadline). Failure modes are classified per
trial from `finish_reason` and the actual per-request limit — never from
global token thresholds. Unexpected harness exceptions (missing
dependencies, hash mismatches, evaluator bugs) are recorded in
`*.harness_errors.csv`, abort the run, and never become trial rows.

## Adding a benchmark

Create `benchmarks/<name>/__init__.py` exposing

```python
BENCHMARK = scaffold.registry.Benchmark(
    name="<name>",
    solve=<async solve(llm, difficulty, test_id, verbose, instance) -> dict>,
    select_instances=<(manifest_dict, difficulty) -> ordered instance list>,
    difficulty_help="...",
)
```

Benchmarks are manifest-only by design: instances are pre-generated,
hashed, and committed before any model is run, so instance pairing across
configurations holds by construction and every state is reproducible.

## Reproducibility

The instance set is fixed and hashed (see `ANALYSIS_PLAN.md` for the
pinned SHA-256); the runner script verifies the hash before spending any
API call, and the benchmark re-verifies each instance's state hash at
load time. Re-running the documented generator command with the same
seed reproduces every instance, hash, and acceptance statistic exactly
(only the `created_utc` timestamp and per-cell `wall_seconds` timing
differ). The solver cache (`data/rubiks_depth_table_d*.bin`, a plain
length-prefixed binary format — no pickle, no code execution on load,
count-verified against the published HTM level sizes) is regenerated
automatically and is not tracked. Provider-side model updates
remain a residual risk; the echoed `response_model`, provider, and
timestamps are recorded per row for that reason.

## Licensing

Dual-licensed: **code under MIT** (`LICENSE`), **data — manifests,
schedules, baselines, result data — under CC BY 4.0** (`LICENSE-DATA`).
In short: the code may be used, modified, and embedded freely (incl.
commercially) as long as the license notice is kept; the data may be
reused and adapted freely with attribution (cite the paper and this
repository). The copyright line uses an anonymous placeholder for
double-blind review — replace it with the real author(s) before the
public release.
