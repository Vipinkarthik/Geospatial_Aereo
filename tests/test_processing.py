"""Unit tests for the end-to-end processing pipeline and Pydantic schema validation."""

import io
from pathlib import Path
import zipfile
import pytest
from sqlalchemy import select

from app.models.feature_record import FeatureRecord
from app.models.uploaded_file import FileStatus, UploadedFile
from app.schemas.files import FileDetailResponse, FileUploadResponse
from app.schemas.measurements import (
    FeatureMeasurementResponse,
    PaginatedMeasurementResponse,
)
from app.services.file_reader import InvalidFileExtensionError
from app.services.processing import (
    ingest_and_process_upload,
    process_uploaded_file,
)

SAMPLE_KML = Path("sample_data/sample.kml")
SAMPLE_SHAPEFILE = Path("sample_data/sample_shapefile.zip")


def test_processing_pipeline_kml_success(db_session, tmp_path):
    """Verify end-to-end pipeline parses KML, persists 3 records, and populates measurements."""
    kml_bytes = SAMPLE_KML.read_bytes()

    file_record = ingest_and_process_upload(
        file_content=kml_bytes,
        original_filename="sample.kml",
        db=db_session,
    )

    assert file_record.status == FileStatus.COMPLETED
    assert file_record.feature_count == 3
    assert file_record.crs == "EPSG:4326"
    assert file_record.error_message is None

    # Verify FeatureRecords persisted in database
    records = db_session.scalars(
        select(FeatureRecord).where(FeatureRecord.uploaded_file_id == file_record.id).order_by(FeatureRecord.feature_index)
    ).all()
    assert len(records) == 3

    # Feature 0: Polygon
    poly_rec = records[0]
    assert poly_rec.geometry_type == "Polygon"
    meas_poly = poly_rec.feature_data["measurement"]
    assert meas_poly["type"] == "area"
    assert meas_poly["supported"] is True
    assert pytest.approx(12111.29, rel=0.01) == meas_poly["value"]
    assert pytest.approx(1.211129, rel=0.01) == meas_poly["hectares"]

    # Feature 1: LineString
    line_rec = records[1]
    assert line_rec.geometry_type == "LineString"
    meas_line = line_rec.feature_data["measurement"]
    assert meas_line["type"] == "length"
    assert meas_line["supported"] is True
    assert pytest.approx(1093.89, rel=0.01) == meas_line["value"]
    assert pytest.approx(1.09389, rel=0.01) == meas_line["kilometers"]

    # Feature 2: Point
    pt_rec = records[2]
    assert pt_rec.geometry_type == "Point"
    meas_pt = pt_rec.feature_data["measurement"]
    assert meas_pt["supported"] is True
    assert meas_pt["value"] is None

    # Validate against Pydantic response models
    upload_resp = FileUploadResponse(
        file_id=file_record.id,
        filename=file_record.filename,
        status=file_record.status,
        feature_count=file_record.feature_count,
        crs=file_record.crs,
        message="Upload processed successfully",
    )
    assert upload_resp.file_id == file_record.id

    feature_resps = [
        FeatureMeasurementResponse(**rec.feature_data)
        for rec in records
    ]
    assert len(feature_resps) == 3

    paginated_resp = PaginatedMeasurementResponse(
        file_id=file_record.id,
        filename=file_record.filename,
        total_features=3,
        page=1,
        page_size=10,
        total_pages=1,
        features=feature_resps,
    )
    assert paginated_resp.total_features == 3


def test_processing_pipeline_shapefile_success(db_session):
    """Verify end-to-end pipeline parses Shapefile zip and computes Polygon area."""
    shp_bytes = SAMPLE_SHAPEFILE.read_bytes()

    file_record = ingest_and_process_upload(
        file_content=shp_bytes,
        original_filename="sample_shapefile.zip",
        db=db_session,
    )

    assert file_record.status == FileStatus.COMPLETED
    assert file_record.feature_count == 1
    assert file_record.crs == "EPSG:4326"

    records = db_session.scalars(
        select(FeatureRecord).where(FeatureRecord.uploaded_file_id == file_record.id)
    ).all()
    assert len(records) == 1
    assert records[0].geometry_type == "Polygon"
    assert pytest.approx(12111.29, rel=0.01) == records[0].feature_data["measurement"]["value"]


def test_processing_failure_corrupt_zip(db_session):
    """Verify corrupt zip fails cleanly with FAILED status and no leaked stack traces."""
    corrupt_bytes = b"PK\x03\x04corrupted_zip_archive_bytes"

    file_record = ingest_and_process_upload(
        file_content=corrupt_bytes,
        original_filename="bad.zip",
        db=db_session,
    )

    assert file_record.status == FileStatus.FAILED
    assert file_record.error_message is not None
    # Verify no Python traceback in user-facing error message
    assert "Traceback" not in file_record.error_message
    assert "File \"" not in file_record.error_message

    # Ensure no orphan feature records were persisted
    records = db_session.scalars(
        select(FeatureRecord).where(FeatureRecord.uploaded_file_id == file_record.id)
    ).all()
    assert len(records) == 0


def test_processing_failure_missing_prj(db_session):
    """Verify shapefile lacking .prj fails gracefully and stores clear diagnostic message."""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as zf:
        zf.writestr("test.shp", b"dummy")
        zf.writestr("test.shx", b"dummy")
        zf.writestr("test.dbf", b"dummy")
        # .prj omitted intentionally

    file_record = ingest_and_process_upload(
        file_content=zip_buffer.getvalue(),
        original_filename="no_crs.zip",
        db=db_session,
    )

    assert file_record.status == FileStatus.FAILED
    assert "missing required .prj" in file_record.error_message.lower()


def test_invalid_extension_rejected_early(db_session):
    """Verify invalid file extensions are rejected before file saving or processing."""
    with pytest.raises(InvalidFileExtensionError):
        ingest_and_process_upload(
            file_content=b'{"type": "FeatureCollection"}',
            original_filename="survey.geojson",
            db=db_session,
        )
