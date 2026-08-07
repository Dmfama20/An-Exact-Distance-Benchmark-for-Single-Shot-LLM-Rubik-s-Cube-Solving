#!/usr/bin/env python3
"""
Module B analysis: nominal scramble length versus verified distance.

CONFIRMATORY GATE (plan v1.4, shared with Module A via gate_lib): model
and provider identity, run-config / schedule / manifest hash binding,
preflight binding, harness-error absence, trial uniqueness, and FULL
completeness (every manifest instance exactly once per arm). Without a
passing gate (or --force), no outputs are produced; --force marks every
output non-confirmatory.

ESTIMAND (frozen): the scramble PROCESS — instances are draws with
replacement, repeated states are legitimate draws; all resampling is by
instance, stratified by nominal length. A unique-state view (dedup by
state_hash) is reported as a labelled sensitivity analysis, never mixed.

PRIMARY Module B test (frozen, plan v1.5): per arm, logistic
regression with CATEGORICAL nominal length (dummy-coded — "within the
same nominal length" is enforced exactly, no linear-logit assumption):
    Pr(success) = sigma(b0 + b_c*collapsed + b_7*I[len=7] + b_8*I[len=8])
over nominal lengths 6..8, LR chi2(1) test of b_c = 0. FAMILY RULE: the
two arm-specific tests ALWAYS form ONE Holm-corrected family of size 2
at alpha = 0.05; a not-estimable arm enters with p = 1 (fail-closed —
the family never shrinks data-dependently). The estimator and its
separation rule (|b_c| > 8 -> not estimable) live in fit_collapsed_lr
and are shared verbatim with power_sim_moduleB.py.
Process-bootstrap 95% CI for b_c (odds-ratio scale); frozen minimum
bootstrap convergence rate: at least 50% of resamples must yield a fit,
otherwise the estimate is reported as not estimable. The share of
successes on collapsed instances is DESCRIPTIVE ONLY and always
reported next to the manifest base rate of collapse.

Outputs (results_nominal/): axes_comparison.csv, collapsed_effect.csv
(primary), collapsed_descriptive.csv, midpoint_shift.csv,
sensitivity_unique_states.csv, manifest.json.
"""

import argparse
import csv
import json
import math
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_lib  # noqa: E402

ARMS = ("gpt55-base", "gpt55-reason")
COLLAPSE_LENGTHS = (6, 7, 8)   # lengths with non-trivial collapse rates
BOOT = 1000
SEED = 20260728
NOMINAL_MANIFEST_SHA_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "instances",
    "MANIFEST_SHAS.json")


def wilson_ci(k, n, z=1.96):
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    denom = 1 + z ** 2 / n
    margin = z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / denom
    centre = (p + z ** 2 / (2 * n)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _sigmoid(eta):
    if eta >= 0:
        return 1.0 / (1.0 + math.exp(-eta))
    e = math.exp(eta)
    return e / (1.0 + e)


def _solve(A, b):
    n = len(b)
    M = [r[:] + [bb] for r, bb in zip(A, b)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        if abs(M[piv][c]) < 1e-12:
            return None
        M[c], M[piv] = M[piv], M[c]
        for r in range(n):
            if r != c and M[r][c] != 0.0:
                f = M[r][c] / M[c][c]
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] for i in range(n)]


def fit_glm(X, y, iters=200):
    k = len(X[0])
    w = [0.0] * k
    for _ in range(iters):
        g = [0.0] * k
        H = [[0.0] * k for _ in range(k)]
        for xi, yi in zip(X, y):
            pr = _sigmoid(sum(a * b for a, b in zip(w, xi)))
            v = pr * (1 - pr)
            for a in range(k):
                g[a] += (yi - pr) * xi[a]
                for b_ in range(a, k):
                    H[a][b_] += v * xi[a] * xi[b_]
        for a in range(k):
            for b_ in range(a):
                H[a][b_] = H[b_][a]
        step = _solve(H, g)
        if step is None:
            return None
        nrm = max(abs(x) for x in step)
        if nrm > 5.0:
            step = [x * 5.0 / nrm for x in step]
        w = [a + b for a, b in zip(w, step)]
        if max(abs(x) for x in step) < 1e-9:
            break
    if not all(math.isfinite(x) for x in w):
        return None
    ll = 0.0
    for xi, yi in zip(X, y):
        eta = sum(a * b for a, b in zip(w, xi))
        ll += yi * eta - (eta + math.log1p(math.exp(-eta)) if eta > 0
                          else math.log1p(math.exp(eta)))
    return w, ll


def chi2_sf_1df(x):
    return math.erfc(math.sqrt(x / 2.0)) if x > 0 else 1.0


# Frozen separation rule shared with the power simulation: fits whose
# collapsed coefficient exceeds this magnitude are treated as separated
# and NOT ESTIMABLE (the simulation's non-estimability
# and convergence statements must be based on exactly the estimator of
# the confirmatory analysis).
SEPARATION_BETA_MAX = 8.0


def fit_collapsed_lr(items):
    """THE Module B primary estimator (single source of truth).

    items: iterable of (nominal_length, collapsed01, success01).
    Fits logistic success ~ collapsed + C(nominal_length) with dummies
    for lengths 7 and 8, returns (beta_collapsed, LR chi2 vs the reduced
    model) or None if not estimable (no outcome/collapse variation, fit
    failure, or |beta_collapsed| > SEPARATION_BETA_MAX).

    Used identically by analyse_nominal.py (confirmatory analysis and
    bootstrap) and power_sim_moduleB.py (precision simulation).
    """
    X = [(1.0, c,
          1.0 if ln == 7 else 0.0,
          1.0 if ln == 8 else 0.0)
         for ln, c, _ in items]
    y = [1 if s else 0 for _, _, s in items]
    n_coll = sum(x[1] for x in X)
    if not (0 < sum(y) < len(y)) or not (0 < n_coll < len(X)):
        return None
    full = fit_glm(X, y)
    red = fit_glm([(x[0], x[2], x[3]) for x in X], y)
    if not (full and red):
        return None
    (b0, bc, b7, b8), ll_f = full
    _, ll_r = red
    if abs(bc) > SEPARATION_BETA_MAX:
        return None
    return bc, max(0.0, 2 * (ll_f - ll_r))


def holm_family_of_two(prim_rows):
    """FAMILY RULE (frozen): the two
    arm-specific primary tests ALWAYS form one Holm family of size 2.

    A not-estimable arm enters the correction fail-closed with p = 1 —
    the family never shrinks data-dependently. The row keeps its
    not-estimable marking and significant_after_holm = False.
    """
    pvals = [(r["p_collapsed"] if r.get("estimable") else 1.0)
             for r in prim_rows]
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    running = 0.0
    for rank, idx in enumerate(order):
        adj = min(1.0, max(running, (m - rank) * pvals[idx]))
        running = adj
        r = prim_rows[idx]
        r["holm_family_size"] = m
        r["p_holm"] = float(f"{adj:.4g}")
        r["significant_after_holm"] = bool(r.get("estimable")) and adj < 0.05
        if not r.get("estimable"):
            r["note"] = (r.get("note", "") +
                         " | entered the Holm family with p=1 "
                         "(fail-closed family rule)").strip(" |")


def analyse(trials, meta, rng, label):
    """All Module B quantities for one dataset view.

    trials: {arm: {instance_id: success_bool}}; meta: instance metadata.
    Returns dict of output-row lists.
    """
    out = {"axes": [], "primary": [], "descr": [], "mid": []}

    for arm in ARMS:
        # ---- axis views ---------------------------------------------------
        for axis in ("nominal_length", "verified_d_star"):
            groups = defaultdict(list)
            for iid, s in trials[arm].items():
                groups[meta[iid][axis]].append(s)
            for v in sorted(groups):
                k, n = sum(groups[v]), len(groups[v])
                lo, hi = wilson_ci(k, n)
                out["axes"].append({
                    "view": label, "arm": arm, "axis": axis, "value": v,
                    "n": n, "solved": k, "pass1": round(k / n, 4),
                    "wilson_lo": round(lo, 4), "wilson_hi": round(hi, 4)})

        # ---- PRIMARY: success ~ collapsed + length over lengths 6..8 -----
        items = [(iid, s) for iid, s in trials[arm].items()
                 if meta[iid]["nominal_length"] in COLLAPSE_LENGTHS]

        def collapsed(iid):
            return 1.0 if (meta[iid]["verified_d_star"]
                           < meta[iid]["nominal_length"]) else 0.0

        def fit_bc(sub):
            # Delegates to the SHARED frozen estimator (categorical
            # nominal length, separation guard) — identical to the
            # power simulation's.
            return fit_collapsed_lr(
                [(meta[i]["nominal_length"], collapsed(i), s)
                 for i, s in sub])

        obs = fit_bc(items)
        if obs is None:
            out["primary"].append({
                "view": label, "arm": arm, "estimable": False,
                "note": ("NOT ESTIMABLE (frozen rule): separation "
                         f"(|beta|>{SEPARATION_BETA_MAX}) or no "
                         "variation in outcome/collapse over lengths "
                         f"{COLLAPSE_LENGTHS}"),
            })
        else:
            bc, lr = obs
            # process bootstrap: resample instances, stratified by length
            strata = defaultdict(list)
            for it in items:
                strata[meta[it[0]]["nominal_length"]].append(it)
            bcs = []
            for _ in range(BOOT):
                sample = []
                for _, li in strata.items():
                    sample.extend(rng.choice(li) for _ in li)
                r = fit_bc(sample)
                if r is not None:
                    bcs.append(r[0])
            bcs.sort()
            # Frozen minimum bootstrap convergence rate (plan v1.5)
            converged_ok = len(bcs) >= 0.5 * BOOT
            lo = bcs[int(0.025 * len(bcs))] if bcs else None
            hi = bcs[int(0.975 * len(bcs)) - 1] if bcs else None
            out["primary"].append({
                "view": label, "arm": arm,
                "estimable": converged_ok,
                "bootstrap_convergence": round(len(bcs) / BOOT, 3),
                "n": len(items),
                "beta_collapsed": round(bc, 4),
                "odds_ratio_collapsed": round(math.exp(bc), 3),
                "lr_chi2_1df": round(lr, 4),
                "p_collapsed": float(f"{chi2_sf_1df(lr):.4g}"),
                "or_boot_ci_lo": (round(math.exp(lo), 3)
                                  if lo is not None else None),
                "or_boot_ci_hi": (round(math.exp(hi), 3)
                                  if hi is not None else None),
                "bootstrap": (f"{len(bcs)} process resamples "
                              f"(instances, stratified by length)"),
            })

        # ---- descriptive: collapsed share among successes vs base rate ---
        succ = [i for i, s in trials[arm].items()
                if s and meta[i]["nominal_length"] >= 6]
        k = sum(1 for i in succ if collapsed(i))
        base_ids = [i for i in trials[arm]
                    if meta[i]["nominal_length"] >= 6]
        base_k = sum(1 for i in base_ids if collapsed(i))
        lo, hi = wilson_ci(k, len(succ))
        out["descr"].append({
            "view": label, "arm": arm,
            "successes_len_ge6": len(succ),
            "on_collapsed": k,
            "share_among_successes": (round(k / len(succ), 4)
                                      if succ else None),
            "share_ci_lo": round(lo, 4), "share_ci_hi": round(hi, 4),
            "collapse_base_rate": round(base_k / len(base_ids), 4),
            "enrichment_vs_base_rate": (
                round((k / len(succ)) / (base_k / len(base_ids)), 3)
                if succ and base_k else None),
            "note": "descriptive; interpret only next to the base rate",
        })

        # ---- midpoint shift (descriptive, process bootstrap) --------------
        def midpoints(sub):
            pn = [(float(meta[i]["nominal_length"]), 1 if s else 0)
                  for i, s in sub]
            pv = [(float(meta[i]["verified_d_star"]), 1 if s else 0)
                  for i, s in sub]

            def fit_ab(points):
                r = fit_glm([(1.0, x) for x, _ in points],
                            [y for _, y in points])
                if r is None:
                    return None
                (a, coef), _ = r
                b = -coef
                if b <= 0:   # frozen rule: non-decaying fit = midpoint
                    return None   # not identifiable
                return a / b
            mn, mv = fit_ab(pn), fit_ab(pv)
            if mn is None or mv is None:
                return None
            return mn, mv

        all_items = list(trials[arm].items())
        obs_mid = midpoints(all_items)
        if obs_mid is None:
            out["mid"].append({"view": label, "arm": arm,
                               "estimable": False,
                               "note": "midpoint not identifiable "
                                       "(frozen rule: beta <= 0 or fit "
                                       "failure)"})
        else:
            strata = defaultdict(list)
            for it in all_items:
                strata[meta[it[0]]["nominal_length"]].append(it)
            shifts = []
            for _ in range(BOOT):
                sample = []
                for _, li in strata.items():
                    sample.extend(rng.choice(li) for _ in li)
                r = midpoints(sample)
                if r is not None:
                    shifts.append(r[0] - r[1])
            shifts.sort()
            lo = shifts[int(0.025 * len(shifts))] if shifts else None
            hi = shifts[int(0.975 * len(shifts)) - 1] if shifts else None
            out["mid"].append({
                "view": label, "arm": arm, "estimable": True,
                "midpoint_nominal": round(obs_mid[0], 3),
                "midpoint_verified": round(obs_mid[1], 3),
                "overstatement": round(obs_mid[0] - obs_mid[1], 3),
                "shift_ci_lo": round(lo, 3) if lo is not None else None,
                "shift_ci_hi": round(hi, 3) if hi is not None else None,
                "bootstrap_converged": len(shifts),
            })
    return out


def write_csv(path, rows):
    if not rows:
        open(path, "w").write("")
        return
    keys = sorted({k for r in rows for k in r})
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-dir", default="logs/nominal_runs")
    ap.add_argument("--manifest",
                    default="instances/rubiks_nominal_manifest_v1.json")
    ap.add_argument("--schedule",
                    default="instances/rubiks_nominal_schedule_v1.json")
    ap.add_argument("--out", default="results_nominal")
    ap.add_argument("--force", action="store_true",
                    help="run despite a failed gate; all outputs are "
                         "marked non-confirmatory")
    ap.add_argument("--boot", type=int, default=1000,
                    help="bootstrap resamples (reduce only for CI smoke "
                         "runs; confirmatory analyses use the default)")
    ap.add_argument("--smoke", action="store_true",
                    help="fast NON-confirmatory pass for CI/readers: "
                         "boot=50 and the unique-state sensitivity view "
                         "is skipped; outputs are marked accordingly. "
                         "Reference runtimes (full analysis, this "
                         "dataset): ~8 s on an Apple M-class laptop, "
                         "a few minutes on older hardware.")
    args = ap.parse_args()
    global BOOT
    BOOT = 50 if args.smoke else args.boot
    os.makedirs(args.out, exist_ok=True)

    rows, audit, api_errors, provenance = gate_lib.load_rows(
        args.runs_dir, ARMS)

    with open(args.manifest) as fh:
        manifest = json.load(fh)
    meta = {i["id"]: i for i in manifest["instances"]}

    known = set()
    if os.path.exists(NOMINAL_MANIFEST_SHA_FILE):
        with open(NOMINAL_MANIFEST_SHA_FILE) as fh:
            known = set(json.load(fh))

    # ---- shared confirmatory gate -----------------------------------------
    gate_problems, gate_info = gate_lib.integrity_problems(
        args.runs_dir, audit, ARMS, rows,
        manifest_path=args.manifest, schedule_path=args.schedule,
        known_manifest_shas=known or None)
    gate_problems.extend(
        gate_lib.completeness_problems(rows, manifest, ARMS))
    for r in rows:
        if r["instance_id"] not in meta:
            gate_problems.append(f"unknown instance {r['instance_id']}")

    gate_status = "passed"
    if gate_problems:
        print("[GATE] Module B confirmatory gate FAILED:")
        for pr in gate_problems:
            print(f"    {pr}")
        if not args.force:
            sys.exit("Module B analysis refused (gate problems above); "
                     "use --force for a NON-confirmatory pass.")
        gate_status = "FORCED-NON-CONFIRMATORY"
    confirmatory = gate_status == "passed"

    trials = defaultdict(dict)
    for r in rows:
        if r["instance_id"] in meta:
            trials[r["model_tag"]][r["instance_id"]] = r["_success"]

    rng = random.Random(SEED)
    primary_view = analyse(trials, meta, rng, "process")

    # FAMILY RULE (frozen): ALWAYS a Holm family of exactly two — a
    # not-estimable arm enters with p=1 (fail-closed), the family never
    # shrinks data-dependently.
    holm_family_of_two(primary_view["primary"])

    # ---- sensitivity: unique-state view (labelled, never mixed) ----------
    if args.smoke:
        sens_view = {k: [] for k in primary_view}
    first_by_state = {}
    for iid in ([] if args.smoke else sorted(meta)):
        h = meta[iid]["state_hash"]
        if h not in first_by_state:
            first_by_state[h] = iid
    if not args.smoke:
        keep = set(first_by_state.values())
        trials_u = {arm: {i: s for i, s in trials[arm].items()
                          if i in keep} for arm in ARMS}
        sens_view = analyse(trials_u, meta, random.Random(SEED + 1),
                            "unique-states-sensitivity")

    for row_list in (primary_view, sens_view):
        for rows_ in row_list.values():
            for r in rows_:
                r["confirmatory"] = confirmatory

    write_csv(os.path.join(args.out, "axes_comparison.csv"),
              primary_view["axes"] + sens_view["axes"])
    write_csv(os.path.join(args.out, "collapsed_effect.csv"),
              primary_view["primary"])
    write_csv(os.path.join(args.out, "collapsed_descriptive.csv"),
              primary_view["descr"])
    write_csv(os.path.join(args.out, "midpoint_shift.csv"),
              primary_view["mid"])
    write_csv(os.path.join(args.out, "sensitivity_unique_states.csv"),
              sens_view["primary"] + sens_view["descr"] + sens_view["mid"])

    with open(os.path.join(args.out, "manifest.json"), "w") as fh:
        json.dump({
            "generated_utc": datetime.now(timezone.utc).isoformat(
                timespec="seconds"),
            "runs_dir": args.runs_dir,
            "smoke_mode": bool(args.smoke),
            "inputs": provenance,
            "excluded_trials_api_error": [
                {"arm": a, "instance_id": i, "error": e}
                for a, i, e in api_errors],
            "frozen_plan": {
                "file": "ANALYSIS_PLAN_FROZEN.md (v1.7.1, included "
                        "verbatim; git tag v1.7.1-freeze)",
                "sha256": "27cce48b1b565c92d2081609b147975c2278ab7eb6"
                          "981e4866ad1a4a613f79c9",
                "note": "immutable deposit made before the first "
                        "trial; file title reads v1.7 (the "
                        "v1.7.1 amendment updated only the "
                        "change log); post-collection "
                        "deviations in DEVIATIONS.md",
            },
            "confirmatory_gate": {"status": gate_status,
                                  "identity": gate_info,
                                  "problems": gate_problems},
            "estimand": ("scramble process (draws with replacement); "
                         "resampling by instance, stratified by nominal "
                         "length; unique-state view is a labelled "
                         "sensitivity analysis"),
            "primary_test": ("per arm: logistic success ~ collapsed + "
                             "nominal_length over lengths 6-8, LR "
                             "chi2(1) on the collapsed coefficient, "
                             "process-bootstrap CI on the odds ratio"),
        }, fh, indent=1)

    print(f"\nWrote Module B results to {args.out}/ "
          f"(gate: {gate_status})")
    for r in primary_view["primary"]:
        if r.get("estimable"):
            print(f"  PRIMARY {r['arm']}: OR(collapsed) = "
                  f"{r['odds_ratio_collapsed']} "
                  f"[{r['or_boot_ci_lo']}, {r['or_boot_ci_hi']}], "
                  f"p = {r['p_collapsed']:.4g}")
        else:
            print(f"  PRIMARY {r['arm']}: {r['note']}")


if __name__ == "__main__":
    main()
