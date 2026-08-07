#!/usr/bin/env python3
"""EXPLORATORY: within-shell structural metrics of the benchmark
instances (purpose: characterise whether states at the same
exact distance differ structurally between the exhaustive/uniform
sample of Module A and the scramble-process sample of Module B).

For every manifest instance with d* <= 6 this computes the number of
OPTIMAL FIRST MOVES: how many of the 18 legal moves reach a state at
exact distance d*-1. (Neighbours of a d*<=6 state that are optimal lie
at depth <=5 and are therefore decided exactly by the BFS table; no
heuristic is involved.) Dependency-free: uses only
data/rubiks_move_perms.json and the manifests.

Output: results_dstar/shell_structure.csv (one row per instance) and
an aggregate printout per (manifest, d*).

Usage:  python scripts/shell_structure.py
"""

import csv
import json
import os
import sys
from collections import defaultdict, deque

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLE_DEPTH = 5
MAX_DSTAR = TABLE_DEPTH + 1


def load_perms():
    with open(os.path.join(REPO, "data", "rubiks_move_perms.json")) as fh:
        return {m: tuple(p) for m, p in json.load(fh).items()}


def net_to_state(net, colmap):
    order = ("up", "left", "front", "right", "back", "down")
    return bytes(colmap[c] for face in order
                 for row in net[face] for c in row)


def build_table(solved, perms):
    """BFS from solved to depth TABLE_DEPTH over the 18 HTM moves."""
    plist = [tuple(p) for p in perms.values()]
    table = {solved: 0}
    frontier = deque([solved])
    for depth in range(1, TABLE_DEPTH + 1):
        nxt = deque()
        while frontier:
            s = frontier.popleft()
            for p in plist:
                t = bytes(s[p[i]] for i in range(54))
                if t not in table:
                    table[t] = depth
                    nxt.append(t)
        frontier = nxt
        print(f"  BFS depth {depth}: {len(table)} states total",
              flush=True)
    return table


def main():
    perms = load_perms()
    manifests = [
        ("dstar", "instances/rubiks_dstar_manifest_v1.json"),
        ("nominal", "instances/rubiks_nominal_manifest_v1.json"),
    ]
    first = json.load(open(os.path.join(
        REPO, manifests[0][1])))["instances"][0]
    colours = sorted({first["facelets"][f][1][1]
                      for f in ("up", "left", "front", "right",
                                "back", "down")})
    colmap = {c: i for i, c in enumerate(colours)}
    solved = net_to_state(
        {f: [[first["facelets"][f][1][1]] * 3 for _ in range(3)]
         for f in ("up", "left", "front", "right", "back", "down")},
        colmap)
    print("building exact BFS table to depth", TABLE_DEPTH, flush=True)
    table = build_table(solved, perms)

    rows = []
    agg = defaultdict(list)
    for name, path in manifests:
        man = json.load(open(os.path.join(REPO, path)))
        for inst in man["instances"]:
            d = inst.get("d_star", inst.get("verified_d_star"))
            if d < 1 or d > MAX_DSTAR:
                continue
            s = net_to_state(inst["facelets"], colmap)
            n_opt = sum(
                1 for p in perms.values()
                if table.get(bytes(s[p[i]] for i in range(54)), 99)
                == d - 1)
            rows.append(dict(manifest=name, instance_id=inst["id"],
                             d_star=d, n_optimal_first_moves=n_opt))
            agg[(name, d)].append(n_opt)
            if n_opt == 0:
                print(f"ERROR: {inst['id']} has no optimal first move")
                sys.exit(1)

    out = os.path.join(REPO, "results_dstar", "shell_structure.csv")
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out} ({len(rows)} instances)")
    print("\nmean (min-max) optimal first moves per shell:")
    for (name, d) in sorted(agg):
        v = agg[(name, d)]
        print(f"  {name:8s} d*={d}: {sum(v)/len(v):.2f} "
              f"({min(v)}-{max(v)}), n={len(v)}")


if __name__ == "__main__":
    main()
