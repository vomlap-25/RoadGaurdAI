"""FastAPI application entry point for RoadGuard AI."""
import logging
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.api.routes.health import router as health_router
from app.api.routes.road_damage import router as road_damage_router
from app.review.dashboard import router as dashboard_router
from app.config.settings import APP_NAME
from app.storage.cloud import configured_repository

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
app = FastAPI(title=APP_NAME, version="0.5.0", description="Road-damage analysis API with deterministic review decisions")
app.state.repository = configured_repository()
app.include_router(health_router)
app.include_router(road_damage_router)
app.include_router(dashboard_router)
app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent / "review" / "static"), name="static")
