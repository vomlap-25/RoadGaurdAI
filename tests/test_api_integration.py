"""Endpoint/integration tests use an explicitly mocked vision analyzer, not a real model."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.storage import SQLiteRepository


@pytest.fixture
def client(tmp_path, monkeypatch):
    app.state.repository = SQLiteRepository(tmp_path / "roadguard-test.sqlite3")
    app.state.video_analyzer = lambda path, gps: [
        {
            "track_id": 7,
            "frame_id": 1,
            "timestamp": 0.0,
            "gps": gps if isinstance(gps, dict) else None,
            "class": "pothole",
            "bbox": [10, 10, 80, 70],
            "confidence": 0.92,
            "severity": {"score": 0.88, "level": "high", "area_ratio": 0.12},
            "evidence_path": "artifacts/evidence/mock/frame_000001.jpg",
        }
    ]
    app.state.detection_processor = None
    # The endpoint uses its real deterministic agent by default when this is None.
    delattr(app.state, "detection_processor") if hasattr(app.state, "detection_processor") else None
    with TestClient(app) as test_client:
        yield test_client
    for attr in ("video_analyzer", "detection_processor"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def upload(client: TestClient, *, filename="road.mp4", content_type="video/mp4", gps_track=None):
    data = {"gps_track": gps_track} if gps_track is not None else {}
    return client.post("/analyze", files={"video": (filename, b"mock-video-bytes", content_type)}, data=data)


def test_analyze_persists_records_and_trace(client):
    response = upload(client, gps_track='{"lat":12.9716,"lon":77.5946}')
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "completed"
    assert body["detection_count"] == 1
    assert body["issue_count"] == 1
    issue_id = body["issues"][0]["issue_id"]
    assert body["issues"][0]["status"] == "human_review_required"
    assert uuid.UUID(issue_id)

    locations = client.get("/locations")
    assert locations.status_code == 200
    assert locations.json()["count"] == 1
    assert locations.json()["locations"][0]["issue_id"] == issue_id

    trace = client.get(f"/trace/{issue_id}")
    assert trace.status_code == 200
    assert trace.json()["count"] >= 1
    assert all({"evidence", "decision", "action", "outcome"}.issubset(item) for item in trace.json()["trace"])


def test_human_review_is_persisted_and_traced(client):
    issue_id = upload(client).json()["issues"][0]["issue_id"]
    response = client.post(f"/locations/{issue_id}/review", json={"decision": "approved", "reviewer": "operator-1", "notes": "Verified on site"})
    assert response.status_code == 200, response.text
    assert response.json()["location"]["status"] == "approved"
    trace = client.get(f"/trace/{issue_id}").json()["trace"]
    assert trace[-1]["decision"] == "human_review_submitted"
    assert trace[-1]["action"] == "approved"
    assert client.post(f"/locations/{issue_id}/review", json={"decision": "rejected"}).status_code == 409


def test_rejects_unsupported_extension(client):
    response = upload(client, filename="payload.exe", content_type="application/octet-stream")
    assert response.status_code == 415


def test_rejects_bad_gps_payload(client):
    response = upload(client, gps_track='{"lat":999,"lon":0}')
    assert response.status_code == 422


def test_human_rejection_is_persisted_and_traced(client):
    issue_id = upload(client).json()["issues"][0]["issue_id"]
    response = client.post(f"/locations/{issue_id}/review", json={"decision": "rejected", "reviewer": "operator-2", "notes": "not road damage"})
    assert response.status_code == 200, response.text
    assert response.json()["location"]["status"] == "rejected"
    trace = client.get(f"/trace/{issue_id}").json()["trace"]
    assert trace[-1]["decision"] == "human_review_submitted"
    assert trace[-1]["action"] == "rejected"


def test_review_validation_and_unknown_ids(client):
    assert client.post("/locations/not-a-uuid/review", json={"decision": "approved"}).status_code == 400
    assert client.post(f"/locations/{uuid.uuid4()}/review", json={"decision": "maybe"}).status_code == 422
    assert client.get(f"/trace/{uuid.uuid4()}").status_code == 404


def test_locations_rejects_invalid_limit_and_status(client):
    assert client.get("/locations?limit=0").status_code == 422
    assert client.get("/locations?status=banana").status_code == 422


def test_health_checks_storage(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_model_not_configured_returns_service_unavailable(client, monkeypatch):
    from app.api.routes import road_damage
    from app.vision.detector import ModelNotConfiguredError

    def unavailable(_path, _gps):
        raise ModelNotConfiguredError("test model is not installed")

    monkeypatch.setattr(road_damage, "analyze_video", unavailable)
    if hasattr(app.state, "video_analyzer"):
        delattr(app.state, "video_analyzer")
    response = upload(client)
    assert response.status_code == 503
    assert "Vision model is not configured" in response.json()["detail"]


def test_real_video_pipeline_to_evidence_and_human_review(tmp_path, monkeypatch):
    """Exercise real video decoding and pipeline with a deliberately mocked detector."""
    import cv2
    import numpy as np
    from app.vision.detector import RawDetection
    from app.vision.pipeline import configure_detector
    import app.api.routes.road_damage as road_damage_module
    import app.review.dashboard as dashboard_module

    video_path = tmp_path / "e2e.avi"
    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*"MJPG"), 5.0, (96, 72))
    assert writer.isOpened()
    for index in range(5):
        frame = np.full((72, 96, 3), 80 + index * 10, dtype=np.uint8)
        writer.write(frame)
    writer.release()

    evidence_root = tmp_path / "evidence"
    monkeypatch.setenv("ROADGUARD_EVIDENCE_DIR", str(evidence_root))
    monkeypatch.setattr(road_damage_module, "EVIDENCE_DIR", evidence_root)
    monkeypatch.setattr(dashboard_module, "EVIDENCE_DIR", evidence_root)
    app.state.repository = SQLiteRepository(tmp_path / "e2e.sqlite3")
    for attr in ("video_analyzer", "detection_processor"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)

    class FixedDetector:
        def predict(self, image):
            height, width = image.shape[:2]
            return [RawDetection("pothole", (width // 4, height // 4, width // 2, height // 2), 0.94)]

    configure_detector(FixedDetector())
    try:
        with TestClient(app) as test_client:
            response = test_client.post(
                "/analyze",
                files={"video": ("e2e.avi", video_path.read_bytes(), "video/x-msvideo")},
                data={"gps_track": '{"lat":12.9716,"lon":77.5946}'},
            )
            assert response.status_code == 201, response.text
            body = response.json()
            assert body["detection_count"] == 5
            assert body["issue_count"] == 1
            issue = body["issues"][0]
            assert issue["recurrence_count"] == 5
            assert len(issue["observations"]) == 5
            assert 0 <= issue["priority"]["score"] <= 100
            issue_id = issue["issue_id"]
            assert issue["representative"]["gps"] == {"lat": 12.9716, "lon": 77.5946}
            evidence_ref = issue["representative"]["evidence_ref"]
            assert evidence_ref
            evidence = test_client.get(f"/evidence/{evidence_ref}")
            assert evidence.status_code == 200
            assert evidence.headers["content-type"] == "image/jpeg"
            assert len(test_client.get(f"/trace/{issue_id}").json()["trace"]) > 0
            approved = test_client.post(f"/locations/{issue_id}/review", json={"decision": "approved", "reviewer": "e2e-test"})
            assert approved.status_code == 200
            assert approved.json()["location"]["status"] == "approved"
            trace = test_client.get(f"/trace/{issue_id}").json()["trace"]
            assert trace[-1]["action"] == "approved"
            assert test_client.post(f"/locations/{issue_id}/review", json={"decision": "rejected"}).status_code == 409
    finally:
        configure_detector(None)
        for attr in ("video_analyzer", "detection_processor"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)


def test_unreadable_video_is_rejected_by_real_pipeline(client):
    from app.vision.pipeline import configure_detector
    from app.vision.detector import RawDetection

    class FixedDetector:
        def predict(self, image):
            return [RawDetection("pothole", (1, 1, 8, 8), 0.9)]

    if hasattr(app.state, "video_analyzer"):
        delattr(app.state, "video_analyzer")
    configure_detector(FixedDetector())
    try:
        response = upload(client)
        assert response.status_code == 422
        assert "video" in response.json()["detail"].lower()
    finally:
        configure_detector(None)
