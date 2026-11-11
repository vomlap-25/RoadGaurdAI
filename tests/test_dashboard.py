"""Dashboard shell, evidence safety, and review workflow integration tests."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.storage import SQLiteRepository
import app.review.dashboard as dashboard_module


def setup_client(tmp_path):
    app.state.repository = SQLiteRepository(tmp_path / "dashboard-test.sqlite3")
    app.state.video_analyzer = lambda path, gps: [{
        "track_id": 22,
        "frame_id": 1,
        "timestamp": 0.0,
        "gps": {"lat": 12.9716, "lon": 77.5946},
        "class": "pothole",
        "bbox": [5, 5, 45, 40],
        "confidence": 0.91,
        "severity": {"score": 0.82, "level": "high", "area_ratio": 0.08},
        "evidence_path": "artifacts/evidence/dashboard-test/frame.jpg",
    }]
    if hasattr(app.state, "detection_processor"):
        delattr(app.state, "detection_processor")
    return TestClient(app)


def test_dashboard_is_served_and_uses_live_api(tmp_path):
    with setup_client(tmp_path) as client:
        response = client.get("/dashboard")
        assert response.status_code == 200
        assert "Road damage inspections" in response.text
        assert "/static/dashboard.js" in response.text
        assert "/locations" not in response.text  # data is fetched by JS, not embedded in HTML
        js = client.get("/static/dashboard.js")
        css = client.get("/static/dashboard.css")
        assert js.status_code == css.status_code == 200
        assert "requestJson('/locations?limit=500')" in js.text
        assert "POST" in js.text and "/review" in js.text


def test_dashboard_review_flow_calls_real_api_and_updates_trace(tmp_path):
    with setup_client(tmp_path) as client:
        analyze = client.post("/analyze", files={"video": ("demo.mp4", b"mock video", "video/mp4")})
        assert analyze.status_code == 201, analyze.text
        issue_id = analyze.json()["issues"][0]["issue_id"]
        listing = client.get("/locations")
        assert listing.status_code == 200 and listing.json()["count"] == 1
        assert listing.json()["locations"][0]["representative"]["class"] == "pothole"
        review = client.post(f"/locations/{issue_id}/review", json={"decision": "approved", "reviewer": "dashboard-test", "notes": "checked"})
        assert review.status_code == 200, review.text
        assert review.json()["location"]["status"] == "approved"
        trace = client.get(f"/trace/{issue_id}").json()["trace"]
        assert trace[-1]["decision"] == "human_review_submitted"
        assert trace[-1]["action"] == "approved"
        assert client.post(f"/locations/{issue_id}/review", json={"decision": "rejected"}).status_code == 409


def test_evidence_route_serves_only_images_inside_evidence_root(tmp_path, monkeypatch):
    root = tmp_path / "evidence"
    root.mkdir()
    image = root / "proof.jpg"
    image.write_bytes(b"fake-jpeg-content-for-route-test")
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"outside")
    monkeypatch.setattr(dashboard_module, "EVIDENCE_DIR", root)
    with setup_client(tmp_path) as client:
        response = client.get("/evidence/proof.jpg")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.content == image.read_bytes()
        assert client.get("/evidence/../outside.jpg").status_code == 404
        assert client.get("/evidence/proof.txt").status_code == 404
        assert client.get("/evidence/missing.jpg").status_code == 404


def test_dashboard_shows_empty_state_from_live_empty_api(tmp_path):
    with setup_client(tmp_path) as client:
        page = client.get("/dashboard")
        locations = client.get("/locations").json()
        assert page.status_code == 200
        assert locations == {"count": 0, "locations": []}
        assert "No inspection records yet" in page.text
