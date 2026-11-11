"""Branch coverage for RoadGuard's deterministic agent decisions."""

import pytest

from app.agent import priority_score, process_detections


def detection(**overrides):
    record = {
        "track_id": 1,
        "frame_id": 1,
        "timestamp": 0.0,
        "gps": {"lat": 12.9716, "lon": 77.5946},
        "class": "pothole",
        "bbox": [10, 10, 60, 50],
        "confidence": 0.85,
        "severity": {"score": 0.82, "level": "high", "area_ratio": 0.02},
        "evidence_path": "mock-evidence.jpg",
    }
    record.update(overrides)
    return record


def test_low_confidence_reanalysis_success_updates_record_and_traces_action():
    record = detection(confidence=0.2, severity={"score": 0.35, "level": "low", "area_ratio": 0.01})
    result = process_detections([record], reanalyzer=lambda r: {"confidence": 0.91, "bbox": [12, 12, 65, 55]})
    issue = result["issues"][0]
    assert issue["representative"]["confidence"] == 0.91
    assert issue["representative"]["reanalysis_status"] == "succeeded"
    assert any(t["action"] == "use_reanalyzed_detection" and t["outcome"] for t in result["trace"])


def test_low_confidence_reanalysis_none_fails_safe_to_human_review():
    result = process_detections([detection(confidence=0.1)], reanalyzer=lambda _: None)
    issue = result["issues"][0]
    assert issue["requires_human_review"] is True
    assert issue["status"] == "human_review_required"
    assert any(t["action"] == "retain_and_flag_for_human_review" for t in result["trace"])


def test_reanalysis_exception_fails_safe_and_keeps_original():
    def fail(_):
        raise OSError("mock detector unavailable")
    result = process_detections([detection(confidence=0.2)], reanalyzer=fail)
    assert len(result["issues"]) == 1
    assert result["issues"][0]["representative"]["confidence"] == 0.2
    assert result["issues"][0]["requires_human_review"]


def test_invalid_detection_is_quarantined_and_traced():
    result = process_detections([detection(bbox=[1, 2, 1, 9])])
    assert result["issues"] == []
    trace = result["trace"][0]
    assert trace["action"] == "quarantine_record"
    assert "excluded" in trace["outcome"]


def test_invalid_confidence_and_invalid_gps_are_quarantined():
    result = process_detections([detection(confidence=1.5), detection(gps={"lat": 999, "lon": 0})])
    assert result["issues"] == []
    assert len([t for t in result["trace"] if t["action"] == "quarantine_record"]) == 2


def test_high_severity_always_escalates_to_human_review():
    result = process_detections([detection()])
    issue = result["issues"][0]
    assert issue["status"] == "human_review_required"
    assert issue["requires_human_review"]
    assert any(t["decision"] == "high_severity_escalation" and t["action"] == "escalate_to_human_review" for t in result["trace"])


def test_missing_gps_does_not_drop_issue_and_adds_follow_up_trace():
    result = process_detections([detection(gps=None, severity={"score": 0.5, "level": "medium", "area_ratio": 0.01})])
    assert len(result["issues"]) == 1
    assert result["issues"][0]["gps_missing"]
    assert any(t["action"] == "retain_without_gps_merge" for t in result["trace"])
    assert any(t["action"] == "mark_location_for_follow_up" for t in result["trace"])


def test_duplicate_records_merge_by_gps_and_increment_recurrence():
    first = detection(track_id=1, frame_id=1)
    second = detection(track_id=2, frame_id=10, timestamp=4.0, gps={"lat": 12.97161, "lon": 77.5946}, confidence=0.9)
    result = process_detections([first, second])
    assert len(result["issues"]) == 1
    assert result["issues"][0]["recurrence_count"] == 2
    assert any(t["action"] == "merge_into_issue" and "GPS distance" in t["evidence"]["match_reason"] for t in result["trace"])


def test_duplicate_records_merge_by_same_frame_spatial_overlap_when_gps_missing():
    first = detection(gps=None, track_id=1, frame_id=5, bbox=[10, 10, 60, 50], severity={"score": 0.5, "level": "medium", "area_ratio": 0.01})
    second = detection(gps=None, track_id=2, frame_id=5, bbox=[12, 12, 62, 52], severity={"score": 0.5, "level": "medium", "area_ratio": 0.01})
    result = process_detections([first, second])
    assert len(result["issues"]) == 1
    assert result["issues"][0]["recurrence_count"] == 2


def test_different_gps_locations_do_not_merge():
    first = detection(track_id=1)
    second = detection(track_id=2, frame_id=8, gps={"lat": 13.1, "lon": 77.7})
    result = process_detections([first, second])
    assert len(result["issues"]) == 2


def test_priority_score_is_weighted_transparent_and_renormalizes_missing_fields():
    issue = {
        "representative": detection(),
        "recurrence_count": 3,
        "traffic_importance": "high",
    }
    score = priority_score(issue)
    assert 0 <= score["score"] <= 100
    assert score["components"]["severity"] == pytest.approx(0.82)
    assert score["components"]["damage_size"] == pytest.approx(0.2)
    assert score["components"]["recurrence"] == pytest.approx(0.6)
    assert score["components"]["traffic_importance"] == 1.0
    assert sum(score["weights_used"].values()) == pytest.approx(1.0)

    no_size_or_traffic = priority_score({"representative": {"severity": "medium"}, "recurrence_count": 1})
    assert set(no_size_or_traffic["missing_components"]) == {"damage_size", "traffic_importance"}
    assert sum(no_size_or_traffic["weights_used"].values()) == pytest.approx(1.0)


def test_non_high_valid_finding_is_prioritized_without_auto_closure():
    result = process_detections([detection(severity={"score": 0.55, "level": "medium", "area_ratio": 0.02})])
    issue = result["issues"][0]
    assert issue["status"] == "prioritized"
    assert any(t["action"] == "publish_priority_for_review_queue" for t in result["trace"])
    assert "human operator retains final disposition" in result["trace"][-1]["outcome"]


def test_reanalyzer_returning_invalid_record_fails_safe():
    result = process_detections([detection(confidence=0.2)], reanalyzer=lambda _: {"confidence": 0.8, "bbox": [1, 1, 0, 0]})
    assert result["issues"][0]["requires_human_review"]
    assert result["issues"][0]["representative"]["reanalysis_status"] == "failed"


def test_every_trace_entry_has_required_audit_fields_and_json_friendly_shape():
    result = process_detections([detection(severity={"score": 0.5, "level": "medium", "area_ratio": 0.02})])
    assert result["trace"]
    assert all({"evidence", "decision", "action", "outcome", "detection_id"} <= set(t) for t in result["trace"])
    assert all(t["outcome"] for t in result["trace"])


def test_default_reanalysis_without_evidence_path_is_failed_and_traced():
    record = detection(confidence=0.2)
    record.pop("evidence_path")
    result = process_detections([record])
    assert result["issues"][0]["requires_human_review"]
    assert result["issues"][0]["representative"]["reanalysis_status"] == "failed"
    assert any("no evidence_path" in t["evidence"].get("error", "") for t in result["trace"])


def test_duplicate_merge_keeps_later_available_traffic_importance():
    first = detection(track_id=77, traffic_importance=None)
    second = detection(track_id=77, frame_id=2, traffic_importance="high", gps=None)
    result = process_detections([first, second])
    assert len(result["issues"]) == 1
    assert result["issues"][0]["traffic_importance"] == "high"
    assert result["issues"][0]["priority"]["components"]["traffic_importance"] == 1.0
