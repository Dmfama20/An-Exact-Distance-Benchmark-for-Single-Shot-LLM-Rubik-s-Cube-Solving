#!/usr/bin/env python3
"""
Paper figures from the analysis outputs (run AFTER analyse_dstar.py /
analyse_nominal.py). Produces PDFs in figures/:

    fig_depth_profiles.pdf   pass@1 vs. verified d* (both arms, Wilson
                             CIs) with the random-sequence baseline
                             overlaid (Module A)
    fig_paired_diff.pdf      per-depth paired risk differences
                             (reason - base) with Newcombe 95% CIs and
                             the pooled primary estimate (Module A)
    fig_axes_contrast.pdf    pass@1 under the nominal axis vs. the same
                             trials re-stratified by verified d*
                             (Module B, per arm)

Requires matplotlib (not part of the runtime lock file; install on the
analysis machine: pip install matplotlib).

Usage:
    python scripts/make_figures.py [--results results_dstar]
        [--nominal results_nominal] [--out figures]
"""

import argparse
import csv
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ARM_STYLE = {
    "gpt55-base": dict(color="#1f77b4", marker="o",
                       label="GPT-5.5, effort none"),
    "gpt55-reason": dict(color="#d62728", marker="s",
                         label="GPT-5.5, effort medium"),
}


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return list(csv.DictReader(fh))


def fig_depth_profiles(results_dir, baseline_path, out):
    rows = read_csv(os.path.join(results_dir, "pass1_dstar.csv"))
    if not rows:
        print("  [skip] pass1_dstar.csv missing")
        return
    fig, ax = plt.subplots(figsize=(6.0, 3.8))
    for arm, style in ARM_STYLE.items():
        pts = sorted(((int(r["d_star"]), float(r["pass1"]),
                       float(r["wilson_lo"]), float(r["wilson_hi"]))
                      for r in rows if r["arm"] == arm))
        if not pts:
            continue
        d, p, lo, hi = zip(*pts)
        ax.errorbar(d, p, yerr=[[pi - l for pi, l in zip(p, lo)],
                                [h - pi for pi, h in zip(p, hi)]],
                    capsize=3, lw=1.5, **style)
    base = read_csv(baseline_path)
    if base:
        pts = sorted((int(r["d_star"]), float(r["random_policy_pass1"]))
                     for r in base if int(r["length_offset"] or 0) == 0)
        if pts:
            d, p = zip(*pts)
            ax.plot(d, p, ls=":", color="grey",
                    label="random-sequence baseline (len = d*)")
    ax.set_xlabel("verified optimal distance $d^*$")
    ax.set_ylabel("pass@1")
    ax.set_ylim(-0.03, 1.03)
    ax.set_xticks(sorted({int(r["d_star"]) for r in rows}))
    ax.legend(frameon=False, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out)
    print(f"  wrote {out}")


def fig_paired_diff(results_dir, out):
    rows = read_csv(os.path.join(results_dir, "h1_mcnemar.csv"))
    glob_rows = read_csv(os.path.join(results_dir, "h1_global.csv"))
    if not rows:
        print("  [skip] h1_mcnemar.csv missing")
        return
    fig, ax = plt.subplots(figsize=(6.0, 3.4))
    d = [int(r["d_star"]) for r in rows]
    diff = [float(r["diff_reason_minus_base"]) for r in rows]
    lo = [float(r["diff_ci_lo"]) for r in rows]
    hi = [float(r["diff_ci_hi"]) for r in rows]
    ax.axhline(0, color="grey", lw=0.8)
    ax.errorbar(d, diff,
                yerr=[[x - l for x, l in zip(diff, lo)],
                      [h - x for x, h in zip(diff, hi)]],
                fmt="o", color="#2ca02c", capsize=3,
                label="per-depth paired difference (Newcombe 95% CI)")
    if glob_rows:
        g = glob_rows[0]
        ax.axhspan(float(g["diff_ci_lo"]), float(g["diff_ci_hi"]),
                   color="#2ca02c", alpha=0.12,
                   label=(f"pooled primary estimate "
                          f"{float(g['diff_reason_minus_base']):+.2f}"))
    ax.set_xlabel("verified optimal distance $d^*$")
    ax.set_ylabel("pass@1 difference\n(reason $-$ base)")
    ax.set_xticks(d)
    ax.legend(frameon=False, fontsize=8)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(out)
    print(f"  wrote {out}")


def fig_axes_contrast(nominal_dir, out):
    rows = [r for r in read_csv(os.path.join(nominal_dir,
                                              "axes_comparison.csv"))
            if r.get("view", "process") == "process"]
    if not rows:
        print("  [skip] axes_comparison.csv missing (Module B not run)")
        return
    fig, axes = plt.subplots(1, 2, figsize=(7.6, 3.4), sharey=True)
    for ax, arm in zip(axes, ARM_STYLE):
        for axis_name, ls, lbl in (
                ("nominal_length", "--", "nominal scramble length"),
                ("verified_d_star", "-", "verified $d^*$ (re-stratified)")):
            pts = sorted(((int(r["value"]), float(r["pass1"]),
                           float(r["wilson_lo"]), float(r["wilson_hi"]))
                          for r in rows
                          if r["arm"] == arm and r["axis"] == axis_name))
            if not pts:
                continue
            v, p, lo, hi = zip(*pts)
            ax.errorbar(v, p,
                        yerr=[[pi - l for pi, l in zip(p, lo)],
                              [h - pi for pi, h in zip(p, hi)]],
                        ls=ls, marker=".", capsize=2, lw=1.3,
                        color=ARM_STYLE[arm]["color"], label=lbl)
        ax.set_title(ARM_STYLE[arm]["label"], fontsize=9)
        ax.set_xlabel("depth (axis as labelled)")
        ax.set_ylim(-0.03, 1.03)
        ax.legend(frameon=False, fontsize=7)
        ax.spines[["top", "right"]].set_visible(False)
    axes[0].set_ylabel("pass@1")
    fig.tight_layout()
    fig.savefig(out)
    print(f"  wrote {out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results_dstar")
    ap.add_argument("--nominal", default="results_nominal")
    ap.add_argument("--baseline",
                    default="instances/rubiks_random_baseline_v1.csv")
    ap.add_argument("--out", default="figures")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    fig_depth_profiles(args.results, args.baseline,
                       os.path.join(args.out, "fig_depth_profiles.pdf"))
    fig_paired_diff(args.results,
                    os.path.join(args.out, "fig_paired_diff.pdf"))
    fig_axes_contrast(args.nominal,
                      os.path.join(args.out, "fig_axes_contrast.pdf"))


if __name__ == "__main__":
    sys.exit(main())
