# Analysis Plan — Verified-Depth Rubik's Cube Benchmark (v1.7.1)

**Role of this file: annotated working copy.** The immutable
pre-collection protocol is `ANALYSIS_PLAN_FROZEN.md` (verbatim from
the freeze commit, sha256 27cce48b…79c9; its TITLE still reads
"v1.7" because the v1.7→v1.7.1 amendment updated only the change log,
not the heading — the deposited hash is authoritative). Post-collection
records live in `DEVIATIONS.md`.

**Status: frozen BEFORE data collection (prespecified).**
No benchmark trial of this study has been run. Versions 1.1–1.7.1
revise v1.0 in response to external pre-collection code reviews;
since no data exist, these are legitimate amendments, recorded here for
transparency. Any deviation after collection starts will be reported as
a deviation. Wording note: we say *prespecified*, not "preregistered" —
a git commit is not an externally timestamped registration; before
collection the plan (hash + commit) should additionally be deposited in
an immutable timestamped archive (e.g. OSF/Zenodo), and the deposit
recorded here.

**Deposit (fulfilled 2026-07-29, before any confirmatory analysis and
at the start of collection):** pre-collection freeze v1.7.1 — release
archive built from commit 5e63374 (zip sha256 f2846b0e…9cf4), this
plan at sha256 27cce48b…79c9 (deposit-time state; the only
post-deposit changes are this deposit-record paragraph and the
version string in the title, as prespecified), the passed
Module A production preflight, and the green Python-3.12 CI log of all
six suites — archived at **DOI 10.5281/zenodo.21670328**
(https://doi.org/10.5281/zenodo.21670328).

**Post-collection deviations: see DEVIATIONS.md.**
The frozen, deposited state of this plan is sha256 27cce48b…79c9
(v1.7.1, commit 5e63374; DOI above). Per the deviation rule in the
header, everything that happened after collection started is recorded
in the separate DEVIATIONS.md — this plan is no longer edited.

**Change log v1.7 → v1.7.1 (preflight probe hardening, pre-data):**
55. **Reasoning-demanding preflight probe (2026-07-29, before any
    benchmark trial):** the first live preflight under v1.7 connected
    successfully (slug accepted, allowlisted echo `openai/gpt-5.5`,
    pinned provider, efforts accepted, base arm 0 reasoning tokens) but
    failed the frozen token-contrast rule because the original probe
    ("add 17 and 25, multiply by 3") is so trivial that the
    medium-effort arm legitimately spent only ~21 reasoning tokens.
    The CONTRAST RULE IS UNCHANGED (reason_min > max(64, 2·base_max),
    ≥3 samples per arm, recomputed by the gate); only the probe prompt
    was replaced by a small constraint-search task that actually
    engages reasoning. The preflight never scores answer content —
    token behaviour, echo, and provider identity only.

**Change log v1.6 → v1.7 (failed production preflight, pre-data):**
54. **Temperature UNSET (amendment forced by the mandatory preflight,
    2026-07-29, before any benchmark trial):** the production preflight
    failed with OpenRouter routing 404 — the pinned OpenAI endpoints
    for GPT-5.5 declare NO sampling-parameter support (`temperature`
    absent from `supported_parameters`; the GPT-5.x API accepts only
    default sampling), so with `require_parameters=true` any request
    carrying a temperature matches no endpoint. Frozen resolution:
    requests OMIT the temperature field entirely (provider default
    decoding applies); `run_config.temperature = null`; every trial row
    records the canonical value `"unset"`, which the gate enforces
    exactly. No statistical rule changes: the decoding-noise limitation
    (one sample per instance) reads unchanged under default decoding,
    and no trial had been run when the amendment was made — the
    preflight caught the invalid configuration exactly as designed.

**Change log v1.5 → v1.6 (seventh pre-collection review, all pre-data):**
46. **Full frozen-configuration enforcement:** the gate compares EVERY
    frozen run-configuration field (model, require_parameters,
    allow_fallbacks, base/reason effort, temperature, max_tokens,
    trial_timeout — `EXPECTED_RUN_CONFIG` in gate_lib) exactly against
    `run_config.json`, and additionally compares the run_config efforts
    directly against the trial rows' `requested_reasoning`. This closes
    the demonstrated gap where a hash-consistent dataset collected with
    `allow_fallbacks=true`/`require_parameters=false`, or with preflight
    efforts differing from the trial efforts, could pass the gate.
47. **Timezone-aware UTC timestamps, strict ordering:** all components
    emit timezone-aware UTC ISO timestamps (`datetime.now(timezone.utc)`
    in the runner; the preflight already did). The gate parses FULL ISO
    timestamps fail-closed — naive (offset-less) timestamps are rejected
    as a row/preflight problem, microsecond precision is preserved — and
    the preflight must be STRICTLY before the first trial (equality
    rejected). Applied uniformly to the preflight-order, first-arm-first,
    within-pair-gap, and monotone-block checks.
48. **Holm family of FIXED size 2 (Module B):** a not-estimable arm
    enters the family correction with p = 1 (fail-closed); the family
    never shrinks data-dependently from two tests to one. The arm's row
    stays "not estimable" with `significant_after_holm = false`.
49. **Single shared estimator:** `fit_collapsed_lr` (categorical-length
    logistic + frozen separation rule |β_collapsed| > 8 → not estimable)
    is the one implementation used by the confirmatory Module B
    analysis, its process bootstrap, and the precision simulation — the
    simulation's non-estimability/convergence statements are based on
    exactly the confirmatory estimator.
50. **Driver-level preflight enforcement:** `run_paired_study.py`
    itself refuses to start without a valid, content-validated passed
    preflight artifact for the exact run configuration (not only the
    shell wrapper); `--non-confirmatory-smoke-test` is the explicit
    opt-out and such runs cannot pass the analysis gate.
51. **Canonical block_index:** the gate accepts `block_index` only as a
    canonical integer (decimal/exponent notations rejected).
52. **Axis-correct audit logs (Module B):** detailed trial logs label
    the run axis as the nominal scramble length and report
    `verified_d_star` alongside; nothing in the logs presents a nominal
    length as a verified distance.
53. **Precision-simulation CSV regenerated (stale artifact):** while
    verifying item 49 it emerged that the CSV committed at the v1.5
    lock did not reproduce from the v1.5 code (it predated the lock);
    the unchanged lock-commit code and the new shared estimator
    produce byte-identical output (seed 99), confirming the refactor
    is behaviour-preserving. results_power/power_sim_moduleB.csv now
    holds the reproducible run (500 sims, 200 boots): type-I 1.2 %,
    power 15 %/62 %/93 % at OR 2/4/8 — all conclusions of item 43
    unchanged.

**Change log v1.4 → v1.5 (fifth pre-collection review, all pre-data):**
37. **Row-level fail-closed provenance:** EVERY scored row must carry
    non-empty timestamp, model_id, requested_reasoning, response_model,
    provider, temperature, requested_max_tokens, run_config_hash,
    schedule_hash, block_index, arm_order; a single empty field in a
    single row is a gate failure (the v1.4 exactly-one rule masked
    mixed empty/valid values). `block_index`/`arm_order` are now
    required columns.
38. **Preflight hash INSIDE the run_config_hash:** the sha256 of
    preflight.json is a field of the hashed run_config and therefore
    part of every trial row; replacing the artifact after run start
    changes the row-bound hash (tamper-evident). The gate additionally
    re-validates the preflight's OBSERVED evidence: ≥3 complete
    reasoning-token samples per arm, exactly one non-empty model echo
    (allowlist) identical across arms, exactly one non-empty provider
    equal to the pin, recomputed token-contrast rule, and a required,
    parseable timestamp strictly before the first trial. A bare
    "verdict: ok" is never sufficient.
39. **Schedule EXECUTION validated, not only the file hash:** per
    instance the gate checks block_index and arm_order against the
    schedule, that the scheduled first arm has the earlier timestamp,
    a frozen maximum within-pair gap of 3600 s, and monotone block
    start order. **Pair-resume rule (frozen):** a block counts as done
    only if both arms exist; orphan rows of half-finished pairs are
    quarantined to a .superseded.csv sidecar and the whole pair is
    re-run, preserving within-pair temporal adjacency.
40. **Module B primary model uses CATEGORICAL nominal length**
    (dummy-coded; "within the same nominal length" holds exactly, no
    linear-logit assumption — consistent with Module A's own
    linear-form caution).
41. **Family rule for Module B:** the two arm-specific primary tests
    form ONE Holm-corrected family at α = 0.05.
42. **Bootstrap convergence rule:** an estimate is reported as not
    estimable unless ≥50 % of process-bootstrap resamples converge;
    the convergence rate is reported.
43. **Module B precision analysis** (scripts/power_sim_moduleB.py,
    results_power/power_sim_moduleB.csv; actual manifest collapse
    distribution 7/6/5 of 50 at lengths 6/7/8): type-I ≈ 1.2 % at the
    Holm-adjusted level; power ≈ 15 % at OR = 2, 62 % at OR = 4, 93 %
    at OR = 8; bootstrap convergence > 92 %. (Numbers per item 53: the
    v1.5 CSV was a stale pre-lock artifact; the reproducible values
    from the frozen estimator differ within simulation noise and change
    no design conclusion.) Frozen consequence: the
    Module B primary test is adequately powered only for LARGE
    collapse effects (OR ≳ 4); a null result is reported with this
    minimal-detectable-effect statement and never as evidence of
    axis-equivalence.
44. **Nominal manifest semantics cleaned:** nominal instances carry
    `nominal_length` and `verified_d_star` ONLY — the field `d_star`
    is reserved for exact distances and absent from Module B
    instances; generator metadata describe the nominal sampling
    correctly; trial rows carry difficulty = nominal length and
    complexity = verified_d_star (never the nominal length). Manifest
    and schedule regenerated and re-pinned.
45. **Provider names canonically normalised** (case-insensitive)
    before all comparisons; items 25/32–33 remain documented but their
    uncertainty treatment is marked as superseded by items 33/40–42.

**Change log v1.3 → v1.4 (fourth pre-collection review, all pre-data):**
29. **Shared end-to-end gate library** (`scripts/gate_lib.py`), used
    identically by Module A and Module B: exactly ONE non-empty
    provider / run_config_hash / schedule_hash / response_model echo
    across BOTH arms (absence is a failure, not only ambiguity);
    manifest AND schedule hashes recomputed from the presented files
    and compared against the rows and `run_config.json`;
    `run_config.json` itself re-hashed; every row's requested
    `model_id` must equal the frozen snapshot; `--schedule` is now
    required for confirmatory outputs alongside `--manifest`.
30. **Preflight fully bound to the run:** field-by-field match of
    `preflight.json` against `run_config.json` (model, temperature,
    provider order, both efforts, max_tokens), preflight timestamp
    strictly before the first trial, and cryptographic binding: the
    driver records the sha256 of the preflight artifact present at run
    start in `run_config.json`, which the gate recomputes.
31. **Module B has the full confirmatory gate** (same library):
    schema checks, duplicate-trial abort, full completeness (every
    manifest instance exactly once per arm), hash/preflight/harness
    checks, explicit confirmatory status in every output.
32. **Module B primary test redefined** (the success-share on collapsed
    instances alone is not interpretable without the base rate): per
    arm, logistic regression success ~ collapsed + nominal_length over
    lengths 6–8; confirmatory quantity = the collapsed coefficient,
    LR χ²(1), with a process-bootstrap 95 % CI on the odds ratio. The
    collapsed share among successes is descriptive only and is always
    reported next to the manifest base rate (as an enrichment ratio);
    "artefact" wording is dropped.
33. **Resampling unit fixed to the declared estimand:** process
    bootstrap by instance, stratified by nominal length (the v1.3
    state-clustered bootstrap contradicted the draws-with-replacement
    estimand and is withdrawn); a unique-state view is reported as a
    labelled sensitivity analysis, never mixed. Frozen not-estimable
    rules for the primary test (separation / no variation) and for the
    logistic midpoints (non-decaying fit β ≤ 0 → not identifiable).
34. **Identity rule frozen:** requests use only the dated slug
    `openai/gpt-5.5-20260423`; response_model echoes are identical to
    it iff ONE single echo string occurs across all trials of both
    arms and is in {dated slug, `openai/gpt-5.5`}; anything else is a
    hard gate failure. The manuscript's alias passage is corrected
    accordingly (it contradicted the plan).
35. **Single retry layer** (supersedes item 27): client-internal
    retries are disabled for the study (`max_retries = 1`); the only
    retry level is the runner's up-to-3 trial attempts, each a single
    real API request, each sidecar-logged, all inside the global 600 s
    deadline.
36. **Gate completeness covers ALL cells** (including the descriptive
    d\* = 1, 9, 10 — every manifest instance exactly once per arm);
    frozen H3 minimum: fewer than 10 discordant pairs → "not
    interpreted"; plan section numbering fixed.

**Change log v1.2 → v1.3 (third pre-collection review, all pre-data):**
20. **Snapshot pinned exactly:** frozen model
    `openai/gpt-5.5-20260423`; the driver refuses any other slug by
    exact string comparison (the earlier digit heuristic accepted the
    alias and is removed). `--allow-unpinned` exists for smoke tests
    only and yields a different run_config_hash, so such rows can never
    enter the confirmatory data.
21. **Provider pinned mandatorily:** exactly one upstream provider via
    `--provider-order` (required), `require_parameters=true`, fallbacks
    off; >1 observed provider is a hard gate failure. The preflight
    uses the exact production payload (incl. the unset-temperature rule
    of item 54) with the
    same pin and writes an artifact (`preflight.json`) whose presence
    and "ok" verdict the gate requires; `SKIP_PREFLIGHT` therefore
    automatically makes the run non-confirmatory.
22. **Run-configuration hash:** every row carries
    `run_config_hash` = sha256(model, provider, efforts, temperature,
    max_tokens, trial timeout, manifest sha, schedule sha); resume
    accepts only rows with the identical hash and aborts on any
    foreign row; mixed hashes are a hard gate failure.
23. **Gate checks exact arm configurations:** the verbatim
    `requested_reasoning` of every row must equal the frozen values
    (base `{"effort": "none"}`, reason `{"effort": "medium"}`);
    `--manifest` is now mandatory for confirmatory outputs (its absence
    is a gate failure, not a note).
24. **Schedule fully pinned and validated:** structural check (every
    manifest instance exactly once, consecutive unique blocks, valid
    arm order); the FULL schedule sha256 is logged per row. Module A
    schedule:
    `3265e31dff3daa4149214ebb825fbb42c0a0cbe2578a35986c88aa15fdabf961`.
25. **Module B is implemented and part of the main study:** manifest
    (400 instances, nominal lengths 1–8, verified_d_star stored),
    paired schedule
    (`instances/rubiks_nominal_schedule_v1.json`), driver support
    (`MODULE=B`), and a dedicated analysis
    (`scripts/analyse_nominal.py`): axis comparison, PRIMARY Module B
    quantity = share of nominal-length ≥ 6 successes on collapsed
    instances (Wilson CI), and the logistic-midpoint shift between the
    axes (uncertainty treatment SUPERSEDED by items 33/40–42). **Estimand:** the nominal
    manifest samples the scramble process (draws with replacement;
    repeated states are legitimate), so claims are process-level and
    all resampling clusters by `state_hash`. Total main-study size:
    936 (Module A) + 800 (Module B) = 1 736 trials.
26. **H3 fallback (frozen):** if the discordant-pair logistic is
    degenerate (all discordants favour one arm, separation, or
    non-convergence), H3 is reported as "not estimable" with the raw
    per-depth discordant counts; no p-value is invented.
27. **Retry layers documented:** the client performs up to 3
    network-level retries per call and the runner up to 3
    transport-level attempts per trial, all inside the single global
    600 s deadline (backoff sleeps are capped by the remaining
    deadline).
28. **Decisions closed (markers removed):** Module B is part of the
    main study (item 25, analyses redefined in item 32); Module C (repeated sampling, k=3 on d* ∈
    {3,5,7} × i001–i020, both arms, ≈ 360 trials) WILL be run as a
    non-confirmatory annex after the main study.

**Change log v1.1 → v1.2 (second pre-collection review, all pre-data):**
11. **Reasoning contrast redefined as a categorical same-lever
    contrast:** base arm = `reasoning effort none`, reason arm =
    `reasoning effort medium` (the previous none/minimal-vs-2000-token
    budget pair risked being normalised by the gateway into similar
    effort levels). A DATED model snapshot is required (the moving alias
    is refused by the driver); OpenRouter routing is pinned
    (`require_parameters=true`, `allow_fallbacks=false`); the verbatim
    reasoning request is logged per trial (`requested_reasoning`
    column); realized reasoning-token distributions are reported; and a
    preflight interface check (no benchmark instances) must verify the
    contrast is real before collection
    (`scripts/preflight_reasoning_check.py`).
12. **Harness errors are fatal, never scored:** unexpected exceptions
    (missing modules, hash mismatches, evaluator bugs, filesystem
    errors) abort the cell, are recorded in `*.harness_errors.csv`, and
    write no trial row; the analysis gate refuses confirmatory outputs
    while harness-error files exist.
13. **One global per-trial deadline:** the 600 s trial timeout covers
    all transport retries and backoff sleeps of that trial; the LLM
    client's 120 s network timeout is a transport-level failure that is
    retried within the same deadline; on expiry exactly one valid
    timeout row is written.
14. **Randomized, hashed pair-level schedule:** all (depth, instance)
    blocks run in one seeded random order; within each block both arms
    run back-to-back in a randomized recorded order. Block index, arm
    order, and schedule hash are logged per trial
    (`scripts/gen_schedule.py`, `scripts/run_paired_study.py`); the
    driver exits non-zero unless every scheduled trial is complete.
15. **H3 corrected:** the v1.1 within-pair label-flip permutation tests
    arm exchangeability (gamma=0 AND delta=0), not the interaction
    alone, and is withdrawn. H3 is now a conditional logistic regression
    on the discordant pairs, LR chi2(1) for delta=0 (discordant pairs
    are independent; pair effects cancel by conditioning), with the
    hierarchy rule: interpreted only if the primary pooled test rejects.
16. **TOST implemented** (previously announced but absent from the
    pipeline): equivalence within +/-15 pp is claimed only if the 90 %
    Newcombe CI of the pooled paired risk difference lies inside the
    margin (reported in `h1_global.csv`).
17. **Gate extended beyond counts:** validates the pinned manifest hash,
    scored instance IDs against the manifest's cells, complexity vs.
    manifest d\*, temperature and generation-ceiling uniformity,
    within-arm consistency of the requested reasoning configuration, and
    the absence of harness errors; snapshot/provider heterogeneity is
    reported as drift warnings. A confirmatory analysis requires
    `--manifest`.
18. **Solver cache no longer uses pickle** (arbitrary-code-execution
    risk): a trivial length-prefixed binary format, verified against the
    published level counts on load.
19. **Module B added to the main study** (nominal-vs-verified contrast,
    §7): the strongest empirical test of the core thesis, previously
    only a "planned extension".

**Change log v1.0 → v1.1 (all pre-data):**
1. Action space aligned: prompt, parser, and evaluator accept only the
   18 HTM face turns in which d\* is defined; the scored parse is the
   strict final answer line (lenient parses are logged as diagnostics
   only).
2. Primary/secondary structure inverted: the PRIMARY confirmatory test
   is now one global paired test pooled over d\* = 2…8; the seven
   per-depth McNemar tests are SECONDARY (Holm within that family).
   Motivated by simulation power analysis (§5).
3. H2 (logistic depth trend) is now purely descriptive, with a
   linear-vs-categorical(depth) deviance check; the v1.0 confirmatory
   one-sided formulation is withdrawn (it was also inconsistent with
   the implementation).
4. H3 inference is pair-preserving: LR statistics are referred to a
   within-instance arm-label permutation distribution instead of
   χ²(1), because the two observations of one instance are dependent.
5. Confirmatory gate: the analysis refuses to run confirmatory outputs
   if pairing is broken, cells are short, or confirmatory depths are
   missing (`--force` yields an explicitly non-confirmatory pass).
6. Transport-failure handling implemented as planned: invalid attempts
   are retried on the same instance and logged to a sidecar file; the
   main CSV holds exactly one valid row per trial.
7. Failure-taxonomy order of precedence fixed as: api_error → timeout →
   compute_bound_truncation → format_error → wrong_solution.
8. "Chance floor" renamed to **random-sequence baseline** and computed
   at output lengths d\*, d\*+1, d\*+2.
9. Arm naming corrected: the base arm is a **minimal-reasoning
   configuration**, not "non-deliberating"; the reasoning arm's budget
   is a *requested* budget. Realized reasoning-token distributions are
   reported per arm; provider and echoed model snapshot are logged per
   trial. If the provider accepts `reasoning effort = none`, `none` is
   used for the base arm and this is recorded before collection.
10. Trial schedule de-confounded: both arms of a depth run back-to-back
    with alternating within-block order (see run script); per-trial
    timestamps allow a time-block covariate as a robustness check.

## 1. Study design

- **Task.** Single-shot Rubik's Cube solving: one completed API call, no
  tools, no simulator access, deterministic verification by replaying the
  returned move sequence (magiccube). pass@1 per trial.
- **Action space.** Exactly the 18 HTM face turns
  {U, D, L, R, F, B} × {90°, −90°, 180°} — the metric in which d\* is
  computed. Slice, rotation, and wide moves are rejected by prompt
  contract and parser. The scored answer is the last non-empty line of
  the response; a lenient fallback parse is logged (`parse_mode`,
  `lenient_rescue`) but never scored.
- **Independent variable.** Verified optimal solution distance
  **d\* ∈ {1, …, 10}** (HTM), certified per instance by an exact solver
  whose BFS levels reproduce the published HTM position counts.
- **Instances.** Pre-generated manifest
  `instances/rubiks_dstar_manifest_v1.json`
  (sha256 `c9fd0c6652e289c01565288326c0ba81e7cecec9603816e19e9d173608c9c044`),
  468 instances: 18 at d\*=1 (full population), 50 per depth at
  d\* = 2…10. Uniform draws from the exhaustive population at d\* ≤ 5;
  distance-certified rejection sampling on random scrambles above
  (per-cell acceptance stats in the manifest). **Known caveat:** the two
  sampling schemes differ, and rejection sampling is not uniform on the
  distance shell (states reachable by more scramble sequences are
  over-weighted). Conclusions are therefore statements about this
  released instance distribution; any depth-profile discontinuity at the
  d\*=5/6 boundary will be flagged as potentially sampling-induced.
  Trial i of every configuration uses the manifest's i-th instance of
  that depth: **instance pairing holds by construction.**
- **Prompt blinding.** The prompt discloses neither d\*, nor scramble
  length, nor any difficulty label; the prompt text is byte-identical
  across depths except for the state (enforced by test).
- **Configurations (primary pair, same backbone, same lever type).**
  1. `gpt55-base` — GPT-5.5 dated snapshot, `reasoning effort = none`,
  2. `gpt55-reason` — the same snapshot, `reasoning effort = medium`.
  Both: temperature UNSET (item 54 — the pinned endpoints support no
  sampling parameters; the field is omitted and provider default
  decoding applies), generation ceiling `max_tokens = 32 000`, the
  same gateway with pinned routing (`require_parameters = true`,
  `allow_fallbacks = false`, and a MANDATORY explicit single-provider order).
  **Snapshot (frozen): `openai/gpt-5.5-20260423`** — the canonical
  dated form listed by the gateway for the current GPT-5.5 snapshot.
  The driver enforces this by exact string comparison; the echoed
  `response_model` is additionally recorded per trial, and mixed
  echoes within an arm are a hard gate failure. If drift occurs,
  collection stops and affected cells are re-collected under the new
  version, reported as such. The verbatim reasoning
  request is logged per trial; realized reasoning-token distributions,
  echoed snapshots, and providers are reported. The preflight interface
  check must pass before the first benchmark call. This is an API-level
  configuration contrast; no mechanism claims. Additional
  configurations may be added later; they are **exploratory** unless a
  new frozen plan precedes their collection.
- **Sample size.** n = 50 per (configuration × depth) cell; d\*=1 is
  capped at its population of 18 and analysed descriptively only.
  Justification by simulation (§5): the primary pooled test has ≥ 0.95
  power at n = 50 for a uniform paired risk difference of ≥ 0.10, and
  ≥ 0.71 for an effect of 0.15 concentrated in only 3 of 7 depths.
  Per-depth secondary tests are underpowered for moderate effects at
  n = 50 (§5) and are interpreted accordingly.
- **Schedule.** Randomized, hashed pair-level schedule
  (`instances/rubiks_schedule_v1.json`): all (depth, instance) blocks in
  one seeded random order, both arms back-to-back per block in a
  randomized recorded order, so neither depth nor arm is confounded
  with wall time and paired calls are temporally adjacent. Block index,
  arm order, and schedule hash are logged per trial.
- **Trial validity.** A trial is *invalid* only for transport-level API
  errors; the harness retries the same instance (up to 3 in-trial
  attempts, invalid attempts logged to a sidecar CSV) and aborts the
  cell unresolved rather than under-filling it. One **global deadline**
  of 600 s per trial covers all such retries; client-internal retries
  are disabled (`max_retries = 1`), so each of the up to 3 trial
  attempts is exactly one real, fully logged API request; the client's
  120 s network timeout is a transport failure retried within that
  deadline. Unexpected
  harness exceptions are **fatal**: recorded in `*.harness_errors.csv`,
  cell aborted, no trial row (they are never scored as model failures).
  Model-attributable outcomes — wrong solutions, non-compliant output
  (no legal HTM sequence on the final line), empty content at the
  ceiling, expiry of the global trial deadline — are all *valid
  failures*.

## 2. Confirmatory analyses

- **PRIMARY — H1-global (mode effect).** One exact McNemar test
  (two-sided, α = 0.05) on the discordant pairs pooled over the seven
  confirmatory depths d\* = 2…8 (pairs are independent instances, so
  pooling is valid; the test is equivalent to a within-pair sign-flip
  permutation test). Reported with the pooled paired risk difference and
  its Newcombe method-10 95 % CI, and discordant counts.
- **SECONDARY — per-depth family.** Seven exact McNemar tests
  (d\* = 2…8), Holm-corrected within this family, each with paired risk
  difference and Newcombe 95 % CI. These localise the effect; they do
  not carry the headline claim.
- **H3 (mode × depth interaction; hierarchical, secondary).** Any
  statement of the form "the reasoning mode shifts the curve without
  changing its slope" (or the converse) must be supported by a
  **conditional logistic regression on the discordant pairs**:
  Pr(reason wins | d\*) = σ(γ + δ·(d\* − mean d\*)) over d\* = 2…8,
  LR χ²(1) for H₀: δ = 0. Pair-specific difficulty cancels by
  conditioning and discordant pairs are independent instances, so the
  χ²(1) reference is valid here. Hierarchy rule (frozen): H3 is
  interpreted only if the primary pooled test rejects at α = 0.05.
  (The v1.1 label-flip permutation is withdrawn — it tests
  exchangeability of the arms, i.e. γ = 0 AND δ = 0, not δ = 0 alone.)

**Interpretation rules (frozen).**
- A non-significant test is reported as "no evidence of a difference",
  never as equivalence, parity, or "on par".
- Equivalence language is licensed only by the implemented TOST
  procedure: the 90 % Newcombe CI of the pooled paired risk difference
  must lie entirely inside ±15 pp (`h1_global.csv`). Margin rationale:
  15 pp corresponds to ~7 net additional solved instances per 50-trial
  cell — below that, switching configurations does not change any
  deployment decision we can articulate; it is also less than half the
  pilot effect that motivated the study. Smaller differences are
  treated as operationally interchangeable, not as "equal".
- Mechanism claims about the reasoning mode are out of scope.
- The confirmatory gate (§1 validity + pairing + cell size) must pass;
  otherwise all outputs are labelled non-confirmatory.

## 3. Descriptive analyses

- **Depth trend (formerly H2).** Per-arm logistic fits
  `σ(α − β·d*)` over d\* = 2…8 with stratified-bootstrap 95 % CIs for β
  (1 000 resamples), observed-vs-fitted per cell, and a
  linear-vs-categorical(depth) deviance comparison per arm (χ², df = 5;
  within one arm trials are independent instances). The linear form is a
  summary; thresholds/plateaus are reported via the categorical cells.
  No per-step-reliability (q^ℓ) interpretation.
- **Failure modes.** Per-trial classification, frozen precedence:
  `api_error` (invalid, re-run) → `timeout` → `compute_bound_truncation`
  (`finish_reason == "length"` or completion tokens ≥ the actual
  per-request limit) → `format_error` → `wrong_solution`.
- **Random-sequence baseline.** Success probability of uniform random
  legal HTM sequences of lengths d\*, d\*+1, d\*+2 per depth
  (`instances/rubiks_random_baseline_v1.csv`) — a named random-policy
  reference, not a universal lower bound.
- **Interface compliance.** Share of strict/lenient/none parses and
  lenient-rescue counts per cell (separates non-compliance from wrong
  plans).
- Solution lengths vs. the manifest's optimal witnesses; token
  footprints (prompt/completion/reasoning separately); d\*=1 cell
  (descriptive, memorisation caveat); d\* ∈ {9, 10} (Wilson CIs,
  excluded from confirmatory tests — floor effects expected).

## 4. What this study does NOT claim

- No cross-domain "planning horizon" claims (single domain, near-solved
  region d\* ≤ 10 of one puzzle).
- No claims that d\* isolates *planning* as a cognitive construct: the
  task compounds state decoding, move simulation, and solution
  composition. d\* is a verified **difficulty** axis; construct-level
  decomposition (representation controls, sub-task controls) is future
  work and any such claims are withheld until then.
- No "same weights / isolated deliberation mechanism" claims.
- No equivalence claims from non-significance (§2).
- No memorisation-immunity claims (instance-level contamination
  resistance only; d\* ≤ 2 state spaces are enumerable).
- pass@1 values are operating points at the stated budget (32 k ceiling,
  default decoding — temperature unset, item 54), not capability
  ceilings.
- **Known design limitation (accepted in advance):** one sample per
  instance under the provider's default decoding leaves decoding
  noise in each trial; a
  discordant pair can arise from sampling variance. The paired design
  keeps the arm contrast unbiased, and the primary test aggregates over
  350 pairs, but per-pair attribution is noisy. Repeated sampling per
  instance (k = 3–5 on a prespecified subset) is an optional annex.

## 5. Power analysis (simulation, pre-collection)

`scripts/power_sim.py` (seed 13, 2 000 simulations per scenario,
α = 0.05; results in `results_power/power_sim.csv`). Paired outcomes are
parameterised by the paired risk difference Δ and extra discordance κ.
Key rows (n = 50 pairs per depth, 7 depths):

| Scenario | Δ | κ | primary pooled | any per-depth (Holm) |
|---|---|---|---|---|
| uniform 7/7 | 0.10 | 0.05 | 1.00 | 0.40 |
| uniform 7/7 | 0.10 | 0.15 | 0.97 | 0.31 |
| uniform 7/7 | 0.15 | 0.15 | 1.00 | 0.65 |
| concentrated 3/7 | 0.10 | 0.15 | 0.41 | 0.16 |
| concentrated 3/7 | 0.15 | 0.05 | 0.95 | 0.60 |
| concentrated 3/7 | 0.20 | 0.15 | 0.91 | 0.64 |

Conclusions drawn in advance: (i) the pooled primary test is adequately
powered at n = 50 for uniform effects ≥ 0.10 and concentrated effects
≥ 0.15–0.20; (ii) per-depth tests at n = 50 are underpowered for
moderate effects and are therefore secondary/localising only; (iii) a
small concentrated effect (Δ = 0.10 in 3 of 7 depths) is NOT reliably
detectable at n = 50 — if the study yields a null primary result, the
minimal detectable effect statement above accompanies it. (The pilot
effect that motivates H1 was Δ ≈ 0.5 at one depth.)

## 6. Module B (main study): nominal versus verified depth

The strongest empirical test of the core thesis — that scramble length
is a misleading difficulty axis — is a within-study contrast, not a
citation. `instances/rubiks_nominal_manifest_v1.json` contains 50
instances per **nominal scramble length** 1…8 (no rejection sampling;
the verified optimal distance of every instance is stored as
`verified_d_star`; observed collapse fractions per cell are in the
manifest). Both arms run on this manifest under the identical protocol.
Prespecified analyses (v1.5 items 40–43; v1.6 items 46–49):
1. **PRIMARY (confirmatory):** per arm, logistic regression
   success ~ collapsed + C(nominal_length) over lengths 6–8
   (categorical length dummies); LR χ²(1) on the collapsed
   coefficient; the two arm tests ALWAYS form ONE Holm family of
   FIXED size 2 (α = 0.05) — a not-estimable arm enters with p = 1
   (fail-closed, item 48); process-bootstrap 95 % CI on the odds
   ratio (resampling instances, stratified by nominal length; ≥50 %
   convergence required). Frozen not-estimable rules: separation
   (|β_collapsed| > 8), no variation, or insufficient bootstrap
   convergence → explicit "not estimable" row; estimator and
   separation rule are one shared implementation with the precision
   simulation (item 49). Precision: powered for OR ≳ 4 (see item 43).
2. Descriptive: pass@1 vs. nominal length compared with the same trials
   re-stratified by verified d\* (both arms); collapsed share among
   successes ALWAYS next to the manifest base rate (enrichment ratio).
3. Descriptive: logistic midpoints under both axes with
   process-bootstrap CIs for the shift; midpoints from non-decaying
   fits (β ≤ 0) are "not identifiable".
A unique-state sensitivity view (dedup by state_hash) accompanies all
three, labelled as such.
Cost note: Module B roughly doubles the number of trials (≈ 800
additional calls at n = 50 × 8 lengths × 2 arms). **Decision
(recorded): Module B is part of the main study**; execution via
`MODULE=B bash scripts/run_dstar_study.sh`, analysis via
`scripts/analyse_nominal.py` (estimand and uncertainty treatment per
items 33 and 40–43; the state-clustered treatment of item 25 is
superseded). Naming note: the schedule files use the block field
`d_star` as the generic difficulty key — for the Module B schedule its
value is the NOMINAL length; the manifests themselves reserve `d_star`
strictly for exact distances.

## 7. Module C (annex): repeated sampling

k = 3 additional samples per instance and arm on the prespecified
subset d\* ∈ {3, 5, 7} × instances i001–i020, stored under
`logs/repeats_runs/` and analysed only for within-instance variance,
test–retest reliability of the depth curve, and pass@1-vs-mean-success
comparison. Never merged into the confirmatory data. **Decision
(recorded): Module C will be run after the main study as a
non-confirmatory annex.**

## 8. Module D (planned exploratory extension, post main study)

To address the multi-family criticism of all three reviews, a GPT-5.6
pair is the designated first extension: one variant (candidate:
GPT-5.6 Terra, standard reasoning mode) under the identical protocol
(`none` vs. `medium`, same manifests and schedules, own preflight, own
run_config_hash). NOT part of the main study; requires its own frozen
plan section (variant, snapshot, provider, budget) before any Module D
call. Rationale for not switching the primary model to GPT-5.6: the
pilot effect anchoring H1 was measured on GPT-5.5; output-side pricing
is essentially unchanged; and the 5.6 family adds a further
provider-side configuration axis (reasoning.mode) that would need its
own identification work.

## 9. Provenance checklist (per run)

Every trial row records: exact requested model id and echoed
`response_model`, provider, `system_fingerprint` (if echoed), reasoning
flag/requested budget, temperature, `requested_max_tokens`,
`finish_reason`, split token counts (prompt / completion / reasoning),
retries and retry kinds (invalid transport attempts additionally in the
sidecar `*.invalid.csv`), wall-clock time and timestamp, manifest
`instance_id`, verified d\* (`complexity`), `parse_mode`,
`lenient_rescue`, the verbatim `requested_reasoning`, and the schedule
provenance (`block_index`, `arm_order`, `schedule_hash`). The manifest hash above pins the instance set; the
runner refuses to start on a hash mismatch; the analysis pipeline
consumes only the per-cell CSVs written by `run_benchmark.py` and emits
a machine-readable provenance manifest (`results_dstar/manifest.json`)
with input hashes, exclusions, the gate verdict, and the test-family
definition.
