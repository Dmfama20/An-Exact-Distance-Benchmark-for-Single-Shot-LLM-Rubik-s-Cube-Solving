#!/usr/bin/env python3
"""EXPLORATORY: classical reference curves on the benchmark instances
(purpose: a classical bounded-search solver and a deliberately
weak heuristic policy, as named references alongside the random
baseline). Dependency-free; uses data/rubiks_move_perms.json only.

Baselines:

  bfs@ball(k) breadth-first search from the instance state over the 18
              HTM moves with a node-expansion budget of exactly
              |ball(k)| (the number of states within distance k, from
              the enumerated shells). With ball-sized budgets the
              outcome is provably deterministic by vertex-transitivity
              of the Cayley graph: expanding all of ball(d*-1)
              generates every distance-d* state (success iff
              d* <= k+1), while any smaller ball cannot reach deeper
              targets (guaranteed failure for d* >= k+2). Arbitrary
              intermediate budgets would be expansion-order-dependent
              in the boundary band and are deliberately not used.
              Verified empirically on sampled instances for the
              cheaper budgets.

  greedy-eps  one-step policy with the EXACT oracle: from each state
              move to a neighbour of minimal true distance, but with
              probability eps take a uniformly random legal move
              instead; an episode fails after 2*d*+4 moves or when a
              random detour leaves the exact-oracle region (d > 5).
              eps = 0 solves everything by construction (the oracle is
              exact), so only noisy variants are informative. Evaluated
              on all instances with d* <= 5.

Output: results_dstar/classical_baselines.csv

Usage:  python scripts/classical_baselines.py [--seed 20260731]
"""

import argparse
import csv
import json
import os
import random
from collections import defaultdict, deque

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLE_DEPTH = 5
BALL_KS = (1, 2, 3, 4, 5)   # budgets |ball(k)|
EPSILONS = (0.25, 0.5)
EPISODES_PER_INSTANCE = 20
VERIFY_PER_DEPTH = 2          # empirical BFS spot checks per depth
VERIFY_KS = (1, 2, 3)       # empirical checks (cheap)


def load_perms():
    with open(os.path.join(REPO, "data", "rubiks_move_perms.json")) as fh:
        return [tuple(p) for p in json.load(fh).values()]


def net_to_state(net, colmap):
    order = ("up", "left", "front", "right", "back", "down")
    return bytes(colmap[c] for face in order
                 for row in net[face] for c in row)


def bounded_bfs(start, solved, perms, budget):
    if start == solved:
        return True
    seen = {start}
    q = deque([start])
    expanded = 0
    while q and expanded < budget:
        s = q.popleft()
        expanded += 1
        for p in perms:
            t = bytes(s[p[i]] for i in range(54))
            if t == solved:
                return True
            if t not in seen:
                seen.add(t)
                q.append(t)
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20260731)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    perms = load_perms()
    man = json.load(open(os.path.join(
        REPO, "instances", "rubiks_dstar_manifest_v1.json")))
    first = man["instances"][0]
    faces = ("up", "left", "front", "right", "back", "down")
    colmap = {c: i for i, c in enumerate(
        sorted({first["facelets"][f][1][1] for f in faces}))}
    solved = net_to_state(
        {f: [[first["facelets"][f][1][1]] * 3 for _ in range(3)]
         for f in faces}, colmap)

    # exact oracle table to depth 5; also yields the shell sizes
    table = {solved: 0}
    shell = [1]
    frontier = deque([solved])
    for depth in range(1, TABLE_DEPTH + 1):
        nxt = deque()
        while frontier:
            s = frontier.popleft()
            for p in perms:
                t = bytes(s[p[i]] for i in range(54))
                if t not in table:
                    table[t] = depth
                    nxt.append(t)
        shell.append(len(nxt))
        frontier = nxt
    cum = []
    total = 0
    for c in shell:
        total += c
        cum.append(total)   # states within distance k = cum[k]
    print("shell sizes:", shell, "cumulative:", cum, flush=True)

    # ---- bfs@ball(k): deterministic with ball-sized budgets --------------
    agg = defaultdict(lambda: [0, 0])
    per_depth = defaultdict(list)
    for inst in man["instances"]:
        per_depth[inst["d_star"]].append(inst)
        for k in BALL_KS:
            key = (f"bfs-ball{k}({cum[k]})", inst["d_star"])
            agg[key][1] += 1
            agg[key][0] += (inst["d_star"] <= k + 1)

    print("verifying ball-budget BFS empirically on sampled instances:",
          flush=True)
    for d, insts in sorted(per_depth.items()):
        for inst in rng.sample(insts, min(VERIFY_PER_DEPTH, len(insts))):
            s = net_to_state(inst["facelets"], colmap)
            for k in VERIFY_KS:
                emp = bounded_bfs(s, solved, perms, cum[k])
                ana = (d <= k + 1)
                assert emp == ana, (inst["id"], k, emp, ana)
        print(f"  d*={d}: ok", flush=True)

    # ---- greedy-eps ------------------------------------------------------
    def greedy_eps(start, d, eps):
        s = start
        for _ in range(2 * d + 4):
            if s == solved:
                return True
            if rng.random() < eps:
                p = rng.choice(perms)
                s = bytes(s[p[i]] for i in range(54))
                continue
            best, best_d = None, 99
            for p in perms:
                t = bytes(s[p[i]] for i in range(54))
                dt = table.get(t, 99)
                if dt < best_d:
                    best, best_d = t, dt
            if best_d == 99:
                return False
            s = best
        return s == solved

    for d, insts in sorted(per_depth.items()):
        if d > TABLE_DEPTH:
            continue
        for inst in insts:
            s = net_to_state(inst["facelets"], colmap)
            for eps in EPSILONS:
                key = (f"greedy-eps{eps}", d)
                wins = sum(greedy_eps(s, d, eps)
                           for _ in range(EPISODES_PER_INSTANCE))
                agg[key][1] += EPISODES_PER_INSTANCE
                agg[key][0] += wins
        print(f"greedy d*={d} done", flush=True)

    out = os.path.join(REPO, "results_dstar", "classical_baselines.csv")
    with open(out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["baseline", "d_star", "solved", "n", "rate"])
        for (name, d), (k, n) in sorted(agg.items()):
            w.writerow([name, d, k, n, round(k / n, 4)])
    print(f"wrote {out}")
    for name in sorted({k[0] for k in agg}):
        row = {d: agg[(name, d)] for (n2, d) in agg if n2 == name}
        print(f"  {name:16s} " + " ".join(
            f"d{d}:{v[0] / v[1]:.2f}" for d, v in sorted(row.items())))


if __name__ == "__main__":
    main()
