#!/usr/bin/env python3
"""
CLI entry point for the benchmark scaffold.

Example (Rubik's, verified optimal distance d* = 5):

    python run_benchmark.py --benchmark rubiks --difficulty 5 --tests 50 \\
        --manifest instances/rubiks_dstar_manifest_v1.json \\
        --model openai/gpt-5.5 --reasoning-effort minimal

Every trial is one completed API call (transport retries are logged per
trial), evaluated deterministically, and written to the metrics CSV with
full per-call audit fields.
"""

import argparse
import asyncio
import sys

from scaffold.registry import load_benchmark
from scaffold.runner import run_benchmark


async def main():
    parser = argparse.ArgumentParser(
        description="Single-shot LLM planning benchmarks "
                    "(manifest-paired, deterministic verification)")
    parser.add_argument("--benchmark", type=str, required=True,
                        help="Benchmark plugin name (package under "
                             "benchmarks/), e.g. 'rubiks'")
    parser.add_argument("--manifest", type=str, required=True,
                        help="Pre-generated instance manifest. Trial i "
                             "always uses the manifest's i-th instance of "
                             "the requested difficulty, so all "
                             "configurations are instance-paired by "
                             "construction.")
    parser.add_argument("--difficulty", type=int, required=True,
                        help="Difficulty cell to run (benchmark-specific; "
                             "see the plugin's difficulty_help)")
    parser.add_argument("--tests", type=int, default=5,
                        help="Number of trials (default: 5)")
    parser.add_argument("--model", type=str, required=True,
                        help="Model slug ('vendor/model' for OpenRouter, "
                             "bare id for direct Anthropic)")
    parser.add_argument("--temperature", type=float, default=None,
                        help="omit for models without sampling-parameter "
                             "support (GPT-5.x); None = not sent")
    parser.add_argument("--enable-reasoning", action="store_true",
                        help="Enable the reasoning mode (explicit thinking "
                             "budget)")
    parser.add_argument("--reasoning-max-tokens", type=int, default=2000)
    parser.add_argument("--reasoning-effort", type=str, default=None,
                        choices=["none", "minimal", "low", "medium",
                                 "high", "xhigh"],
                        help="Reasoning effort level (categorical control; "
                             "'none' disables reasoning where supported)")
    parser.add_argument("--require-parameters", action="store_true",
                        help="OpenRouter: refuse providers that do not "
                             "support every requested parameter")
    parser.add_argument("--no-fallbacks", action="store_true",
                        help="OpenRouter: disable cross-provider fallback "
                             "routing")
    parser.add_argument("--provider-order", type=str, default=None,
                        help="OpenRouter: comma-separated provider "
                             "preference list (pins routing)")
    parser.add_argument("--max-tokens", type=int, default=None,
                        help="Generation ceiling (default: 32000)")
    parser.add_argument("--model-tag", type=str, default=None,
                        help="Configuration tag written to the model_tag "
                             "CSV column (e.g. 'gpt55-base')")
    parser.add_argument("--metrics-file", type=str, default=None,
                        help="Explicit metrics CSV path; existing files "
                             "are appended to (resume mode)")
    parser.add_argument("--start-test", type=int, default=1,
                        help="Resume from this 1-indexed trial")
    parser.add_argument("--trial-timeout", type=int, default=None,
                        help="Hard wall-clock limit per trial in seconds")
    parser.add_argument("--ollama", action="store_true",
                        help="Use a local Ollama server instead of a cloud "
                             "API")
    parser.add_argument("--ollama-base-url", type=str,
                        default="http://ollama:11434")
    parser.add_argument("--quiet", action="store_true")

    args = parser.parse_args()
    benchmark = load_benchmark(args.benchmark)

    results, _logger = await run_benchmark(
        benchmark=benchmark,
        manifest_path=args.manifest,
        difficulty=args.difficulty,
        num_tests=args.tests,
        model=args.model,
        temperature=args.temperature,
        enable_reasoning=args.enable_reasoning,
        reasoning_max_tokens=args.reasoning_max_tokens,
        reasoning_effort=args.reasoning_effort,
        max_tokens=args.max_tokens,
        verbose=not args.quiet,
        model_tag=args.model_tag,
        use_ollama=args.ollama,
        ollama_base_url=args.ollama_base_url,
        metrics_file=args.metrics_file,
        start_test=args.start_test,
        trial_timeout=args.trial_timeout,
        require_parameters=args.require_parameters,
        allow_fallbacks=not args.no_fallbacks,
        provider_order=(args.provider_order.split(",")
                        if args.provider_order else None),
    )
    # Low pass@1 is a valid scientific result — exit 0 when the cell
    # completed normally so sweep sentinels are written.
    return 0 if results is not None else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n\nBenchmark interrupted by user")
        sys.exit(1)
