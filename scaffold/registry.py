"""
Benchmark plugin registry.

A benchmark is a self-contained package under ``benchmarks/<name>/`` whose
``__init__`` exposes a module-level ``BENCHMARK`` object (an instance of
:class:`Benchmark`).  The scaffold discovers it by name at runtime; adding
a new benchmark requires no change to the scaffold.

Design contract (kept deliberately small):

- ``solve`` is an async callable with the signature
  ``solve(llm, difficulty, test_id, verbose, instance) -> dict``.
  It performs exactly one evaluated trial and returns a result dict whose
  keys map onto the metrics schema (see scaffold.metrics.TrialMetrics);
  unknown keys are ignored.
- ``select_instances(manifest_data, difficulty)`` returns the ordered list
  of manifest instances for one difficulty cell.  Trial i of every
  configuration receives the i-th instance, so pairing across
  configurations holds by construction.
- Benchmarks are manifest-only: instances are pre-generated, hashed, and
  committed before any model is run.  The scaffold refuses to run without
  a manifest.  (Runtime instance generation
  is how unpaired arms and irreproducible cells happen.)
"""

from dataclasses import dataclass
from importlib import import_module
from typing import Awaitable, Callable, Dict, List, Optional


@dataclass(frozen=True)
class Benchmark:
    name: str
    solve: Callable[..., Awaitable[dict]]
    select_instances: Callable[[dict, int], List[dict]]
    difficulty_help: str


def load_benchmark(name: str) -> Benchmark:
    try:
        module = import_module(f"benchmarks.{name}")
    except ModuleNotFoundError as exc:
        raise SystemExit(
            f"Unknown benchmark {name!r} (no package benchmarks/{name}/): {exc}")
    bench: Optional[Benchmark] = getattr(module, "BENCHMARK", None)
    if not isinstance(bench, Benchmark):
        raise SystemExit(
            f"benchmarks/{name}/__init__.py must expose a module-level "
            f"BENCHMARK = scaffold.registry.Benchmark(...)")
    return bench
