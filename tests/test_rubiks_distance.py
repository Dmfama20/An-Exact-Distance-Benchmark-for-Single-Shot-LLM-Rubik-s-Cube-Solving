#!/usr/bin/env python3
"""
Validation suite for rubiks_distance.py.

Run:  python tests/test_rubiks_distance.py [--table-depth 4]

Checks (all must pass before any instance manifest is generated):
1. Move-permutation sanity: X and X' are inverse, X2 = X X, X^4 = identity.
2. Differential test vs magiccube: identical nets after random sequences.
3. BFS level sizes match published HTM position counts (raises inside
   DistanceSolver if violated).
4. distance() agrees with scramble-length upper bounds and with an
   independent brute-force BFS distance on small cases.
5. solve_optimal() witnesses replay to a solved cube in magiccube and have
   length == distance().
"""

import argparse
import random
import sys
import time

sys.path.insert(0, __import__("os").path.join(
    __import__("os").path.dirname(__import__("os").path.abspath(__file__)), ".."))

from magiccube import Cube  # noqa: E402

from benchmarks.rubiks.distance import (  # noqa: E402
    MOVES, DistanceSolver, apply_move, apply_sequence, invert_sequence,
    load_move_permutations, net_from_cube, random_scramble,
)


def check(name: str, ok: bool, detail: str = ""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table-depth", type=int, default=5,
                    help="BFS table depth (5 for full validation, 4 for a quick run)")
    ap.add_argument("--diff-sequences", type=int, default=200)
    args = ap.parse_args()

    rng = random.Random(1234)

    # ------------------------------------------------------------------
    print("[1] Move permutation sanity")
    perms = load_move_permutations()
    solved = net_from_cube(Cube(3))
    identity = tuple(range(54))

    def compose(p, q):  # apply p then q
        return tuple(p[q[i]] for i in range(54))

    for face in "UDLRFB":
        p, pp, p2 = perms[face], perms[face + "'"], perms[face + "2"]
        check(f"{face}' inverts {face}", compose(p, pp) == identity)
        check(f"{face}2 == {face} {face}", compose(p, p) == p2)
        q = p
        for _ in range(3):
            q = compose(q, p)
        check(f"{face}^4 == identity", q == identity)

    # ------------------------------------------------------------------
    print(f"[2] Differential test vs magiccube ({args.diff_sequences} random sequences)")
    for k in range(args.diff_sequences):
        seq = " ".join(rng.choice(MOVES) for _ in range(rng.randint(1, 20)))
        cube = Cube(3)
        cube.rotate(seq)
        mine = apply_sequence(solved, seq, perms)
        if mine != net_from_cube(cube):
            check(f"sequence {k}: {seq}", False)
    check("all sequences agree", True)

    # ------------------------------------------------------------------
    print(f"[3] BFS depth table (depth {args.table_depth}; "
          "published-count check is enforced inside the builder)")
    t0 = time.time()
    solver = DistanceSolver(table_depth=args.table_depth, verbose=True)
    print(f"  table ready in {time.time() - t0:.1f}s, {len(solver.table):,} states")

    # ------------------------------------------------------------------
    print("[4] distance() spot checks")
    check("solved state has distance 0", solver.distance(solved) == 0)
    for move in MOVES:
        check(f"single move {move} has distance 1",
              solver.distance(apply_move(solved, perms[move])) == 1, move)

    # scramble-length upper bound + inverse-replay consistency
    max_d = args.table_depth + 5
    for k in range(40):
        length = rng.randint(1, max_d)
        seq = random_scramble(length, rng)
        net = apply_sequence(solved, seq, perms)
        d = solver.distance(net, max_depth=max_d)
        check(f"scramble len {length}: d*={d} <= {length}",
              d is not None and d <= length, seq)
        back = apply_sequence(net, invert_sequence(seq), perms)
        check(f"inverse replay solves (len {length})", back == solved)

    # independent brute-force check on small distances
    print("  brute-force cross-check (distances 1-3)")
    bf = {solved: 0}
    frontier = [solved]
    for depth in range(1, 4):
        nxt = []
        for st in frontier:
            for m in MOVES:
                ch = apply_move(st, perms[m])
                if ch not in bf:
                    bf[ch] = depth
                    nxt.append(ch)
        frontier = nxt
    sample = rng.sample(list(bf.items()), 300)
    for st, d_true in sample:
        if solver.distance(st) != d_true:
            check("brute-force agreement", False, f"expected {d_true}")
    check("300 sampled states agree with brute-force BFS", True)

    # ------------------------------------------------------------------
    print("[5] solve_optimal() witnesses")
    t0 = time.time()
    for k in range(15):
        length = rng.randint(1, max_d)
        seq = random_scramble(length, rng)
        cube = Cube(3)
        cube.rotate(seq)
        net = net_from_cube(cube)
        d = solver.distance(net, max_depth=max_d)
        witness = solver.solve_optimal(net, max_depth=max_d)
        check(f"witness length == d* ({d})",
              witness is not None and len(witness.split()) == d)
        cube.rotate(witness)
        check("witness solves cube in magiccube", cube.is_done(), witness)
    print(f"  witness checks in {time.time() - t0:.1f}s")

    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
