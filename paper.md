---
title: 'rubiks-planning-bench: A verified-difficulty benchmark for single-shot LLM Rubik''s Cube solving'
tags:
  - Python
  - large language models
  - benchmarking
  - evaluation
  - planning
  - Rubik's Cube
  - reproducibility
authors:
  - name: Alexander Dominicus
    orcid: 0009-0000-9244-1679
    affiliation: 1
affiliations:
  - name: Hochschule Bochum, Bochum, Germany
    index: 1
date: 7 August 2026
bibliography: paper.bib
---

# Summary

`rubiks-planning-bench` is a benchmark and evaluation harness for
measuring how well large language models (LLMs) solve the
$3\times3\times3$ Rubik's Cube in a single shot: given a scrambled
state, the model must emit a complete move sequence in one API call,
which is then applied to an independent copy of the state and verified
deterministically. Unlike prior depth-indexed puzzle benchmarks, the
difficulty axis is the *verified optimal solution distance* $d^*$ in the
half-turn metric — computed per instance by an exact meet-in-the-middle
solver whose breadth-first level sizes are asserted against the
published cube-group position counts, and independently re-certified by
a second, pruning-free implementation. The software ships a hashed
instance manifest, the exact solver and its validation suite, a
random-sequence baseline, a manifest-paired experiment runner with
audit-grade per-trial logging (provider `finish_reason`, split
prompt/completion/reasoning token counts, and the actual per-request
generation limit), and an analysis pipeline that is gated on pairing
and cell-size integrity and emits a machine-readable provenance
manifest. The codebase is organised as a benchmark-agnostic scaffold
plus a cube plugin, so additional models and additional puzzles can be
added without changing the core runner.

# Statement of need

Benchmarks that scale a "difficulty" knob are the standard instrument
for probing LLM planning, yet the knob itself is rarely validated. A
scramble of length $\ell$ does not require $\ell$ moves to undo:
consecutive moves cancel and merge, so the true optimal distance
satisfies $d(s)\le\ell$ and is often much smaller — a gap that recent
free-form cube evaluations acknowledge but still index by nominal
scramble length [@utsho_complexity_2026]. Difficulty labels leak into
prompts, run-time instance generation silently breaks the pairing that
"paired" comparisons assume, and compute-bound failures are frequently
inferred from aggregate token counts rather than from the per-request
limit that actually bounds a call. Each of these defects invalidates a
different downstream conclusion — the depth curve, the causal reading of
the knob, the paired tests, the failure taxonomy — and they recur
across depth-indexed evaluations of LLM reasoning
[@valmeekam_planbench_2023; @shojaee_illusion_2025].

`rubiks-planning-bench` exists to remove these construct-validity
failures by construction rather than by convention. It targets the one
domain where the difficulty axis can be made exact for the range of
interest ($d^*=1$–$10$, the near-solved neighbourhood of the cube whose
diameter is 20 [@rokicki_godsnumber_2014]): move semantics are derived
from the evaluation library itself so that solver, prompt renderer, and
verifier share one definition; instances are pre-generated into a
SHA-256-pinned manifest and served byte-identically to every
configuration, so pairing holds by construction, not by run-time
seeding; the prompt discloses no difficulty information; and a
frozen, pre-registered analysis plan drives paired significance tests
(exact McNemar with Newcombe intervals [@mcnemar_1947; @newcombe_1998])
that the pipeline refuses to run if integrity gates fail. The intended
users are researchers evaluating LLM planning or reasoning who need a
difficulty variable that means what it says, and benchmark authors who
want a reusable template for verified-difficulty, instance-paired,
audit-logged evaluation.

The design is complementary to existing cube benchmarks. Exact-distance
indexing is shared with Cube Bench [@cubebench_mllm_2025], which
computes $d^*$ with an IDA*/pattern-database oracle
[@korf_15puzzle_1997; @kociemba_twophase_1992] for step-wise,
candidate-action tasks; `rubiks-planning-bench` instead realises that
axis for *free-form, full-sequence, single-shot* generation and packages
the surrounding integrity protocol — pairing, blinding, chance floors,
per-request audit logging, and a fail-closed analysis pipeline — as a
released, tested artifact following community reproducibility guidance
[@pineau_reproducibility_2021]. Every distance label of the released
manifests is certified twice and the full test battery runs in
continuous integration.

# Acknowledgements

The author thanks the maintainers of the open-source cube libraries used
for move-semantics derivation and independent distance certification.

# References
