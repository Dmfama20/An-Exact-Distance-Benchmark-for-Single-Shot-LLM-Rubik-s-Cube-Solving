#!/usr/bin/env python3
"""Test scripts/analyse_nominal.py (Module B) against synthetic data with
known truth: shared confirmatory gate, primary collapsed-effect test,
enrichment descriptive, midpoint shift, unique-state sensitivity,
duplicate abort, and gate failure on incompleteness.

STRUCTURE: nothing expensive runs at import time.
The pure unit tests (test_holm_family_of_two, test_shared_estimator)
are conventional zero-setup test functions; the end-to-end pipeline
checks live in run_pipeline_checks(), invoked by main().

Run:      python tests/test_analyse_nominal.py
Runtime:  ~10 s on an Apple M-class laptop (five pipeline
          invocations with --boot 150); minutes on older hardware.
"""

import csv
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime as _dt, timedelta as _td, timezone as _tz

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))

MANIFEST_PATH = os.path.join(REPO, "instances",
                             "rubiks_nominal_manifest_v1.json")
SCHEDULE_PATH = os.path.join(REPO, "instances",
                             "rubiks_nominal_schedule_v1.json")
MODEL = "openai/gpt-5.5-20260423"
_T0 = _dt(2026, 7, 24, 0, 0, 0, tzinfo=_tz.utc)

COLS = ["timestamp", "benchmark", "test_id", "difficulty", "success",
        "num_moves", "tokens_used", "time_seconds", "error", "instance_id",
        "size", "complexity", "similarity", "parse_mode", "lenient_rescue",
        "model_id", "model_tag", "reasoning", "temperature", "backend",
        "finish_reason", "prompt_tokens", "completion_tokens",
        "reasoning_tokens", "requested_max_tokens", "requested_reasoning",
        "retries", "retry_errors", "response_model", "provider",
        "system_fingerprint", "run_config_hash", "schedule_hash",
        "block_index", "arm_order"]


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f" — {detail}" if detail else ""))
    if not cond:
        sys.exit(1)


# ---------------------------------------------------------------------------
# Pure unit tests (no setup; discoverable by any conventional runner)
# ---------------------------------------------------------------------------

def test_holm_family_of_two():
    import analyse_nominal as an
    fam = [
        {"arm": "gpt55-base", "estimable": True, "p_collapsed": 0.03},
        {"arm": "gpt55-reason", "estimable": False,
         "note": "NOT ESTIMABLE"},
    ]
    an.holm_family_of_two(fam)
    assert all(r["holm_family_size"] == 2 for r in fam)
    assert abs(fam[0]["p_holm"] - 0.06) < 1e-9
    assert fam[1]["p_holm"] == 1.0
    assert fam[1]["significant_after_holm"] is False


def test_shared_estimator():
    import analyse_nominal as an
    import power_sim_moduleB as ps
    assert ps.fit_collapsed_lr is an.fit_collapsed_lr
    sep_items = [(ln, c, int(c)) for ln in (6, 7, 8)
                 for c in (1.0, 0.0) for _ in range(20)]
    assert an.fit_collapsed_lr(sep_items) is None


# ---------------------------------------------------------------------------
# End-to-end pipeline checks (synthetic world; subprocess invocations)
# ---------------------------------------------------------------------------

def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 16), b""):
            h.update(c)
    return h.hexdigest()


def _build_world(work):
    runs = os.path.join(work, "runs")
    os.makedirs(runs)
    sched = json.load(open(SCHEDULE_PATH))
    block = {b["instance_id"]: (b["block"], b["first_arm"])
             for b in sched["blocks"]}
    preflight = {
        "timestamp_utc": "2026-07-23T09:00:00+00:00", "model": MODEL,
        "temperature": None, "provider_order": ["OpenAI"],
        "max_tokens": 32000, "base_effort": "none",
        "reason_effort": "medium",
        "observed": {
            "base": {"reasoning_tokens": [0, 0, 0],
                     "snapshots": ["openai/gpt-5.5"],
                     "providers": ["OpenAI"]},
            "reason": {"reasoning_tokens": [3100, 2800, 3300],
                       "snapshots": ["openai/gpt-5.5"],
                       "providers": ["OpenAI"]},
        },
        "verdict": "ok",
    }
    pfp = os.path.join(runs, "preflight.json")
    json.dump(preflight, open(pfp, "w"))
    run_config = {
        "model": MODEL, "preflight_sha256": _sha(pfp),
        "provider_order": ["OpenAI"],
        "require_parameters": True, "allow_fallbacks": False,
        "base_effort": "none", "reason_effort": "medium",
        "temperature": None, "max_tokens": 32000, "trial_timeout": 600,
        "manifest_sha256": _sha(MANIFEST_PATH),
        "schedule_sha256": _sha(SCHEDULE_PATH),
    }
    rch = hashlib.sha256(
        json.dumps(run_config, sort_keys=True).encode()).hexdigest()

    def row(arm, inst, success):
        blk, first = block[inst["id"]]
        is_first = (arm == "gpt55-base") == (first == "base")
        ts = (_T0 + _td(seconds=blk * 120
                        + (0 if is_first else 60))).isoformat()
        return {
            "timestamp": ts, "benchmark": "rubiks",
            "test_id": 1, "difficulty": inst["nominal_length"],
            "success": str(success), "num_moves": "",
            "tokens_used": 3000, "time_seconds": 10,
            "error": "" if success else "Cube not solved",
            "instance_id": inst["id"], "size": "3x3x3",
            "complexity": inst["verified_d_star"], "similarity": "",
            "parse_mode": "strict", "lenient_rescue": "",
            "model_id": MODEL, "model_tag": arm, "reasoning": "",
            "temperature": "unset", "backend": "openrouter",
            "finish_reason": "stop", "prompt_tokens": 1000,
            "completion_tokens": 2000, "reasoning_tokens": "",
            "requested_max_tokens": 32000,
            "requested_reasoning": ('{"effort": "none"}'
                                    if arm == "gpt55-base"
                                    else '{"effort": "medium"}'),
            "retries": 0, "retry_errors": "",
            "response_model": "openai/gpt-5.5", "provider": "OpenAI",
            "system_fingerprint": "", "run_config_hash": rch,
            "schedule_hash": _sha(SCHEDULE_PATH), "block_index": blk,
            "arm_order": f"{first}-first",
        }

    manifest = json.load(open(MANIFEST_PATH))
    rng = random.Random(7)
    # Ground truth: success decays in VERIFIED d*; collapsed instances
    # are systematically easier at equal nominal length -> primary
    # collapsed effect must come out positive (OR > 1).
    for arm, base_p in (("gpt55-base", 0.93), ("gpt55-reason", 0.97)):
        rows = []
        for inst in manifest["instances"]:
            v = inst["verified_d_star"]
            p = base_p ** (2 ** max(0, v - 1))
            rows.append(row(arm, inst, rng.random() < p))
        path = os.path.join(runs,
                            f"{arm}__rubiks__dstarX__batch_metrics.csv")
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=COLS)
            w.writeheader()
            w.writerows(rows)
    json.dump({"run_config": run_config, "run_config_hash": rch},
              open(os.path.join(runs, "run_config.json"), "w"))
    return runs, manifest, row


def _run(runs_dir, out, *extra):
    return subprocess.run(
        [sys.executable,
         os.path.join(REPO, "scripts", "analyse_nominal.py"),
         "--runs-dir", runs_dir, "--manifest", MANIFEST_PATH,
         "--schedule", SCHEDULE_PATH, "--out", out, "--boot", "150",
         *extra],
        capture_output=True, text=True)


def run_pipeline_checks():
    work = tempfile.mkdtemp(prefix="analyse_nominal_test_")
    runs, manifest, row = _build_world(work)

    out = os.path.join(work, "out")
    proc = _run(runs, out)
    print(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr)
        sys.exit(1)
    check("gate passed", "gate: passed" in proc.stdout)

    with open(os.path.join(out, "collapsed_effect.csv")) as fh:
        prim = {r["arm"]: r for r in csv.DictReader(fh)}
    for arm in prim:
        check(f"primary estimable ({arm})",
              prim[arm]["estimable"] == "True")
        check(f"primary OR > 1 ({arm})",
              float(prim[arm]["odds_ratio_collapsed"]) > 1.0,
              prim[arm]["odds_ratio_collapsed"])
        check(f"primary confirmatory flag ({arm})",
              prim[arm]["confirmatory"] == "True")
    check("primary p small for base arm",
          float(prim["gpt55-base"]["p_collapsed"]) < 0.05,
          prim["gpt55-base"]["p_collapsed"])
    check("Holm family over the two arm tests reported",
          prim["gpt55-base"]["p_holm"] != ""
          and float(prim["gpt55-base"]["p_holm"])
          >= float(prim["gpt55-base"]["p_collapsed"]))
    check("bootstrap convergence rate above the frozen minimum",
          float(prim["gpt55-base"]["bootstrap_convergence"]) >= 0.5)
    check("bootstrap CI present",
          prim["gpt55-base"]["or_boot_ci_lo"] != "")

    with open(os.path.join(out, "collapsed_descriptive.csv")) as fh:
        de = {r["arm"]: r for r in csv.DictReader(fh)}
    check("descriptive reports base rate next to share",
          float(de["gpt55-base"]["collapse_base_rate"]) > 0
          and de["gpt55-base"]["enrichment_vs_base_rate"] != "")
    check("enrichment > 1 (collapsed easier by construction)",
          float(de["gpt55-base"]["enrichment_vs_base_rate"]) > 1.0)

    with open(os.path.join(out, "midpoint_shift.csv")) as fh:
        mid = {r["arm"]: r for r in csv.DictReader(fh)}
    check("midpoints estimable", mid["gpt55-base"]["estimable"] == "True")
    check("nominal axis overstates (shift > 0)",
          float(mid["gpt55-base"]["overstatement"]) > 0)
    check("unique-state sensitivity written",
          os.path.getsize(os.path.join(
              out, "sensitivity_unique_states.csv")) > 0)

    with open(os.path.join(out, "manifest.json")) as fh:
        man = json.load(fh)
    check("gate identity recorded",
          man["confirmatory_gate"]["identity"]["provider"] == "openai")
    check("estimand recorded as process-level",
          "scramble process" in man["estimand"])

    # ---- smoke mode: fast, marked, skips sensitivity -----------------
    proc_s = _run(runs, os.path.join(work, "out_smoke"), "--smoke")
    check("--smoke completes and passes gate", proc_s.returncode == 0
          and "gate: passed" in proc_s.stdout)
    with open(os.path.join(work, "out_smoke", "manifest.json")) as fh:
        check("--smoke marked in provenance",
              json.load(fh)["smoke_mode"] is True)

    # ---- duplicate trial -> loader abort ------------------------------
    runs2 = os.path.join(work, "runs_dup")
    shutil.copytree(runs, runs2)
    dup = row("gpt55-base", manifest["instances"][0], True)
    path = os.path.join(runs2,
                        "gpt55-base__rubiks__dstarX__batch_metrics.csv")
    with open(path, "a", newline="") as fh:
        csv.DictWriter(fh, fieldnames=COLS).writerow(dup)
    proc2 = _run(runs2, os.path.join(work, "out_dup"))
    check("duplicate trial aborts loudly", proc2.returncode != 0
          and "duplicate trial" in (proc2.stdout + proc2.stderr))

    # ---- missing instance -> gate failure; --force works --------------
    runs3 = os.path.join(work, "runs_short")
    shutil.copytree(runs, runs3)
    path = os.path.join(runs3,
                        "gpt55-reason__rubiks__dstarX__batch_metrics.csv")
    lines = open(path).read().rstrip("\n").split("\n")
    open(path, "w").write("\n".join(lines[:-1]) + "\n")
    proc3 = _run(runs3, os.path.join(work, "out_short"))
    check("incomplete run refused", proc3.returncode != 0
          and "refused" in (proc3.stdout + proc3.stderr))
    proc4 = _run(runs3, os.path.join(work, "out_forced"), "--force")
    check("--force yields non-confirmatory pass", proc4.returncode == 0
          and "FORCED-NON-CONFIRMATORY" in proc4.stdout)
    with open(os.path.join(work, "out_forced",
                           "collapsed_effect.csv")) as fh:
        pf2 = list(csv.DictReader(fh))
    check("forced outputs marked non-confirmatory",
          all(r["confirmatory"] == "False" for r in pf2))


def main():
    test_holm_family_of_two()
    print("  PASS  Holm family of two (unit)")
    test_shared_estimator()
    print("  PASS  shared estimator + separation rule (unit)")
    run_pipeline_checks()
    print("\nAll Module B analysis checks passed.")


if __name__ == "__main__":
    main()
