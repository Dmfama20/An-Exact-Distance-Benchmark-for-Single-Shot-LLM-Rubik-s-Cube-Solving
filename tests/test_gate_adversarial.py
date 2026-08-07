#!/usr/bin/env python3
"""Adversarial gate battery (design condition: the CI must PROVE that
non-conforming data are rejected, not only that conforming data pass).

Each case starts from a fully valid synthetic audit/row state and
corrupts exactly ONE dimension; the shared gate must raise at least one
problem mentioning that dimension. Single-row corruption is included
(a lone empty field among many valid rows).

Run:  python tests/test_gate_adversarial.py
"""

import copy
import hashlib
import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import gate_lib  # noqa: E402

ARMS = ("gpt55-base", "gpt55-reason")
WORK = tempfile.mkdtemp(prefix="gate_adv_")
MODEL = gate_lib.EXPECTED_MODEL


def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


# ---- build a minimal fully-valid world -------------------------------------
SCHEDULE = {
    "manifest_sha256": "x",
    "blocks": [
        {"block": 1, "d_star": 3, "instance_id": "i-001",
         "first_arm": "base"},
        {"block": 2, "d_star": 4, "instance_id": "i-002",
         "first_arm": "reason"},
    ],
}
sched_path = os.path.join(WORK, "schedule.json")
json.dump(SCHEDULE, open(sched_path, "w"))
SCHED_SHA = gate_lib.sha256_file(sched_path)

PREFLIGHT = {
    "timestamp_utc": "2026-07-23T09:00:00+00:00", "model": MODEL,
    "temperature": None, "provider_order": ["openai"],
    "max_tokens": 32000, "base_effort": "none",
    "reason_effort": "medium",
    "observed": {
        "base": {"reasoning_tokens": [0, 0, 0],
                 "snapshots": ["openai/gpt-5.5"],
                 "providers": ["openai"]},
        "reason": {"reasoning_tokens": [3000, 2900, 3100],
                   "snapshots": ["openai/gpt-5.5"],
                   "providers": ["openai"]},
    },
    "verdict": "ok",
}


def build_world(workdir, preflight=None, cfg_mut=None):
    os.makedirs(workdir, exist_ok=True)
    pf = preflight if preflight is not None else copy.deepcopy(PREFLIGHT)
    pfp = os.path.join(workdir, "preflight.json")
    json.dump(pf, open(pfp, "w"))
    cfg = {
        "model": MODEL, "preflight_sha256": gate_lib.sha256_file(pfp),
        "provider_order": ["openai"],
        "require_parameters": True, "allow_fallbacks": False,
        "base_effort": "none", "reason_effort": "medium",
        "temperature": None, "max_tokens": 32000, "trial_timeout": 600,
        "manifest_sha256": "m", "schedule_sha256": SCHED_SHA,
    }
    if cfg_mut:
        cfg_mut(cfg)
    rch = gate_lib.config_hash(cfg)
    json.dump({"run_config": cfg, "run_config_hash": rch},
              open(os.path.join(workdir, "run_config.json"), "w"))

    rows = []
    for b in SCHEDULE["blocks"]:
        for arm_idx, tag in enumerate(ARMS):
            first = ("gpt55-base" if b["first_arm"] == "base"
                     else "gpt55-reason")
            ts = ("2026-07-24T10:00:00" if tag == first
                  else "2026-07-24T10:05:00")
            rows.append({
                "timestamp": f"{ts[:14]}{b['block']:02d}:00",
                "model_id": MODEL, "model_tag": tag,
                "instance_id": b["instance_id"],
                "requested_reasoning": gate_lib.EXPECTED_REASONING[tag],
                "response_model": "openai/gpt-5.5",
                "provider": "openai", "temperature": "unset",
                "requested_max_tokens": "32000",
                "run_config_hash": rch, "schedule_hash": SCHED_SHA,
                "block_index": str(b["block"]),
                "arm_order": f"{b['first_arm']}-first",
                "_success": True,
            })
    # fix timestamps: first arm strictly before second, blocks in order,
    # timezone-aware UTC (naive timestamps are rejected fail-closed)
    t = {("i-001", "gpt55-base"): "2026-07-24T10:00:00+00:00",
         ("i-001", "gpt55-reason"): "2026-07-24T10:03:00+00:00",
         ("i-002", "gpt55-reason"): "2026-07-24T10:10:00+00:00",
         ("i-002", "gpt55-base"): "2026-07-24T10:13:00+00:00"}
    for r in rows:
        r["timestamp"] = t[(r["instance_id"], r["model_tag"])]
    return rows, cfg, rch


def audit_from(rows, workdir):
    def _live(r):
        return not (r.get("error") or "").startswith(
            gate_lib.TIMEOUT_ERROR_PREFIX)
    a = {
        "temperatures": {r["temperature"] for r in rows},
        "max_tokens": {r["requested_max_tokens"] for r in rows
                       if _live(r)},
        "model_ids": {r["model_id"] for r in rows},
        "reasoning_by_arm": {
            arm: {r["requested_reasoning"] for r in rows
                  if r["model_tag"] == arm and _live(r)}
            for arm in ARMS},
        "echoes": {r["response_model"] for r in rows if _live(r)},
        "providers": {gate_lib.norm_provider(r["provider"])
                      for r in rows if _live(r)},
        "run_config_hashes": {r["run_config_hash"] for r in rows},
        "schedule_hashes": {r["schedule_hash"] for r in rows},
        "min_trial_timestamp": min(r["timestamp"] for r in rows),
        "row_problems": [],
        "harness_error_files": [],
        "preflight_artifact": os.path.join(workdir, "preflight.json"),
        "run_config_file": os.path.join(workdir, "run_config.json"),
    }
    # row-level fail-closed check, same as gate_lib.load_rows
    for r in rows:
        is_to = (r.get("error") or "").startswith(
            gate_lib.TIMEOUT_ERROR_PREFIX)
        empty = [f for f in gate_lib.NON_EMPTY_FIELDS
                 if not (is_to and f in gate_lib.TIMEOUT_EXEMPT_FIELDS)
                 and (r.get(f) or "").strip() == ""]
        if empty:
            a["row_problems"].append(
                f"{r['model_tag']}/{r['instance_id']}: empty provenance "
                f"field(s) {empty}")
    return a


def gate(rows, workdir):
    return gate_lib.integrity_problems(
        workdir, audit_from(rows, workdir), ARMS, rows,
        manifest_path=None, schedule_path=sched_path)[0]


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f" — {detail}" if detail else ""))
    if not cond:
        sys.exit(1)


def expect_reject(name, rows, workdir, needle):
    probs = gate(rows, workdir)
    hit = [p for p in probs if needle.lower() in p.lower()]
    check(f"REJECT: {name}", bool(hit),
          f"no problem matching {needle!r}; got {probs[:3]}")


# ---- baseline must pass (except the intentionally absent manifest) ---------
W0 = os.path.join(WORK, "ok")
rows0, cfg0, rch0 = build_world(W0)
probs0 = gate(rows0, W0)
non_manifest = [p for p in probs0 if "--manifest" not in p]
check("valid world raises no problems (besides absent manifest)",
      non_manifest == [], str(non_manifest[:3]))

# ---- single-row empty-field cases -------------------------------------------
for field in ("provider", "response_model", "run_config_hash",
              "schedule_hash", "requested_reasoning", "timestamp",
              "block_index", "arm_order"):
    rows = copy.deepcopy(rows0)
    rows[2][field] = ""
    expect_reject(f"single row with empty {field}", rows, W0,
                  "empty" if field in gate_lib.NON_EMPTY_FIELDS
                  else field)

# ---- wrong values ------------------------------------------------------------
rows = copy.deepcopy(rows0)
for r in rows:
    r["model_id"] = "WRONG-UNPINNED-MODEL"
expect_reject("wrong model_id everywhere", rows, W0, "frozen snapshot")

rows = copy.deepcopy(rows0)
for r in rows:
    r["provider"] = "someothercloud"
expect_reject("wrong provider vs pin", rows, W0, "pinned")

rows = copy.deepcopy(rows0)
for r in rows:
    r["response_model"] = "some/other-model"
expect_reject("echo outside allowlist", rows, W0, "identity rule")

rows = copy.deepcopy(rows0)
for r in rows:
    r["run_config_hash"] = "f00" * 21 + "f"
expect_reject("foreign run_config_hash", rows, W0, "run_config")

rows = copy.deepcopy(rows0)
for r in rows:
    r["schedule_hash"] = "ba4" * 21 + "d"
expect_reject("schedule hash mismatch vs file", rows, W0,
              "schedule_hash of the trial rows")

# ---- schedule EXECUTION violations ------------------------------------------
rows = copy.deepcopy(rows0)
rows[0]["block_index"] = "99"
expect_reject("wrong block_index", rows, W0, "block_index")

rows = copy.deepcopy(rows0)
rows[0]["arm_order"] = "reason-first"
expect_reject("wrong arm_order", rows, W0, "arm_order")

rows = copy.deepcopy(rows0)
for r in rows:
    if r["instance_id"] == "i-001" and r["model_tag"] == "gpt55-base":
        r["timestamp"] = "2026-07-24T10:09:00+00:00"  # after second arm
expect_reject("scheduled first arm ran after second", rows, W0,
              "AFTER the second arm")

rows = copy.deepcopy(rows0)
for r in rows:
    if r["instance_id"] == "i-002" and r["model_tag"] == "gpt55-base":
        r["timestamp"] = "2026-07-24T13:00:00+00:00"   # ~3h pair gap
expect_reject("within-pair gap exceeds frozen maximum", rows, W0,
              "within-pair gap")

rows = copy.deepcopy(rows0)
for r in rows:
    if r["instance_id"] == "i-001" and r["model_tag"] == "gpt55-base":
        r["timestamp"] = "2026-07-24T10:00:00"   # NAIVE (no UTC offset)
expect_reject("naive (offset-less) trial timestamp", rows, W0,
              "unparseable")

rows = copy.deepcopy(rows0)
rows[0]["block_index"] = "1.9"
expect_reject("non-canonical block_index (1.9)", rows, W0,
              "canonical integer")

# ---- frozen run-configuration enforcement -----------------------------------
W6 = os.path.join(WORK, "cfg_fallbacks")
rows6, _, _ = build_world(
    W6, cfg_mut=lambda c: c.update(allow_fallbacks=True))
expect_reject("run_config with allow_fallbacks=true", rows6, W6,
              "allow_fallbacks")

W7 = os.path.join(WORK, "cfg_reqparams")
rows7, _, _ = build_world(
    W7, cfg_mut=lambda c: c.update(require_parameters=False))
expect_reject("run_config with require_parameters=false", rows7, W7,
              "require_parameters")

W8 = os.path.join(WORK, "cfg_timeout")
rows8, _, _ = build_world(
    W8, cfg_mut=lambda c: c.update(trial_timeout=9999))
expect_reject("run_config with foreign trial_timeout", rows8, W8,
              "trial_timeout")

# preflight AND run_config agree on foreign efforts while the trial rows
# carry the frozen ones (cross-manipulation case)
pf_eff = copy.deepcopy(PREFLIGHT)
pf_eff["base_effort"] = "low"
pf_eff["reason_effort"] = "high"
W9 = os.path.join(WORK, "cfg_efforts")
rows9, _, _ = build_world(
    W9, preflight=pf_eff,
    cfg_mut=lambda c: c.update(base_effort="low", reason_effort="high"))
expect_reject("preflight+run_config efforts differ from trials/frozen",
              rows9, W9, "base_effort")

# ---- preflight violations -----------------------------------------------------
pf = copy.deepcopy(PREFLIGHT)
del pf["observed"]
W1 = os.path.join(WORK, "pf_noobs")
rows1, _, _ = build_world(W1, preflight=pf)
expect_reject("preflight without observed evidence", rows1, W1,
              "observed")

pf = copy.deepcopy(PREFLIGHT)
del pf["timestamp_utc"]
W2 = os.path.join(WORK, "pf_nots")
rows2, _, _ = build_world(W2, preflight=pf)
expect_reject("preflight without timestamp", rows2, W2, "timestamp_utc")

pf = copy.deepcopy(PREFLIGHT)
pf["timestamp_utc"] = "2026-07-25T09:00:00+00:00"   # after trials
W3 = os.path.join(WORK, "pf_late")
rows3, _, _ = build_world(W3, preflight=pf)
expect_reject("preflight AFTER first trial", rows3, W3, "AFTER")

pf = copy.deepcopy(PREFLIGHT)
pf["timestamp_utc"] = "2026-07-24T10:00:00+00:00"  # EQUAL to 1st trial
W3b = os.path.join(WORK, "pf_equal")
rows3b, _, _ = build_world(W3b, preflight=pf)
expect_reject("preflight timestamp EQUAL to first trial (strictness)",
              rows3b, W3b, "strictly before")

pf = copy.deepcopy(PREFLIGHT)
pf["timestamp_utc"] = "2026-07-23T09:00:00"   # naive: offset stripped
W3c = os.path.join(WORK, "pf_naive")
rows3c, _, _ = build_world(W3c, preflight=pf)
expect_reject("naive preflight timestamp (timezone required)",
              rows3c, W3c, "timezone-aware")

pf = copy.deepcopy(PREFLIGHT)
pf["observed"]["reason"]["reasoning_tokens"] = [10, 12, 9]  # no contrast
W4 = os.path.join(WORK, "pf_nocontrast")
rows4, _, _ = build_world(W4, preflight=pf)
expect_reject("preflight token contrast fails on recomputation",
              rows4, W4, "token-contrast")

# tampered preflight AFTER run start: replace artifact, keep rows
W5 = os.path.join(WORK, "pf_tamper")
rows5, _, _ = build_world(W5)
json.dump({**PREFLIGHT, "verdict": "ok", "model": MODEL,
           "note": "tampered"},
          open(os.path.join(W5, "preflight.json"), "w"))
expect_reject("preflight replaced after run start (sha binding)",
              rows5, W5, "sha256 does not match")

# ---- timeout rows (deviation item 56) ---------------------------------------
rows = copy.deepcopy(rows0)
rows[2]["error"] = "Trial timeout (600s, global deadline incl. transport retries)"
rows[2]["_success"] = False
for fld in ("requested_reasoning", "response_model", "provider",
            "requested_max_tokens"):
    rows[2][fld] = ""
probs = gate(rows, W0)
non_manifest = [p for p in probs if "--manifest" not in p]
check("ACCEPT: timeout row with empty echo/audit fields (item 56)",
      non_manifest == [], str(non_manifest[:3]))

rows = copy.deepcopy(rows0)
rows[2]["error"] = "Trial timeout (600s, global deadline incl. transport retries)"
rows[2]["_success"] = False
rows[2]["run_config_hash"] = ""
expect_reject("timeout row still needs its config binding", rows, W0,
              "empty")

print("\nAll adversarial gate checks passed.")
