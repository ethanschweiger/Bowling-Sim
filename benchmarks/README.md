# Simulation benchmark

This benchmark measures the pure Python trajectory integrator, one completed
60-foot throw per sample. It intentionally excludes HTTP, JSON serialization,
pin collision, scoring, lane wear, release sampling, and database I/O. The
goal is a reproducible measurement of the numerical model, not an end-to-end
API load test.

## Run it

From a fresh clone:

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd ..
python benchmarks/benchmark_simulation.py --throws 10000
```

The workload uses the bundled `reactive_pearl` ball, a fresh house-shot lane,
the production 0.05-foot integration stride, and 256 releases sampled ahead
of time from fixed seeds. It runs 100 unmeasured warmups by default. Timing
uses Python's monotonic high-resolution performance counter and reports the
median, nearest-rank p95, and aggregate throughput.

Use `--json` for machine-readable output. For comparisons, close heavy
background processes, keep the machine on power, and run the command at least
three times. Report every run or the median run; do not keep only the fastest.

## Recorded release result

The root README records one release run with its hardware, OS, Python version,
workload size, and date. Its captured stdout is in
[`results/2026-08-30-release.txt`](results/2026-08-30-release.txt). It is a local
development-machine measurement, not a production capacity claim. Results vary
with hardware, power state, interpreter, and background load.
