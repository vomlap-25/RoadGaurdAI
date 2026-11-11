"""Environment-based settings for the RoadGuard AI service."""
import os
from pathlib import Path

APP_NAME = os.getenv("APP_NAME", "RoadGuard AI")
APP_ENV = os.getenv("APP_ENV", "development")
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")
DATABASE_PATH = os.getenv("ROADGUARD_DATABASE_PATH", "artifacts/roadguard.sqlite3")
UPLOAD_DIR = Path(os.getenv("ROADGUARD_UPLOAD_DIR", "uploads")).resolve()
EVIDENCE_DIR = Path(os.getenv("ROADGUARD_EVIDENCE_DIR", "artifacts/evidence")).resolve()
MAX_UPLOAD_BYTES = int(os.getenv("ROADGUARD_MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}
ALLOWED_VIDEO_CONTENT_TYPES = {"video/mp4", "video/quicktime", "video/x-msvideo", "video/x-matroska", "video/x-m4v", "application/octet-stream"}
