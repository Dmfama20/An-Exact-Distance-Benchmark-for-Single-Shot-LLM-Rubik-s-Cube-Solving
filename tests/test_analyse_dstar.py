#!/usr/bin/env python3
"""Test scripts/analyse_dstar.py against synthetic CSVs with known truth.

Covers: the confirmatory gate (pass / hard-fail / --force), the pooled
primary McNemar test, the secondary per-depth family, permutation-based
H3, the descriptive trend with linear-form check, the frozen failure
taxonomy, and provenance outputs.

Run:  python tests/test_analyse_dstar.py
"""

import csv
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = tempfile.mkdtemp(prefix="analyse_dstar_test_")
RUNS = os.path.join(WORK, "runs")
OUT = os.path.join(WORK, "results")
os.makedirs(RUNS, exist_ok=True)

COLS = ["timestamp", "benchmark", "test_id", "difficulty", "success",
        "num_moves", "tokens_used", "time_seconds", "error", "instance_id",
        "size", "complexity", "similarity", "parse_mode", "lenient_rescue",
        "model_id", "model_tag", "reasoning", "temperature", "backend",
        "finish_reason", "prompt_tokens", "completion_tokens",
        "reasoning_tokens", "requested_max_tokens", "retries",
        "retry_errors", "response_model", "provider", "system_fingerprint",
        "requested_reasoning", "run_config_hash", "schedule_hash",
        "block_index", "arm_order"]


from datetime import datetime as _dt, timedelta as _td, timezone as _tz

_T0 = _dt(2026, 7, 24, 0, 0, 0, tzinfo=_tz.utc)


def _ts(arm, iid):
    """Timezone-aware UTC timestamp consistent with the real schedule:
    pairs run back-to-back (60 s apart), blocks in schedule order."""
    blk, first = BLOCK[iid]
    is_first = (arm == "gpt55-base") == (first == "base")
    return (_T0 + _td(seconds=blk * 120
                      + (0 if is_first else 60))).isoformat()


def row(arm, d, i, success, error="", finish="stop", comp=2000, req=32000,
        num_moves=None, complexity=None):
    return {
        "timestamp": _ts(arm, f"rubiks-d{d:02d}-i{i:03d}"),
        "benchmark": "rubiks",
        "test_id": i, "difficulty": d, "success": str(success),
        "num_moves": num_moves or "", "tokens_used": 3000,
        "time_seconds": 10, "error": error,
        "instance_id": f"rubiks-d{d:02d}-i{i:03d}", "size": "3x3x3",
        "complexity": d if complexity is None else complexity,
        "similarity": "", "parse_mode": "strict", "lenient_rescue": "",
        "model_id": "openai/gpt-5.5-20260423", "model_tag": arm, "reasoning": "",
        "temperature": "unset", "backend": "openrouter",
        "finish_reason": finish, "prompt_tokens": 1000,
        "completion_tokens": comp, "reasoning_tokens": "",
        "requested_max_tokens": req, "retries": 0, "retry_errors": "",
        "response_model": "openai/gpt-5.5", "provider": "OpenAI",
        "system_fingerprint": "",
        "requested_reasoning": ('{"effort": "none"}' if arm == "gpt55-base"
                                else '{"effort": "medium"}'),
        "run_config_hash": RUN_CONFIG_HASH, "schedule_hash": SCHEDULE_SHA,
        "block_index": BLOCK[f"rubiks-d{d:02d}-i{i:03d}"][0],
        "arm_order": f'{BLOCK[f"rubiks-d{d:02d}-i{i:03d}"][1]}-first',
    }


def make_cell(runs_dir, arm, d, successes, rows_override=None, n=50):
    rows = []
    for i in range(1, n + 1):
        if rows_override and i in rows_override:
            rows.append(rows_override[i](arm, d, i))
        elif i <= successes:
            rows.append(row(arm, d, i, True, num_moves=d))
        else:
            rows.append(row(arm, d, i, False, error="Cube not solved"))
    path = os.path.join(runs_dir,
                        f"{arm}__rubiks__dstar{d}__batch_metrics.csv")
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f" — {detail}" if detail else ""))
    if not cond:
        sys.exit(1)


def run_analysis(runs_dir, out_dir, *extra):
    return subprocess.run(
        [sys.executable, os.path.join(REPO, "scripts", "analyse_dstar.py"),
         "--runs-dir", runs_dir, "--out", out_dir, *extra],
        capture_output=True, text=True)


# ---- frozen run configuration (gate binding, plan v1.5) -------------------
import hashlib as _hl, json as _json

MANIFEST_PATH = os.path.join(REPO, "instances",
                             "rubiks_dstar_manifest_v1.json")
SCHEDULE_PATH = os.path.join(REPO, "instances", "rubiks_schedule_v1.json")


def _sha(path):
    h = _hl.sha256()
    with open(path, "rb") as fh:
        for c in iter(lambda: fh.read(1 << 16), b""):
            h.update(c)
    return h.hexdigest()


MANIFEST_SHA = _sha(MANIFEST_PATH)
SCHEDULE_SHA = _sha(SCHEDULE_PATH)

# schedule lookup for row-level execution provenance
_SCHED = _json.load(open(SCHEDULE_PATH))
BLOCK = {b["instance_id"]: (b["block"], b["first_arm"])
         for b in _SCHED["blocks"]}

# preflight artifact FIRST (its sha is bound INSIDE the run config)
PREFLIGHT = {
    "timestamp_utc": "2026-07-23T09:00:00+00:00",  # before all trials
    "model": "openai/gpt-5.5-20260423",
    "temperature": None,
    "provider_order": ["OpenAI"],
    "max_tokens": 32000,
    "base_effort": "none",
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
_pf_path = os.path.join(RUNS, "preflight.json")
_json.dump(PREFLIGHT, open(_pf_path, "w"))
PF_SHA = _sha(_pf_path)

RUN_CONFIG = {
    "model": "openai/gpt-5.5-20260423",
    "preflight_sha256": PF_SHA,
    "provider_order": ["OpenAI"],
    "require_parameters": True,
    "allow_fallbacks": False,
    "base_effort": "none",
    "reason_effort": "medium",
    "temperature": None,
    "max_tokens": 32000,
    "trial_timeout": 600,
    "manifest_sha256": MANIFEST_SHA,
    "schedule_sha256": SCHEDULE_SHA,
}
RUN_CONFIG_HASH = _hl.sha256(
    _json.dumps(RUN_CONFIG, sort_keys=True).encode()).hexdigest()

# ---- ground truth ---------------------------------------------------------
# reason solves 1..k_r, base solves 1..k_b (nested), so only_base = 0 and
# only_reason = k_r - k_b per depth. Full completeness now also requires
# the descriptive cells d* = 1 (18 instances), 9 and 10.
CELLS = [(2, 45, 48), (3, 10, 40), (4, 5, 30), (5, 3, 12), (6, 1, 5),
         (7, 0, 2), (8, 0, 0), (9, 0, 1), (10, 0, 0)]
make_cell(RUNS, "gpt55-base", 1, 12, n=18)
make_cell(RUNS, "gpt55-reason", 1, 16, n=18)
for d, k_base, k_reason in CELLS:
    make_cell(RUNS, "gpt55-base", d, k_base)
    if d == 5:
        continue  # written below with one only-base discordant pair
    make_cell(RUNS, "gpt55-reason", d, k_reason)

# d*=5 reason arm: solves instances 2..13 (skips 1) -> only_base = 1 at
# instance 1, only_reason = 10 (instances 4..13). This keeps the
# conditional H3 model identifiable (not all discordants favour reason).
ov5 = {1: lambda a, d, i: row(a, d, i, False, error="Cube not solved")}
make_cell(RUNS, "gpt55-reason", 5, 13, rows_override=ov5)

# Failure-taxonomy specials inside the d*=8 base cell (all valid failures,
# cell stays at n=50):
overrides = {
    5: lambda a, d, i: row(a, d, i, False,
                           error="Empty model content (finish_reason=length, "
                                 "reasoning_field=present, tokens_used=33000)",
                           finish="length", comp=32000),
    6: lambda a, d, i: row(a, d, i, False, error="Cube not solved",
                           finish="stop", comp=32000, req=32000),
    7: lambda a, d, i: row(a, d, i, False, error="Trial timeout (600s)",
                           finish="", complexity=""),  # loader falls back
    8: lambda a, d, i: row(a, d, i, False,
                           error="No legal HTM move sequence on the final "
                                 "line"),
}
make_cell(RUNS, "gpt55-base", 8, 0, rows_override=overrides)

# run_config.json (preflight sha is INSIDE the hashed config)
def write_run_artifacts(runs_dir):
    pfp = os.path.join(runs_dir, "preflight.json")
    _json.dump(PREFLIGHT, open(pfp, "w"))
    _json.dump({"run_config": RUN_CONFIG,
                "run_config_hash": RUN_CONFIG_HASH},
               open(os.path.join(runs_dir, "run_config.json"), "w"))


write_run_artifacts(RUNS)

# ---- clean run: gate must pass (incl. manifest validation) ---------------
proc = run_analysis(RUNS, OUT, "--manifest", MANIFEST_PATH,
                    "--schedule", SCHEDULE_PATH)
print(proc.stdout)
if proc.returncode != 0:
    print(proc.stderr)
    sys.exit(1)
check("gate passed on complete data", "Confirmatory gate: passed" in proc.stdout)

# primary pooled test: B = 1, C = 3+30+25+10+4+2+0 = 74
with open(os.path.join(OUT, "h1_global.csv")) as fh:
    g = list(csv.DictReader(fh))[0]
check("primary n_pairs = 350", g["n_pairs"] == "350")
check("primary discordants 1:74",
      g["only_base"] == "1" and g["only_reason"] == "74")
check("primary diff = 73/350",
      math.isclose(float(g["diff_reason_minus_base"]), 73 / 350,
                   abs_tol=5e-5))
check("primary p astronomically small",
      float(g["p_mcnemar_exact"]) < 1e-15)
check("primary CI excludes 0", float(g["diff_ci_lo"]) > 0)
check("primary marked confirmatory", g["confirmatory"] == "True")
check("TOST reported and (correctly) NOT equivalent",
      g["tost_equivalent_pm15pp"] == "False"
      and float(g["tost_ci90_lo"]) > 0)

with open(os.path.join(OUT, "h1_mcnemar.csv")) as fh:
    h1 = {int(r["d_star"]): r for r in csv.DictReader(fh)}
p_expected = min(1.0, 2 * 0.5 ** 30)
check("secondary d*=3 exact p = 2*2^-30",
      math.isclose(float(h1[3]["p_mcnemar_exact"]), p_expected,
                   rel_tol=1e-3))
check("secondary d*=3 survives Holm",
      h1[3]["significant_after_holm"] == "True")
check("secondary d*=8 p = 1.0", float(h1[8]["p_mcnemar_exact"]) == 1.0)

with open(os.path.join(OUT, "pass1_dstar.csv")) as fh:
    p1 = {(r["arm"], int(r["d_star"])): r for r in csv.DictReader(fh)}
check("base d*=3 pass1 = 0.2",
      math.isclose(float(p1[("gpt55-base", 3)]["pass1"]), 0.2))
r8b = p1[("gpt55-base", 8)]
check("d*=8 taxonomy: 2 compute-bound",
      r8b["compute_bound_truncation"] == "2")
check("d*=8 taxonomy: 1 timeout (kept via difficulty fallback)",
      r8b["timeout"] == "1")
check("d*=8 taxonomy: 1 format (new parser message)",
      r8b["format_error"] == "1")
check("d*=8 cell complete (n=50)", r8b["n"] == "50")

with open(os.path.join(OUT, "h2_trend.csv")) as fh:
    h2 = {r["arm"]: r for r in csv.DictReader(fh)}
check("trend beta > 0 for both arms",
      float(h2["gpt55-base"]["beta_hat"]) > 0
      and float(h2["gpt55-reason"]["beta_hat"]) > 0)
check("linear-form check present and in [0,1]",
      0 <= float(h2["gpt55-base"]["p_linear_form_adequate"]) <= 1)
check("trend marked descriptive",
      "descriptive" in h2["gpt55-base"]["note"])

with open(os.path.join(OUT, "h3_interaction.csv")) as fh:
    h3 = list(csv.DictReader(fh))[0]
check("h3 uses conditional logistic on discordants",
      "conditional" in h3["model"] and "chi2(1)" in h3["inference"])
check("h3 counts the 75 discordant pairs",
      h3["n_discordant_pairs"] == "75")
check("h3 gamma > 0 (reason wins discordants)",
      float(h3["gamma_hat"]) > 0)
check("h3 delta p in [0,1]",
      0 <= float(h3["p_delta_chi2_1df"]) <= 1)
check("h3 hierarchy rule recorded", "PRIMARY" in h3["hierarchy"])

with open(os.path.join(OUT, "manifest.json")) as fh:
    man = json.load(fh)
check("manifest gate status recorded",
      man["confirmatory_gate"]["status"] == "passed")
check("instance identity validated against pinned manifest",
      man["confirmatory_gate"]["manifest_validated"] is True)
check("manifest primary test described",
      "pooled" in man["tests"]["primary"])
check("manifest inputs hashed",
      len(man["inputs"]) == 20
      and all(len(i["sha256"]) == 64 for i in man["inputs"]))

# ---- gate failure: short cell + api_error --------------------------------
RUNS2 = os.path.join(WORK, "runs_short")
shutil.copytree(RUNS, RUNS2)
overrides_api = dict(overrides)
overrides_api[9] = lambda a, d, i: row(
    a, d, i, False, error="API error: APIConnectionError: boom", finish="")
make_cell(RUNS2, "gpt55-base", 8, 0, rows_override=overrides_api)

write_run_artifacts(RUNS2)
proc_fail = run_analysis(RUNS2, os.path.join(WORK, "out_fail"),
                         "--manifest", MANIFEST_PATH,
                         "--schedule", SCHEDULE_PATH)
check("gate hard-stops on short cell (api_error excluded -> n=49)",
      proc_fail.returncode != 0
      and "Confirmatory analyses refused" in
          (proc_fail.stdout + proc_fail.stderr))

proc_force = run_analysis(RUNS2, os.path.join(WORK, "out_force"),
                          "--manifest", MANIFEST_PATH,
                          "--schedule", SCHEDULE_PATH, "--force")
check("--force runs anyway", proc_force.returncode == 0)
check("--force marks outputs non-confirmatory",
      "NON-confirmatory" in proc_force.stdout)
with open(os.path.join(WORK, "out_force", "manifest.json")) as fh:
    man_f = json.load(fh)
check("forced gate status recorded",
      man_f["confirmatory_gate"]["status"] == "FORCED-NON-CONFIRMATORY")
check("api_error exclusion recorded",
      len(man_f["excluded_trials_api_error"]) == 1)
with open(os.path.join(WORK, "out_force", "h1_global.csv")) as fh:
    gf = list(csv.DictReader(fh))[0]
check("forced primary marked non-confirmatory",
      gf["confirmatory"] == "False")

# ---- single-row empty provenance field -> e2e gate failure -----------------
RUNS4 = os.path.join(WORK, "runs_rowcorrupt")
shutil.copytree(RUNS, RUNS4)
_pth = os.path.join(RUNS4, "gpt55-base__rubiks__dstar4__batch_metrics.csv")
_lines = open(_pth).read().split("\n")
_hdr = _lines[0].split(",")
_cells = _lines[10].split(",")
_cells[_hdr.index("provider")] = ""          # exactly ONE empty field
_lines[10] = ",".join(_cells)
open(_pth, "w").write("\n".join(_lines))
proc_row = run_analysis(RUNS4, os.path.join(WORK, "out_rowcorrupt"),
                        "--manifest", MANIFEST_PATH,
                        "--schedule", SCHEDULE_PATH)
check("single empty field in ONE row fails the e2e gate",
      proc_row.returncode != 0
      and "empty" in (proc_row.stdout + proc_row.stderr).lower())

# ---- header validation -----------------------------------------------------
BADDIR = os.path.join(WORK, "bad_runs")
os.makedirs(BADDIR, exist_ok=True)
with open(os.path.join(BADDIR, "broken__rubiks__dstar3__batch_metrics.csv"),
          "w") as fh:
    fh.write("just,some,data,without,proper,header\n1,2,3,4,5,6\n")
proc_bad = run_analysis(BADDIR, os.path.join(WORK, "bad_out"))
check("header-less CSV rejected loudly",
      proc_bad.returncode != 0
      and "missing required columns" in (proc_bad.stdout + proc_bad.stderr))

print("\nAll analysis-pipeline checks passed.")
