"""
Generic single-shot benchmark runner.

The scaffold owns everything benchmark-agnostic: manifest loading and
instance pairing, the trial loop with per-trial timeouts, metrics CSV
writing, resume logic, and fatal-error propagation for billing/auth
failures.  The benchmark plugin owns the task itself: prompt, parsing,
deterministic verification (see scaffold.registry.Benchmark).
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from scaffold.llm import RobustLLM
from scaffold.metrics import TrialLogger, TrialMetrics
from scaffold.registry import Benchmark

FATAL_ERROR_KEYWORDS = [
    "402", "payment", "insufficient", "billing",
    "401", "unauthorized", "invalid api key", "no credits",
    "quota_exceeded", "insufficient_credits",
]

# Transport-level failures are INVALID trials per the analysis plan: they
# must be retried on the same instance, never scored. Attempts that fail
# this way are logged to a sidecar CSV (provenance) so the main metrics
# CSV contains exactly one valid row per trial — which also keeps
# row-count-based resume and sweep sentinels truthful.
TRANSPORT_ERROR_PREFIXES = ("API error", "Max retries exceeded")
TRANSPORT_RETRIES_PER_TRIAL = 3
TRANSPORT_RETRY_DELAY = 5.0  # seconds, scaled by attempt number


def _is_transport_error(error: Optional[str]) -> bool:
    return bool(error) and error.startswith(TRANSPORT_ERROR_PREFIXES)


async def execute_trial(
    benchmark: Benchmark,
    llm: RobustLLM,
    difficulty: int,
    test_id: int,
    instance: dict,
    logger: TrialLogger,
    identity: dict,
    trial_timeout: Optional[int] = None,
    verbose: bool = True,
) -> TrialMetrics:
    """Run exactly one trial with the full validity semantics.

    - One GLOBAL deadline (trial_timeout) covers all transport retries and
      backoff sleeps; on expiry exactly one valid timeout row is written.
    - Transport failures retry the SAME instance (sidecar-logged); when
      they persist the cell aborts via SystemExit (resumable).
    - Unexpected exceptions are harness errors: sidecar-logged, cell
      aborts, NO trial row is written (never scored as model failures).

    ``identity`` supplies run metadata (model_id, model_tag, reasoning,
    temperature, backend, and optional block_index / arm_order /
    schedule_hash). The final metrics row (and detailed log) is written
    here and returned.
    """
    start_time = time.time()

    def base_metrics(**overrides) -> TrialMetrics:
        # Every row — including timeout paths — carries the instance
        # identity and the verified difficulty (complexity), so no valid
        # failure can silently drop out of the analysis.
        defaults = dict(
            # Timezone-aware UTC (frozen): the gate rejects naive
            # timestamps, and strict preflight/pair ordering needs one
            # clock across all components.
            timestamp=datetime.now(timezone.utc).isoformat(),
            benchmark=benchmark.name,
            test_id=test_id,
            difficulty=difficulty,
            success=False,
            num_moves=None,
            tokens_used=None,
            time_seconds=time.time() - start_time,
            error=None,
            instance_id=instance.get("id"),
            complexity=difficulty,
            **identity,
        )
        defaults.update(overrides)
        return TrialMetrics(**defaults)

    deadline = (start_time + trial_timeout) if trial_timeout else None

    result = None
    timed_out = False
    for attempt in range(1, TRANSPORT_RETRIES_PER_TRIAL + 1):
        remaining = (deadline - time.time()) if deadline else None
        if remaining is not None and remaining <= 0:
            timed_out = True
            break
        try:
            coro = benchmark.solve(
                llm=llm,
                difficulty=difficulty,
                test_id=test_id,
                verbose=verbose,
                instance=instance,
            )
            if remaining is not None:
                result = await asyncio.wait_for(coro, timeout=remaining)
            else:
                result = await coro
        except asyncio.TimeoutError:
            timed_out = True
            break
        except Exception as exc:
            # NOT a model outcome: hash mismatches, missing modules,
            # evaluator bugs, filesystem errors etc. must never be scored
            # as model failures ("wrong_solution").
            logger.log_harness_error(
                base_metrics(error=f"{type(exc).__name__}: {exc}"))
            raise SystemExit(
                f"Trial {test_id}: harness error "
                f"({type(exc).__name__}: {exc}) — aborting cell; "
                f"no trial row was written (resumable)")

        if not _is_transport_error(result.get("error")):
            break  # completed model outcome (success or valid failure)

        logger.log_invalid_attempt(
            base_metrics(
                error=result.get("error"),
                tokens_used=result.get("tokens_used"),
                retries=result.get("retries"),
                retry_errors=result.get("retry_errors")),
            attempt=attempt)
        print(f"\n! transport error on trial {test_id} "
              f"(attempt {attempt}/{TRANSPORT_RETRIES_PER_TRIAL}): "
              f"{result.get('error')}")
        if any(kw in (result.get("error") or "").lower()
               for kw in FATAL_ERROR_KEYWORDS):
            raise SystemExit(
                f"Fatal billing/auth error on trial {test_id}: "
                f"{result.get('error')}")
        if attempt == TRANSPORT_RETRIES_PER_TRIAL:
            raise SystemExit(
                f"Trial {test_id}: transport errors persisted after "
                f"{TRANSPORT_RETRIES_PER_TRIAL} attempts — aborting cell "
                f"(resumable)")
        backoff = TRANSPORT_RETRY_DELAY * attempt
        if deadline is not None:
            backoff = max(0.0, min(backoff, deadline - time.time()))
        await asyncio.sleep(backoff)
        result = None

    if timed_out or (result is None and deadline is not None
                     and time.time() >= deadline):
        # Request-side audit fields are known BEFORE the call and are
        # backfilled here (deviation item 56); the response-side echoes
        # (response_model, provider) cannot exist for a call that never
        # returned and legitimately stay empty on timeout rows.
        requested_reasoning = None
        if getattr(llm, "reasoning_effort", None):
            requested_reasoning = json.dumps(
                {"effort": llm.reasoning_effort})
        metrics = base_metrics(
            error=f"Trial timeout ({trial_timeout}s, global deadline "
                  f"incl. transport retries)",
            requested_reasoning=requested_reasoning,
            requested_max_tokens=getattr(llm, "max_tokens", None))
        logger.log_metrics(metrics)
        print(f"\n✗ TIMEOUT in test {test_id} after {trial_timeout}s")
        return metrics

    metrics = base_metrics(
        success=result.get("success", False),
        num_moves=result.get("num_moves"),
        tokens_used=result.get("tokens_used"),
        error=result.get("error"),
        instance_id=result.get("instance_id"),
        size=result.get("size"),
        complexity=result.get("complexity"),
        similarity=result.get("similarity"),
        parse_mode=result.get("parse_mode"),
        lenient_rescue=result.get("lenient_rescue"),
        finish_reason=result.get("finish_reason"),
        prompt_tokens=result.get("prompt_tokens"),
        completion_tokens=result.get("completion_tokens"),
        reasoning_tokens=result.get("reasoning_tokens"),
        requested_max_tokens=result.get("requested_max_tokens"),
        requested_reasoning=result.get("requested_reasoning"),
        retries=result.get("retries"),
        retry_errors=result.get("retry_errors"),
        response_model=result.get("response_model"),
        provider=result.get("provider"),
        system_fingerprint=result.get("system_fingerprint"),
    )
    logger.log_metrics(metrics)
    if "detailed_log" in result:
        logger.log_detailed_test(test_id, result["detailed_log"])

    status = "✓ SOLVED" if metrics.success else "✗ FAILED"
    print(f"\n{status} in {metrics.time_seconds:.1f}s")
    if metrics.error:
        print(f"Error: {metrics.error}")
    return metrics


async def run_benchmark(
    benchmark: Benchmark,
    manifest_path: str,
    difficulty: int,
    num_tests: int,
    model: str,
    temperature: Optional[float] = None,
    enable_reasoning: bool = False,
    reasoning_max_tokens: int = 2000,
    reasoning_effort: Optional[str] = None,
    max_tokens: Optional[int] = None,
    verbose: bool = True,
    model_tag: Optional[str] = None,
    use_ollama: bool = False,
    ollama_base_url: str = "http://ollama:11434",
    metrics_file: Optional[str] = None,
    start_test: int = 1,
    trial_timeout: Optional[int] = None,
    require_parameters: bool = False,
    allow_fallbacks: bool = True,
    provider_order: Optional[List[str]] = None,
) -> Tuple[List[TrialMetrics], TrialLogger]:
    # ---- manifest instances (pairing by construction) -------------------
    with open(manifest_path) as fh:
        manifest_data = json.load(fh)
    instances = benchmark.select_instances(manifest_data, difficulty)
    if len(instances) < num_tests:
        raise SystemExit(
            f"Manifest {manifest_path} has only {len(instances)} instances "
            f"at difficulty {difficulty}, but --tests {num_tests} was "
            f"requested")

    logger = TrialLogger(benchmark.name, metrics_file=metrics_file)

    llm_kwargs = dict(
        model=model,
        temperature=temperature,
        enable_reasoning=enable_reasoning,
        reasoning_max_tokens=reasoning_max_tokens,
        reasoning_effort=reasoning_effort,
        use_ollama=use_ollama,
        ollama_base_url=ollama_base_url,
        require_parameters=require_parameters,
        allow_fallbacks=allow_fallbacks,
        provider_order=provider_order,
    )
    if max_tokens:
        llm_kwargs["max_tokens"] = max_tokens
    llm = RobustLLM(**llm_kwargs)

    backend = "ollama" if use_ollama else (
        "openrouter" if "/" in model else "anthropic")

    print("=" * 70)
    print(f"BENCHMARK: {benchmark.name}")
    print("=" * 70)
    print(f"Tests: {num_tests}")
    print(f"Difficulty: {difficulty} "
          f"(manifest: {manifest_path}, {len(instances)} instances)")
    print(f"Model: {model}")
    print(f"Temperature: {temperature}")
    if enable_reasoning:
        print(f"Reasoning: enabled (max_tokens={reasoning_max_tokens})")
    if reasoning_effort:
        print(f"Reasoning effort: {reasoning_effort}")
    print(f"Metrics file: {logger.metrics_file}")
    if start_test > 1:
        print(f"Resuming from test {start_test}")
    print("=" * 70)

    results: List[TrialMetrics] = []

    identity = dict(
        model_id=model,
        model_tag=model_tag,
        reasoning=enable_reasoning,
        temperature="unset" if temperature is None else temperature,
        backend=backend,
    )

    for i in range(1, num_tests + 1):
        if i < start_test:
            continue
        print(f"\n{'=' * 70}\nTest {i}/{num_tests}\n{'=' * 70}")
        metrics = await execute_trial(
            benchmark=benchmark,
            llm=llm,
            difficulty=difficulty,
            test_id=i,
            instance=instances[i - 1],
            logger=logger,
            identity=identity,
            trial_timeout=trial_timeout,
            verbose=verbose,
        )
        results.append(metrics)

    # ---- summary ---------------------------------------------------------
    scored = [m for m in results if m is not None]
    if scored:
        solved = sum(1 for m in scored if m.success)
        print(f"\n{'=' * 70}")
        print(f"pass@1: {solved}/{len(scored)} "
              f"({solved / len(scored):.0%}) — metrics in "
              f"{logger.metrics_file}")
        print("=" * 70)
    return results, logger
