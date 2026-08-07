#!/usr/bin/env python3
"""
Analysis pipeline for the v2 verified-depth Rubik's study.

Implements EXACTLY the rules frozen in ANALYSIS_PLAN.md (committed before
data collection). Consumes the per-cell CSVs written by
scripts/run_dstar_study.sh and produces, in results_dstar/:

    pass1_dstar.csv     per-cell pass@1 + Wilson 95% CI + failure modes
    h1_global.csv       PRIMARY confirmatory test: exact McNemar on the
                        discordant pairs pooled over d* = 2..8 (the pairs
                        are independent instances, so pooling is valid),
                        with the pooled paired risk difference and
                        Newcombe (method 10) 95% CI
    h1_mcnemar.csv      SECONDARY per-depth McNemar comparisons
                        (d* = 2..8), Holm-adjusted, with paired risk
                        differences and Newcombe 95% CIs
    h2_trend.csv        DESCRIPTIVE logistic depth-trend fits per arm over
                        d* = 2..8 with percentile-bootstrap 95% CIs, plus
                        a linear-vs-categorical(depth) deviance comparison
                        per arm (guards against over-reading the linear
                        logit form)
    h3_interaction.csv  mode x depth interaction via CONDITIONAL logistic
                        regression on the discordant pairs
                        (Pr(reason wins | d*) = sigma(gamma + delta*d*)):
                        pair effects cancel by conditioning, discordant
                        pairs are independent, so the chi2(1) LR reference
                        is valid; hierarchical rule: interpret only if the
                        primary pooled test rejects
    fit_diagnostics.csv observed vs fitted pass@1 per depth cell for every
                        logistic fit (goodness-of-fit report)
    failure_modes.csv   per-cell counts under the frozen classification
    pairing_audit.csv   instance-pairing check per depth (must be "ok")
    exploratory.csv     solution lengths / token footprints / parse modes
                        (labelled exploratory in the plan)
    manifest.json       machine-readable provenance: input files with
                        SHA-256, rows consumed/excluded per file, the
                        frozen test-family definition, output mapping

Confirmatory gate: the script REFUSES to run the confirmatory analyses if
instance pairing is broken, cells are short of the planned n, or
confirmatory depths are missing (override with --force, which marks every
output as non-confirmatory).

Usage:
    python scripts/analyse_dstar.py [--runs-dir logs/dstar_runs]
                                    [--out results_dstar] [--expected-n 50]
                                    [--manifest instances/..._v1.json]
                                    [--force]

--manifest activates instance-identity validation against the pinned
manifest (hash, IDs, complexities) and is REQUIRED for a confirmatory
analysis."""

import argparse
import csv
import glob
import hashlib
import json
import math
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timezone

ARMS = ("gpt55-base", "gpt55-reason")
CONFIRMATORY_DEPTHS = list(range(2, 9))   # d* = 2..8 (frozen)
TREND_DEPTHS = list(range(2, 9))          # descriptive trend: same range
BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_SEED = 20260724
H3_MIN_DISCORDANT = 10   # frozen: below this, H3 is not interpreted
PINNED_MANIFEST_SHA = ("c9fd0c6652e289c01565288326c0ba81e7cecec96038"
                       "16e19e9d173608c9c044")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gate_lib  # noqa: E402  (shared confirmatory gate, plan v1.4)


# ---------------------------------------------------------------------------
# Statistics helpers
# ---------------------------------------------------------------------------

def wilson_ci(k, n, z=1.96):
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    denom = 1 + z ** 2 / n
    centre = (p + z ** 2 / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def mcnemar_exact(b, c):
    """Two-sided exact McNemar p from discordant counts."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def holm(pvals):
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [0.0] * m
    running = 0.0
    for rank, idx in enumerate(order):
        val = (m - rank) * pvals[idx]
        running = max(running, val)
        adj[idx] = min(1.0, running)
    return adj


def newcombe_paired_diff_ci(a, b, c, d, z=1.96):
    """95% CI for p1 - p2 with paired binary data (Newcombe 1998, method 10).

    a = both success, b = only arm1, c = only arm2, d = both fail.
    p1 = (a+b)/n (arm1), p2 = (a+c)/n (arm2).
    """
    n = a + b + c + d
    if n == 0:
        return float("nan"), float("nan"), float("nan")
    p1, p2 = (a + b) / n, (a + c) / n
    diff = p1 - p2
    l1, u1 = wilson_ci(a + b, n, z)
    l2, u2 = wilson_ci(a + c, n, z)
    denom = (a + b) * (c + d) * (a + c) * (b + d)
    if denom > 0:
        phi = (a * d - b * c) / math.sqrt(denom)
    else:
        phi = 0.0
    dl = diff - math.sqrt(max(0.0, (p1 - l1) ** 2
                              - 2 * phi * (p1 - l1) * (u2 - p2)
                              + (u2 - p2) ** 2))
    du = diff + math.sqrt(max(0.0, (u1 - p1) ** 2
                              - 2 * phi * (u1 - p1) * (p2 - l2)
                              + (p2 - l2) ** 2))
    return diff, max(-1.0, dl), min(1.0, du)


def _sigmoid(eta):
    if eta >= 0:
        return 1.0 / (1.0 + math.exp(-eta))
    e = math.exp(eta)
    return e / (1.0 + e)


def _solve_linear(A, b):
    """Gaussian elimination with partial pivoting (small dense systems)."""
    n = len(b)
    M = [rowa[:] + [bb] for rowa, bb in zip(A, b)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            return None
        M[col], M[piv] = M[piv], M[col]
        for r in range(n):
            if r != col and M[r][col] != 0.0:
                f = M[r][col] / M[col][col]
                for c in range(col, n + 1):
                    M[r][c] -= f * M[col][c]
    return [M[i][n] / M[i][i] for i in range(n)]


def fit_glm(X, y, iters=200):
    """Logistic MLE for arbitrary design matrix via Newton's method.

    Returns (weights, log_likelihood) or None on failure. Step size is
    capped for stability on (quasi-)separable bootstrap resamples.
    """
    k = len(X[0])
    w = [0.0] * k
    for _ in range(iters):
        grad = [0.0] * k
        hess = [[0.0] * k for _ in range(k)]
        for xi, yi in zip(X, y):
            eta = sum(wj * xj for wj, xj in zip(w, xi))
            p = _sigmoid(eta)
            r = yi - p
            v = p * (1 - p)
            for a in range(k):
                grad[a] += r * xi[a]
                for b_ in range(a, k):
                    hess[a][b_] += v * xi[a] * xi[b_]
        for a in range(k):
            for b_ in range(a):
                hess[a][b_] = hess[b_][a]
        step = _solve_linear(hess, grad)
        if step is None:
            return None
        norm = max(abs(s) for s in step)
        if norm > 5.0:
            step = [s * 5.0 / norm for s in step]
        w = [wj + s for wj, s in zip(w, step)]
        if max(abs(s) for s in step) < 1e-9:
            break
    if not all(math.isfinite(wj) for wj in w):
        return None
    ll = 0.0
    for xi, yi in zip(X, y):
        eta = sum(wj * xj for wj, xj in zip(w, xi))
        # stable log-likelihood: y*eta - log(1 + e^eta)
        ll += yi * eta - (eta + math.log1p(math.exp(-eta)) if eta > 0
                          else math.log1p(math.exp(eta)))
    return w, ll


def fit_logistic(points):
    """MLE for Pr(success) = sigmoid(alpha - beta*d).

    points: list of (d, success 0/1). Returns (alpha, beta) or None.
    """
    fit = fit_glm([(1.0, d) for d, _ in points], [y for _, y in points])
    if fit is None:
        return None
    (alpha, coef_d), _ = fit
    return alpha, -coef_d


def chi2_sf(x, df):
    """Survival function of chi-squared with integer df (recurrence)."""
    if x <= 0:
        return 1.0
    if df == 1:
        return math.erfc(math.sqrt(x / 2.0))
    if df == 2:
        return math.exp(-x / 2.0)
    # Q(x; k+2) = Q(x; k) + (x/2)^(k/2) e^(-x/2) / Gamma(k/2 + 1)
    k = df - 2
    return min(1.0, chi2_sf(x, k)
               + (x / 2.0) ** (k / 2.0) * math.exp(-x / 2.0)
               / math.gamma(k / 2.0 + 1.0))


# ---------------------------------------------------------------------------
# Data loading + frozen failure classification
# ---------------------------------------------------------------------------

def to_int(s):
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def classify_failure(row):
    """Frozen order of precedence (ANALYSIS_PLAN.md §3)."""
    err = (row.get("error") or "")
    if err.startswith("API error") or err == "Max retries exceeded":
        return "api_error"
    if "timeout" in err.lower():
        return "timeout"
    finish = (row.get("finish_reason") or "").strip().lower()
    comp = to_int(row.get("completion_tokens"))
    req = to_int(row.get("requested_max_tokens"))
    if finish == "length" or (comp is not None and req is not None
                              and comp >= req):
        return "compute_bound_truncation"
    if err.startswith(("No valid Singmaster", "No legal HTM")):
        return "format_error"
    return "wrong_solution"


sha256_file = gate_lib.sha256_file


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs-dir", default="logs/dstar_runs")
    ap.add_argument("--out", default="results_dstar")
    ap.add_argument("--expected-n", type=int, default=50,
                    help="planned cell size for confirmatory depths")
    ap.add_argument("--force", action="store_true",
                    help="run despite a failed confirmatory gate; all "
                         "outputs are then marked non-confirmatory")
    ap.add_argument("--manifest", default=None,
                    help="instance manifest to validate against (hash "
                         "pinned; scored instance IDs and complexities "
                         "must match). REQUIRED for confirmatory outputs.")
    ap.add_argument("--schedule", default=None,
                    help="schedule file to validate against (its sha256 "
                         "must match the rows and run_config.json). "
                         "REQUIRED for confirmatory outputs.")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    rows, run_audit, api_errors, provenance = gate_lib.load_rows(
        args.runs_dir, ARMS)
    cells = defaultdict(dict)
    for r in rows:
        d = to_int(r.get("complexity"))
        if d is None:
            d = to_int(r.get("difficulty"))
        if d is None:
            sys.exit(f"row without difficulty ({r.get('model_tag')}/"
                     f"{r.get('instance_id')})")
        cells[(r["model_tag"], d)][r["instance_id"]] = r
    if api_errors:
        print(f"[warn] {len(api_errors)} api_error trials excluded "
              f"(re-run required for full n):")
        for arm, iid, err in api_errors[:10]:
            print(f"    {arm} {iid}: {err}")

    depths = sorted({d for (_, d) in cells})

    # ---- pairing audit --------------------------------------------------
    audit_rows = []
    for d in depths:
        ids = {arm: set(cells.get((arm, d), {})) for arm in ARMS}
        ok = ids[ARMS[0]] == ids[ARMS[1]] and len(ids[ARMS[0]]) > 0
        audit_rows.append({
            "d_star": d,
            "n_" + ARMS[0]: len(ids[ARMS[0]]),
            "n_" + ARMS[1]: len(ids[ARMS[1]]),
            "identical_instance_sets": "ok" if ok else "MISMATCH",
            "only_in_" + ARMS[0]: ";".join(sorted(ids[ARMS[0]] - ids[ARMS[1]])),
            "only_in_" + ARMS[1]: ";".join(sorted(ids[ARMS[1]] - ids[ARMS[0]])),
        })
    write_csv(os.path.join(args.out, "pairing_audit.csv"), audit_rows)

    # ---- confirmatory gate (shared library + Module A specifics) --------
    gate_problems = []

    # Module A cell rules: confirmatory depths complete AND paired
    for d in CONFIRMATORY_DEPTHS:
        ids = {arm: set(cells.get((arm, d), {})) for arm in ARMS}
        if not ids[ARMS[0]] and not ids[ARMS[1]]:
            gate_problems.append(f"d*={d}: confirmatory depth missing")
            continue
        if ids[ARMS[0]] != ids[ARMS[1]]:
            gate_problems.append(
                f"d*={d}: instance sets differ between arms "
                f"({len(ids[ARMS[0]])} vs {len(ids[ARMS[1]])})")
        for arm in ARMS:
            if len(ids[arm]) != args.expected_n:
                gate_problems.append(
                    f"d*={d}/{arm}: {len(ids[arm])} valid trials, "
                    f"expected {args.expected_n}")

    # Shared end-to-end integrity gate (identity, hashes, preflight, ...)
    lib_problems, gate_info = gate_lib.integrity_problems(
        args.runs_dir, run_audit, ARMS, rows,
        manifest_path=args.manifest, schedule_path=args.schedule,
        known_manifest_shas={PINNED_MANIFEST_SHA})
    gate_problems.extend(lib_problems)

    # Manifest identity + FULL completeness (all cells, incl. the
    # descriptive d* = 1, 9, 10)
    if args.manifest:
        with open(args.manifest) as fh:
            mani = json.load(fh)
        gate_problems.extend(
            gate_lib.completeness_problems(rows, mani, ARMS))
        dstar_by_id = {i["id"]: i["d_star"] for i in mani["instances"]}
        for (arm, d), trials in cells.items():
            for iid in trials:
                if iid in dstar_by_id and dstar_by_id[iid] != d:
                    gate_problems.append(
                        f"{arm}/{iid}: complexity {d} contradicts "
                        f"manifest d*={dstar_by_id[iid]}")

    gate_status = "passed"
    if gate_problems:
        print("[GATE] Confirmatory gate FAILED:")
        for p in gate_problems:
            print(f"    {p}")
        if not args.force:
            sys.exit("Confirmatory analyses refused (see gate problems "
                     "above). Complete the runs, or re-run with --force "
                     "for a NON-confirmatory exploratory pass.")
        gate_status = "FORCED-NON-CONFIRMATORY"
        print("[GATE] --force given: outputs are NON-confirmatory.")

    # ---- per-cell pass@1 + failure modes --------------------------------
    pass_rows, fail_rows = [], []
    for arm in ARMS:
        for d in depths:
            trials = cells.get((arm, d), {})
            if not trials:
                continue
            n = len(trials)
            k = sum(1 for r in trials.values() if r["_success"])
            lo, hi = wilson_ci(k, n)
            counts = defaultdict(int)
            for r in trials.values():
                if not r["_success"]:
                    counts[classify_failure(r)] += 1
            pass_rows.append({
                "arm": arm, "d_star": d, "n": n, "solved": k,
                "pass1": round(k / n, 4),
                "wilson_lo": round(lo, 4), "wilson_hi": round(hi, 4),
                **{c: counts.get(c, 0) for c in (
                    "wrong_solution", "format_error",
                    "compute_bound_truncation", "timeout")},
            })
            fail_rows.extend(
                {"arm": arm, "d_star": d, "category": c, "count": v}
                for c, v in sorted(counts.items()))
    write_csv(os.path.join(args.out, "pass1_dstar.csv"), pass_rows)
    write_csv(os.path.join(args.out, "failure_modes.csv"), fail_rows)

    # ---- H1 helpers ------------------------------------------------------
    def paired_counts(d):
        """(a, b, c, dd, common) for one depth: both / only-base /
        only-reason / neither successful, over the paired instances."""
        t1 = cells.get((ARMS[0], d), {})
        t2 = cells.get((ARMS[1], d), {})
        common = sorted(set(t1) & set(t2))
        a = sum(1 for i in common
                if t1[i]["_success"] and t2[i]["_success"])
        b = sum(1 for i in common
                if t1[i]["_success"] and not t2[i]["_success"])
        c = sum(1 for i in common
                if not t1[i]["_success"] and t2[i]["_success"])
        dd = len(common) - a - b - c
        return a, b, c, dd, common

    # ---- H1 PRIMARY: global paired test over d* = 2..8 -------------------
    # Pairs are independent instances; pooling the discordant pairs across
    # depths yields one exact McNemar test of the global mode effect
    # (equivalent to a within-pair sign-flip permutation test).
    A = B = C = DD = 0
    for d in CONFIRMATORY_DEPTHS:
        a, b, c, dd, _ = paired_counts(d)
        A, B, C, DD = A + a, B + b, C + c, DD + dd
    g_diff, g_lo, g_hi = newcombe_paired_diff_ci(A, C, B, DD)
    # TOST at alpha=0.05 via the 90% Newcombe CI: equivalence within
    # +/-15 pp is claimed only if that CI lies entirely inside the margin.
    _, t_lo, t_hi = newcombe_paired_diff_ci(A, C, B, DD, z=1.645)
    h1_global = [{
        "test": (f"global paired mode effect, {ARMS[1]} vs {ARMS[0]}, "
                 f"pooled d*={CONFIRMATORY_DEPTHS[0]}.."
                 f"{CONFIRMATORY_DEPTHS[-1]}"),
        "n_pairs": A + B + C + DD,
        "both_success": A, "only_base": B, "only_reason": C,
        "both_fail": DD,
        "diff_reason_minus_base": round(g_diff, 4),
        "diff_ci_lo": round(g_lo, 4), "diff_ci_hi": round(g_hi, 4),
        "p_mcnemar_exact": float(f"{mcnemar_exact(B, C):.4g}"),
        "tost_ci90_lo": round(t_lo, 4),
        "tost_ci90_hi": round(t_hi, 4),
        "tost_equivalent_pm15pp": (-0.15 < t_lo and t_hi < 0.15),
        "confirmatory": gate_status == "passed",
    }]
    write_csv(os.path.join(args.out, "h1_global.csv"), h1_global)

    # ---- H1 SECONDARY: per-depth McNemar family --------------------------
    h1 = []
    for d in CONFIRMATORY_DEPTHS:
        a, b, c, dd, common = paired_counts(d)
        diff, dlo, dhi = newcombe_paired_diff_ci(a, c, b, dd)
        # NOTE: diff is reported as reason - base, hence (a, c, b, d):
        # arm1 = reason (a+c successes), arm2 = base.
        h1.append({
            "comparison": f"{ARMS[1]} vs {ARMS[0]} @ d*={d}",
            "d_star": d, "n_pairs": len(common),
            "both_success": a, "only_base": b, "only_reason": c,
            "both_fail": dd,
            "diff_reason_minus_base": round(diff, 4),
            "diff_ci_lo": round(dlo, 4), "diff_ci_hi": round(dhi, 4),
            "p_mcnemar_exact": mcnemar_exact(b, c),
        })
    pvals = [r["p_mcnemar_exact"] for r in h1]
    for r, p_adj in zip(h1, holm(pvals)):
        r["significant_after_holm"] = p_adj < 0.05
        # 4 significant figures, never decimal rounding: exact-test
        # p-values can be ~1e-9 and must survive the CSV round trip.
        r["p_mcnemar_exact"] = float(f"{r['p_mcnemar_exact']:.4g}")
        r["p_holm"] = float(f"{p_adj:.4g}")
    write_csv(os.path.join(args.out, "h1_mcnemar.csv"), h1)

    # ---- H2 (DESCRIPTIVE): logistic depth trend + form check ------------
    # The linear-logit curve is a compact summary, not a structural claim:
    # each arm's fit is accompanied by a deviance comparison against the
    # saturated categorical-depth model, so thresholds/plateaus that the
    # linear form cannot express are flagged instead of hidden.
    rng = random.Random(BOOTSTRAP_SEED)
    h2 = []
    for arm in ARMS:
        points, by_depth = [], {}
        for d in TREND_DEPTHS:
            rows = [(d, 1 if r["_success"] else 0)
                    for r in cells.get((arm, d), {}).values()]
            if rows:
                by_depth[d] = rows
                points.extend(rows)
        if not points:
            continue
        fit = fit_logistic(points)
        betas = []
        for _ in range(BOOTSTRAP_RESAMPLES):
            sample = []
            for d, rows in by_depth.items():
                sample.extend(rng.choice(rows) for _ in rows)
            f = fit_logistic(sample)
            if f is not None:
                betas.append(f[1])
        betas.sort()
        lo = betas[int(0.025 * len(betas))] if betas else float("nan")
        hi = betas[int(0.975 * len(betas)) - 1] if betas else float("nan")

        # Linear vs saturated categorical-depth model (within one arm the
        # trials are independent instances, so the chi2 reference holds).
        lr_cat = p_cat = None
        if fit is not None and len(by_depth) > 2:
            alpha_hat, beta_hat = fit
            ll_lin = ll_sat = 0.0
            for d, rows in by_depth.items():
                n_d = len(rows)
                k_d = sum(y for _, y in rows)
                p_lin = _sigmoid(alpha_hat - beta_hat * d)
                p_lin = min(max(p_lin, 1e-12), 1 - 1e-12)
                ll_lin += k_d * math.log(p_lin) \
                    + (n_d - k_d) * math.log(1 - p_lin)
                if 0 < k_d < n_d:
                    p_hat = k_d / n_d
                    ll_sat += k_d * math.log(p_hat) \
                        + (n_d - k_d) * math.log(1 - p_hat)
                # k_d in {0, n_d}: saturated ll contribution is 0
            lr_cat = max(0.0, 2 * (ll_sat - ll_lin))
            df_cat = len(by_depth) - 2
            p_cat = float(f"{chi2_sf(lr_cat, df_cat):.4g}")

        h2.append({
            "arm": arm,
            "depth_range": f"{TREND_DEPTHS[0]}-{TREND_DEPTHS[-1]}",
            "n_trials": len(points),
            "alpha_hat": round(fit[0], 4) if fit else None,
            "beta_hat": round(fit[1], 4) if fit else None,
            "beta_ci_lo": round(lo, 4),
            "beta_ci_hi": round(hi, 4),
            "bootstrap_resamples_converged": len(betas),
            "lr_linear_vs_categorical": (round(lr_cat, 4)
                                         if lr_cat is not None else None),
            "p_linear_form_adequate": p_cat,
            "note": "descriptive summary; not a confirmatory test",
        })
    write_csv(os.path.join(args.out, "h2_trend.csv"), h2)

    # ---- goodness of fit: observed vs fitted per depth cell -------------
    gof_rows = []
    arm_fits = {r["arm"]: (r["alpha_hat"], r["beta_hat"]) for r in h2
                if r["alpha_hat"] is not None}
    for arm, (alpha, beta) in arm_fits.items():
        for d in TREND_DEPTHS:
            trials = cells.get((arm, d), {})
            if not trials:
                continue
            n = len(trials)
            k = sum(1 for r in trials.values() if r["_success"])
            gof_rows.append({
                "arm": arm, "d_star": d, "n": n,
                "observed_pass1": round(k / n, 4),
                "fitted_pass1": round(_sigmoid(alpha - beta * d), 4),
            })
    write_csv(os.path.join(args.out, "fit_diagnostics.csv"), gof_rows)

    # ---- H3: mode x depth interaction (conditional on discordant pairs) --
    # A naive within-pair label-flip permutation tests exchangeability of
    # the arms (gamma = 0 AND delta = 0), not the interaction alone; and an
    # unpaired chi-squared LR ignores the pair dependence. The valid paired
    # analysis conditions on the discordant pairs (1:1 matched conditional
    # logistic regression): among discordant pairs,
    #     Pr(reason wins | d*) = sigma(gamma + delta * d*).
    # Pair-specific difficulty cancels by conditioning; discordant pairs
    # are independent instances, so the chi-squared(1) LR reference is
    # valid. delta tests whether the direction of discordance changes with
    # depth (the interaction); gamma = 0 in the reduced model is the
    # pooled McNemar, i.e. the PRIMARY test — hence the hierarchy rule.
    disc = []   # (d, z) with z = 1 if only-reason wins, 0 if only-base
    for d in TREND_DEPTHS:
        t1 = cells.get((ARMS[0], d), {})
        t2 = cells.get((ARMS[1], d), {})
        for iid in set(t1) & set(t2):
            yb = t1[iid]["_success"]
            yr = t2[iid]["_success"]
            if yb != yr:
                disc.append((float(d), 1 if yr else 0))

    h3 = []
    if disc and len(disc) < H3_MIN_DISCORDANT:
        h3.append({
            "model": "conditional logistic on discordant pairs",
            "inference": (f"NOT INTERPRETED: only {len(disc)} discordant "
                          f"pairs (< frozen minimum "
                          f"{H3_MIN_DISCORDANT})"),
            "n_discordant_pairs": len(disc),
            "estimable": False,
            "confirmatory": gate_status == "passed",
        })
        disc = []
    if disc and not (0 < sum(z for _, z in disc) < len(disc)):
        # Degenerate: every discordant pair favours the same arm —
        # report "not estimable" explicitly (frozen fallback rule)
        # instead of silently omitting H3.
        per_depth = defaultdict(lambda: [0, 0])
        for d, z in disc:
            per_depth[int(d)][z] += 1
        h3.append({
            "model": "conditional logistic on discordant pairs",
            "inference": "NOT ESTIMABLE: all discordant pairs favour "
                         "one arm (separation); raw counts reported",
            "n_discordant_pairs": len(disc),
            "per_depth_only_base_only_reason": json.dumps(
                {d: per_depth[d] for d in sorted(per_depth)}),
            "estimable": False,
            "confirmatory": gate_status == "passed",
        })
    if disc and 0 < sum(z for _, z in disc) < len(disc):
        # centre depth for a stable, interpretable intercept
        dbar = sum(d for d, _ in disc) / len(disc)
        X_full = [(1.0, d - dbar) for d, _ in disc]
        X_red = [(1.0,) for _ in disc]
        y = [z for _, z in disc]
        f_full = fit_glm(X_full, y)
        f_red = fit_glm(X_red, y)
        if f_full and f_red:
            (g_f, delta_f), ll_full = f_full
            (g_r,), ll_red = f_red
            lr_delta = max(0.0, 2 * (ll_full - ll_red))
            h3.append({
                "model": ("conditional logistic on discordant pairs: "
                          "Pr(reason wins | d*) = "
                          "sigma(gamma + delta*(d* - mean(d*))), "
                          f"d*={TREND_DEPTHS[0]}..{TREND_DEPTHS[-1]}"),
                "inference": ("LR chi2(1); discordant pairs are "
                              "independent instances, pair effects cancel "
                              "by conditioning"),
                "n_discordant_pairs": len(disc),
                "mean_depth_centering": round(dbar, 4),
                "gamma_hat": round(g_f, 4),
                "delta_hat": round(delta_f, 4),
                "lr_delta": round(lr_delta, 4),
                "p_delta_chi2_1df": float(f"{chi2_sf(lr_delta, 1):.4g}"),
                "hierarchy": ("interpret only if the PRIMARY pooled test "
                              "(h1_global) rejects at alpha = 0.05"),
                "confirmatory": gate_status == "passed",
            })
    write_csv(os.path.join(args.out, "h3_interaction.csv"), h3)

    # ---- exploratory: solution length + tokens --------------------------
    expl = []
    for arm in ARMS:
        for d in depths:
            trials = cells.get((arm, d), {})
            if not trials:
                continue
            lens = [to_int(r.get("num_moves")) for r in trials.values()
                    if r["_success"] and to_int(r.get("num_moves"))]
            comp = [to_int(r.get("completion_tokens")) for r in trials.values()
                    if to_int(r.get("completion_tokens")) is not None]
            reas = [to_int(r.get("reasoning_tokens")) for r in trials.values()
                    if to_int(r.get("reasoning_tokens")) is not None]
            parse_modes = defaultdict(int)
            for r in trials.values():
                parse_modes[(r.get("parse_mode") or "n/a")] += 1
            rescue = sum(1 for r in trials.values()
                         if r.get("lenient_rescue") == "True")
            expl.append({
                "arm": arm, "d_star": d,
                "n_success": len(lens),
                "optimal_length_successes":
                    sum(1 for x in lens if x == d),
                "median_solution_length":
                    (sorted(lens)[len(lens) // 2] if lens else None),
                "mean_completion_tokens":
                    (round(sum(comp) / len(comp)) if comp else None),
                "mean_reasoning_tokens":
                    (round(sum(reas) / len(reas)) if reas else None),
                "parse_strict": parse_modes.get("strict", 0),
                "parse_lenient": parse_modes.get("lenient", 0),
                "parse_none": parse_modes.get("none", 0),
                "lenient_rescues": rescue,
            })
    write_csv(os.path.join(args.out, "exploratory.csv"), expl)

    # ---- provenance manifest ---------------------------------------------
    plan_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "ANALYSIS_PLAN.md")
    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(
            timespec="seconds"),
        "analysis_plan": {
            "file": "ANALYSIS_PLAN.md",
            "sha256": (sha256_file(plan_path)
                       if os.path.exists(plan_path) else None),
        },
        "runs_dir": args.runs_dir,
        "inputs": provenance,
        "excluded_trials_api_error": [
            {"arm": a, "instance_id": i, "error": e}
            for a, i, e in api_errors],
        "frozen_plan": {
            "file": "ANALYSIS_PLAN_FROZEN.md (v1.7.1, included verbatim; git tag v1.7.1-freeze)",
            "sha256": "27cce48b1b565c92d2081609b147975c2278ab7eb6981e4"
                      "866ad1a4a613f79c9",
            "note": "immutable deposit made before the first trial; "
                    "file title reads v1.7 (the v1.7.1 "
                    "amendment updated only the change log); "
                    "post-collection deviations in DEVIATIONS.md",
        },
        "confirmatory_gate": {
            "status": gate_status,
            "expected_n": args.expected_n,
            "manifest_validated": bool(args.manifest),
            "schedule_validated": bool(args.schedule),
            "identity": gate_info,
            "problems": gate_problems,
        },
        "tests": {
            "primary": (f"global paired mode effect, exact McNemar on "
                        f"discordants pooled over d*="
                        f"{CONFIRMATORY_DEPTHS[0]}.."
                        f"{CONFIRMATORY_DEPTHS[-1]}"),
            "secondary_family": [f"{ARMS[1]} vs {ARMS[0]} @ d*={d}"
                                 for d in CONFIRMATORY_DEPTHS],
            "correction": "holm (within the secondary family)",
            "alpha": 0.05,
            "h3_inference": ("conditional logistic on discordant pairs, "
                             "LR chi2(1); hierarchical: interpret only "
                             "if the primary test rejects"),
            "equivalence": "TOST via 90% Newcombe CI, margin +/-15pp",
        },
        "trend": {"depths": TREND_DEPTHS,
                  "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
                  "bootstrap_seed": BOOTSTRAP_SEED,
                  "role": "descriptive"},
        "outputs": {
            "pass1_dstar.csv": "per-cell pass@1, Wilson CIs, failure modes",
            "h1_global.csv": "PRIMARY pooled paired test (confirmatory)",
            "h1_mcnemar.csv": "secondary per-depth McNemar family",
            "h2_trend.csv": "logistic depth trends (descriptive) + "
                            "linear-vs-categorical form check",
            "h3_interaction.csv": "mode x depth interaction: conditional "
                                  "logistic on discordant pairs "
                                  "(LR chi2(1); raw counts if separated)",
            "fit_diagnostics.csv": "observed vs fitted pass@1 per cell",
            "failure_modes.csv": "frozen failure taxonomy counts",
            "pairing_audit.csv": "instance-pairing check per depth",
            "exploratory.csv": "solution lengths / tokens / parse modes",
        },
    }
    with open(os.path.join(args.out, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)

    # ---- console summary -------------------------------------------------
    print(f"\nWrote results to {args.out}/")
    print(f"Confirmatory gate: {gate_status}")
    bad = [r for r in audit_rows if r["identical_instance_sets"] != "ok"]
    if bad:
        print(f"[WARN] pairing audit failed for depths: "
              f"{[r['d_star'] for r in bad]}")
    else:
        print("Pairing audit: identical instance sets in both arms "
              "at every depth.")
    for r in h1_global:
        print(f"  H1 PRIMARY (pooled d*=2..8): "
              f"diff={r['diff_reason_minus_base']:+.2f} "
              f"[{r['diff_ci_lo']:+.2f}, {r['diff_ci_hi']:+.2f}], "
              f"b:c={r['only_base']}:{r['only_reason']}, "
              f"p={r['p_mcnemar_exact']:.4g}")
    for r in h1:
        print(f"  H1 sec d*={r['d_star']}: "
              f"diff={r['diff_reason_minus_base']:+.2f} "
              f"[{r['diff_ci_lo']:+.2f}, {r['diff_ci_hi']:+.2f}], "
              f"b:c={r['only_base']}:{r['only_reason']}, "
              f"p={r['p_mcnemar_exact']:.4g}, Holm={r['p_holm']:.4g}")
    for r in h2:
        print(f"  H2 {r['arm']} (descriptive): beta={r['beta_hat']} "
              f"[{r['beta_ci_lo']}, {r['beta_ci_hi']}], "
              f"linear-form p={r['p_linear_form_adequate']}")
    for r in h3:
        if r.get("estimable"):
            print(f"  H3 (discordant-pair conditional logistic): "
                  f"delta={r['delta_hat']}, "
                  f"p={r['p_delta_chi2_1df']:.4g} "
                  f"(n_disc={r['n_discordant_pairs']}; interpret only "
                  f"if primary rejects)")
        else:
            print(f"  H3: {r.get('inference')} "
                  f"(n_disc={r.get('n_discordant_pairs')})")


def write_csv(path, rows):
    if not rows:
        with open(path, "w") as fh:
            fh.write("")
        return
    keys = list(rows[0].keys())
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
