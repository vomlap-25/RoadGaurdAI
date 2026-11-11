"""Health endpoint."""
from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    repository = getattr(request.app.state, "repository", None)
    if repository is not None and not repository.health():
        raise HTTPException(status_code=503, detail="Application is running but metadata storage is unavailable")
    return {"status": "ok"}
