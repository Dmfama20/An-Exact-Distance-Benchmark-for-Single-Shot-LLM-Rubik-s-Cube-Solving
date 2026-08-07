#!/usr/bin/env python3
"""
Paired-schedule study driver (Module A: verified d*; Module B: nominal).

Executes a pre-generated, hashed pair-level schedule: all (depth,
instance) blocks in one seeded random order; within each block both arms
run back-to-back in the schedule's recorded arm order.

Experimental-identity guarantees:
  - the model slug must EXACTLY equal the frozen snapshot recorded in
    the analysis plan (no digit heuristics; --allow-unpinned exists for
    smoke tests only and taints nothing because it changes the
    run_config_hash);
  - exactly ONE upstream provider must be pinned (--provider-order),
    with require_parameters=true and fallbacks disabled;
  - every row carries a run_config_hash over (model, provider, efforts,
    temperature, max_tokens, trial timeout, manifest sha, schedule sha);
    resume skips a trial ONLY if its existing row carries the identical
    hash — any row with a different hash in the runs dir aborts the run;
  - the schedule is structurally validated against the manifest (every
    instance exactly once, consecutive unique blocks, valid arm order)
    and its FULL sha256 is logged per row.

Exit status is non-zero unless ALL scheduled trials are complete.

Usage (Module A):
    python scripts/run_paired_study.py --provider-order openai
Usage (Module B):
    python scripts/run_paired_study.py --provider-order openai \
        --manifest instances/rubiks_nominal_manifest_v1.json \
        --schedule instances/rubiks_nominal_schedule_v1.json \
        --runs-dir logs/nominal_runs
"""

import argparse
import asyncio
import csv
import glob
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import gate_lib  # noqa: E402
from scaffold.llm import RobustLLM  # noqa: E402
from scaffold.metrics import TrialLogger  # noqa: E402
from scaffold.registry import load_benchmark  # noqa: E402
from scaffold.runner import execute_trial  # noqa: E402

# Frozen snapshot (ANALYSIS_PLAN.md §1). Update ONLY together with the
# plan, before collection.
EXPECTED_MODEL = "openai/gpt-5.5-20260423"

# Known pinned instance sets (full sha256).
KNOWN_MANIFESTS = {
    "c9fd0c6652e289c01565288326c0ba81e7cecec9603816e19e9d173608c9c044":
        "rubiks_dstar_manifest_v1 (Module A)",
}
# Module B manifest sha is appended at generation time via
# instances/MANIFEST_SHAS.json (kept out of this file so the driver need
# not change when Module B is regenerated).
_SHAS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", "instances", "MANIFEST_SHAS.json")
if os.path.exists(_SHAS_FILE):
    with open(_SHAS_FILE) as _fh:
        KNOWN_MANIFESTS.update(json.load(_fh))

BASE_EFFORT = "none"
REASON_EFFORT = "medium"


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_schedule(schedule, manifest):
    """Structural validation: every manifest instance exactly
    once, consecutive unique block numbers, valid first_arm values."""
    inst_ids = {i["id"] for i in manifest["instances"]}
    d_by_id = {i["id"]: i.get("nominal_length", i.get("d_star"))
               for i in manifest["instances"]}
    seen = set()
    blocks = schedule["blocks"]
    for pos, b in enumerate(blocks, start=1):
        if b["block"] != pos:
            sys.exit(f"Schedule: block numbers not consecutive at {pos}")
        if b["first_arm"] not in ("base", "reason"):
            sys.exit(f"Schedule: invalid first_arm in block {pos}")
        iid = b["instance_id"]
        if iid in seen:
            sys.exit(f"Schedule: duplicate instance {iid}")
        if iid not in inst_ids:
            sys.exit(f"Schedule: unknown instance {iid}")
        if d_by_id[iid] != b["d_star"]:
            sys.exit(f"Schedule: depth mismatch for {iid}")
        seen.add(iid)
    if seen != inst_ids:
        missing = sorted(inst_ids - seen)[:5]
        sys.exit(f"Schedule: {len(inst_ids - seen)} manifest instances "
                 f"missing, e.g. {missing}")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=EXPECTED_MODEL,
                    help=f"must equal the frozen snapshot "
                         f"{EXPECTED_MODEL!r} (plan §1)")
    ap.add_argument("--allow-unpinned", action="store_true",
                    help="smoke tests only: accept a non-frozen model "
                         "slug (the run_config_hash differs, so such "
                         "rows can never mix into the confirmatory data)")
    ap.add_argument("--provider-order", required=True,
                    help="REQUIRED: exactly one upstream provider to pin")
    ap.add_argument("--manifest",
                    default="instances/rubiks_dstar_manifest_v1.json")
    ap.add_argument("--schedule",
                    default="instances/rubiks_schedule_v1.json")
    ap.add_argument("--base-effort", default=BASE_EFFORT,
                    choices=["none", "minimal", "low"])
    ap.add_argument("--reason-effort", default=REASON_EFFORT,
                    choices=["low", "medium", "high", "xhigh"])
    # Frozen (plan v1.7, item 54): temperature is UNSET — GPT-5.5
    # endpoints support no sampling parameters; requests omit the field
    # entirely and rows record the canonical value "unset".
    ap.add_argument("--temperature", type=float, default=None)
    ap.add_argument("--max-tokens", type=int, default=32000)
    ap.add_argument("--trial-timeout", type=int, default=600)
    ap.add_argument("--runs-dir", default="logs/dstar_runs")
    ap.add_argument("--limit-blocks", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--non-confirmatory-smoke-test", action="store_true",
                    help="start WITHOUT a valid preflight.json in the "
                         "runs dir (plan §9 requires a passed preflight "
                         "strictly before the first call; such runs can "
                         "never pass the confirmatory gate)")
    args = ap.parse_args()

    # ---- experimental identity -------------------------------------------
    if args.model != EXPECTED_MODEL and not args.allow_unpinned:
        sys.exit(f"Expected frozen model {EXPECTED_MODEL!r}, got "
                 f"{args.model!r} (plan §1). Use --allow-unpinned only "
                 f"for smoke tests.")
    providers = [p.strip() for p in args.provider_order.split(",")
                 if p.strip()]
    if len(providers) != 1:
        sys.exit("--provider-order must pin exactly ONE provider "
                 f"(got {providers!r})")

    manifest_sha = sha256_file(args.manifest)
    if manifest_sha not in KNOWN_MANIFESTS:
        sys.exit(f"Manifest {args.manifest} (sha {manifest_sha[:12]}…) is "
                 f"not a pinned instance set (instances/MANIFEST_SHAS.json)")
    with open(args.schedule) as fh:
        schedule = json.load(fh)
    if schedule.get("manifest_sha256") != manifest_sha:
        sys.exit("Schedule was generated for a different manifest")
    schedule_sha = sha256_file(args.schedule)

    with open(args.manifest) as fh:
        manifest = json.load(fh)
    validate_schedule(schedule, manifest)

    os.makedirs(args.runs_dir, exist_ok=True)
    # Preflight binding INSIDE the hashed config:
    # replacing preflight.json after the run started changes the
    # run_config_hash carried by every row -> resume/gate mismatch.
    preflight_path = os.path.join(args.runs_dir, "preflight.json")
    preflight_sha = (sha256_file(preflight_path)
                     if os.path.exists(preflight_path) else None)
    run_config = {
        "model": args.model,
        "preflight_sha256": preflight_sha,
        "provider_order": providers,
        "require_parameters": True,
        "allow_fallbacks": False,
        "base_effort": args.base_effort,
        "reason_effort": args.reason_effort,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "trial_timeout": args.trial_timeout,
        "manifest_sha256": manifest_sha,
        "schedule_sha256": schedule_sha,
    }
    run_config_hash = hashlib.sha256(
        json.dumps(run_config, sort_keys=True).encode()).hexdigest()

    # Preflight enforcement: the plan requires a
    # PASSED preflight strictly before the first benchmark call, so the
    # driver itself refuses to start without a valid preflight.json —
    # not only the shell wrapper. --non-confirmatory-smoke-test is the
    # explicit opt-out (such runs cannot pass the analysis gate anyway,
    # since preflight_sha256 is bound into every row's config hash).
    if not args.dry_run and not args.non_confirmatory_smoke_test:
        if preflight_sha is None:
            sys.exit(f"{preflight_path} missing — run "
                     f"scripts/preflight_reasoning_check.py first "
                     f"(plan §9), or pass --non-confirmatory-smoke-test")
        with open(preflight_path) as fh:
            pf = json.load(fh)
        pf_problems = []
        gate_lib._validate_preflight(
            pf, run_config, gate_lib.norm_provider(providers[0]),
            None, pf_problems)
        if pf_problems:
            for p in pf_problems:
                print(f"  [preflight] {p}")
            sys.exit("preflight.json is not a valid PASSED preflight "
                     "for this exact configuration — refusing to start")

    inst_by_id = {i["id"]: i for i in manifest["instances"]}
    pos_in_cell = {}
    per_depth = {}
    for inst in sorted(manifest["instances"], key=lambda i: i["id"]):
        per_depth.setdefault(inst.get("nominal_length", inst.get("d_star")),
                             []).append(inst["id"])
    for d, ids in per_depth.items():
        for idx, iid in enumerate(ids, start=1):
            pos_in_cell[iid] = idx

    benchmark = load_benchmark("rubiks")
    os.makedirs(args.runs_dir, exist_ok=True)
    with open(os.path.join(args.runs_dir, "run_config.json"), "w") as fh:
        json.dump({"run_config": run_config,
                   "run_config_hash": run_config_hash}, fh, indent=1)

    arms = {
        "base": dict(tag="gpt55-base", effort=args.base_effort),
        "reason": dict(tag="gpt55-reason", effort=args.reason_effort),
    }
    llms = {
        arm: RobustLLM(
            model=args.model,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            reasoning_effort=cfg["effort"],
            require_parameters=True,
            allow_fallbacks=False,
            provider_order=providers,
            # SINGLE retry layer (plan v1.4): client-internal retries are
            # disabled; the runner's up-to-3 trial attempts are the only
            # retry level, and every real request is sidecar-logged.
            max_retries=1,
        )
        for arm, cfg in arms.items()
    }

    # ---- resume: ONLY rows from the identical configuration --------------
    done = set()
    rows_by_file = {}
    for path in glob.glob(os.path.join(args.runs_dir, "*_metrics.csv")):
        with open(path) as fh:
            reader = csv.DictReader(fh)
            fields = reader.fieldnames
            rows_by_file[path] = (fields, list(reader))
        for row in rows_by_file[path][1]:
            if not (row.get("model_tag") and row.get("instance_id")):
                continue
            row_hash = row.get("run_config_hash") or ""
            if row_hash != run_config_hash:
                sys.exit(
                    f"{path}: existing row for "
                    f"{row['model_tag']}/{row['instance_id']} was "
                    f"collected under a DIFFERENT configuration "
                    f"(run_config_hash {row_hash[:12]}… vs current "
                    f"{run_config_hash[:12]}…). Refusing to mix runs "
                    f"— use a fresh --runs-dir.")
            done.add((row["model_tag"], row["instance_id"]))

    # Frozen pair-resume rule (plan v1.5): a block counts as done only if
    # BOTH arms are present. Orphan rows of half-finished pairs are moved
    # to a .superseded.csv sidecar and the WHOLE pair is re-run, so the
    # within-pair temporal adjacency guarantee holds for every scored
    # pair.
    tags = {a: arms[a]["tag"] for a in arms}
    orphans = set()
    for b in schedule["blocks"]:
        present = [a for a in ("base", "reason")
                   if (tags[a], b["instance_id"]) in done]
        if len(present) == 1:
            orphans.add((tags[present[0]], b["instance_id"]))
    if orphans and not args.dry_run:
        print(f"[resume] {len(orphans)} half-finished pair(s) — "
              f"quarantining orphan rows and re-running whole pairs")
        for path, (fields, rws) in rows_by_file.items():
            keep = [r for r in rws
                    if (r.get("model_tag"), r.get("instance_id"))
                    not in orphans]
            drop = [r for r in rws
                    if (r.get("model_tag"), r.get("instance_id"))
                    in orphans]
            if not drop:
                continue
            with open(path + ".superseded.csv", "a", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=fields)
                if fh.tell() == 0:
                    w.writeheader()
                w.writerows(drop)
            with open(path, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=fields)
                w.writeheader()
                w.writerows(keep)
        done -= orphans

    loggers = {}

    def logger_for(tag, d):
        key = (tag, d)
        if key not in loggers:
            loggers[key] = TrialLogger(
                "rubiks",
                metrics_file=os.path.join(
                    args.runs_dir,
                    f"{tag}__rubiks__dstar{d}__batch_metrics.csv"))
        return loggers[key]

    blocks = schedule["blocks"]
    if args.limit_blocks:
        blocks = blocks[:args.limit_blocks]

    print(f"Paired schedule: {len(blocks)} blocks | model {args.model} | "
          f"provider {providers[0]}")
    print(f"Arms: base=effort:{args.base_effort}, "
          f"reason=effort:{args.reason_effort} | "
          f"run_config_hash {run_config_hash[:16]}…")
    print(f"schedule sha256 {schedule_sha}")

    executed = skipped = 0
    for b in blocks:
        d = b["d_star"]
        iid = b["instance_id"]
        order = (["base", "reason"] if b["first_arm"] == "base"
                 else ["reason", "base"])
        for arm in order:
            tag = arms[arm]["tag"]
            if (tag, iid) in done:
                skipped += 1
                continue
            if args.dry_run:
                print(f"  [dry] block {b['block']:3d} d*={d} {iid} {tag}")
                executed += 1
                continue
            await execute_trial(
                benchmark=benchmark,
                llm=llms[arm],
                difficulty=d,
                test_id=pos_in_cell[iid],
                instance=inst_by_id[iid],
                logger=logger_for(tag, d),
                identity=dict(
                    model_id=args.model,
                    model_tag=tag,
                    reasoning=(arm == "reason"),
                    temperature=("unset" if args.temperature is None
                                 else args.temperature),
                    backend="openrouter",
                    block_index=b["block"],
                    arm_order=f"{b['first_arm']}-first",
                    schedule_hash=schedule_sha,
                    run_config_hash=run_config_hash,
                ),
                trial_timeout=args.trial_timeout,
                verbose=False,
            )
            done.add((tag, iid))
            executed += 1

    # ---- completeness check ------------------------------------------------
    missing = []
    for b in (blocks if args.limit_blocks else schedule["blocks"]):
        for arm in ("base", "reason"):
            tag = arms[arm]["tag"]
            if (tag, b["instance_id"]) not in done and not args.dry_run:
                missing.append((tag, b["d_star"], b["instance_id"]))
    print(f"\nExecuted {executed}, skipped (already done) {skipped}.")
    if missing and not args.dry_run:
        print(f"[INCOMPLETE] {len(missing)} scheduled trials missing, "
              f"e.g. {missing[:5]} — re-run to resume.")
        sys.exit(1)
    print("STUDY COMPLETE — all scheduled trials present."
          if not args.dry_run else "Dry run complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted — resumable.")
        sys.exit(1)
