"""Video processing, tracking, preliminary severity, and crop reanalysis."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import cv2
import numpy as np

from app.vision.detector import Detector, ModelNotConfiguredError, OpenCVDNNDetector, RawDetection

# Override with configure_detector() in tests or when embedding the pipeline.
_detector_override: Detector | None = None


def configure_detector(detector: Detector | None) -> None:
    """Set an injected detector. Pass None to return to environment configuration."""
    global _detector_override
    _detector_override = detector


def _get_detector() -> Detector:
    if _detector_override is not None:
        return _detector_override
    model_path = os.getenv("ROADGUARD_ONNX_MODEL", "models/road_damage.onnx")
    class_names = tuple(
        name.strip() for name in os.getenv("ROADGUARD_CLASS_NAMES", "pothole,crack").split(",") if name.strip()
    )
    try:
        return OpenCVDNNDetector(
            model_path,
            class_names=class_names,
            input_size=int(os.getenv("ROADGUARD_MODEL_INPUT_SIZE", "640")),
            confidence_threshold=float(os.getenv("ROADGUARD_CONFIDENCE_THRESHOLD", "0.25")),
        )
    except ModelNotConfiguredError:
        raise
    except (ValueError, cv2.error) as exc:
        raise RuntimeError(f"Could not initialize the configured ONNX detector: {exc}") from exc


def image_quality(frame: np.ndarray) -> dict[str, float | bool]:
    """Return simple, transparent blur and brightness signals (not a classifier)."""
    if frame is None or frame.size == 0:
        return {"is_blurry": True, "is_dark": True, "laplacian_variance": 0.0, "mean_brightness": 0.0}
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    variance = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(np.mean(gray))
    return {
        "is_blurry": variance < float(os.getenv("ROADGUARD_BLUR_THRESHOLD", "35")),
        "is_dark": brightness < float(os.getenv("ROADGUARD_DARK_THRESHOLD", "35")),
        "laplacian_variance": variance,
        "mean_brightness": brightness,
    }


def preprocess_frame(frame: np.ndarray, max_dimension: int = 1280) -> np.ndarray:
    """Resize large frames while preserving aspect ratio; return an independent image."""
    if frame is None or frame.size == 0:
        raise ValueError("Cannot preprocess an empty frame")
    height, width = frame.shape[:2]
    scale = min(1.0, max_dimension / max(height, width))
    if scale < 1.0:
        return cv2.resize(frame, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_AREA)
    return frame.copy()


def intersection_over_union(a: Sequence[float], b: Sequence[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    iw, ih = max(0.0, min(ax2, bx2) - max(ax1, bx1)), max(0.0, min(ay2, by2) - max(ay1, by1))
    intersection = iw * ih
    union = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1) + max(0.0, bx2 - bx1) * max(0.0, by2 - by1) - intersection
    return intersection / union if union > 0 else 0.0


def score_severity(frame: np.ndarray, detection: Mapping[str, Any]) -> dict[str, float | str]:
    """Preliminary score: 60% detector confidence + 40% bbox-area estimate.

    Area is the bounding-box area divided by frame area, not segmented damage area.
    The area contribution saturates at a 10% frame-area ratio. This is a heuristic,
    not a validated road-safety rating.
    """
    if frame is None or frame.size == 0:
        raise ValueError("score_severity requires a non-empty frame")
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in detection["bbox"]]
    area_ratio = max(0.0, min(1.0, (max(0.0, x2 - x1) * max(0.0, y2 - y1)) / (width * height)))
    confidence = max(0.0, min(1.0, float(detection.get("confidence", 0.0))))
    area_component = min(area_ratio / 0.10, 1.0)
    score = 0.60 * confidence + 0.40 * area_component
    level = "high" if score >= 0.70 else "medium" if score >= 0.40 else "low"
    return {"score": round(score, 4), "level": level, "area_ratio": round(area_ratio, 6)}


def _gps_at(gps_track: Any, timestamp: float) -> dict[str, float] | None:
    """Choose the nearest timestamped GPS point; accept a single lat/lon mapping too."""
    if gps_track is None:
        return None
    if isinstance(gps_track, Mapping):
        if "lat" in gps_track and "lon" in gps_track:
            return {"lat": float(gps_track["lat"]), "lon": float(gps_track["lon"])}
        points = gps_track.get("points", [])
    else:
        points = gps_track
    best: tuple[float, Mapping[str, Any]] | None = None
    for point in points or []:
        if not isinstance(point, Mapping) or "lat" not in point or "lon" not in point:
            continue
        point_time = float(point.get("timestamp", point.get("time", timestamp)))
        distance = abs(point_time - timestamp)
        if best is None or distance < best[0]:
            best = (distance, point)
    if best is None:
        return None
    return {"lat": float(best[1]["lat"]), "lon": float(best[1]["lon"])}


def _save_evidence(frame: np.ndarray, directory: Path, frame_id: int, bbox: Sequence[int]) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    x1, y1, x2, y2 = [int(v) for v in bbox]
    annotated = frame.copy()
    cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 180, 255), 2)
    path = directory / f"frame_{frame_id:06d}.jpg"
    if not cv2.imwrite(str(path), annotated):
        raise OSError(f"Failed to save evidence frame: {path}")
    return str(path.as_posix())


def _track_detections(current: list[dict[str, Any]], previous: list[dict[str, Any]], next_id: int, threshold: float = 0.30) -> int:
    """Greedy class-aware IoU association; update track ids in place."""
    candidates: list[tuple[float, int, int]] = []
    for ci, cur in enumerate(current):
        for pi, prev in enumerate(previous):
            if cur["class"] == prev["class"]:
                overlap = intersection_over_union(cur["bbox"], prev["bbox"])
                if overlap >= threshold:
                    candidates.append((overlap, ci, pi))
    used_current: set[int] = set()
    used_previous: set[int] = set()
    for _, ci, pi in sorted(candidates, reverse=True):
        if ci in used_current or pi in used_previous:
            continue
        current[ci]["track_id"] = previous[pi]["track_id"]
        used_current.add(ci)
        used_previous.add(pi)
    for index, detection in enumerate(current):
        if index not in used_current:
            detection["track_id"] = next_id
            next_id += 1
    return next_id


def analyze_video(path: str | Path, gps_track: Any = None) -> list[dict[str, Any]]:
    """Read a video, run the configured detector, track detections, and save evidence.

    GPS accepts a {"lat": ..., "lon": ...} mapping or an iterable of mappings with
    lat/lon and optional timestamp (seconds). No synthetic detections are generated.
    """
    video_path = Path(path)
    if not video_path.is_file():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    detector = _get_detector()  # Fail clearly before processing if no real model is configured.
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise ValueError(f"Could not open video file: {video_path}")
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    if not np.isfinite(fps) or fps <= 0:
        fps = 30.0
    output_root = Path(os.getenv("ROADGUARD_EVIDENCE_DIR", "artifacts/evidence")) / video_path.stem
    all_detections: list[dict[str, Any]] = []
    previous: list[dict[str, Any]] = []
    next_track_id = 1
    frame_id = 0
    try:
        while True:
            ok, raw_frame = capture.read()
            if not ok:
                break
            frame_id += 1
            timestamp = (frame_id - 1) / fps
            frame = preprocess_frame(raw_frame)
            # Quality measurements are included in the detection record for downstream policy.
            quality = image_quality(frame)
            raw_detections = detector.predict(frame)
            current: list[dict[str, Any]] = []
            for raw in raw_detections:
                bbox = [int(v) for v in raw.bbox]
                if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                    continue
                record: dict[str, Any] = {
                    "track_id": 0,
                    "frame_id": frame_id,
                    "timestamp": round(timestamp, 3),
                    "gps": _gps_at(gps_track, timestamp),
                    "class": str(raw.class_name),
                    "bbox": bbox,
                    "confidence": round(float(raw.confidence), 5),
                    "severity": {},
                    "evidence_path": "",
                    "image_quality": quality,
                }
                record["severity"] = score_severity(frame, record)
                current.append(record)
            next_track_id = _track_detections(current, previous, next_track_id)
            for record in current:
                record["evidence_path"] = _save_evidence(frame, output_root, frame_id, record["bbox"])
                all_detections.append(record)
            previous = current
    finally:
        capture.release()
    if frame_id == 0:
        raise ValueError("Video contains no decodable frames; check that the file is a supported, non-empty video")
    return all_detections


def reanalyze(frame: np.ndarray, bbox: Sequence[int], mode: str = "tiled") -> dict[str, Any] | None:
    """Re-run detection on a bbox crop, optionally enlarged or split into tiles.

    Returns the highest-confidence detection in original-frame coordinates, or None.
    Supported modes: ``tiled`` (crop and scan overlapping tiles) and ``hires`` (enlarge crop).
    """
    if frame is None or frame.size == 0:
        raise ValueError("reanalyze requires a non-empty frame")
    if mode not in {"tiled", "hires"}:
        raise ValueError("mode must be 'tiled' or 'hires'")
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = [int(round(float(v))) for v in bbox]
    x1, x2 = max(0, x1), min(width, x2)
    y1, y2 = max(0, y1), min(height, y2)
    if x2 <= x1 or y2 <= y1:
        raise ValueError("bbox must describe a non-empty region within the frame")
    detector = _get_detector()
    crop = frame[y1:y2, x1:x2].copy()
    regions: list[tuple[int, int, np.ndarray]] = []
    hires_scale = 1.0
    if mode == "hires":
        hires_scale = min(4.0, max(1.0, 640.0 / max(crop.shape[:2])))
        enlarged = cv2.resize(crop, None, fx=hires_scale, fy=hires_scale, interpolation=cv2.INTER_CUBIC)
        regions.append((x1, y1, enlarged))
    else:
        # Four overlapping tiles improve small-defect visibility while keeping work bounded.
        ch, cw = crop.shape[:2]
        tile_w, tile_h = max(1, int(cw * 0.65)), max(1, int(ch * 0.65))
        starts_x = sorted(set([0, max(0, cw - tile_w)]))
        starts_y = sorted(set([0, max(0, ch - tile_h)]))
        for ty in starts_y:
            for tx in starts_x:
                regions.append((x1 + tx, y1 + ty, crop[ty:ty + tile_h, tx:tx + tile_w]))
    found: list[dict[str, Any]] = []
    for ox, oy, region in regions:
        rh, rw = region.shape[:2]
        for detection in detector.predict(region):
            bx1, by1, bx2, by2 = detection.bbox
            if mode == "hires":
                # Map enlarged-crop coordinates back to the original frame.
                bx1, bx2 = bx1 / hires_scale, bx2 / hires_scale
                by1, by2 = by1 / hires_scale, by2 / hires_scale
            mapped = [
                max(0, min(width, int(round(ox + bx1)))),
                max(0, min(height, int(round(oy + by1)))),
                max(0, min(width, int(round(ox + bx2)))),
                max(0, min(height, int(round(oy + by2)))),
            ]
            if mapped[2] <= mapped[0] or mapped[3] <= mapped[1]:
                continue
            item: dict[str, Any] = {"class": detection.class_name, "bbox": mapped, "confidence": float(detection.confidence)}
            item["severity"] = score_severity(frame, item)
            item["mode"] = mode
            found.append(item)
    return max(found, key=lambda d: d["confidence"]) if found else None
