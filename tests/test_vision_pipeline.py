from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from app.vision.detector import ModelNotConfiguredError, OpenCVDNNDetector, RawDetection
from app.vision.pipeline import (
    analyze_video,
    configure_detector,
    image_quality,
    intersection_over_union,
    preprocess_frame,
    reanalyze,
    score_severity,
)


class MockDetector:
    """Explicit test fixture; these results are not real model predictions."""
    def __init__(self):
        self.calls = 0

    def predict(self, image):
        self.calls += 1
        h, w = image.shape[:2]
        if self.calls == 1:
            return [RawDetection("pothole", (w // 4, h // 4, w // 2, h // 2), 0.72)]
        return [RawDetection("pothole", (w // 4 + 1, h // 4, w // 2 + 1, h // 2), 0.75)]


def test_preprocess_resizes_and_preserves_aspect_ratio():
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    resized = preprocess_frame(frame, max_dimension=640)
    assert resized.shape[:2] == (360, 640)
    assert resized is not frame


def test_iou_and_severity_are_bounded_and_transparent():
    assert intersection_over_union([0, 0, 10, 10], [0, 0, 10, 10]) == 1.0
    assert intersection_over_union([0, 0, 10, 10], [20, 20, 30, 30]) == 0.0
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    result = score_severity(frame, {"bbox": [0, 0, 50, 50], "confidence": 0.5})
    assert result == {"score": 0.7, "level": "high", "area_ratio": 0.25}


def test_quality_checks_return_blur_and_brightness_signals():
    result = image_quality(np.zeros((32, 32, 3), dtype=np.uint8))
    assert result["is_dark"] is True
    assert result["is_blurry"] is True


def test_analyze_video_tracks_detections_and_saves_evidence(tmp_path, monkeypatch):
    video_path = tmp_path / "sample.avi"
    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"MJPG"), 5.0, (64, 48))
    assert writer.isOpened()
    for i in range(3):
        frame = np.full((48, 64, 3), i * 30, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    monkeypatch.setenv("ROADGUARD_EVIDENCE_DIR", str(tmp_path / "evidence"))
    configure_detector(MockDetector())
    try:
        records = analyze_video(video_path, [{"timestamp": 0.0, "lat": 12.9, "lon": 77.5}])
    finally:
        configure_detector(None)
    assert len(records) == 3
    assert records[0]["track_id"] == records[1]["track_id"] == records[2]["track_id"]
    assert records[0]["gps"] == {"lat": 12.9, "lon": 77.5}
    assert records[0]["class"] == "pothole"
    assert records[0]["severity"]["score"] > 0
    assert all(Path(item["evidence_path"]).is_file() for item in records)


def test_reanalyze_uses_injected_detector_and_maps_crop_coordinates():
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    class CropDetector:
        def predict(self, image):
            return [RawDetection("crack", (2, 3, 10, 12), 0.8)]
    configure_detector(CropDetector())
    try:
        result = reanalyze(frame, [20, 30, 60, 70], mode="tiled")
    finally:
        configure_detector(None)
    assert result is not None
    assert result["class"] == "crack"
    assert result["bbox"] == [22, 33, 30, 42]
    assert result["mode"] == "tiled"


def test_no_model_is_an_explicit_blocker(tmp_path, monkeypatch):
    monkeypatch.setenv("ROADGUARD_ONNX_MODEL", str(tmp_path / "missing.onnx"))
    with pytest.raises(ModelNotConfiguredError, match="no model is downloaded automatically"):
        OpenCVDNNDetector(tmp_path / "missing.onnx")


def test_analyze_video_rejects_video_with_no_decodable_frames(tmp_path, monkeypatch):
    video_path = tmp_path / "corrupt.avi"
    video_path.write_bytes(b"not a valid video stream")

    class EmptyCapture:
        def __init__(self, _path):
            self.released = False
        def isOpened(self):
            return True
        def get(self, _property):
            return 30.0
        def read(self):
            return False, None
        def release(self):
            self.released = True

    capture = EmptyCapture(video_path)
    monkeypatch.setattr("app.vision.pipeline.cv2.VideoCapture", lambda _path: capture)
    configure_detector(MockDetector())
    try:
        with pytest.raises(ValueError, match="no decodable frames"):
            analyze_video(video_path)
    finally:
        configure_detector(None)
    assert capture.released is True
