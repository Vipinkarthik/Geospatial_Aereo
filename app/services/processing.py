"""End-to-end processing pipeline orchestrating upload persistence, geospatial parsing, planar measurement, and database storage.

Designed for synchronous execution within request lifecycles while isolating the worker
boundary so migration to background task queues (Celery, ARQ, Redis Queue) requires zero logic changes.
"""

import logging
from pathlib import Path
from typing import BinaryIO, Union
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models.feature_record import FeatureRecord
from app.models.uploaded_file import FileStatus, UploadedFile
from app.services.crs import MissingCRSError
from app.services.feature_extractor import extract_features_from_geodataframe
from app.services.file_reader import (
    GeospatialReaderError,
    InvalidFileExtensionError,
    read_geospatial_file,
)
from app.services.measurement import measure_geometry
from app.services.storage import StorageError, save_upload_stream

logger = logging.getLogger("app.services.processing")


def process_uploaded_file(file_id: int, db: Session) -> UploadedFile:
    """
    Worker task: Processes an existing UploadedFile record by reading its stored file,
    extracting spatial features, calculating planar measurements, and persisting records.

    This function represents the exact unit of execution that can be dispatched to a background worker.

    Lifecycle:
        1. Query UploadedFile by ID.
        2. Set status to PROCESSING.
        3. Read and validate spatial layers and CRS.
        4. Extract features and compute metric measurements.
        5. Insert FeatureRecord rows with precomputed JSON data.
        6. Update feature_count, CRS, and mark COMPLETED.
        7. On failure: rollback partial records, record human-readable error, and mark FAILED.
    """
    file_record = db.get(UploadedFile, file_id)
    if not file_record:
        raise ValueError(f"UploadedFile record with ID {file_id} not found.")

    logger.info(f"Starting processing pipeline for file_id {file_id} ('{file_record.filename}').")
    file_record.status = FileStatus.PROCESSING
    db.commit()

    file_path = Path(file_record.stored_path)

    try:
        # 1. Read geospatial dataset and validate source CRS
        gdf, source_crs = read_geospatial_file(file_path)

        if not source_crs:
            raise MissingCRSError("Dataset has no coordinate reference system defined.")

        # 2. Extract standardized JSON-safe feature representations
        raw_features = extract_features_from_geodataframe(gdf, source_crs=source_crs)

        # 3. Compute metric measurements and prepare database records
        feature_records: list[FeatureRecord] = []

        for feat in raw_features:
            idx = feat["feature_index"]
            shapely_geom = gdf.iloc[idx].geometry

            # Calculate planar measurements using the selected projected CRS
            measurement_data = measure_geometry(shapely_geom, source_crs=source_crs)

            # Assemble complete feature payload for JSON caching
            complete_payload = {
                "feature_index": feat["feature_index"],
                "geometry_type": feat["geometry_type"],
                "geometry": feat["geometry"],
                "crs": source_crs,
                "properties": feat["properties"],
                "measurement": measurement_data,
                "warnings": measurement_data.get("warnings", []),
            }

            feature_record = FeatureRecord(
                uploaded_file_id=file_record.id,
                feature_index=feat["feature_index"],
                geometry_type=feat["geometry_type"],
                feature_data=complete_payload,
            )
            feature_records.append(feature_record)
            db.add(feature_record)

        # Flush to generate primary keys for each feature record
        db.flush()

        # Stamp feature_id into each persisted JSON payload (reassign dict to trigger ORM change tracking)
        for rec in feature_records:
            payload = dict(rec.feature_data)
            payload["feature_id"] = rec.id
            rec.feature_data = payload

        # 4. Finalize file record status
        file_record.feature_count = len(feature_records)
        file_record.crs = source_crs
        file_record.status = FileStatus.COMPLETED
        file_record.error_message = None

        db.commit()
        db.refresh(file_record)

        logger.info(
            f"Successfully processed file_id {file_id} ('{file_record.filename}'): "
            f"{len(feature_records)} features persisted (CRS: {source_crs})."
        )
        return file_record

    except Exception as exc:
        # Rollback any uncommitted database changes
        db.rollback()

        # Remove any partially inserted feature records for this file
        try:
            db.execute(
                delete(FeatureRecord).where(FeatureRecord.uploaded_file_id == file_id)
            )
            db.commit()
        except Exception as cleanup_err:
            logger.warning(f"Error during feature record cleanup for file_id {file_id}: {cleanup_err}")

        # Preserve full traceback on server console/logs
        logger.error(
            f"Failed processing file_id {file_id} ('{file_record.filename}'): {exc}",
            exc_info=True,
        )

        # Build clean, user-facing error message without stack trace details
        if isinstance(exc, (GeospatialReaderError, StorageError, MissingCRSError, ValueError)):
            clean_error = str(exc)
        else:
            clean_error = "An unexpected error occurred while parsing and measuring the geospatial dataset."

        # Truncate if excessively long
        if len(clean_error) > 500:
            clean_error = clean_error[:497] + "..."

        file_record = db.get(UploadedFile, file_id)
        if file_record:
            file_record.status = FileStatus.FAILED
            file_record.error_message = clean_error
            db.commit()
            db.refresh(file_record)

        return file_record


def ingest_and_process_upload(
    file_content: Union[BinaryIO, bytes],
    original_filename: str,
    db: Session,
) -> UploadedFile:
    """
    Synchronous ingestion endpoint workflow:
        1. Validates extension.
        2. Safely persists file to disk.
        3. Creates initial UploadedFile record in PROCESSING state.
        4. Triggers process_uploaded_file synchronously.
        5. Returns the updated UploadedFile instance.
    """
    ext = Path(original_filename).suffix.lower()
    if ext not in (".zip", ".kml"):
        raise InvalidFileExtensionError(
            f"Unsupported file extension '{ext}'. Only .zip (Shapefile) and .kml are supported."
        )

    # 1. Safely persist upload to disk with size limits and path traversal checks
    stored_path = save_upload_stream(
        content_stream=file_content,
        original_filename=original_filename,
    )

    # 2. Create initial database tracking record
    file_record = UploadedFile(
        filename=original_filename,
        stored_path=str(stored_path),
        status=FileStatus.PROCESSING,
        feature_count=0,
    )
    db.add(file_record)
    db.commit()
    db.refresh(file_record)

    # 3. Synchronously execute the processing pipeline
    return process_uploaded_file(file_id=file_record.id, db=db)
