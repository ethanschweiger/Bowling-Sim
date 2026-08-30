#!/usr/bin/env python3
"""Measure the pure bowling trajectory simulation with reproducible inputs.

Run from the repository root after installing ``backend/requirements.txt``:

    python benchmarks/benchmark_simulation.py --throws 10000

Setup, release sampling, and result formatting are deliberately outside the
timed region. Each timed sample is one call to ``simulate_throw`` using a
precomputed release and an unchanged lane snapshot.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import statistics
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.physics.ball import BALL_CATALOG  # noqa: E402
from app.physics.lane import LaneCondition  # noqa: E402
from app.physics.simulate import STEP_FT, simulate_throw  # noqa: E402
from app.physics.throw import Throw, sample_release  # noqa: E402


@dataclass(frozen=True)
class BenchmarkResult:
    recorded_at_utc: str
    machine: str
    operating_system: str
    python: str
    ball: str
    integration_stride_ft: float
    throws: int
    warmup_throws: int
    median_ms: float
    p95_ms: float
    throughput_per_second: float


def _machine_name() -> str:
    if platform.system() == "Darwin":
        try:
            value = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if value:
                return value
        except (OSError, subprocess.CalledProcessError):
            pass
    return platform.processor() or platform.machine() or "unknown"


def _percentile_nearest_rank(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def run_benchmark(throws: int, warmup_throws: int) -> BenchmarkResult:
    if throws < 1:
        raise ValueError("--throws must be at least 1")
    if warmup_throws < 0:
        raise ValueError("--warmup must be non-negative")

    ball = BALL_CATALOG["reactive_pearl"]
    lane = LaneCondition.house_shot()
    requested = Throw()
    releases = tuple(sample_release(requested, seed)[0] for seed in range(256))

    for index in range(warmup_throws):
        simulate_throw(ball, releases[index % len(releases)], lane)

    latencies_ms: list[float] = []
    run_started_ns = time.perf_counter_ns()
    for index in range(throws):
        sample_started_ns = time.perf_counter_ns()
        result = simulate_throw(ball, releases[index % len(releases)], lane)
        sample_finished_ns = time.perf_counter_ns()
        if not result.terminal.reached_pin_deck:
            raise RuntimeError("simulation stopped before reaching the pin deck")
        latencies_ms.append((sample_finished_ns - sample_started_ns) / 1_000_000)
    elapsed_seconds = (time.perf_counter_ns() - run_started_ns) / 1_000_000_000

    return BenchmarkResult(
        recorded_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        machine=_machine_name(),
        operating_system=f"{platform.system()} {platform.release()} ({platform.machine()})",
        python=platform.python_version(),
        ball=ball.id,
        integration_stride_ft=STEP_FT,
        throws=throws,
        warmup_throws=warmup_throws,
        median_ms=statistics.median(latencies_ms),
        p95_ms=_percentile_nearest_rank(latencies_ms, 0.95),
        throughput_per_second=throws / elapsed_seconds,
    )


def _print_human_readable(result: BenchmarkResult) -> None:
    print(f"Recorded (UTC): {result.recorded_at_utc}")
    print(f"Machine: {result.machine}")
    print(f"OS: {result.operating_system}")
    print(f"Python: {result.python}")
    print(f"Ball: {result.ball}")
    print(f"Integration stride: {result.integration_stride_ft:.2f} ft")
    print(f"Throws: {result.throws:,}")
    print(f"Warmup throws: {result.warmup_throws:,}")
    print(f"Median simulation latency: {result.median_ms:.3f} ms")
    print(f"p95 simulation latency: {result.p95_ms:.3f} ms")
    print(f"Throughput: {result.throughput_per_second:,.1f} simulations/sec")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--throws", type=int, default=10_000, help="measured throws")
    parser.add_argument("--warmup", type=int, default=100, help="unmeasured warmup throws")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args()

    try:
        result = run_benchmark(args.throws, args.warmup)
    except ValueError as error:
        parser.error(str(error))

    if args.json:
        print(json.dumps(asdict(result), indent=2))
    else:
        _print_human_readable(result)


if __name__ == "__main__":
    main()
