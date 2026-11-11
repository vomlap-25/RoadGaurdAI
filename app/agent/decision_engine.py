"""Deterministic decision engine for RoadGuard vision detection records.

The engine is intentionally rule-based. It never suppresses high-severity findings
from human review, and every performed action emits an evidence -> decision ->
action -> outcome trace record.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import cv2

from app.vision import reanalyze

Detection = Mapping[str, Any]
Reanalyzer = Callable[[dict[str, Any]], Mapping[str, Any] | None]

DEFAULT_LOW_CONFIDENCE = 0.45
DEFAULT_GPS_DUPLICATE_DISTANCE_M = 8.0
DEFAULT_SPATIAL_IOU = 0.50


def _trace(
    trace: list[dict[str, Any]],
    *,
    evidence: Any,
    decision: str,
    action: str,
    outcome: str,
    detection_id: str | None = None,
) -> None:
    """Append a JSON-serializable, consistently shaped audit event."""
    trace.append({
        "evidence": evidence,
        "decision": decision,
        "action": action,
        "outcome": outcome,
        "detection_id": detection_id,
    })


def _valid_detection(detection: Any) -> tuple[bool, str]:
    if not isinstance(detection, Mapping):
        return False, "record is not a mapping"
    if not isinstance(detection.get("class"), str) or not detection["class"].strip():
        return False, "missing or invalid class"
    bbox = detection.get("bbox")
    if not isinstance(bbox, Sequence) or isinstance(bbox, (str, bytes)) or len(bbox) != 4:
        return False, "bbox must contain four coordinates [x1,y1,x2,y2]"
    try:
        x1, y1, x2, y2 = (float(v) for v in bbox)
        confidence = float(detection.get("confidence"))
    except (TypeError, ValueError, OverflowError):
        return False, "bbox/confidence contains a non-numeric value"
    if not all(math.isfinite(v) for v in (x1, y1, x2, y2, confidence)):
        return False, "bbox/confidence contains a non-finite value"
    if x2 <= x1 or y2 <= y1:
        return False, "bbox has zero or negative area"
    if not 0.0 <= confidence <= 1.0:
        return False, "confidence must be between 0 and 1"
    gps = detection.get("gps")
    if gps is not None and _valid_gps(gps) is None:
        return False, "GPS must contain finite latitude/longitude within valid ranges"
    return True, "valid detection record"


def _valid_gps(gps: Any) -> tuple[float, float] | None:
    if not isinstance(gps, Mapping):
        return None
    try:
        lat, lon = float(gps["lat"]), float(gps["lon"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(lat) or not math.isfinite(lon) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6_371_000.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


def _iou(a: Sequence[float], b: Sequence[float]) -> float:
    ax1, ay1, ax2, ay2 = map(float, a)
    bx1, by1, bx2, by2 = map(float, b)
    inter = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))
    union = (ax2 - ax1) * (ay2 - ay1) + (bx2 - bx1) * (by2 - by1) - inter
    return inter / union if union > 0 else 0.0


def _default_reanalyzer(record: dict[str, Any]) -> Mapping[str, Any] | None:
    """Use the existing vision crop reanalysis when an evidence image is available."""
    evidence_path = record.get("evidence_path")
    if not evidence_path:
        raise FileNotFoundError("detection has no evidence_path for reanalysis")
    frame = cv2.imread(str(evidence_path))
    if frame is None:
        raise FileNotFoundError(f"could not read evidence image: {evidence_path}")
    result = reanalyze(frame, record["bbox"], mode="tiled")
    if result is None:
        return None
    return {
        "class": result.get("class", record["class"]),
        "bbox": result.get("bbox", record["bbox"]),
        "confidence": result.get("confidence"),
        "severity": result.get("severity", record.get("severity", {})),
        "evidence_path": record.get("evidence_path"),
    }


def _severity_component(record: Detection) -> float:
    severity = record.get("severity")
    if isinstance(severity, Mapping):
        score = severity.get("score")
        if score is not None:
            try:
                return max(0.0, min(1.0, float(score)))
            except (TypeError, ValueError):
                pass
        level = str(severity.get("level", "")).lower()
    else:
        level = str(severity or "").lower()
    return {"high": 1.0, "medium": 0.6, "low": 0.25}.get(level, 0.5)


def _damage_size_component(record: Detection) -> float | None:
    severity = record.get("severity")
    ratio = severity.get("area_ratio") if isinstance(severity, Mapping) else None
    if ratio is None:
        ratio = record.get("damage_area_ratio")
    try:
        ratio = float(ratio)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(ratio) or ratio < 0:
        return None
    # Matches the vision module's transparent 10%-of-frame saturation point.
    return min(1.0, ratio / 0.10)


def _traffic_component(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str):
        return {"low": 0.25, "medium": 0.60, "high": 1.0}.get(value.lower())
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, numeric)) if math.isfinite(numeric) else None


def priority_score(issue: Mapping[str, Any]) -> dict[str, Any]:
    """Return weighted 0..100 priority plus component details and missing inputs.

    Weights: severity 0.40, damage size 0.25, recurrence 0.20, traffic 0.15.
    If damage size or traffic importance is unavailable, remaining weights are
    renormalized rather than fabricating a value.
    """
    recurrence = max(1, int(issue.get("recurrence_count", 1)))
    representative = issue.get("representative", {})
    # The issue-level recurrence/traffic fields combine with the representative
    # observation's severity and area estimate.
    source = {**dict(representative), **dict(issue)} if isinstance(representative, Mapping) else dict(issue)
    components: dict[str, float | None] = {
        "severity": _severity_component(source),
        "damage_size": _damage_size_component(source),
        "recurrence": min(1.0, recurrence / 5.0),
        "traffic_importance": _traffic_component(issue.get("traffic_importance", source.get("traffic_importance"))),
    }
    weights = {"severity": 0.40, "damage_size": 0.25, "recurrence": 0.20, "traffic_importance": 0.15}
    included = {key: value for key, value in components.items() if value is not None}
    weight_total = sum(weights[key] for key in included)
    score = 100.0 * sum(float(value) * weights[key] for key, value in included.items()) / weight_total
    return {
        "score": round(score, 2),
        "components": components,
        "weights_used": {key: round(weights[key] / weight_total, 4) for key in included},
        "missing_components": [key for key, value in components.items() if value is None],
    }


def _is_duplicate(
    candidate: Mapping[str, Any],
    existing: Mapping[str, Any],
    *,
    gps_distance_m: float,
    spatial_iou: float,
) -> tuple[bool, str]:
    if str(candidate.get("class", "")).lower() != str(existing.get("class", "")).lower():
        return False, "different damage classes"
    c_track, e_track = candidate.get("track_id"), existing.get("track_id")
    if c_track is not None and e_track is not None and str(c_track) == str(e_track):
        return True, "same vision track_id"
    c_gps, e_gps = _valid_gps(candidate.get("gps")), _valid_gps(existing.get("gps"))
    if c_gps and e_gps:
        distance = _haversine_m(c_gps, e_gps)
        return (distance <= gps_distance_m, f"GPS distance {distance:.2f} m")
    # Image coordinates are comparable only within the same frame.
    if candidate.get("frame_id") == existing.get("frame_id") and candidate.get("frame_id") is not None:
        overlap = _iou(candidate["bbox"], existing["bbox"])
        return overlap >= spatial_iou, f"same-frame bbox IoU {overlap:.3f}"
    return False, "insufficient comparable location evidence"


def process_detections(
    detections: Sequence[Any],
    *,
    reanalyzer: Reanalyzer | None = None,
    low_confidence_threshold: float = DEFAULT_LOW_CONFIDENCE,
    gps_duplicate_distance_m: float = DEFAULT_GPS_DUPLICATE_DISTANCE_M,
    spatial_iou_threshold: float = DEFAULT_SPATIAL_IOU,
) -> dict[str, Any]:
    """Validate, reanalyse, deduplicate, score, and route detection records.

    Returns ``{"issues": [...], "trace": [...]}``. The caller may persist both
    lists. ``reanalyzer`` is injectable for deterministic tests and offline use;
    absent injection, the existing OpenCV vision reanalysis is used.
    """
    if not 0 <= low_confidence_threshold <= 1:
        raise ValueError("low_confidence_threshold must be in [0, 1]")
    if gps_duplicate_distance_m < 0 or not 0 <= spatial_iou_threshold <= 1:
        raise ValueError("duplicate thresholds must be non-negative / within [0, 1]")

    trace: list[dict[str, Any]] = []
    valid: list[dict[str, Any]] = []
    reanalyze_fn = reanalyzer or _default_reanalyzer

    for index, original in enumerate(detections):
        detection_id = str(original.get("detection_id", original.get("track_id", f"det-{index + 1}"))) if isinstance(original, Mapping) else f"det-{index + 1}"
        ok, reason = _valid_detection(original)
        if not ok:
            _trace(trace, evidence={"record": original, "validation": reason}, decision="invalid_detection", action="quarantine_record", outcome="record excluded from automated prioritization; review required", detection_id=detection_id)
            continue
        record = dict(original)
        record["detection_id"] = detection_id
        _trace(trace, evidence={"confidence": record["confidence"], "class": record["class"], "bbox": record["bbox"]}, decision="validate_detection", action="accept_record", outcome="valid record admitted to decision pipeline", detection_id=detection_id)

        if float(record["confidence"]) < low_confidence_threshold:
            _trace(trace, evidence={"confidence": record["confidence"], "threshold": low_confidence_threshold, "evidence_path": record.get("evidence_path")}, decision="low_confidence", action="reanalyze_detection", outcome="reanalysis started", detection_id=detection_id)
            try:
                revised = reanalyze_fn(record)
                if revised is None:
                    raise RuntimeError("reanalyzer returned no detection")
                confidence = float(revised.get("confidence"))
                bbox = revised.get("bbox", record["bbox"])
                candidate = {**record, **dict(revised), "bbox": list(bbox), "confidence": confidence}
                revised_ok, revised_reason = _valid_detection(candidate)
                if not revised_ok:
                    raise ValueError(f"reanalyzer returned invalid result: {revised_reason}")
                record = candidate
                record["reanalysis_status"] = "succeeded"
                _trace(trace, evidence={"new_confidence": confidence, "new_bbox": record["bbox"]}, decision="reanalysis_succeeded", action="use_reanalyzed_detection", outcome="updated record accepted", detection_id=detection_id)
            except Exception as exc:  # fail-safe: retain finding and request review
                record["reanalysis_status"] = "failed"
                record["reanalysis_error"] = f"{type(exc).__name__}: {exc}"
                record["requires_human_review"] = True
                _trace(trace, evidence={"error": record["reanalysis_error"]}, decision="reanalysis_failed", action="retain_and_flag_for_human_review", outcome="original finding retained; no automated suppression", detection_id=detection_id)
        else:
            record["reanalysis_status"] = "not_needed"
        if _valid_gps(record.get("gps")) is None:
            record["gps_missing"] = True
            _trace(trace, evidence={"gps": record.get("gps")}, decision="location_unavailable", action="retain_without_gps_merge", outcome="finding retained; GPS-based deduplication skipped and location marked unknown", detection_id=detection_id)
        else:
            record["gps_missing"] = False
        valid.append(record)

    issues: list[dict[str, Any]] = []
    for record in valid:
        matched_issue: dict[str, Any] | None = None
        matched_reason = ""
        for issue in issues:
            duplicate, reason = _is_duplicate(record, issue["representative"], gps_distance_m=gps_duplicate_distance_m, spatial_iou=spatial_iou_threshold)
            if duplicate:
                matched_issue, matched_reason = issue, reason
                break
        if matched_issue is not None:
            matched_issue["observations"].append(record)
            matched_issue["recurrence_count"] = len(matched_issue["observations"])
            if matched_issue.get("traffic_importance") is None and record.get("traffic_importance") is not None:
                matched_issue["traffic_importance"] = record["traffic_importance"]
            if _valid_gps(record.get("gps")) is not None:
                matched_issue["gps_missing"] = False
            # Keep strongest confidence and most severe observation as representative.
            current = matched_issue["representative"]
            if _severity_component(record) > _severity_component(current) or (
                _severity_component(record) == _severity_component(current) and float(record["confidence"]) > float(current["confidence"])
            ):
                matched_issue["representative"] = record
            _trace(trace, evidence={"detection_id": record["detection_id"], "match_reason": matched_reason}, decision="duplicate_record", action="merge_into_issue", outcome=f"merged into {matched_issue['issue_id']}; recurrence_count={matched_issue['recurrence_count']}", detection_id=record["detection_id"])
            continue

        issue_id = f"issue-{len(issues) + 1:04d}"
        issues.append({
            "issue_id": issue_id,
            "class": record["class"],
            "representative": record,
            "observations": [record],
            "recurrence_count": 1,
            "traffic_importance": record.get("traffic_importance"),
            "requires_human_review": bool(record.get("requires_human_review", False)),
            "gps_missing": bool(record.get("gps_missing", False)),
            "status": "pending_decision",
        })

    for issue in issues:
        representative = issue["representative"]
        severity = _severity_component(representative)
        severity_level = str((representative.get("severity") or {}).get("level", "")).lower() if isinstance(representative.get("severity"), Mapping) else str(representative.get("severity", "")).lower()
        high_severity = severity_level == "high" or severity >= 0.70
        priority = priority_score(issue)
        issue["priority"] = priority
        issue["severity_level"] = severity_level or ("high" if severity >= 0.70 else "medium" if severity >= 0.40 else "low")
        issue["status"] = "human_review_required" if high_severity or issue["requires_human_review"] else "prioritized"
        if high_severity:
            issue["requires_human_review"] = True
            action = "escalate_to_human_review"
            decision = "high_severity_escalation"
            outcome = "issue queued for mandatory human review; automation cannot close it"
        elif issue["requires_human_review"]:
            action = "escalate_to_human_review"
            decision = "review_required_by_guardrail"
            outcome = "issue queued for human review due to unresolved reanalysis"
        else:
            action = "publish_priority_for_review_queue"
            decision = "priority_calculated"
            outcome = f"issue prioritized at {priority['score']}/100; human operator retains final disposition"
        _trace(trace, evidence={"issue_id": issue["issue_id"], "severity": issue["severity_level"], "priority": priority, "recurrence_count": issue["recurrence_count"], "gps_missing": issue["gps_missing"]}, decision=decision, action=action, outcome=outcome, detection_id=str(representative["detection_id"]))
        # Explicit output event: a missing GPS fix never discards an issue.
        if issue["gps_missing"]:
            _trace(trace, evidence={"issue_id": issue["issue_id"], "gps": None}, decision="geolocation_incomplete", action="mark_location_for_follow_up", outcome="issue remains in queue without map pin; location follow-up required", detection_id=str(representative["detection_id"]))

    # Ensure result is JSON serializable before returning, which also catches accidental opaque values.
    json.dumps({"issues": issues, "trace": trace}, default=str)
    return {"issues": issues, "trace": trace}


__all__ = ["process_detections", "priority_score"]
