#!/usr/bin/env python3
"""Independent certification of every manifest distance label
(design requirement: the exactness claim for d* > 5 must not rest
solely on the benchmark's own solver).

INDEPENDENCE. This check shares NOTHING with the benchmark's solver
or evaluation stack except the scramble strings and the claimed
distances:

  - state representation: Kociemba's cubie-level coordinates
    (corner/edge permutation + orientation) from the third-party
    RubikOptimal package (H. Kociemba), instead of facelet
    permutations extracted from magiccube;
  - move semantics: RubikOptimal's own basic move cubes;
  - search: a PRUNING-FREE breadth-first table to depth
    TABLE_DEPTH around solved plus a PRUNING-FREE brute-force DFS
    over all 18^j sequences from the query state — complete by
    construction, so none of the benchmark solver's pruning rules
    (whose completeness is argued separately in the appendix) is
    assumed here.

EXTERNAL ANCHOR. The independent BFS layer sizes are compared to the
published HTM ball sizes (18/243/3240/43239/574908) before any
instance is checked.

CERTIFICATE per instance with claimed distance d:
  d <= TABLE_DEPTH : table[state] == d  (exact two-sided).
  d >  TABLE_DEPTH : (a) the manifest witness has length d (upper
    bound, replayed by the INDEPENDENT engine), and (b) no node at
    DFS depth j <= d-1-TABLE_DEPTH satisfies table[state]+j <= d-1
    (lower bound: no solution of length <= d-1 exists, since any such
    solution of length L splits as j + t with t <= TABLE_DEPTH and
    j = L - t <= d-1-TABLE_DEPTH).

Usage:  python scripts/independent_distance_check.py \
            [--manifests instances/rubiks_dstar_manifest_v1.json \
                         instances/rubiks_nominal_manifest_v1.json]
"""

import argparse
import json
import os
import sys
import time
from collections import deque

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLE_DEPTH = 5
PUBLISHED_SHELLS = [1, 18, 243, 3240, 43239, 574908]

try:
    import optimal.face  # noqa: F401  (breaks the package's circular
    #                       face<->cubie import: face must load first)
    from optimal import cubie
    from optimal.enums import Move
except ImportError as exc:
    sys.exit(f"This check needs the third-party RubikOptimal package "
             f"(pip install RubikOptimal): {exc}")

SINGMASTER_TO_MOVE = {}
for face_ in "URFDLB":
    SINGMASTER_TO_MOVE[face_] = Move[f"{face_}1"]
    SINGMASTER_TO_MOVE[face_ + "2"] = Move[f"{face_}2"]
    SINGMASTER_TO_MOVE[face_ + "'"] = Move[f"{face_}3"]


def key(cc):
    """Canonical hashable state (independent representation)."""
    return bytes(cc.cp) + bytes(cc.co) + bytes(cc.ep) + bytes(cc.eo)


def child(cc, m):
    d = cubie.CubieCube(cc.cp[:], cc.co[:], cc.ep[:], cc.eo[:])
    d.move(m)
    return d


def apply_scramble(scramble):
    cc = cubie.CubieCube()
    for tok in scramble.split():
        cc.move(SINGMASTER_TO_MOVE[tok])
    return cc


def build_table():
    print(f"building independent BFS table to depth {TABLE_DEPTH} "
          f"(no pruning)...", flush=True)
    solved = cubie.CubieCube()
    table = {key(solved): 0}
    frontier = [solved]
    shells = [1]
    for depth in range(1, TABLE_DEPTH + 1):
        nxt = []
        for cc in frontier:
            for m in Move:
                ch = child(cc, m)
                k = key(ch)
                if k not in table:
                    table[k] = depth
                    nxt.append(ch)
        shells.append(len(nxt))
        print(f"  depth {depth}: shell {len(nxt)}", flush=True)
        frontier = nxt
    if shells != PUBLISHED_SHELLS[:TABLE_DEPTH + 1]:
        sys.exit(f"FATAL: independent shell sizes {shells} do not match "
                 f"the published HTM counts "
                 f"{PUBLISHED_SHELLS[:TABLE_DEPTH + 1]}")
    print("  shell sizes match the published HTM ball exactly", flush=True)
    return table


def shorter_solution_exists(start, d, table):
    """Complete brute-force check for any solution of length <= d-1."""
    max_j = d - 1 - TABLE_DEPTH
    # depth-first over ALL 18^j prefixes, j = 0..max_j (no pruning)
    stack = [(start, 0)]
    while stack:
        cc, j = stack.pop()
        t = table.get(key(cc))
        if t is not None and t + j <= d - 1:
            return True
        if j < max_j:
            for m in Move:
                stack.append((child(cc, m), j + 1))
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifests", nargs="+", default=[
        "instances/rubiks_dstar_manifest_v1.json",
        "instances/rubiks_nominal_manifest_v1.json"])
    args = ap.parse_args()

    try:
        from importlib.metadata import version
        checker_ver = version("RubikOptimal")
    except Exception:
        checker_ver = "unknown"
    print(f"independent engine: RubikOptimal {checker_ver} "
          f"(pinned in requirements-audit.txt)", flush=True)
    table = build_table()
    total = bad = 0
    for mpath in args.manifests:
        with open(os.path.join(REPO, mpath)) as fh:
            man = json.load(fh)
        name = os.path.basename(mpath)
        t0 = time.time()
        for inst in man["instances"]:
            d = inst.get("d_star", inst.get("verified_d_star"))
            cc = apply_scramble(inst["scramble"])
            problems = []
            # independent witness replay (upper bound)
            w = inst["optimal_witness"]
            wc = cubie.CubieCube(cc.cp[:], cc.co[:], cc.ep[:], cc.eo[:])
            for tok in w.split():
                wc.move(SINGMASTER_TO_MOVE[tok])
            if key(wc) != key(cubie.CubieCube()):
                problems.append("witness does not solve (indep. engine)")
            if len(w.split()) != d:
                problems.append("witness length != claimed d")
            # exact certificate
            if d <= TABLE_DEPTH:
                if table.get(key(cc)) != d:
                    problems.append(
                        f"table distance {table.get(key(cc))} != {d}")
            else:
                if shorter_solution_exists(cc, d, table):
                    problems.append(
                        f"a solution shorter than {d} exists")
            total += 1
            if problems:
                bad += 1
                print(f"FAIL {inst['id']}: {'; '.join(problems)}")
        print(f"{name}: {len(man['instances'])} instances checked "
              f"in {time.time() - t0:.0f}s", flush=True)

    print(f"\nINDEPENDENT CERTIFICATION: {total - bad}/{total} instance "
          f"distances confirmed (engine: RubikOptimal {checker_ver}, "
          f"cubie coordinates; search: pruning-free BFS+DFS)")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
