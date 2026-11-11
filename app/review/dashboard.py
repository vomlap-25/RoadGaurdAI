"""Server-rendered entry point and safe local evidence delivery for the review UI."""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse

from app.config.settings import EVIDENCE_DIR
from app.storage.cloud import configured_evidence_store

router = APIRouter(tags=["review-dashboard"])
REVIEW_DIR = Path(__file__).resolve().parent
DASHBOARD_FILE = REVIEW_DIR / "dashboard.html"
ALLOWED_EVIDENCE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard() -> HTMLResponse:
    """Serve the dashboard shell; all record data is loaded from the real API."""
    return HTMLResponse(DASHBOARD_FILE.read_text(encoding="utf-8"))


@router.get("/evidence/{reference:path}", include_in_schema=False)
def serve_evidence(reference: str):
    """Serve local evidence safely or stream an object from the configured private S3 bucket."""
    if not reference or "\\" in reference or "\x00" in reference:
        raise HTTPException(status_code=404, detail="Evidence image not found")
    if reference.startswith("s3/"):
        key = reference[3:]
        if not key or key.startswith("/") or ".." in Path(key).parts or Path(key).suffix.lower() not in ALLOWED_EVIDENCE_SUFFIXES:
            raise HTTPException(status_code=404, detail="Evidence image not found")
        store = configured_evidence_store()
        if store is None:
            raise HTTPException(status_code=404, detail="Evidence image not found")
        try:
            body = store.get_object(key)["Body"]
        except Exception as exc:
            raise HTTPException(status_code=404, detail="Evidence image not found") from exc
        media_type = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}[Path(key).suffix.lower()]
        return StreamingResponse(body.iter_chunks(chunk_size=64 * 1024), media_type=media_type, headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=60"})
    root = EVIDENCE_DIR.resolve()
    candidate = (root / reference).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Evidence image not found") from exc
    if candidate.suffix.lower() not in ALLOWED_EVIDENCE_SUFFIXES or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Evidence image not found")
    return FileResponse(
        candidate,
        media_type={".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}[candidate.suffix.lower()],
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=60"},
    )
