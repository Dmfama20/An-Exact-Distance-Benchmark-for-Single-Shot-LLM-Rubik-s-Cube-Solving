"""
Trial metrics schema and CSV logging.

One row per trial, with full per-call auditability: model identity as
echoed by the API, the actual per-request generation limit,
finish_reason, split token counts, and the transport retry trail.
Failure-mode analyses classify from these fields — never from global
token thresholds.
"""

import csv
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


@dataclass
class TrialMetrics:
    timestamp: str
    benchmark: str
    test_id: int
    difficulty: int      # verified instance difficulty (e.g. Rubik's d*)
    success: bool
    num_moves: Optional[int]
    tokens_used: Optional[int]
    time_seconds: float
    error: Optional[str]

    # Instance identity — the pairing key across configurations
    instance_id: Optional[str] = None
    size: Optional[str] = None          # e.g. "3x3x3"
    complexity: Optional[int] = None    # verified difficulty echo (= d*)
    similarity: Optional[float] = None  # benchmark-specific secondary metric
    parse_mode: Optional[str] = None    # "strict" | "lenient" | "none"
    lenient_rescue: Optional[bool] = None  # lenient parse would have solved

    # Model / run identity
    model_id: Optional[str] = None      # requested API model slug
    model_tag: Optional[str] = None     # human-readable configuration tag
    reasoning: Optional[bool] = None
    temperature: Optional[object] = None   # float, or "unset" when the
                                           # request omits the parameter
    backend: Optional[str] = None       # "openrouter" | "ollama" | "anthropic"

    # Paired-schedule provenance (populated by the paired driver)
    block_index: Optional[int] = None
    arm_order: Optional[str] = None      # e.g. "base-first" / "reason-first"
    schedule_hash: Optional[str] = None  # FULL sha256 of the schedule file
    run_config_hash: Optional[str] = None  # sha256 over the frozen run config

    # Per-call audit fields (populated from LLMResponse.audit_fields())
    finish_reason: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    requested_max_tokens: Optional[int] = None
    requested_reasoning: Optional[str] = None  # verbatim reasoning request
    retries: Optional[int] = None
    retry_errors: Optional[str] = None
    response_model: Optional[str] = None
    provider: Optional[str] = None
    system_fingerprint: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TrialLogger:
    """Appends metrics rows to a CSV and per-trial logs to a directory."""

    def __init__(self, benchmark: str, log_dir: str = "logs",
                 metrics_file: Optional[str] = None):
        self.benchmark = benchmark
        self.log_dir = log_dir
        self.timestamp = datetime.now(timezone.utc).strftime(
            "%Y%m%d_%H%M%S")
        os.makedirs(log_dir, exist_ok=True)

        if metrics_file:
            parent = os.path.dirname(metrics_file)
            if parent:
                os.makedirs(parent, exist_ok=True)
            self.metrics_file = metrics_file
        else:
            self.metrics_file = os.path.join(
                log_dir, f"{benchmark}_metrics_{self.timestamp}.csv")

        self.detailed_log_dir = os.path.join(
            log_dir, f"{benchmark}_{self.timestamp}")
        os.makedirs(self.detailed_log_dir, exist_ok=True)

        # Write the header only for new files; existing files are resumed.
        if not os.path.exists(self.metrics_file):
            fields = list(TrialMetrics(
                timestamp="", benchmark="", test_id=0, difficulty=0,
                success=False, num_moves=None, tokens_used=None,
                time_seconds=0.0, error=None).to_dict().keys())
            with open(self.metrics_file, "w", newline="") as fh:
                csv.DictWriter(fh, fieldnames=fields).writeheader()

    def log_metrics(self, metrics: TrialMetrics):
        with open(self.metrics_file, "a", newline="") as fh:
            writer = csv.DictWriter(
                fh, fieldnames=list(metrics.to_dict().keys()))
            writer.writerow(metrics.to_dict())

    def log_detailed_test(self, test_id: int, content: str):
        path = os.path.join(self.detailed_log_dir, f"test_{test_id:03d}.log")
        with open(path, "w") as fh:
            fh.write(content)

    def log_harness_error(self, metrics: TrialMetrics):
        """Record an infrastructure/harness failure (never a model outcome).

        Harness errors abort the cell; their presence is checked by the
        analysis gate, and they must never become scored trial rows.
        """
        sidecar = self.metrics_file + ".harness_errors.csv"
        row = metrics.to_dict()
        new_file = not os.path.exists(sidecar)
        with open(sidecar, "a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(row.keys()))
            if new_file:
                writer.writeheader()
            writer.writerow(row)

    def log_invalid_attempt(self, metrics: TrialMetrics, attempt: int):
        """Record a transport-level failed attempt to the sidecar CSV.

        Invalid attempts are retried on the same instance and must never
        appear in the main metrics CSV (one valid row per trial), but they
        are kept for provenance and cost accounting.
        """
        sidecar = self.metrics_file + ".invalid.csv"
        row = metrics.to_dict()
        row["invalid_attempt"] = attempt
        new_file = not os.path.exists(sidecar)
        with open(sidecar, "a", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(row.keys()))
            if new_file:
                writer.writeheader()
            writer.writerow(row)
