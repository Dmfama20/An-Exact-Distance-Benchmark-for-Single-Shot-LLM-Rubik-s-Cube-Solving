#!/usr/bin/env python3
"""
Generate Rubik's Cube benchmark instances at *verified* optimal distance d*.

Addresses the central construct-validity pitfall of scramble-indexed
benchmarks:
scramble length d is only a generator parameter (moves can cancel or merge),
so instances are certified here by their exact optimal solution distance
(half-turn metric) and the benchmark conditions on d*, not on d.

Every instance is generated once, stored with a stable ID and a state hash,
and later served unchanged to every model configuration (pre-generated
instance manifest).  Pairing across
configurations is therefore guaranteed by construction rather than by
runtime seeding.

Usage:
    python scripts/gen_rubiks_instances.py \
        --depths 1,2,3,4,5,6,7,8 --per-depth 50 --seed 42 \
        --out instances/rubiks_dstar_manifest_v1.json

The manifest records, per instance:
    id              stable identifier ("rubiks-d05-i017")
    d_star          verified optimal distance (the benchmark condition)
    scramble        generator move sequence (replayed by the harness to
                    reconstruct the state; NEVER shown to the model)
    facelets        six 3x3 colour grids exactly as shown in the prompt
    state_hash      sha256 over the canonical facelet JSON
    optimal_witness one optimal solution (diagnostics; never shown)
and, per depth cell: acceptance statistics of the rejection sampling
(they quantify how often random scrambles collapse below their
nominal length).
"""

import argparse
import hashlib
import json
import os
import random
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".."))

from benchmarks.rubiks.distance import (  # noqa: E402
    DistanceSolver, apply_sequence, invert_sequence, net_to_grids,
    net_from_cube, load_move_permutations, random_scramble,
    KNOWN_HTM_COUNTS,
)


def state_hash(facelets: dict) -> str:
    canonical = json.dumps(facelets, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--depths", type=str, default="1,2,3,4,5,6,7,8",
                    help="comma-separated verified target distances d*")
    ap.add_argument("--per-depth", type=int, default=50,
                    help="instances per depth cell")
    ap.add_argument("--seed", type=int, default=42,
                    help="master seed for the generator RNG")
    ap.add_argument("--table-depth", type=int, default=5)
    ap.add_argument("--max-attempts-factor", type=int, default=200,
                    help="abort a cell after per_depth * factor attempts")
    ap.add_argument("--out", type=str,
                    default="instances/rubiks_dstar_manifest_v1.json")
    ap.add_argument("--nominal", action="store_true",
                    help="nominal-axis mode: accept every random scramble "
                         "AT ITS NOMINAL LENGTH (no rejection sampling); "
                         "instances carry nominal_length plus the exact "
                         "verified_d_star — the field d_star is RESERVED "
                         "for exact distances and absent from nominal "
                         "manifests. Used for the nominal-vs-verified "
                         "module.")
    args = ap.parse_args()

    depths = sorted({int(d) for d in args.depths.split(",")})
    max_supported = args.table_depth + 5
    for d in depths:
        if d < 1 or d > max_supported:
            sys.exit(f"target depth {d} outside supported range "
                     f"1..{max_supported} (table_depth + 5)")

    print(f"Building solver (table depth {args.table_depth}) ...")
    solver = DistanceSolver(table_depth=args.table_depth, verbose=True)
    perms = load_move_permutations()

    from magiccube import Cube
    solved_net = net_from_cube(Cube(3))

    rng = random.Random(args.seed)
    instances = []
    cell_stats = {}

    id_prefix = "rubiksnom" if args.nominal else "rubiks"

    def make_instance(d: int, idx: int, net: bytes, scramble: str,
                      verified: int = None) -> dict:
        facelets = net_to_grids(net)
        inst = {
            "id": f"{id_prefix}-d{d:02d}-i{idx:03d}",
            "scramble": scramble,
            "facelets": facelets,
            "state_hash": state_hash(facelets),
            "optimal_witness": solver.solve_optimal(net, max_depth=d),
        }
        if args.nominal:
            # Semantics: d_star is RESERVED for the exact
            # optimal distance and therefore not present in nominal
            # instances; the two axes are named explicitly.
            inst["nominal_length"] = d
            inst["verified_d_star"] = verified
        else:
            inst["d_star"] = d
        return inst

    for d in depths:
        t0 = time.time()
        accepted = []

        if args.nominal:
            # Nominal axis: every scramble of length d is accepted; the
            # verified distance is recorded per instance (this quantifies
            # scramble collapse instead of filtering it out). NO dedup:
            # small-length cells (18 states at length 1) would otherwise
            # never fill, and a nominal-axis benchmark faithfully
            # reflects that random scrambles repeat states.
            seen_states = set()
            dist_counts = {}
            while len(accepted) < args.per_depth:
                scramble = random_scramble(d, rng)
                net = apply_sequence(solved_net, scramble, perms)
                if net == solved_net:
                    continue
                seen_states.add(net)
                verified = solver.distance(net, max_depth=d)
                dist_counts[verified] = dist_counts.get(verified, 0) + 1
                accepted.append(make_instance(
                    d, len(accepted) + 1, net, scramble, verified=verified))
            elapsed = time.time() - t0
            collapsed = sum(v for k, v in dist_counts.items() if k < d)
            cell_stats[str(d)] = {
                "sampling": "nominal-length-no-rejection",
                "nominal_length": d,
                "distinct_states": len(seen_states),
                "accepted": len(accepted),
                "verified_distance_distribution": {
                    str(k): v for k, v in sorted(dist_counts.items())},
                "collapsed_fraction": round(collapsed / len(accepted), 4),
                "wall_seconds": round(elapsed, 1),
            }
            print(f"len={d}: {len(accepted)} accepted "
                  f"({len(seen_states)} distinct), "
                  f"{collapsed} collapsed ({collapsed/len(accepted):.0%}), "
                  f"dist={dict(sorted(dist_counts.items()))} "
                  f"({elapsed:.1f}s)")
            instances.extend(accepted)
            continue

        if d <= args.table_depth:
            # Shallow depths: the full population is enumerated in the BFS
            # table, so sample uniformly (exact, no rejection).  Cells with
            # fewer states than requested (e.g. 18 at d*=1) are capped at
            # the population size.
            population = [net for net, dep in solver.table.items() if dep == d]
            population.sort()  # deterministic order before seeded sampling
            n = min(args.per_depth, len(population))
            if n < args.per_depth:
                print(f"d*={d}: population has only {len(population)} states; "
                      f"capping cell at {n}")
            for idx, net in enumerate(rng.sample(population, n), start=1):
                witness = solver.solve_optimal(net, max_depth=d)
                accepted.append(make_instance(
                    d, idx, net, scramble=invert_sequence(witness)))
            elapsed = time.time() - t0
            cell_stats[str(d)] = {
                "sampling": "uniform-from-exhaustive-BFS-population",
                "population_size": len(population),
                "requested": args.per_depth,
                "accepted": len(accepted),
                "wall_seconds": round(elapsed, 1),
            }
            print(f"d*={d}: {len(accepted)} sampled uniformly from "
                  f"{len(population):,} states ({elapsed:.1f}s)")
        else:
            # Deeper depths: rejection sampling on random scrambles,
            # certified by exact distance verification.
            seen_states = set()
            attempts = 0
            rejected_shorter = 0
            rejected_duplicate = 0
            max_attempts = args.per_depth * args.max_attempts_factor

            while len(accepted) < args.per_depth:
                attempts += 1
                if attempts > max_attempts:
                    sys.exit(f"d*={d}: exceeded {max_attempts} attempts "
                             f"({len(accepted)} accepted) — inspect "
                             f"acceptance rate")
                scramble = random_scramble(d, rng)
                net = apply_sequence(solved_net, scramble, perms)
                if net in seen_states:
                    rejected_duplicate += 1
                    continue
                d_star = solver.distance(net, max_depth=d)
                if d_star != d:
                    # scramble collapsed: true distance below target depth
                    rejected_shorter += 1
                    continue
                seen_states.add(net)
                accepted.append(make_instance(
                    d, len(accepted) + 1, net, scramble))

            elapsed = time.time() - t0
            cell_stats[str(d)] = {
                "sampling": "rejection-on-random-scrambles",
                "requested": args.per_depth,
                "accepted": len(accepted),
                "attempts": attempts,
                "rejected_distance_below_target": rejected_shorter,
                "rejected_duplicate_state": rejected_duplicate,
                "acceptance_rate": round(len(accepted) / attempts, 4),
                "wall_seconds": round(elapsed, 1),
            }
            print(f"d*={d}: {len(accepted)}/{attempts} accepted "
                  f"(rate {len(accepted)/attempts:.2f}, "
                  f"{rejected_shorter} collapsed, {rejected_duplicate} dupes, "
                  f"{elapsed:.1f}s)")
        instances.extend(accepted)

    manifest = {
        "version": ("rubiks-nominal-v1" if args.nominal else "rubiks-dstar-v1"),
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generator": {
            "script": "scripts/gen_rubiks_instances.py",
            "master_seed": args.seed,
            "metric": "HTM (18 face turns U D L R F B, quarter and half)",
            "depth_definition": (
                "NOMINAL axis: nominal_length = number of random scramble "
                "moves (no rejection sampling); verified_d_star = exact "
                "optimal solution distance of the resulting state, "
                "computed by the same count-verified solver. The field "
                "d_star is reserved for exact distances and is absent "
                "from nominal instances."
                if args.nominal else
                "d_star = exact optimal solution distance, verified by "
                "meet-in-the-middle search against a BFS table whose level "
                "sizes match the published HTM position counts"),
            "table_depth": args.table_depth,
            "bfs_level_counts_verified": {
                str(k): v for k, v in KNOWN_HTM_COUNTS.items()
                if k <= args.table_depth},
            "scramble_policy": (
                "nominal mode: random face turns with immediate same-face "
                "repeats suppressed, EVERY draw accepted (no rejection, "
                "no dedup — draws with replacement over the scramble "
                "process); per-cell verified-distance distributions in "
                "cell_stats"
                if args.nominal else
                "depths <= table_depth: uniform draw from the exhaustive "
                "BFS population at that exact distance (scramble stored as "
                "the inverted optimal witness); deeper depths: random face "
                "turns with immediate same-face repeats suppressed, "
                "accepted only if the verified d_star equals the target "
                "depth (rejection sampling); per-cell statistics in "
                "cell_stats"),
            "prompt_blinding": (
                "the prompt shows only the cube state; neither d_star, nor "
                "scramble length, nor any qualitative difficulty label is "
                "disclosed to the model"),
        },
        "cell_stats": cell_stats,
        "instances": instances,
    }

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(manifest, fh, indent=1)
    print(f"\nWrote {len(instances)} instances "
          f"({len(depths)} depth cells) to {args.out}")

    # ---- post-write self-check: replay every scramble through magiccube ----
    print("Self-check: replaying every scramble through magiccube ...")
    bad = 0
    for inst in instances:
        cube = Cube(3)
        cube.rotate(inst["scramble"])
        if net_from_cube(cube) != apply_sequence(
                solved_net, inst["scramble"], perms):
            bad += 1
            print(f"  MISMATCH {inst['id']}")
        cube.rotate(inst["optimal_witness"])
        if not cube.is_done():
            bad += 1
            print(f"  WITNESS FAILS {inst['id']}")
    if bad:
        sys.exit(f"self-check failed for {bad} instances")
    print("Self-check passed: all states reproducible, all witnesses solve.")


if __name__ == "__main__":
    main()
