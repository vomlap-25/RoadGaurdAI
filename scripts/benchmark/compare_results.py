#!/usr/bin/env python3
"""Compare two JSON outputs from benchmark_video.py without inventing missing data."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", help="x86 baseline JSON")
    parser.add_argument("candidate", help="Graviton/COOL JSON")
    args = parser.parse_args()
    base = json.loads(Path(args.baseline).read_text())
    cand = json.loads(Path(args.candidate).read_text())
    same_hash = base["input"]["sha256"] == cand["input"]["sha256"]
    fields = [
        ("Mean latency (ms)", "latency_ms_mean", "lower_is_better"),
        ("P95 latency (ms)", "latency_ms_p95", "lower_is_better"),
        ("Throughput (frames/s)", "throughput_frames_per_second", "higher_is_better"),
        ("CPU % of total logical capacity", "process_cpu_percent_of_total_logical_capacity", "lower_is_better"),
    ]
    print(f"Same input SHA-256: {same_hash}")
    if not same_hash:
        print("WARNING: inputs differ; comparison is not apples-to-apples.")
    print(f"Baseline: {base['environment']['machine_architecture']} / {base['environment']['cpu_model']} / OpenCV {base['environment']['opencv_version']}")
    print(f"Candidate: {cand['environment']['machine_architecture']} / {cand['environment']['cpu_model']} / OpenCV {cand['environment']['opencv_version']}")
    print("Metric | Baseline | Candidate | Candidate vs baseline")
    print("---|---:|---:|---:")
    for label, key, direction in fields:
        b, c = base["run"][key], cand["run"][key]
        ratio = c / b if b else None
        change = "n/a" if ratio is None else f"{(ratio - 1) * 100:+.1f}%"
        print(f"{label} | {b:.4f} | {c:.4f} | {change}")
    bcost = base.get("cost_assumptions", {}).get("estimated_compute_usd_per_1000_frames")
    ccost = cand.get("cost_assumptions", {}).get("estimated_compute_usd_per_1000_frames")
    print(f"Compute cost / 1,000 frames (USD; caller-supplied hourly rates): {bcost if bcost is not None else 'not supplied'} | {ccost if ccost is not None else 'not supplied'}")


if __name__ == "__main__":
    main()
