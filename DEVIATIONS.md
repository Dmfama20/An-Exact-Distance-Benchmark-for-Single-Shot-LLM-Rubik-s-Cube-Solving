# Deviations and post-collection notes

**Frozen reference:** the prespecified protocol is ANALYSIS_PLAN.md at
sha256 `27cce48b1b565c92d2081609b147975c2278ab7eb6981e4866ad1a4a613f79c9`
(v1.7.1, commit 5e63374), archived immutably BEFORE the first
confirmatory trial together with the release archive
(sha256 f2846b0e…9cf4), the passed Module A production preflight, and
the green Python-3.12 CI log — DOI 10.5281/zenodo.21670328.
Everything in THIS file happened after collection started and is
reported as required by the plan's deviation rule. The frozen plan
itself was not edited beyond the deposit pointer; the deviation
records formerly appended to the working copy of the plan live here.

---

## D1 (2026-07-30) — Timeout rows vs. row-level provenance
*(plan item 56; outcome-independent rule clarification)*

Module A completed with 3 of 936 trials ending in the frozen 600 s
global-deadline timeout (all gpt55-reason: rubiks-d05-i007,
rubiks-d06-i046, rubiks-d07-i013; Module B later added 2:
rubiksnom-d05-i011, rubiksnom-d06-i011). Timeout rows are valid
scored failures per the frozen runner rule, but the row-level
fail-closed provenance rule (item 37) did not anticipate them:
response-side echoes cannot exist for a call that never returned, and
the v1.7.1 runner also left the two request-side audit fields
unfilled, so the confirmatory gate refused the complete dataset.

**Resolution:** rows carrying the runner's timeout error are exempt
from the non-empty rule for exactly `requested_reasoning`,
`response_model`, `provider`, `requested_max_tokens`, and are excluded
from those exactly-one audit sets; every identity-critical field
(timestamp, model_id, temperature, run_config_hash, schedule_hash,
block_index, arm_order) remains required and is present on all
affected rows. The runner now backfills the request-side fields for
future runs.

**Outcome-independence:** timeout rows are scored failures before and
after; no success value, exclusion, or test direction changes. The
alternatives (re-running the trials = selective resampling; excluding
them = incompleteness; --force = losing confirmatory status) would
all be scientifically worse.

## D2 (2026-07-31) — Exploratory projection check added
*(plan item 57; addition, not a rule change)*

Module B executed fully as prespecified (800/800 under gate); the
primary collapsed-effect test resolved NOT ESTIMABLE in both arms
(separation — every deep success lies on a collapsed instance) and
entered the fixed-size Holm family with p=1 per frozen item 48. No
confirmatory collapse claim is made. One clearly-labelled EXPLORATORY
analysis was added after collection: composing the Module A depth
curve with the manifest's exact collapse distribution to predict the
nominal curve (results_nominal/projection_check.csv; agreement within
8 pp at every length). It supports no confirmatory claim.

## D3 (2026-07-31) — Documentation-only corrections
Non-rule fixes made post-collection, none touching analysis logic or
data: stale H3 file-description string in the analysis provenance
manifest corrected and manifests regenerated (bit-identical
statistics); console-summary crash on the prespecified H3
not-estimable fallback fixed (display only; all output files had been
written correctly).

## D4 (2026-07-31) — Exploratory shell-structure check added
*(addition, not a rule change)*

To address within-shell representativeness,
scripts/shell_structure.py computes, dependency-free from the
released move permutations, the number of optimal first moves for
every instance with d* <= 6 (all deeper neighbours of optimal moves
lie in the exact BFS table, so the count is exact). Result
(results_dstar/shell_structure.csv): mean 1.0-1.2 of 18 possible
moves, range 1-2, with no systematic difference between the
exhaustive Module A sample and the scramble-process Module B sample.
Exploratory; supports no confirmatory claim.

## D5 (2026-08-01) — Exploratory classical reference curves added
*(addition, not a rule change)*

scripts/classical_baselines.py (dependency-free, exact): (i) bounded
BFS with ball-sized node budgets — provably deterministic success
iff d* <= k+1 at |ball(k)| expansions, empirically spot-verified;
(ii) a noisy one-step policy over the exact distance oracle
(eps = 0.25 / 0.5). Both are named references;
exploratory; they support no confirmatory claim.

## D6 (2026-08-03) — Independent distance certification added
*(addition, not a rule change)*

All 868 manifest distance labels were re-certified by an independent
implementation (scripts/independent_distance_check.py): third-party
cubie-coordinate engine (RubikOptimal), published HTM ball sizes as
external anchor, pruning-free BFS+brute-force search sharing nothing
with the benchmark stack except scramble strings and claimed
distances. Result: 868/868 confirmed. Additionally, the depth-5
lookup cache is released as a deterministic artifact with checksum
(scripts/emit_depth_table.py), and a completeness proof for the
solver's pruning rules was added to the paper appendix. No label,
rule, or result changed.
