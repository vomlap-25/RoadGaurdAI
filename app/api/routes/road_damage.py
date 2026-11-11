"""Analysis, ranked-location, human-review, and audit-trace endpoints."""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from app.agent import process_detections
from app.config.settings import ALLOWED_VIDEO_CONTENT_TYPES, ALLOWED_VIDEO_EXTENSIONS, EVIDENCE_DIR, MAX_UPLOAD_BYTES, UPLOAD_DIR
from app.storage import Repository
from app.storage.cloud import configured_evidence_store
from app.vision import analyze_video
from app.vision.detector import ModelNotConfiguredError

logger = logging.getLogger(__name__)
router = APIRouter(tags=["road-damage"])
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$")
CHUNK_SIZE = 1024 * 1024


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(pattern=r"^(approved|rejected)$")
    reviewer: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)


def _repository(request: Request) -> Repository:
    return request.app.state.repository


def _validate_id(value: str) -> str:
    if not UUID_RE.fullmatch(value):
        raise HTTPException(status_code=400, detail="id must be a valid UUID")
    return value.lower()


def _parse_gps(raw: str | None) -> Any:
    if raw is None or not raw.strip():
        return None
    try:
        gps = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="gps_track must be valid JSON") from exc
    if isinstance(gps, dict):
        if set(("lat", "lon")).issubset(gps):
            try:
                lat, lon = float(gps["lat"]), float(gps["lon"])
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail="GPS lat/lon must be numeric") from exc
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise HTTPException(status_code=422, detail="GPS latitude/longitude is out of range")
            return {"lat": lat, "lon": lon}
        raise HTTPException(status_code=422, detail="GPS object must contain lat and lon")
    if isinstance(gps, list):
        if len(gps) > 100_000:
            raise HTTPException(status_code=413, detail="gps_track contains too many points")
        for index, point in enumerate(gps):
            if not isinstance(point, dict) or not {"lat", "lon"}.issubset(point):
                raise HTTPException(status_code=422, detail=f"GPS point {index} must contain lat and lon")
            try:
                lat, lon = float(point["lat"]), float(point["lon"])
                timestamp = float(point.get("timestamp", point.get("time", 0)))
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=422, detail=f"GPS point {index} contains non-numeric values") from exc
            if not (-90 <= lat <= 90 and -180 <= lon <= 180) or not all(map(lambda x: abs(x) != float("inf") and x == x, (lat, lon, timestamp))):
                raise HTTPException(status_code=422, detail=f"GPS point {index} contains invalid coordinates or timestamp")
        return gps
    raise HTTPException(status_code=422, detail="gps_track must be a coordinate object or a list of points")


async def _persist_upload(upload: UploadFile) -> tuple[Path, str]:
    filename = Path(upload.filename or "").name
    suffix = Path(filename).suffix.lower()
    if not filename or suffix not in ALLOWED_VIDEO_EXTENSIONS:
        raise HTTPException(status_code=415, detail=f"Unsupported video extension. Allowed: {', '.join(sorted(ALLOWED_VIDEO_EXTENSIONS))}")
    content_type = (upload.content_type or "application/octet-stream").lower().split(";", 1)[0].strip()
    if content_type not in ALLOWED_VIDEO_CONTENT_TYPES:
        raise HTTPException(status_code=415, detail=f"Unsupported content type: {content_type}")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # Ignore the client filename when creating the path; it is used only for display.
    destination = UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
    total = 0
    try:
        with destination.open("xb") as target:
            while True:
                chunk = await upload.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail=f"Upload exceeds the {MAX_UPLOAD_BYTES}-byte limit")
                target.write(chunk)
        if total == 0:
            raise HTTPException(status_code=422, detail="Uploaded video is empty")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return destination, filename


def _evidence_reference(path_value: Any) -> str | None:
    if not isinstance(path_value, (str, Path)) or not path_value:
        return None
    raw = str(path_value)
    if raw.startswith("s3://"):
        parsed = urlparse(raw)
        expected_bucket = os.getenv("ROADGUARD_S3_BUCKET", "").strip()
        if parsed.netloc == expected_bucket and parsed.path.lstrip("/") and ".." not in Path(parsed.path).parts:
            return "s3/" + parsed.path.lstrip("/")
        return None
    try:
        return Path(raw).resolve().relative_to(EVIDENCE_DIR.resolve()).as_posix()
    except (ValueError, OSError):
        # Do not disclose an arbitrary or out-of-root filesystem path.
        return None


def _replace_evidence_paths(value: Any, uploaded: dict[str, str]) -> None:
    if isinstance(value, dict):
        for key, item in list(value.items()):
            if key == "evidence_path" and isinstance(item, str):
                try:
                    replacement = uploaded.get(str(Path(item).resolve()))
                except OSError:
                    replacement = None
                if replacement:
                    value[key] = replacement
            else:
                _replace_evidence_paths(item, uploaded)
    elif isinstance(value, list):
        for item in value:
            _replace_evidence_paths(item, uploaded)


def _public_issue(issue: dict[str, Any]) -> dict[str, Any]:
    """Avoid exposing absolute filesystem paths; evidence remains a separate asset."""
    result = json.loads(json.dumps(issue, default=str))
    records = [result.get("representative", {})] + result.get("observations", [])
    for record in records:
        path_value = record.pop("evidence_path", None)
        if path_value:
            record["evidence_ref"] = _evidence_reference(path_value)
    return result


def _sanitize_trace(value: Any) -> Any:
    """Recursively remove filesystem paths from trace evidence before persistence."""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if key == "evidence_path":
                cleaned["evidence_ref"] = _evidence_reference(item)
            else:
                cleaned[key] = _sanitize_trace(item)
        return cleaned
    if isinstance(value, list):
        return [_sanitize_trace(item) for item in value]
    return value


@router.post("/analyze", status_code=status.HTTP_201_CREATED)
async def analyze_endpoint(
    request: Request,
    video: UploadFile = File(...),
    gps_track: str | None = Form(default=None, description="Optional JSON coordinate object or timestamped GPS list"),
) -> dict[str, Any]:
    gps = _parse_gps(gps_track)
    saved_path, original_filename = await _persist_upload(video)
    inspection_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        # This path was generated by the server; never accept a client-supplied path.
        analyzer = getattr(request.app.state, "video_analyzer", analyze_video)
        raw_detections = analyzer(str(saved_path), gps)
        if not isinstance(raw_detections, list):
            raise RuntimeError("Vision pipeline returned an invalid detection collection")
        processor = getattr(request.app.state, "detection_processor", process_detections)
        processed = processor(raw_detections)
        if not isinstance(processed, dict) or not isinstance(processed.get("issues"), list) or not isinstance(processed.get("trace"), list):
            raise RuntimeError("Agent returned an invalid result; expected issues and trace lists")

        # Optional cloud evidence offload happens after agent re-analysis so its current
        # local-file contract remains intact. Local evidence remains available as a fallback.
        evidence_store = configured_evidence_store()
        if evidence_store:
            uploaded: dict[str, str] = {}
            for detection in raw_detections:
                evidence_path = detection.get("evidence_path") if isinstance(detection, dict) else None
                if not evidence_path:
                    continue
                try:
                    resolved = str(Path(evidence_path).resolve())
                    if resolved not in uploaded and Path(resolved).is_file():
                        key = evidence_store.upload_file(resolved, inspection_id=inspection_id)
                        uploaded[resolved] = f"s3://{evidence_store.bucket}/{key}"
                except (OSError, ValueError):
                    logger.warning("Could not upload one evidence image to S3", exc_info=True)
            _replace_evidence_paths(raw_detections, uploaded)
            _replace_evidence_paths(processed["issues"], uploaded)
            _replace_evidence_paths(processed["trace"], uploaded)

        # Give each persisted issue a globally unique identifier, independent of the
        # agent's per-batch issue-0001 identifiers.
        id_map: dict[str, str] = {}
        for issue in processed["issues"]:
            old_id = str(issue.get("issue_id", ""))
            new_id = str(uuid.uuid4())
            if old_id:
                id_map[old_id] = new_id
            issue["issue_id"] = new_id
        for event in processed["trace"]:
            if event.get("issue_id") in id_map:
                event["issue_id"] = id_map[event["issue_id"]]
            outcome = event.get("outcome")
            if isinstance(outcome, str):
                for old_id, new_id in id_map.items():
                    outcome = outcome.replace(old_id, new_id)
                event["outcome"] = outcome

        issues = [_public_issue(issue) for issue in processed["issues"]]
        safe_trace = [_sanitize_trace(event) for event in processed["trace"]]
        repository = _repository(request)
        repository.save_inspection({
            "inspection_id": inspection_id,
            "created_at": created_at,
            "filename": original_filename,
            "status": "completed",
            "detection_count": len(raw_detections),
        }, issues, safe_trace)
        return {
            "inspection_id": inspection_id,
            "status": "completed",
            "filename": original_filename,
            "detection_count": len(raw_detections),
            "issue_count": len(issues),
            "issues": issues,
            "trace_count": len(safe_trace),
        }
    except HTTPException:
        raise
    except ModelNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=f"Vision model is not configured: {exc}") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"Required analysis resource is unavailable: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Video analysis failed: {exc}") from exc
    except Exception as exc:
        logger.exception("RoadGuard analysis failed (inspection_id=%s)", inspection_id)
        raise HTTPException(status_code=500, detail="Analysis failed unexpectedly; inspect server logs using the inspection ID if available") from exc
    finally:
        saved_path.unlink(missing_ok=True)


@router.get("/locations")
def get_locations(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status", max_length=40),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    allowed_statuses = {"pending_decision", "prioritized", "human_review_required", "approved", "rejected"}
    if status_filter is not None and status_filter not in allowed_statuses:
        raise HTTPException(status_code=422, detail=f"status must be one of: {', '.join(sorted(allowed_statuses))}")
    records = _repository(request).list_locations(status=status_filter, limit=limit)
    return {"count": len(records), "locations": records}


@router.post("/locations/{location_id}/review")
def review_location(location_id: str, payload: ReviewRequest, request: Request) -> dict[str, Any]:
    issue_id = _validate_id(location_id)
    repository = _repository(request)
    existing = repository.get_location(issue_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Road-damage record not found")
    if existing.get("status") in {"approved", "rejected"}:
        raise HTTPException(status_code=409, detail="This record has already been reviewed")
    reviewed = repository.review_location(issue_id, decision=payload.decision, reviewer=payload.reviewer, notes=payload.notes)
    if reviewed is None:
        raise HTTPException(status_code=404, detail="Road-damage record not found")
    return {"location": reviewed, "message": f"Record {payload.decision}"}


@router.get("/trace/{location_id}")
def get_trace(location_id: str, request: Request) -> dict[str, Any]:
    issue_id = _validate_id(location_id)
    trace = _repository(request).get_trace(issue_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Road-damage record not found")
    return {"location_id": issue_id, "count": len(trace), "trace": trace}
