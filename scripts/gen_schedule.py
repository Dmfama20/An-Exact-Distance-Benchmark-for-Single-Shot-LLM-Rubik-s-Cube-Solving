#!/usr/bin/env python3
"""
Generate the randomized, hashed pair-level execution schedule.

Review requirement: depth and arm order must not be confounded with wall
time. The schedule shuffles all (depth, instance) blocks into one random
order and randomizes the within-block arm order; both calls of a pair run
back-to-back. The schedule is committed (hash-pinned) before collection.

Usage:
    python scripts/gen_schedule.py \
        [--manifest instances/rubiks_dstar_manifest_v1.json] \
        [--seed 20260726] [--out instances/rubiks_schedule_v1.json]
"""

import argparse
import hashlib
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".."))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest",
                    default="instances/rubiks_dstar_manifest_v1.json")
    ap.add_argument("--seed", type=int, default=20260726)
    ap.add_argument("--out", default="instances/rubiks_schedule_v1.json")
    args = ap.parse_args()

    with open(args.manifest, "rb") as fh:
        manifest_bytes = fh.read()
    manifest = json.loads(manifest_bytes)
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()

    rng = random.Random(args.seed)
    blocks = []
    for inst in manifest["instances"]:
        blocks.append({
            "d_star": inst.get("nominal_length", inst.get("d_star")),
            "instance_id": inst["id"]})
    rng.shuffle(blocks)
    for idx, b in enumerate(blocks, start=1):
        b["block"] = idx
        b["first_arm"] = rng.choice(["base", "reason"])

    schedule = {
        "version": "rubiks-schedule-v1",
        "seed": args.seed,
        "manifest_sha256": manifest_sha,
        "n_blocks": len(blocks),
        "policy": ("all (depth, instance) blocks in one seeded random "
                   "order; within each block both arms run back-to-back "
                   "in the recorded first_arm order"),
        "blocks": blocks,
    }
    body = json.dumps(schedule, indent=1)
    with open(args.out, "w") as fh:
        fh.write(body)
    sha = hashlib.sha256(body.encode()).hexdigest()
    print(f"Wrote {len(blocks)} blocks to {args.out}")
    print(f"schedule sha256: {sha}")
    first_arms = sum(1 for b in blocks if b["first_arm"] == "base")
    print(f"arm-order balance: base-first {first_arms}, "
          f"reason-first {len(blocks) - first_arms}")


if __name__ == "__main__":
    main()
