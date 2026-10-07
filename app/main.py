"""FastAPI application entrypoint, lifespan configuration, route registration, and global error handling."""

from contextlib import asynccontextmanager
import logging
import os
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.files import router as files_router
from app.config import settings
from app.db import init_db
from app.services.crs import MissingCRSError
from app.services.file_reader import (
    CorruptZipFileError,
    DecompressionBombError,
    InvalidFileContentError,
    InvalidFileExtensionError,
    MissingCRSFileError,
    MissingShapefileComponentError,
    UnreadableKMLError,
    ZeroFeaturesError,
    ZipSlipError,
)
from app.services.storage import EmptyFileError, FileTooLargeError, PathTraversalError

logger = logging.getLogger("app.main")


def make_error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Format all HTTP error responses into the uniform specification structure."""
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
            }
        },
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan managing directory creation and database table schema setup."""
    os.makedirs(settings.upload_dir, exist_ok=True)
    init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="High-performance geospatial measurement API for aerial surveys and GIS data.",
    lifespan=lifespan,
)

# Register API routes under /api prefix
app.include_router(files_router, prefix="/api")


# ---------------------------------------------------------------------------
# Global Exception Handlers (Guarantee uniform {"error": {"code", "message"}})
# ---------------------------------------------------------------------------

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if isinstance(exc.detail, dict):
        code = exc.detail.get("code", "HTTP_ERROR")
        message = exc.detail.get("message", "An error occurred.")
    else:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            code = "NOT_FOUND"
        elif exc.status_code == status.HTTP_409_CONFLICT:
            code = "CONFLICT"
        elif exc.status_code == status.HTTP_400_BAD_REQUEST:
            code = "BAD_REQUEST"
        else:
            code = f"HTTP_{exc.status_code}"
        message = str(exc.detail)

    return make_error_response(exc.status_code, code, message)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    first_msg = errors[0].get("msg", "Invalid request parameter.") if errors else "Validation failed."
    return make_error_response(status.HTTP_422_UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", first_msg)


@app.exception_handler(InvalidFileExtensionError)
async def invalid_extension_handler(request: Request, exc: InvalidFileExtensionError):
    return make_error_response(status.HTTP_400_BAD_REQUEST, "INVALID_FILE_EXTENSION", str(exc))


@app.exception_handler(InvalidFileContentError)
async def invalid_content_handler(request: Request, exc: InvalidFileContentError):
    return make_error_response(status.HTTP_400_BAD_REQUEST, "INVALID_FILE_CONTENT", str(exc))


@app.exception_handler(EmptyFileError)
async def empty_file_handler(request: Request, exc: EmptyFileError):
    return make_error_response(status.HTTP_400_BAD_REQUEST, "EMPTY_FILE", str(exc))


@app.exception_handler(FileTooLargeError)
async def file_too_large_handler(request: Request, exc: FileTooLargeError):
    return make_error_response(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "FILE_TOO_LARGE", str(exc))


@app.exception_handler(PathTraversalError)
async def path_traversal_handler(request: Request, exc: PathTraversalError):
    return make_error_response(status.HTTP_400_BAD_REQUEST, "PATH_TRAVERSAL_DETECTED", str(exc))


@app.exception_handler(CorruptZipFileError)
async def corrupt_zip_handler(request: Request, exc: CorruptZipFileError):
    return make_error_response(status.HTTP_400_BAD_REQUEST, "CORRUPT_ARCHIVE", str(exc))


@app.exception_handler(ZipSlipError)
async def zip_slip_handler(request: Request, exc: ZipSlipError):
    return make_error_response(status.HTTP_400_BAD_REQUEST, "SECURITY_VIOLATION", str(exc))


@app.exception_handler(DecompressionBombError)
async def bomb_handler(request: Request, exc: DecompressionBombError):
    return make_error_response(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "ARCHIVE_TOO_LARGE", str(exc))


@app.exception_handler(MissingShapefileComponentError)
async def missing_shp_handler(request: Request, exc: MissingShapefileComponentError):
    return make_error_response(status.HTTP_400_BAD_REQUEST, "MISSING_SHAPEFILE_COMPONENTS", str(exc))


@app.exception_handler(MissingCRSFileError)
async def missing_crs_file_handler(request: Request, exc: MissingCRSFileError):
    return make_error_response(status.HTTP_400_BAD_REQUEST, "MISSING_CRS", str(exc))


@app.exception_handler(MissingCRSError)
async def missing_crs_handler(request: Request, exc: MissingCRSError):
    return make_error_response(status.HTTP_400_BAD_REQUEST, "MISSING_CRS", str(exc))


@app.exception_handler(UnreadableKMLError)
async def unreadable_kml_handler(request: Request, exc: UnreadableKMLError):
    return make_error_response(status.HTTP_400_BAD_REQUEST, "UNREADABLE_KML", str(exc))


@app.exception_handler(ZeroFeaturesError)
async def zero_features_handler(request: Request, exc: ZeroFeaturesError):
    return make_error_response(status.HTTP_422_UNPROCESSABLE_ENTITY, "ZERO_FEATURES", str(exc))


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled server exception on {request.method} {request.url.path}: {exc}", exc_info=True)
    return make_error_response(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "INTERNAL_SERVER_ERROR",
        "An unexpected server error occurred.",
    )


# ---------------------------------------------------------------------------
# Health Check Endpoint
# ---------------------------------------------------------------------------

@app.get("/health", tags=["health"], summary="Service operational health check")
def health_check() -> dict[str, str]:
    """Health check endpoint confirming service status."""
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
    }
