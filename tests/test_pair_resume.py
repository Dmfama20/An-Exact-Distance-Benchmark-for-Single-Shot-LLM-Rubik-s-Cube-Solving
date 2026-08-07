#!/usr/bin/env python3
"""Test the driver's frozen pair-resume rule: a half-finished pair is
quarantined (.superseded.csv) and the WHOLE pair re-run.

Run:  python tests/test_pair_resume.py
"""

import asyncio
import csv
import hashlib
import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "scripts"))

from scaffold.llm import LLMResponse  # noqa: E402
import run_paired_study as driver  # noqa: E402

WORK = tempfile.mkdtemp(prefix="pair_resume_")
RUNS = os.path.join(WORK, "runs")
os.makedirs(RUNS)
MANIFEST = os.path.join(REPO, "instances", "rubiks_dstar_manifest_v1.json")
SCHEDULE = os.path.join(REPO, "instances", "rubiks_schedule_v1.json")

# The driver refuses to start without a valid passed preflight (plan
# item 50), so the test provides one for its exact configuration.
PREFLIGHT = {
    "timestamp_utc": "2026-07-23T09:00:00+00:00",
    "model": "openai/gpt-5.5-20260423",
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
json.dump(PREFLIGHT, open(os.path.join(RUNS, "preflight.json"), "w"))


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f" — {detail}" if detail else ""))
    if not cond:
        sys.exit(1)


class StubLLM:
    def __init__(self, **kw):
        self.model = kw.get("model", "stub")
        self.witnesses = json.load(open(MANIFEST))
        self.by_id = {i["id"]: i["optimal_witness"]
                      for i in self.witnesses["instances"]}
        self._current = None

    async def call(self, messages, system=None):
        return LLMResponse(
            success=True, content="X",  # wrong answer, valid failure
            tokens_used=100, finish_reason="stop", prompt_tokens=50,
            completion_tokens=50, requested_max_tokens=32000, retries=0,
            response_model="openai/gpt-5.5", provider="openai")


async def main():
    driver.RobustLLM = StubLLM

    # Plan item 50: without a preflight artifact the driver must refuse
    # to start (the shell wrapper is not the only enforcement point).
    nopf = os.path.join(WORK, "runs_nopf")
    os.makedirs(nopf)
    sys.argv = ["x", "--provider-order", "openai",
                "--manifest", MANIFEST, "--schedule", SCHEDULE,
                "--runs-dir", nopf, "--limit-blocks", "1"]
    refused = False
    try:
        await driver.main()
    except SystemExit as e:
        refused = "preflight" in str(e.code).lower()
    check("driver refuses to start without a preflight artifact",
          refused)

    # First run: only the first 2 schedule blocks, then abort mid-pair by
    # pre-seeding an orphan: run blocks 1-2 fully first.
    sys.argv = ["x", "--provider-order", "openai",
                "--manifest", MANIFEST, "--schedule", SCHEDULE,
                "--runs-dir", RUNS, "--limit-blocks", "2",
                "--trial-timeout", "60"]
    try:
        await driver.main()
    except SystemExit as e:
        # incomplete study exit (blocks 3+ missing) is expected
        pass

    # Manually delete ONE arm of block 2 -> orphan pair
    sched = json.load(open(SCHEDULE))
    b2 = sched["blocks"][1]
    victim_tag = ("gpt55-base" if b2["first_arm"] == "base"
                  else "gpt55-reason")
    target = os.path.join(
        RUNS, f"{victim_tag}__rubiks__dstar{b2['d_star']}__batch_metrics.csv")
    rows = list(csv.DictReader(open(target)))
    kept = [r for r in rows if r["instance_id"] != b2["instance_id"]]
    fields = list(rows[0].keys())
    with open(target, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(kept)
    # now the OTHER arm's row for block 2 is an orphan

    # Resume: driver must quarantine the orphan and re-run the whole pair
    try:
        await driver.main()
    except SystemExit:
        pass

    other_tag = ("gpt55-reason" if victim_tag == "gpt55-base"
                 else "gpt55-base")
    other_file = os.path.join(
        RUNS, f"{other_tag}__rubiks__dstar{b2['d_star']}__batch_metrics.csv")
    sup = other_file + ".superseded.csv"
    check("orphan row quarantined to .superseded.csv",
          os.path.exists(sup)
          and any(r["instance_id"] == b2["instance_id"]
                  for r in csv.DictReader(open(sup))))
    for tag in (victim_tag, other_tag):
        f = os.path.join(
            RUNS, f"{tag}__rubiks__dstar{b2['d_star']}__batch_metrics.csv")
        n = sum(1 for r in csv.DictReader(open(f))
                if r["instance_id"] == b2["instance_id"])
        check(f"pair re-run complete, exactly one row ({tag})", n == 1)
    print("\nAll pair-resume checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
