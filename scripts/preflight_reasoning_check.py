#!/usr/bin/env python3
"""
Preflight capability check for the reasoning contrast (NO benchmark
instances are used — interface verification only).

Sends a handful of small reasoning-demanding probe prompts (cheap, but
NOT trivial — see PROBE) through BOTH arm configurations and
verifies, before any benchmark call is spent:
  1. both requests are accepted (no parameter rejection),
  2. the echoed model snapshot and provider are identical across arms,
  3. the realized reasoning-token distributions clearly differ
     (base arm ~0, reason arm substantially > 0).

Exit status 0 = contrast verified; anything else = do not collect.

Usage:
    python scripts/preflight_reasoning_check.py --model <dated-snapshot>
        [--base-effort none] [--reason-effort medium] [--samples 3]
"""

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), ".."))

from scaffold.llm import RobustLLM  # noqa: E402

# The probe must actually DEMAND reasoning (plan item 55): on a trivial
# prompt a medium-effort model legitimately spends ~0 reasoning tokens
# and the frozen contrast rule (reason_min > max(64, 2*base_max))
# cannot be evidenced. A small constraint-search task forces genuine
# multi-step reasoning while keeping the response (and cost) tiny.
# The preflight never checks the ANSWER — only token behaviour,
# echo, and provider identity.
PROBE = ("Find every three-digit number that satisfies ALL of these "
         "conditions: (a) its digits sum to 18, (b) it is divisible "
         "by 7, (c) its middle digit is twice its first digit. "
         "Check your candidates carefully. "
         "Reply with the number(s) only, separated by spaces.")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--base-effort", default="none")
    ap.add_argument("--reason-effort", default="medium")
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--provider-order", default=None,
                    help="same single-provider pin as the main run")
    # Frozen (plan v1.7, item 54): None = the parameter is OMITTED —
    # GPT-5.5 endpoints support no sampling parameters, and with
    # require_parameters=true a request carrying temperature matches no
    # endpoint at all (OpenRouter routing 404).
    ap.add_argument("--temperature", type=float, default=None)
    ap.add_argument("--out", default=None,
                    help="write a preflight artifact JSON here; the "
                         "analysis gate requires it for confirmatory runs")
    args = ap.parse_args()
    providers_pin = ([p.strip() for p in args.provider_order.split(",")]
                     if args.provider_order else None)

    # EXACT production payload (incl. temperature): a preflight that
    # differs from the study configuration proves nothing.
    arms = {
        "base": RobustLLM(model=args.model, temperature=args.temperature,
                          max_tokens=32000,
                          reasoning_effort=args.base_effort,
                          require_parameters=True, allow_fallbacks=False,
                          provider_order=providers_pin, max_retries=1),
        "reason": RobustLLM(model=args.model,
                            temperature=args.temperature,
                            max_tokens=32000,
                            reasoning_effort=args.reason_effort,
                            require_parameters=True, allow_fallbacks=False,
                            provider_order=providers_pin, max_retries=1),
    }

    stats = {}
    for arm, llm in arms.items():
        tokens, snapshots, providers = [], set(), set()
        for i in range(args.samples):
            r = await llm.call([{"role": "user", "content": PROBE}])
            if not r.success:
                print(f"[FAIL] {arm}: request rejected/failed: {r.error}")
                sys.exit(1)
            tokens.append(r.reasoning_tokens or 0)
            snapshots.add(r.response_model or "?")
            providers.add(r.provider or "?")
            print(f"  {arm} probe {i + 1}: reasoning_tokens="
                  f"{r.reasoning_tokens}, model={r.response_model}, "
                  f"provider={r.provider}, "
                  f"requested={r.requested_reasoning}")
        stats[arm] = dict(tokens=tokens, snapshots=snapshots,
                          providers=providers)

    ok = True
    if stats["base"]["snapshots"] != stats["reason"]["snapshots"]:
        print("[FAIL] arms were served by different model snapshots")
        ok = False
    if stats["base"]["providers"] != stats["reason"]["providers"]:
        print("[FAIL] arms were served by different providers")
        ok = False
    base_max = max(stats["base"]["tokens"])
    reason_min = min(stats["reason"]["tokens"])
    if not (reason_min > max(64, 2 * base_max)):
        print(f"[FAIL] reasoning-token distributions do not clearly "
              f"differ (base max {base_max}, reason min {reason_min}) — "
              f"the contrast may have been normalised away")
        ok = False
    if args.out:
        import datetime
        artifact = {
            "timestamp_utc": datetime.datetime.now(
                datetime.timezone.utc).isoformat(timespec="seconds"),
            "model": args.model,
            "temperature": args.temperature,
            "provider_order": providers_pin,
            "max_tokens": 32000,
            "base_effort": args.base_effort,
            "reason_effort": args.reason_effort,
            "observed": {arm: {
                "reasoning_tokens": stats[arm]["tokens"],
                "snapshots": sorted(stats[arm]["snapshots"]),
                "providers": sorted(stats[arm]["providers"]),
            } for arm in stats},
            "verdict": "ok" if ok else "FAILED",
        }
        os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
        with open(args.out, "w") as fh:
            json.dump(artifact, fh, indent=1)
        print(f"preflight artifact written to {args.out}")
    if ok:
        print(f"\nPREFLIGHT OK: base tokens {stats['base']['tokens']}, "
              f"reason tokens {stats['reason']['tokens']}, "
              f"snapshot {sorted(stats['base']['snapshots'])[0]}")
        return
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
