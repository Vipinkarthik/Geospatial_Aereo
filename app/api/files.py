"""API routes for geospatial file upload, file details, and feature measurements."""

import math
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.feature_record import FeatureRecord
from app.models.uploaded_file import FileStatus, UploadedFile
from app.schemas.files import FileDetailResponse, FileUploadResponse
from app.schemas.measurements import (
    FeatureMeasurementResponse,
    PaginatedMeasurementResponse,
)
from app.services.processing import ingest_and_process_upload

router = APIRouter(prefix="/files", tags=["files"])


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=FileUploadResponse,
    summary="Upload and process geospatial dataset (.zip Shapefile or .kml)",
)
def upload_file(
    file: UploadFile = File(..., description="Geospatial file (.zip Shapefile or .kml)"),
    db: Session = Depends(get_db),
) -> FileUploadResponse:
    """
    Ingest, validate, extract features, compute planar measurements,
    and persist results synchronously to the database.
    """
    filename = file.filename or "unknown_upload"
    file_record = ingest_and_process_upload(
        file_content=file.file,
        original_filename=filename,
        db=db,
    )

    if file_record.status == FileStatus.FAILED:
        msg = file_record.error_message or "Failed to process geospatial file."
        code = "PROCESSING_FAILED"
        msg_lower = msg.lower()
        if "corrupt" in msg_lower or "not a zip file" in msg_lower:
            code = "CORRUPT_ARCHIVE"
        elif "missing" in msg_lower and (".prj" in msg_lower or "crs" in msg_lower or "coordinate reference" in msg_lower):
            code = "MISSING_CRS"
        elif "does not contain any .shp" in msg_lower or ("missing" in msg_lower and ("companion" in msg_lower or ".shp" in msg_lower or "component" in msg_lower)):
            code = "MISSING_SHAPEFILE_COMPONENTS"
        elif "zero" in msg_lower and ("feature" in msg_lower or "layer" in msg_lower):
            code = "ZERO_FEATURES"
        elif "unreadable" in msg_lower or "not contain valid kml" in msg_lower or "kml" in msg_lower:
            code = "UNREADABLE_KML"
        elif "exceeds limit" in msg_lower or "bomb" in msg_lower:
            code = "ARCHIVE_TOO_LARGE"
        elif "zip-slip" in msg_lower or "traversal" in msg_lower:
            code = "SECURITY_VIOLATION"

        status_code = (
            status.HTTP_422_UNPROCESSABLE_ENTITY
            if code == "ZERO_FEATURES"
            else (
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
                if code == "ARCHIVE_TOO_LARGE"
                else status.HTTP_400_BAD_REQUEST
            )
        )

        raise HTTPException(
            status_code=status_code,
            detail={
                "code": code,
                "message": msg,
            },
        )

    return FileUploadResponse(
        id=file_record.id,
        filename=file_record.filename,
        feature_count=file_record.feature_count,
        crs=file_record.crs,
        status=file_record.status,
    )


@router.get(
    "/{id}/",
    response_model=FileDetailResponse,
    summary="Get uploaded file details and processing status",
)
def get_file_detail(
    id: int,
    db: Session = Depends(get_db),
) -> FileDetailResponse:
    """Retrieve metadata, current processing state, and error logs for an uploaded file."""
    file_record = db.get(UploadedFile, id)
    if not file_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "FILE_NOT_FOUND",
                "message": f"File with ID {id} not found.",
            },
        )

    return FileDetailResponse(
        id=file_record.id,
        filename=file_record.filename,
        feature_count=file_record.feature_count,
        crs=file_record.crs,
        status=file_record.status,
        error_message=file_record.error_message,
        created_at=file_record.created_at,
    )


@router.get(
    "/{id}/measurements/",
    response_model=PaginatedMeasurementResponse,
    summary="Retrieve precomputed spatial measurements for an uploaded file",
)
def get_file_measurements(
    id: int,
    limit: int = Query(default=50, ge=1, le=500, description="Page size limit"),
    offset: int = Query(default=0, ge=0, description="Offset index"),
    geometry_type: str | None = Query(default=None, description="Filter by geometry type (e.g. Polygon, LineString)"),
    db: Session = Depends(get_db),
) -> PaginatedMeasurementResponse:
    """
    Retrieve stored planar measurements for features belonging to a file.
    Does not re-read or reprocess raw files from disk.
    """
    file_record = db.get(UploadedFile, id)
    if not file_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "FILE_NOT_FOUND",
                "message": f"File with ID {id} not found.",
            },
        )

    if file_record.status != FileStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "FILE_NOT_READY",
                "message": (
                    f"File is currently in status '{file_record.status.value}'. "
                    f"Measurements are only available for COMPLETED files."
                ),
            },
        )

    query = select(FeatureRecord).where(FeatureRecord.uploaded_file_id == id)
    if geometry_type:
        query = query.where(FeatureRecord.geometry_type.ilike(geometry_type.strip()))

    count_stmt = select(func.count()).select_from(query.subquery())
    total_matching = db.scalar(count_stmt) or 0

    records = db.scalars(
        query.order_by(FeatureRecord.feature_index).offset(offset).limit(limit)
    ).all()

    feature_items = [
        FeatureMeasurementResponse(**rec.feature_data)
        for rec in records
    ]

    total_pages = math.ceil(total_matching / limit) if total_matching > 0 else 1
    current_page = (offset // limit) + 1 if limit > 0 else 1

    return PaginatedMeasurementResponse(
        file_id=file_record.id,
        filename=file_record.filename,
        total_features=total_matching,
        total=total_matching,
        page=current_page,
        page_size=limit,
        total_pages=total_pages,
        limit=limit,
        offset=offset,
        features=feature_items,
    )
