from contextlib import asynccontextmanager
import os
from fastapi import FastAPI

from app.api.files import router as files_router
from app.config import settings
from app.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler managing application startup and shutdown."""
    # Ensure file upload directory exists on startup
    os.makedirs(settings.upload_dir, exist_ok=True)
    # Initialize database tables
    init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="Geospatial measurement API for parsing spatial layers and computing geometric properties.",
    lifespan=lifespan,
)

# Register routers
app.include_router(files_router)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Health check endpoint to verify API availability."""
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
    }
