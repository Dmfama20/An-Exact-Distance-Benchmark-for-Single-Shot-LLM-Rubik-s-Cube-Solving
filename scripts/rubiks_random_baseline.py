#!/usr/bin/env python3
"""
Random-sequence baseline for the verified-depth Rubik's benchmark.

Estimates, per depth cell of the instance manifest, the success
probability of a UNIFORM RANDOM POLICY that emits a legal HTM move
sequence of a given length (Monte-Carlo with the verified move engine).

Naming note: this is a reference point for a specific random
policy, NOT a universal lower bound on the task — the model may emit
sequences of any length. To make the reference robust we therefore report
several output lengths per depth: exactly d*, d*+1, and d*+2. (At d*=1 the
length-1 value is analytically 1/18.)

Usage:
    python scripts/rubiks_random_baseline.py \
        [--manifest instances/rubiks_dstar_manifest_v1.json] \
        [--samples-per-cell 200000] [--seed 7] \
        [--out instances/rubiks_random_baseline_v1.csv]
"""

import argparse
import csv
import json
import math
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".."))

from benchmarks.rubiks.distance import (  # noqa: E402
    MOVES, apply_move, apply_sequence, load_move_permutations, net_from_cube,
)


def wilson_ci(k, n, z=1.96):
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    denom = 1 + z ** 2 / n
    margin = z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / denom
    centre = (p + z ** 2 / (2 * n)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest",
                    default="instances/rubiks_dstar_manifest_v1.json")
    ap.add_argument("--samples-per-cell", type=int, default=200_000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--length-offsets", type=str, default="0,1,2",
                    help="output lengths relative to d* (comma-separated)")
    ap.add_argument("--out",
                    default="instances/rubiks_random_baseline_v1.csv")
    args = ap.parse_args()

    with open(args.manifest) as fh:
        manifest = json.load(fh)
    perms = load_move_permutations()
    move_perms = [perms[m] for m in MOVES]
    offsets = [int(x) for x in args.length_offsets.split(",")]

    from magiccube import Cube
    solved = net_from_cube(Cube(3))

    by_depth = defaultdict(list)
    for inst in manifest["instances"]:
        net = apply_sequence(solved, inst["scramble"], perms)
        by_depth[inst["d_star"]].append(net)

    rng = random.Random(args.seed)
    rows = []
    for d in sorted(by_depth):
        nets = by_depth[d]
        for off in offsets:
            length = d + off
            hits = 0
            n = args.samples_per_cell
            for i in range(n):
                state = nets[i % len(nets)]
                for _ in range(length):
                    state = apply_move(state, rng.choice(move_perms))
                if state == solved:
                    hits += 1
            lo, hi = wilson_ci(hits, n)
            rows.append({
                "d_star": d,
                "instances": len(nets),
                "sequence_length": length,
                "length_offset": off,
                "mc_samples": n,
                "hits": hits,
                "random_policy_pass1": f"{hits / n:.6f}",
                "wilson_lo": f"{lo:.6f}",
                "wilson_hi": f"{hi:.6f}",
                "analytic_note": ("exactly 1/18 per attempt"
                                  if d == 1 and off == 0 else ""),
            })
            print(f"d*={d}, len={length}: {hits}/{n} "
                  f"({hits / n:.5f} [{lo:.5f}, {hi:.5f}])")

    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
