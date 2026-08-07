#!/usr/bin/env python3
"""Module B precision/power simulation (frozen pre-collection).

Uses the ACTUAL collapse distribution of the nominal manifest
(lengths 6/7/8: collapsed 7/6/5 of 50 each) to quantify, for the
prespecified categorical-length logistic primary test: detection power,
separation/non-estimability rate, bootstrap convergence, and CI width.

Usage: python scripts/power_sim_moduleB.py [--sims 500] [--boot 200]
"""
import argparse, csv, math, os, random, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyse_nominal import fit_collapsed_lr, chi2_sf_1df  # noqa: E402

COLLAPSED = {6: 7, 7: 6, 8: 5}
N = 50

def simulate(rng, p_nc, or_eff):
    items = []
    for ln, nc in COLLAPSED.items():
        pc = min(0.99, (p_nc[ln] / (1 - p_nc[ln]) * or_eff)
                 / (1 + p_nc[ln] / (1 - p_nc[ln]) * or_eff))
        for i in range(N):
            c = 1.0 if i < nc else 0.0
            p = pc if c else p_nc[ln]
            items.append((ln, c, 1 if rng.random() < p else 0))
    return items

def fit(items):
    # SHARED estimator + separation rule: identical to
    # the confirmatory analysis by construction.
    return fit_collapsed_lr(items)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=500)
    ap.add_argument("--boot", type=int, default=200)
    ap.add_argument("--out", default="results_power/power_sim_moduleB.csv")
    args = ap.parse_args()
    rng = random.Random(99)
    p_nc = {6: 0.35, 7: 0.2, 8: 0.1}
    rows = []
    for or_eff in (1.0, 2.0, 4.0, 8.0):
        rej = notest = 0; widths = []; conv = []
        for _ in range(args.sims):
            items = simulate(rng, p_nc, or_eff)
            r = fit(items)
            if r is None:
                notest += 1; continue
            bc, lr = r
            if chi2_sf_1df(lr) < 0.025:   # Holm worst case for 2 tests
                rej += 1
            strata = {}
            for it in items:
                strata.setdefault(it[0], []).append(it)
            bcs = []
            for _ in range(args.boot):
                samp = []
                for li in strata.values():
                    samp.extend(rng.choice(li) for _ in li)
                rr = fit(samp)
                if rr is not None:
                    bcs.append(rr[0])
            conv.append(len(bcs) / args.boot)
            if len(bcs) >= 0.5 * args.boot:
                bcs.sort()
                widths.append(math.exp(bcs[int(0.975*len(bcs))-1])
                              - math.exp(bcs[int(0.025*len(bcs))]))
        est = args.sims - notest
        rows.append({
            "true_OR": or_eff, "sims": args.sims,
            "power_at_holm_alpha": round(rej / args.sims, 3),
            "not_estimable_rate": round(notest / args.sims, 3),
            "mean_bootstrap_convergence":
                round(sum(conv) / len(conv), 3) if conv else None,
            "median_OR_CI_width":
                round(sorted(widths)[len(widths)//2], 1) if widths else None,
        })
        print(rows[-1])
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    w = csv.DictWriter(open(args.out, "w", newline=""),
                       fieldnames=list(rows[0]))
    w.writeheader(); w.writerows(rows)
    print(f"wrote {args.out}")

if __name__ == "__main__":
    main()
