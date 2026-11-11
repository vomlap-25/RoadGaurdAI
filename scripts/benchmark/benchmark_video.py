#!/usr/bin/env python3
"""Reproducible OpenCV preprocessing benchmark on a real input video.

This deliberately benchmarks decode + resize + grayscale + blur + Canny + contours,
not the full ONNX detector/agent. It emits measured values only; hourly compute cost
is estimated only when the caller supplies a rate for the instance being tested.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import json
import os
import platform
import statistics
import subprocess
import time
from pathlib import Path

import cv2
import psutil


def cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(errors="ignore").splitlines():
            # `processor: 0` is a logical CPU index on some ARM hosts, not a model.
            if line.lower().startswith(("model name", "hardware", "cpu model", "cpu implementer")) and ":" in line:
                value = line.split(":", 1)[1].strip()
                if value:
                    return value
    except OSError:
        pass
    return platform.processor() or "unknown"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def process_frame(frame, max_width: int):
    height, width = frame.shape[:2]
    if width > max_width:
        new_height = max(1, round(height * max_width / width))
        frame = cv2.resize(frame, (max_width, new_height), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)


def run(args) -> dict:
    video = Path(args.video).resolve()
    if not video.is_file():
        raise SystemExit(f"Input video does not exist: {video}")
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"OpenCV could not open input video: {video}")
    source_fps = cap.get(cv2.CAP_PROP_FPS)
    source_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    source_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    latencies: list[float] = []
    warmup_done = 0
    measured = 0
    process = psutil.Process(os.getpid())
    cpu_start = None
    wall_start = None
    try:
        while measured < args.max_frames:
            if warmup_done < args.warmup:
                ok, frame = cap.read()
                if not ok:
                    break
                process_frame(frame, args.max_width)
                warmup_done += 1
                continue
            if wall_start is None:
                # Exclude warmup from throughput/CPU denominators. Each latency
                # includes decode plus the exact same preprocessing pipeline.
                wall_start = time.perf_counter()
                cpu_start = process.cpu_times()
            frame_start = time.perf_counter_ns()
            ok, frame = cap.read()
            if not ok:
                break
            process_frame(frame, args.max_width)
            latencies.append((time.perf_counter_ns() - frame_start) / 1_000_000)
            measured += 1
    finally:
        cap.release()
    wall_elapsed = time.perf_counter() - wall_start if wall_start is not None else 0.0
    cpu_end = process.cpu_times()
    cpu_seconds = ((cpu_end.user + cpu_end.system) - (cpu_start.user + cpu_start.system)) if cpu_start else 0.0
    if not latencies:
        raise SystemExit("No frames were measured; increase --max-frames or check the video")
    throughput = len(latencies) / wall_elapsed if wall_elapsed else 0.0
    cpu_one_core = 100.0 * cpu_seconds / wall_elapsed if wall_elapsed else 0.0
    cpu_total_machine = cpu_one_core / max(1, psutil.cpu_count(logical=True) or 1)
    ordered_latencies = sorted(latencies)
    p95_index = max(0, min(len(ordered_latencies) - 1, math.ceil(0.95 * len(ordered_latencies)) - 1))
    hourly_cost = args.hourly_cost_usd
    result = {
        "benchmark": "opencv_preprocessing_video_v1",
        "scope": "per-frame latency includes video decode plus resize, grayscale, Gaussian blur, Canny edges, and contour extraction; throughput and CPU exclude warmup; excludes ONNX inference and agent/storage/network latency",
        "input": {"path_basename": video.name, "sha256": sha256(video), "bytes": video.stat().st_size,
                  "width": source_width, "height": source_height, "source_fps": source_fps if source_fps > 0 else None},
        "run": {"warmup_frames": warmup_done, "measured_frames": len(latencies), "max_width": args.max_width,
                "elapsed_seconds": wall_elapsed, "latency_ms_mean": statistics.mean(latencies),
                "latency_ms_median": statistics.median(latencies),
                "latency_ms_p95": ordered_latencies[p95_index],
                "throughput_frames_per_second": throughput,
                "process_cpu_percent_one_core_normalized": cpu_one_core,
                "process_cpu_percent_of_total_logical_capacity": cpu_total_machine},
        "environment": {"python": platform.python_version(), "platform": platform.platform(),
                        "machine_architecture": platform.machine(), "cpu_model": cpu_model(),
                        "logical_cpu_count": psutil.cpu_count(logical=True), "opencv_version": cv2.__version__,
                        "opencv_build_information_first_lines": cv2.getBuildInformation().splitlines()[:12]},
        "cost_assumptions": {"instance_hourly_usd_input": hourly_cost,
                             "estimated_compute_usd_per_1000_frames": (hourly_cost / (throughput * 3600) * 1000) if hourly_cost is not None and throughput > 0 else None,
                             "excludes_storage_network_marketplace_tax_and_idle_time": True},
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", help="Path to the same input video on each comparison machine")
    parser.add_argument("--max-frames", type=int, default=1000, help="Maximum measured frames (default: 1000)")
    parser.add_argument("--warmup", type=int, default=30, help="Frames processed before timing (default: 30)")
    parser.add_argument("--max-width", type=int, default=640, help="Resize wider frames to this width (default: 640)")
    parser.add_argument("--hourly-cost-usd", type=float, default=None, help="Your region/OS/instance hourly rate in USD; not fetched automatically")
    parser.add_argument("--output", default="benchmark-result.json", help="Output JSON path")
    args = parser.parse_args()
    if args.max_frames < 1 or args.warmup < 0 or args.max_width < 1:
        parser.error("--max-frames and --max-width must be positive; --warmup cannot be negative")
    if args.hourly_cost_usd is not None and args.hourly_cost_usd < 0:
        parser.error("--hourly-cost-usd cannot be negative")
    result = run(args)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, allow_nan=False))
    print(f"\nWrote measured results to: {output.resolve()}")


if __name__ == "__main__":
    main()
