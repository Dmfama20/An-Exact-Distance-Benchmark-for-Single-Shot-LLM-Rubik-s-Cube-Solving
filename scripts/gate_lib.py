#!/usr/bin/env python3
"""
Shared confirmatory-gate library (plan v1.5). Both analyses (Module A: analyse_dstar.py, Module B:
analyse_nominal.py) MUST use these functions so identical rules apply.

Fail-closed principle (frozen): EVERY scored trial row must carry a
complete, non-empty provenance record; a single empty field in a single
row is a gate failure — not only globally contradictory values.

Frozen identity rule: every request uses the frozen snapshot slug;
response_model echoes are identical to it iff ONE single non-empty echo
string occurs across all trials of BOTH arms and is in ALLOWED_ECHOES.
Provider names are canonically normalised (case-insensitive) before
comparison.

Preflight binding: the sha256 of preflight.json is INSIDE run_config
(and therefore inside the run_config_hash carried by every trial row);
the gate recomputes it from the presented artifact and additionally
re-validates the preflight's OBSERVED evidence (echoes, providers,
sample counts, and the frozen reasoning-token contrast rule) — the
"verdict" field is never trusted on its own.

Schedule execution: the gate does not only hash the schedule file; it
verifies that the trial rows were EXECUTED according to it
(block_index, arm_order, first-arm-first timestamps, bounded within-pair
gap, monotone block order).

Frozen-configuration enforcement: every field of
EXPECTED_RUN_CONFIG is compared exactly against run_config.json, and
the run_config efforts are compared directly against the trial rows —
hash consistency alone is never sufficient.

Timestamps: all comparisons parse full ISO-8601 and
REQUIRE timezone-aware values (naive timestamps fail closed);
microsecond precision is preserved; the preflight must be STRICTLY
before the first trial.
"""

import csv
import glob
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

EXPECTED_MODEL = "openai/gpt-5.5-20260423"
ALLOWED_ECHOES = {EXPECTED_MODEL, "openai/gpt-5.5"}

EXPECTED_REASONING = {
    "gpt55-base": '{"effort": "none"}',
    "gpt55-reason": '{"effort": "medium"}',
}

# Frozen run configuration (plan §1/§9). The gate compares EVERY one of
# these fields exactly against run_config.json — a hash-consistent
# dataset collected with, e.g., allow_fallbacks=true must NOT pass.
# provider_order is deliberately absent:
# the single pinned provider is validated separately against the rows.
EXPECTED_RUN_CONFIG = {
    "model": EXPECTED_MODEL,
    "require_parameters": True,
    "allow_fallbacks": False,
    "base_effort": "none",
    "reason_effort": "medium",
    # UNSET (plan v1.7, item 54): GPT-5.5 endpoints declare no
    # sampling-parameter support; the request omits temperature and
    # every trial row records the canonical value "unset".
    "temperature": None,
    "max_tokens": 32000,
    "trial_timeout": 600,
}

# Canonical row representation of an unset run_config value.
def _row_repr(value):
    return "unset" if value is None else str(value)

# Frozen preflight evidence rules (mirror scripts/preflight_reasoning_check)
PREFLIGHT_MIN_SAMPLES = 3
PREFLIGHT_CONTRAST_FLOOR = 64      # reason_min > max(floor, 2 * base_max)

# Frozen pairing rule: both arms of a block must run within this window.
MAX_PAIR_GAP_SECONDS = 3600

REQUIRED_COLUMNS = {
    "timestamp", "success", "model_id", "model_tag", "difficulty",
    "complexity", "instance_id", "error", "finish_reason",
    "completion_tokens", "requested_max_tokens", "requested_reasoning",
    "response_model", "provider", "temperature",
    "run_config_hash", "schedule_hash", "block_index", "arm_order",
}

# Row-level fail-closed: these fields must be non-empty in EVERY scored row.
NON_EMPTY_FIELDS = (
    "timestamp", "model_id", "requested_reasoning", "response_model",
    "provider", "temperature", "requested_max_tokens",
    "run_config_hash", "schedule_hash", "block_index", "arm_order",
)

# Deviation item 56 (documented, outcome-independent): a global-deadline
# timeout is a VALID scored failure whose call never returned — the
# response-side echoes (response_model, provider) cannot exist, and the
# v1.7.1 runner did not backfill the request-side audit fields either.
# EXACTLY these four fields may be empty on rows whose error is the
# runner's timeout marker; every identity-critical field (timestamp,
# model_id, temperature, run_config_hash, schedule_hash, block_index,
# arm_order) remains required, and the row stays bound to the exact
# configuration via run_config_hash. Timeout rows are excluded from the
# exactly-one audit sets of the four fields (they would inject "").
TIMEOUT_ERROR_PREFIX = "Trial timeout"
TIMEOUT_EXEMPT_FIELDS = frozenset(
    ("requested_reasoning", "response_model", "provider",
     "requested_max_tokens"))

TRANSPORT_PREFIXES = ("API error", "Max retries exceeded")


def norm_provider(name):
    return (name or "").strip().lower()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def config_hash(cfg: dict) -> str:
    return hashlib.sha256(
        json.dumps(cfg, sort_keys=True).encode()).hexdigest()


def load_rows(runs_dir, arms):
    """Load and audit all trial rows.

    Hard-exits on: missing headers, duplicate (arm, instance_id) trials,
    rows without instance identity. Row-level provenance gaps are
    collected into audit["row_problems"] (gate failures, --force-able).
    Transport (api_error) rows are collected separately, never scored.
    """
    files = sorted(glob.glob(os.path.join(runs_dir, "*_metrics.csv")))
    if not files:
        sys.exit(f"No *_metrics.csv found in {runs_dir}")
    rows, api_errors, provenance = [], [], []
    seen = set()
    audit = {
        "temperatures": set(),
        "max_tokens": set(),
        "model_ids": set(),
        "reasoning_by_arm": defaultdict(set),
        "echoes": set(),
        "providers": set(),
        "run_config_hashes": set(),
        "schedule_hashes": set(),
        "min_trial_timestamp": None,
        "row_problems": [],
        "harness_error_files": sorted(
            glob.glob(os.path.join(runs_dir, "*.harness_errors.csv"))),
        "preflight_artifact": os.path.join(runs_dir, "preflight.json"),
        "run_config_file": os.path.join(runs_dir, "run_config.json"),
    }
    for path in files:
        scored = excluded = 0
        with open(path) as fh:
            reader = csv.DictReader(fh)
            missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
            if missing:
                sys.exit(f"{path}: missing required columns "
                         f"{sorted(missing)} — header-less or "
                         f"pre-freeze file?")
            for row in reader:
                arm = row.get("model_tag") or ""
                if arm not in arms:
                    continue
                iid = row.get("instance_id") or ""
                if not iid:
                    sys.exit(f"{path}: row without instance_id "
                             f"(test_id={row.get('test_id')})")
                row["_success"] = (row.get("success") == "True")
                err = row.get("error") or ""
                if not row["_success"] and err.startswith(
                        TRANSPORT_PREFIXES):
                    api_errors.append((arm, iid, err))
                    excluded += 1
                    continue
                if (arm, iid) in seen:
                    sys.exit(f"{path}: duplicate trial {arm}/{iid}")
                seen.add((arm, iid))
                # ---- row-level fail-closed provenance ----
                # (timeout rows: see TIMEOUT_EXEMPT_FIELDS, item 56)
                is_timeout = err.startswith(TIMEOUT_ERROR_PREFIX)
                required = [f for f in NON_EMPTY_FIELDS
                            if not (is_timeout
                                    and f in TIMEOUT_EXEMPT_FIELDS)]
                empty = [f for f in required
                         if (row.get(f) or "").strip() == ""]
                if not empty and _parse_ts(row.get("timestamp")) is None:
                    empty = ["timestamp (not a timezone-aware ISO "
                             "timestamp)"]
                if empty and len(audit["row_problems"]) < 20:
                    audit["row_problems"].append(
                        f"{arm}/{iid}: empty provenance field(s) "
                        f"{empty}")
                elif empty:
                    audit["row_problems"].append("... (more rows)")
                rows.append(row)
                scored += 1
                audit["temperatures"].add(row.get("temperature") or "")
                audit["model_ids"].add(row.get("model_id") or "")
                if not is_timeout:
                    audit["max_tokens"].add(
                        row.get("requested_max_tokens") or "")
                    audit["reasoning_by_arm"][arm].add(
                        row.get("requested_reasoning") or "")
                    audit["echoes"].add(row.get("response_model") or "")
                    audit["providers"].add(
                        norm_provider(row.get("provider")))
                else:
                    audit.setdefault("timeout_row_ids", []).append(
                        f"{arm}/{iid}")
                audit["run_config_hashes"].add(
                    row.get("run_config_hash") or "")
                audit["schedule_hashes"].add(row.get("schedule_hash") or "")
                dt = _parse_ts(row.get("timestamp"))
                if dt is not None and (
                        audit["min_trial_timestamp"] is None
                        or dt < _parse_ts(audit["min_trial_timestamp"])):
                    audit["min_trial_timestamp"] = row["timestamp"].strip()
        provenance.append({"file": path, "sha256": sha256_file(path),
                           "rows_scored": scored,
                           "rows_excluded_api_error": excluded})
    return rows, audit, api_errors, provenance


def _exactly_one(values: set, what: str, problems: list):
    """Fail-closed: any empty value present, zero values, or >1 distinct
    values is a failure."""
    if "" in values:
        problems.append(f"{what}: at least one row has an EMPTY value "
                        f"(fail-closed rule)")
    vals = values - {""}
    if len(vals) == 0:
        problems.append(f"{what}: no non-empty value recorded — the run "
                        f"is not bound to a configuration")
        return None
    if len(vals) > 1:
        problems.append(f"{what}: mixed values {sorted(vals)}")
        return None
    return next(iter(vals))


def _parse_ts(ts):
    """Full ISO-8601 parse, fail-closed.

    Returns a timezone-AWARE datetime or None. Naive timestamps are
    rejected: truncating the UTC offset silently reinterprets, e.g.,
    2026-07-24T08:30:00-02:00 as 08:30 local instead of 10:30 UTC.
    Microseconds are preserved so calls within the same second remain
    ordered.
    """
    ts = (ts or "").strip()
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        return None
    return dt


def _validate_preflight(pf, cfg, expected_provider, min_trial_ts,
                        problems):
    """Content-level preflight validation — the verdict field is never
    trusted on its own."""
    if pf.get("verdict") != "ok":
        problems.append("preflight verdict is not 'ok'")
    if pf.get("model") != EXPECTED_MODEL:
        problems.append("preflight model differs from the frozen snapshot")
    pf_dt = _parse_ts(pf.get("timestamp_utc"))
    if pf_dt is None:
        problems.append("preflight timestamp_utc missing or not a "
                        "timezone-aware ISO timestamp (required)")
    elif min_trial_ts:
        first_dt = _parse_ts(min_trial_ts)
        if first_dt is None:
            problems.append("first trial timestamp is not a "
                            "timezone-aware ISO timestamp")
        elif not (pf_dt < first_dt):
            problems.append(
                "preflight timestamp is AFTER or equal to the first "
                "trial (frozen rule: strictly before)")
    if cfg:
        if str(pf.get("temperature")) != str(cfg.get("temperature")):
            problems.append("preflight temperature differs from run_config")
        if pf.get("provider_order") != cfg.get("provider_order"):
            problems.append("preflight provider_order differs from "
                            "run_config")
        if (pf.get("base_effort") != cfg.get("base_effort")
                or pf.get("reason_effort") != cfg.get("reason_effort")):
            problems.append("preflight reasoning efforts differ from "
                            "run_config")
        if pf.get("max_tokens") is not None and \
                str(pf.get("max_tokens")) != str(cfg.get("max_tokens")):
            problems.append("preflight max_tokens differs from run_config")
    obs = pf.get("observed")
    if not isinstance(obs, dict) or set(obs) != {"base", "reason"}:
        problems.append("preflight lacks 'observed' evidence for both "
                        "arms — a bare verdict is not acceptable")
        return
    echoes, providers = set(), set()
    tokens = {}
    for arm in ("base", "reason"):
        o = obs.get(arm) or {}
        toks = o.get("reasoning_tokens")
        if not isinstance(toks, list) or \
                len(toks) < PREFLIGHT_MIN_SAMPLES or \
                any(t is None for t in toks):
            problems.append(f"preflight {arm}: fewer than "
                            f"{PREFLIGHT_MIN_SAMPLES} complete "
                            f"reasoning-token samples")
            return
        tokens[arm] = toks
        snaps = [s for s in (o.get("snapshots") or []) if s]
        provs = [norm_provider(p) for p in (o.get("providers") or []) if p]
        if len(snaps) != 1:
            problems.append(f"preflight {arm}: model echo not exactly "
                            f"one non-empty value ({snaps})")
        else:
            echoes.add(snaps[0])
        if len(provs) != 1:
            problems.append(f"preflight {arm}: provider not exactly one "
                            f"non-empty value ({provs})")
        else:
            providers.add(provs[0])
    if len(echoes) == 1:
        echo = next(iter(echoes))
        if echo not in ALLOWED_ECHOES:
            problems.append(f"preflight model echo {echo!r} outside the "
                            f"frozen allowlist")
    elif echoes:
        problems.append(f"preflight arms saw different model echoes "
                        f"{sorted(echoes)}")
    if len(providers) == 1:
        if expected_provider and next(iter(providers)) != expected_provider:
            problems.append("preflight provider differs from the pinned "
                            "provider")
    elif providers:
        problems.append(f"preflight arms saw different providers "
                        f"{sorted(providers)}")
    if tokens:
        base_max = max(tokens["base"])
        reason_min = min(tokens["reason"])
        if not (reason_min > max(PREFLIGHT_CONTRAST_FLOOR, 2 * base_max)):
            problems.append(
                f"preflight token-contrast rule fails on recomputation "
                f"(base max {base_max}, reason min {reason_min}) — the "
                f"recorded verdict is not supported by the evidence")


def schedule_execution_problems(rows, schedule):
    """Verify the rows were EXECUTED according to the schedule:
    block index, arm order, first-arm-first timestamps,
    bounded within-pair gap, monotone block start order."""
    problems = []
    by_iid = {b["instance_id"]: b for b in schedule["blocks"]}
    pairs = defaultdict(dict)   # iid -> {tag: row}
    for r in rows:
        pairs[r["instance_id"]][r["model_tag"]] = r

    def ts_seconds(ts):
        dt = _parse_ts(ts)
        return dt.timestamp() if dt is not None else None

    pair_start = {}
    for iid, arm_rows in pairs.items():
        b = by_iid.get(iid)
        if b is None:
            problems.append(f"{iid}: not in the schedule")
            continue
        expected_order = f"{b['first_arm']}-first"
        for tag, r in arm_rows.items():
            bi = (r.get("block_index") or "").strip()
            if bi and not re.fullmatch(r"[0-9]+", bi):
                problems.append(f"{tag}/{iid}: block_index {bi!r} is not "
                                f"a canonical integer (decimal/exponent "
                                f"notation rejected)")
            elif bi and int(bi) != b["block"]:
                problems.append(f"{tag}/{iid}: block_index {bi} does not "
                                f"match schedule block {b['block']}")
            ao = (r.get("arm_order") or "").strip()
            if ao and ao != expected_order:
                problems.append(f"{tag}/{iid}: arm_order {ao!r} does not "
                                f"match schedule ({expected_order!r})")
        if len(arm_rows) == 2:
            first_tag = ("gpt55-base" if b["first_arm"] == "base"
                         else "gpt55-reason")
            second_tag = ("gpt55-reason" if b["first_arm"] == "base"
                          else "gpt55-base")
            t1 = ts_seconds(arm_rows[first_tag]["timestamp"])
            t2 = ts_seconds(arm_rows[second_tag]["timestamp"])
            if t1 is None or t2 is None:
                problems.append(f"{iid}: unparseable trial timestamps")
            else:
                if t2 < t1:
                    problems.append(
                        f"{iid}: scheduled first arm ({first_tag}) ran "
                        f"AFTER the second arm")
                if abs(t2 - t1) > MAX_PAIR_GAP_SECONDS:
                    problems.append(
                        f"{iid}: within-pair gap {abs(t2 - t1):.0f}s "
                        f"exceeds the frozen maximum "
                        f"{MAX_PAIR_GAP_SECONDS}s")
                pair_start[b["block"]] = min(t1, t2)
    # monotone block order (resume keeps schedule order, so pair start
    # times must be non-decreasing in block index; ties allowed)
    prev_b, prev_t = None, None
    for blk in sorted(pair_start):
        t = pair_start[blk]
        if prev_t is not None and t < prev_t - 1.0:
            problems.append(f"block {blk} started before block {prev_b} "
                            f"— execution order deviates from the "
                            f"schedule")
        prev_b, prev_t = blk, t
    if len(problems) > 25:
        problems = problems[:25] + [f"... ({len(problems) - 25} more "
                                    f"schedule-execution problems)"]
    return problems


def integrity_problems(runs_dir, audit, arms, rows,
                       manifest_path=None, schedule_path=None,
                       known_manifest_shas=None):
    """The shared end-to-end integrity gate. Returns (problems, info)."""
    problems, info = [], {}

    problems.extend(audit["row_problems"])

    provider = _exactly_one(audit["providers"], "upstream provider",
                            problems)
    rch = _exactly_one(audit["run_config_hashes"], "run_config_hash",
                       problems)
    sch = _exactly_one(audit["schedule_hashes"], "schedule_hash", problems)
    echo = _exactly_one(audit["echoes"], "response_model echo", problems)
    model_id = _exactly_one(audit["model_ids"], "requested model_id",
                            problems)
    info.update(provider=provider, run_config_hash=rch,
                schedule_hash=sch, echo=echo, model_id=model_id,
                timeout_rows=audit.get("timeout_row_ids", []))

    if echo is not None and echo not in ALLOWED_ECHOES:
        problems.append(f"response_model echo {echo!r} is outside the "
                        f"frozen identity rule {sorted(ALLOWED_ECHOES)}")
    if model_id is not None and model_id != EXPECTED_MODEL:
        problems.append(f"requested model_id {model_id!r} differs from "
                        f"the frozen snapshot {EXPECTED_MODEL!r}")

    for arm in arms:
        req = audit["reasoning_by_arm"].get(arm, set())
        if "" in req:
            problems.append(f"{arm}: at least one row has an EMPTY "
                            f"reasoning request (fail-closed rule)")
        req = req - {""}
        if len(req) != 1 or req != {EXPECTED_REASONING[arm]}:
            problems.append(
                f"{arm}: reasoning request(s) {sorted(req)} do not "
                f"uniquely match the frozen value "
                f"{EXPECTED_REASONING[arm]!r}")

    # ---- run_config.json: recompute + bind --------------------------------
    cfg = None
    if not os.path.exists(audit["run_config_file"]):
        problems.append("run_config.json missing from the runs dir")
    else:
        with open(audit["run_config_file"]) as fh:
            rc = json.load(fh)
        cfg = rc.get("run_config", {})
        recomputed = config_hash(cfg)
        if recomputed != rc.get("run_config_hash"):
            problems.append("run_config.json: recorded hash does not "
                            "match its own recomputed config hash")
        if rch is not None and recomputed != rch:
            problems.append("run_config_hash of the trial rows does not "
                            "match the recomputed run_config.json hash")
        # Frozen-configuration enforcement:
        # EVERY frozen field must equal the plan value exactly — a
        # formally hash-consistent dataset collected with, e.g.,
        # allow_fallbacks=true or require_parameters=false must fail.
        for key, expected in EXPECTED_RUN_CONFIG.items():
            if cfg.get(key) != expected:
                problems.append(
                    f"run_config {key} = {cfg.get(key)!r} differs from "
                    f"the frozen value {expected!r}")
        # Direct run_config <-> trial-row effort comparison (the rows are
        # additionally checked against EXPECTED_REASONING above, so all
        # three of frozen values, run_config, and rows must agree).
        for arm, effort_key in (("gpt55-base", "base_effort"),
                                ("gpt55-reason", "reason_effort")):
            expect_req = json.dumps({"effort": cfg.get(effort_key)})
            observed = audit["reasoning_by_arm"].get(arm, set()) - {""}
            if observed and observed != {expect_req}:
                problems.append(
                    f"{arm}: trial reasoning request(s) "
                    f"{sorted(observed)} do not match run_config "
                    f"{effort_key} = {cfg.get(effort_key)!r}")
        pinned = [norm_provider(x)
                  for x in (cfg.get("provider_order") or [])]
        if provider is not None and pinned != [provider]:
            problems.append("observed provider does not match the pinned "
                            "provider_order in run_config.json")
        if audit["temperatures"] - {_row_repr(cfg.get("temperature"))}:
            problems.append(
                f"row temperatures {sorted(audit['temperatures'])} differ "
                f"from run_config temperature "
                f"{_row_repr(cfg.get('temperature'))!r}")
        if audit["max_tokens"] - {str(cfg.get("max_tokens"))}:
            problems.append(
                f"row max_tokens {sorted(audit['max_tokens'])} differ "
                f"from run_config max_tokens {cfg.get('max_tokens')}")
        if not cfg.get("preflight_sha256"):
            problems.append("run_config carries no preflight_sha256 — "
                            "the preflight is not bound into the "
                            "run_config_hash of the trial rows")
    info["run_config"] = cfg

    # ---- manifest / schedule hash recomputation ---------------------------
    if manifest_path is None:
        problems.append("no --manifest given: instance identity not "
                        "validated (REQUIRED for confirmatory analysis)")
    else:
        msha = sha256_file(manifest_path)
        info["manifest_sha256"] = msha
        if known_manifest_shas and msha not in known_manifest_shas:
            problems.append(f"manifest sha {msha[:12]}… is not a pinned "
                            f"instance set")
        if cfg and cfg.get("manifest_sha256") != msha:
            problems.append("run_config manifest_sha256 does not match "
                            "the presented manifest file")
    if schedule_path is None:
        problems.append("no --schedule given: schedule binding not "
                        "validated (REQUIRED for confirmatory analysis)")
    else:
        ssha = sha256_file(schedule_path)
        info["schedule_sha256"] = ssha
        if sch is not None and ssha != sch:
            problems.append("schedule_hash of the trial rows does not "
                            "match the presented schedule file")
        if cfg and cfg.get("schedule_sha256") != ssha:
            problems.append("run_config schedule_sha256 does not match "
                            "the presented schedule file")
        # ---- schedule EXECUTION validation (not just the file hash) ----
        with open(schedule_path) as fh:
            schedule = json.load(fh)
        problems.extend(schedule_execution_problems(rows, schedule))

    # ---- preflight: cryptographic binding + content validation -----------
    pfp = audit["preflight_artifact"]
    if not os.path.exists(pfp):
        problems.append(f"preflight artifact missing ({pfp})")
    else:
        with open(pfp) as fh:
            pf = json.load(fh)
        info["preflight"] = {k: pf.get(k) for k in
                             ("timestamp_utc", "model", "verdict")}
        if cfg and cfg.get("preflight_sha256") != sha256_file(pfp):
            problems.append("preflight.json sha256 does not match the "
                            "value bound into run_config (artifact "
                            "changed after the run started?)")
        _validate_preflight(pf, cfg, provider,
                            audit["min_trial_timestamp"], problems)

    if audit["harness_error_files"]:
        problems.append("harness errors recorded: "
                        + "; ".join(audit["harness_error_files"]))
    return problems, info


def completeness_problems(rows, manifest, arms):
    """Every manifest instance must have exactly one valid trial per arm."""
    problems = []
    have = defaultdict(set)
    for r in rows:
        have[r["model_tag"]].add(r["instance_id"])
    expected = {i["id"] for i in manifest["instances"]}
    for arm in arms:
        missing = expected - have.get(arm, set())
        extra = have.get(arm, set()) - expected
        if missing:
            problems.append(f"{arm}: {len(missing)} scheduled instances "
                            f"missing, e.g. {sorted(missing)[:3]}")
        if extra:
            problems.append(f"{arm}: {len(extra)} rows for instances not "
                            f"in the manifest, e.g. {sorted(extra)[:3]}")
    return problems
