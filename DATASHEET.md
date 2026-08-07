# Datasheet: Rubik's Cube Exact-Distance Benchmark Manifests

Dataset documentation following Datasheets for Datasets (Gebru et al.,
2021) and the NeurIPS Datasets & Benchmarks guidelines, as required by
DMLR for submissions introducing new datasets.

## Motivation

**Why was the dataset created?** To provide benchmark instances for
single-shot, full-sequence LLM Rubik's Cube solving whose difficulty
label is the *verified optimal solution distance* d\* (half-turn
metric), rather than the unvalidated scramble length used by prior
depth-indexed benchmarks. A companion manifest indexed by nominal
scramble length exists specifically to measure what the verified axis
changes.

**Who created it / funded it?** The authors of the accompanying paper.
No external funding.

## Composition

Two instance manifests (JSON, UTF-8), each with top-level fields
`version`, `created_utc`, `generator` (full generation parameters,
master seed 42), `cell_stats` (per-cell population / rejection
statistics), and `instances`:

1. **`instances/rubiks_dstar_manifest_v1.json`** — 468 instances at
   verified optimal distance d\* = 1..10 (18 instances at d\* = 1, the
   full population; 50 per depth otherwise).
2. **`instances/rubiks_nominal_manifest_v1.json`** — 400 instances at
   nominal scramble length 1..8 (50 per length), each additionally
   carrying its verified distance (`verified_d_star`).

Per instance: stable `id`; `facelets` (six 3×3 colour grids exactly as
prompted); `state_hash` (SHA-256 over the canonical facelet JSON,
re-verified at load time); private generator `scramble`; and one
`optimal_witness` solution (diagnostics; never shown to models).

The data are synthetic cube states. They contain **no personal data,
no human-derived content, and no offensive content**.

## Collection process

States are generated programmatically (`scripts/gen_rubiks_instances.py`,
master seed 42). For d\* ≤ 5, cells are sampled uniformly from the
exhaustively enumerated exact-distance shells; for d\* ≥ 6, rejection
sampling accepts only scrambles whose verified distance equals the
target (acceptance statistics released in `cell_stats`). Every distance
label is certified by the benchmark's exact solver (breadth-first
levels asserted against the published HTM position counts) and
re-certified by an independent implementation
(`scripts/independent_distance_check.py`); see the paper's appendices.

## Uses

**Intended:** paired evaluation of LLM configurations on verified-depth
instances; construct-validity studies of difficulty axes; reuse as a
development set for single-shot cube evaluation.

**Cautions:** the public manifests include scrambles and witness
solutions, so any evaluation that grants models repository or tool
access can read the answers — the release designates these manifests as
the *development set* and documents a held-out protocol (states only,
ground truth server-side) for future test sets. Conclusions attach to
the released instance distribution (sampling scheme changes at the
d\* = 5/6 boundary; rejection sampling is not uniform on shells).

## Distribution & licensing

Distributed in this repository and archived immutably (with the frozen
analysis plan, preflight artifacts, and CI log) at
DOI [10.5281/zenodo.21670328](https://doi.org/10.5281/zenodo.21670328).
Manifests, schedules, and result data: **CC BY 4.0** (see
`LICENSE-DATA`). Code: **MIT** (see `LICENSE`).

## Hosting & maintenance plan

The Zenodo deposit is immutable and version-controlled by DOI; the
GitHub repository hosts the working copy. The corresponding author
maintains both; errata are released as new manifest versions (`_v2`,
...) with changelogs — released manifests are never mutated in place,
so existing hashes stay valid indefinitely. Regeneration from the
master seed reproduces every instance and hash exactly
(`README.md`, section "Reproducibility").

## Author statement

The authors bear all responsibility for the released data and confirm
the stated licenses; the data violate no rights of third parties.
