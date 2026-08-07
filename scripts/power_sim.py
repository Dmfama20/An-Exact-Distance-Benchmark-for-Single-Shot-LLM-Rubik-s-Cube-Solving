#!/usr/bin/env python3
"""
Simulation-based power analysis for the prespecified tests
(design requirement: no verbal power claims without simulation).

Paired binary outcomes are parameterised directly by the quantities that
determine McNemar power: the paired risk difference Delta (reason - base)
and the EXTRA discordance kappa (discordant pairs beyond the minimum
|Delta| required by the marginals). Per pair:

    P(discordant) = p_d = |Delta| + kappa
    P(favours reason | discordant) = theta, with p_d*(2*theta-1) = Delta

Scenarios: Delta in {0.10, 0.15, 0.20, 0.30}, kappa in {0.05, 0.15},
n in {50, 100} pairs per depth, effect either uniform over the 7
confirmatory depths or concentrated in 3 of 7 depths (floor/ceiling
elsewhere). 2000 simulations per scenario, alpha = 0.05.

Reported power:
    - PRIMARY: exact McNemar on discordants pooled over the 7 depths
    - SECONDARY: probability that at least one per-depth McNemar survives
      Holm, and the per-comparison power of a single depth cell

Usage:  python scripts/power_sim.py [--sims 2000]
        [--out results_power/power_sim.csv]
"""

import argparse
import csv
import math
import os
import random
import sys


def mcnemar_exact(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def holm_any_reject(pvals, alpha=0.05):
    m = len(pvals)
    for rank, p in enumerate(sorted(pvals)):
        if p <= alpha / (m - rank):
            return True
        return False  # step-down: first non-rejection stops
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--out", default="results_power/power_sim.csv")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    depths = 7  # confirmatory cells d* = 2..8
    rows = []

    for pattern, effective in (("uniform-7-of-7", 7), ("concentrated-3-of-7", 3)):
        for delta in (0.10, 0.15, 0.20, 0.30):
            for kappa in (0.05, 0.15):
                for n in (50, 100):
                    p_d = min(1.0, delta + kappa)
                    theta = 0.5 * (1 + delta / p_d)
                    hit_primary = hit_any_holm = hit_single = 0
                    for _ in range(args.sims):
                        pvals, B, C = [], 0, 0
                        for j in range(depths):
                            if j < effective:
                                nd = rng.binomialvariate(n, p_d)
                                c = rng.binomialvariate(nd, theta)
                            else:
                                # null cell: only residual discordance
                                nd = rng.binomialvariate(n, kappa)
                                c = rng.binomialvariate(nd, 0.5)
                            b = nd - c
                            B += b
                            C += c
                            pvals.append(mcnemar_exact(b, c))
                        if mcnemar_exact(B, C) <= args.alpha:
                            hit_primary += 1
                        if holm_any_reject(pvals, args.alpha):
                            hit_any_holm += 1
                        if pvals[0] <= args.alpha / depths:
                            # worst case for a single cell: Bonferroni-level
                            hit_single += 1
                    rows.append({
                        "effect_pattern": pattern,
                        "delta": delta,
                        "extra_discordance_kappa": kappa,
                        "n_per_depth": n,
                        "sims": args.sims,
                        "power_primary_pooled_mcnemar":
                            round(hit_primary / args.sims, 3),
                        "power_any_perdepth_after_holm":
                            round(hit_any_holm / args.sims, 3),
                        "power_single_depth_at_bonferroni":
                            round(hit_single / args.sims, 3),
                    })
                    print(f"{pattern} Δ={delta} κ={kappa} n={n}: "
                          f"primary={rows[-1]['power_primary_pooled_mcnemar']}, "
                          f"any-Holm={rows[-1]['power_any_perdepth_after_holm']}, "
                          f"single-cell={rows[-1]['power_single_depth_at_bonferroni']}")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    sys.exit(main())
