#!/usr/bin/env python3
"""End-to-end test of the scaffold + rubiks plugin with a stubbed LLM.

No API calls.  Verifies: manifest pairing, prompt blinding, HTM-only
action space with strict final-line parsing (lenient parses logged, never
scored), hash-verified instance reconstruction, per-call audit fields in
the CSV, timeout rows that keep their difficulty, and in-trial retry of
transport failures with a sidecar log.

Run:  python tests/test_pipeline_stub.py
"""

import asyncio
import csv
import json
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from scaffold.llm import LLMResponse  # noqa: E402
from scaffold.registry import load_benchmark  # noqa: E402
import scaffold.runner as runner_mod  # noqa: E402
from benchmarks.rubiks.benchmark import solve  # noqa: E402

MANIFEST = os.path.join(REPO, "instances", "rubiks_dstar_manifest_v1.json")

captured_prompts = []
captured_systems = []


def ok_response(content, **kw):
    defaults = dict(success=True, content=content, tokens_used=3000,
                    finish_reason="stop", prompt_tokens=1000,
                    completion_tokens=2000, requested_max_tokens=32000,
                    retries=0, response_model="stub/model-2026-01-01",
                    provider="StubProvider", system_fingerprint="fp_abc")
    defaults.update(kw)
    return LLMResponse(**defaults)


class ScriptedLLM:
    def __init__(self, replies, **_kw):
        self.model = "stub/model"
        self.replies = list(replies)

    async def call(self, messages, system=None):
        captured_prompts.append(messages[0]["content"])
        captured_systems.append(system or "")
        reply = self.replies.pop(0)
        if reply == "SLEEP":
            await asyncio.sleep(5)
        return reply


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + (f" — {detail}" if detail else ""))
    if not cond:
        sys.exit(1)


async def main():
    with open(MANIFEST) as fh:
        manifest = json.load(fh)
    by4 = sorted((i for i in manifest["instances"] if i["d_star"] == 4),
                 key=lambda i: i["id"])

    workdir = tempfile.mkdtemp(prefix="pipeline_stub_")
    os.chdir(workdir)
    csv_path = os.path.join(workdir, "metrics.csv")

    # ------------------------------------------------------------------
    # Run 1: witness / wrong-moves / compute-bound + a transport failure
    # that must be retried in-trial (trial 2 succeeds on attempt 2).
    replies = [
        ok_response("Analysis.\n" + by4[0]["optimal_witness"],
                    reasoning_tokens=1500),
        LLMResponse(success=False, content="",
                    error="API error: APIConnectionError: boom",
                    requested_max_tokens=32000, retries=2,
                    retry_errors="attempt 1: APITimeoutError"),
        ok_response("R U R' U'", tokens_used=1200, completion_tokens=200,
                    retries=1, retry_errors="attempt 1: APITimeoutError"),
        LLMResponse(success=False, content="",
                    error="Empty model content (finish_reason=length, "
                          "reasoning_field=present, tokens_used=33000)",
                    tokens_used=33000, finish_reason="length",
                    prompt_tokens=1000, completion_tokens=32000,
                    reasoning_tokens=32000, requested_max_tokens=32000,
                    retries=0),
    ]
    runner_mod.RobustLLM = lambda **kw: ScriptedLLM(replies, **kw)
    runner_mod.TRANSPORT_RETRY_DELAY = 0.0  # no real backoff in tests
    bench = load_benchmark("rubiks")
    await runner_mod.run_benchmark(
        benchmark=bench, manifest_path=MANIFEST, difficulty=4,
        num_tests=3, model="stub/model", verbose=False,
        model_tag="stub-test", metrics_file=csv_path)

    with open(csv_path) as fh:
        rows = list(csv.DictReader(fh))
    check("3 valid rows written", len(rows) == 3)
    r1, r2, r3 = rows

    check("trial 1 success (witness, strict parse)",
          r1["success"] == "True" and r1["parse_mode"] == "strict")
    check("trial 1 instance_id + d*", r1["instance_id"] == by4[0]["id"]
          and r1["complexity"] == "4")
    check("trial 1 audit fields", r1["finish_reason"] == "stop"
          and r1["reasoning_tokens"] == "1500"
          and r1["requested_max_tokens"] == "32000"
          and r1["provider"] == "StubProvider"
          and r1["response_model"] == "stub/model-2026-01-01")

    check("trial 2 valid outcome after in-trial transport retry",
          r2["success"] == "False" and r2["error"] == "Cube not solved"
          and r2["instance_id"] == by4[1]["id"])
    sidecar = csv_path + ".invalid.csv"
    check("transport attempt logged to sidecar", os.path.exists(sidecar))
    with open(sidecar) as fh:
        inv = list(csv.DictReader(fh))
    check("sidecar row is the API error on the same instance",
          len(inv) == 1 and inv[0]["instance_id"] == by4[1]["id"]
          and inv[0]["error"].startswith("API error")
          and inv[0]["invalid_attempt"] == "1")

    check("trial 3 compute-bound signature",
          r3["success"] == "False" and r3["finish_reason"] == "length"
          and r3["completion_tokens"] == "32000")

    # ---- prompt blinding + action-space contract -------------------------
    joined = [p + s for p, s in zip(captured_prompts, captured_systems)]
    for leak in ["easy", "medium", "hard", "scrambled with", "random moves",
                 "d*", "d_star", "difficulty", "distance"]:
        check(f"prompt does not leak {leak!r}",
              all(leak not in t.lower() for t in joined))
    check("system prompt forbids slice/rotation/wide moves",
          "NOT allowed" in captured_systems[0]
          and "M, E, S" in captured_systems[0])
    heads = {p.split("Cube state")[0] for p in captured_prompts}
    check("prompt head identical across trials", len(heads) == 1)

    # ---- parser semantics (direct solve() calls) --------------------------
    inst = by4[0]

    # (a) trailing prose after the moves: strict fails, lenient rescues
    r = await solve(llm=ScriptedLLM([ok_response(
            inst["optimal_witness"] + "\nHope that helps!")]),
        difficulty=4, test_id=90, verbose=False, instance=inst)
    check("trailing prose -> valid failure (format), not scored",
          r["success"] is False and r["parse_mode"] == "lenient")
    check("lenient rescue logged (would have solved)",
          r["lenient_rescue"] is True)

    # (b) slice-move answer is rejected even though magiccube could apply it
    r = await solve(llm=ScriptedLLM([ok_response("M E S x y z")]),
        difficulty=4, test_id=91, verbose=False, instance=inst)
    check("slice/rotation answer rejected (action space)",
          r["success"] is False and r["parse_mode"] == "none")

    # (c) strict last line with legal HTM moves still scores
    r = await solve(llm=ScriptedLLM([ok_response(
            "reasoning...\n" + inst["optimal_witness"])]),
        difficulty=4, test_id=92, verbose=False, instance=inst)
    check("strict last-line parse scores", r["success"] is True
          and r["parse_mode"] == "strict")

    # ---- timeout row keeps difficulty (bug fix) ---------------------------
    csv_t = os.path.join(workdir, "metrics_timeout.csv")
    runner_mod.RobustLLM = lambda **kw: ScriptedLLM(
        ["SLEEP", ok_response(by4[1]["optimal_witness"])], **kw)
    await runner_mod.run_benchmark(
        benchmark=bench, manifest_path=MANIFEST, difficulty=4,
        num_tests=2, model="stub/model", verbose=False,
        model_tag="stub-test", metrics_file=csv_t, trial_timeout=1)
    with open(csv_t) as fh:
        trows = list(csv.DictReader(fh))
    check("timeout trial logged as valid failure",
          trows[0]["error"].startswith("Trial timeout"))
    check("timeout row carries complexity = d*",
          trows[0]["complexity"] == "4"
          and trows[0]["instance_id"] == by4[0]["id"])
    check("run continues after timeout", trows[1]["success"] == "True")

    # ---- harness errors are fatal, never scored ---------------------------
    from scaffold.registry import Benchmark

    async def broken_solve(**_kw):
        raise ModuleNotFoundError("No module named 'magiccube'")

    broken_bench = Benchmark(
        name="rubiks", solve=broken_solve,
        select_instances=bench.select_instances, difficulty_help="")
    csv_h = os.path.join(workdir, "metrics_harness.csv")
    runner_mod.RobustLLM = lambda **kw: ScriptedLLM([], **kw)
    try:
        await runner_mod.run_benchmark(
            benchmark=broken_bench, manifest_path=MANIFEST, difficulty=4,
            num_tests=2, model="stub/model", verbose=False,
            model_tag="stub-test", metrics_file=csv_h)
        check("harness exception aborts the cell", False)
    except SystemExit as exc:
        check("harness exception aborts the cell",
              "harness error" in str(exc))
    with open(csv_h) as fh:
        hrows = list(csv.DictReader(fh))
    check("harness error produced NO scored trial row", len(hrows) == 0)
    hfile = csv_h + ".harness_errors.csv"
    check("harness error recorded in sidecar", os.path.exists(hfile))
    with open(hfile) as fh:
        hrec = list(csv.DictReader(fh))
    check("harness sidecar names the exception",
          "ModuleNotFoundError" in hrec[0]["error"])

    # ---- transport exhaustion aborts the cell (no silent underfill) -------
    csv_x = os.path.join(workdir, "metrics_exhaust.csv")
    bad = LLMResponse(success=False, content="",
                      error="API error: APIConnectionError: down")
    runner_mod.RobustLLM = lambda **kw: ScriptedLLM([bad, bad, bad], **kw)
    try:
        await runner_mod.run_benchmark(
            benchmark=bench, manifest_path=MANIFEST, difficulty=4,
            num_tests=1, model="stub/model", verbose=False,
            model_tag="stub-test", metrics_file=csv_x)
        check("persistent transport failure aborts cell", False)
    except SystemExit as exc:
        check("persistent transport failure aborts cell",
              "transport errors persisted" in str(exc))
    with open(csv_x) as fh:
        xrows = list(csv.DictReader(fh))
    check("no valid row written for the aborted trial", len(xrows) == 0)

    print("\nAll pipeline checks passed.")


if __name__ == "__main__":
    asyncio.run(main())
