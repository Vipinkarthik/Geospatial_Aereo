"""Integration tests for FastAPI endpoints, pagination, filtering, 404/409 handling, and standard error envelopes."""

import io
from pathlib import Path
import zipfile
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models.uploaded_file import FileStatus, UploadedFile

SAMPLE_KML = Path("sample_data/sample.kml")
SAMPLE_SHAPEFILE = Path("sample_data/sample_shapefile.zip")


@pytest.fixture
def client() -> TestClient:
    """FastAPI TestClient fixture with overridden in-memory database using StaticPool."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


def test_health_endpoint(client: TestClient):
    """Verify /health returns HTTP 200 with operational status."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "app" in data
    assert "version" in data


def test_upload_kml_success(client: TestClient):
    """Verify POST /api/files/ with valid KML returns 201 and persists 3 features."""
    with open(SAMPLE_KML, "rb") as f:
        response = client.post(
            "/api/files/",
            files={"file": ("sample.kml", f, "application/vnd.google-earth.kml+xml")},
        )

    assert response.status_code == 201
    data = response.json()
    assert data["id"] > 0
    assert data["filename"] == "sample.kml"
    assert data["feature_count"] == 3
    assert data["crs"] == "EPSG:4326"
    assert data["status"] == "COMPLETED"


def test_upload_shapefile_zip_success(client: TestClient):
    """Verify POST /api/files/ with valid Shapefile zip returns 201 and persists 1 feature."""
    with open(SAMPLE_SHAPEFILE, "rb") as f:
        response = client.post(
            "/api/files/",
            files={"file": ("sample_shapefile.zip", f, "application/zip")},
        )

    assert response.status_code == 201
    data = response.json()
    assert data["id"] > 0
    assert data["filename"] == "sample_shapefile.zip"
    assert data["feature_count"] == 1
    assert data["crs"] == "EPSG:4326"
    assert data["status"] == "COMPLETED"


def test_get_file_detail(client: TestClient):
    """Verify GET /api/files/{id}/ returns file metadata."""
    # First upload
    with open(SAMPLE_KML, "rb") as f:
        post_resp = client.post(
            "/api/files/",
            files={"file": ("sample.kml", f, "application/vnd.google-earth.kml+xml")},
        )
    file_id = post_resp.json()["id"]

    # Then retrieve
    get_resp = client.get(f"/api/files/{file_id}/")
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert data["id"] == file_id
    assert data["filename"] == "sample.kml"
    assert data["feature_count"] == 3
    assert data["status"] == "COMPLETED"
    assert data["error_message"] is None
    assert "created_at" in data


def test_get_file_detail_unknown_404(client: TestClient):
    """Verify GET /api/files/99999/ returns 404 with standard error envelope."""
    response = client.get("/api/files/99999/")
    assert response.status_code == 404
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "FILE_NOT_FOUND"
    assert "99999" in data["error"]["message"]


def test_get_measurements_pagination_and_filtering(client: TestClient):
    """Verify GET /api/files/{id}/measurements/ supports limit, offset, and geometry_type filter."""
    with open(SAMPLE_KML, "rb") as f:
        post_resp = client.post(
            "/api/files/",
            files={"file": ("sample.kml", f, "application/vnd.google-earth.kml+xml")},
        )
    file_id = post_resp.json()["id"]

    # 1. Fetch all measurements
    meas_resp = client.get(f"/api/files/{file_id}/measurements/")
    assert meas_resp.status_code == 200
    meas_data = meas_resp.json()
    assert meas_data["total_features"] == 3
    assert len(meas_data["features"]) == 3

    # Check Polygon feature measurement
    poly_feat = next(f for f in meas_data["features"] if f["geometry_type"] == "Polygon")
    assert poly_feat["measurement"]["type"] == "area"
    assert poly_feat["measurement"]["unit"] == "m²"
    assert pytest.approx(12111.29, rel=0.01) == poly_feat["measurement"]["value"]
    assert pytest.approx(1.211129, rel=0.01) == poly_feat["measurement"]["hectares"]

    # Check LineString feature measurement
    line_feat = next(f for f in meas_data["features"] if f["geometry_type"] == "LineString")
    assert line_feat["measurement"]["type"] == "length"
    assert line_feat["measurement"]["unit"] == "m"
    assert pytest.approx(1093.89, rel=0.01) == line_feat["measurement"]["value"]

    # Check Point feature measurement
    pt_feat = next(f for f in meas_data["features"] if f["geometry_type"] == "Point")
    assert pt_feat["measurement"]["supported"] is True
    assert pt_feat["measurement"]["value"] is None

    # 2. Filter by geometry_type=Polygon
    filtered_resp = client.get(f"/api/files/{file_id}/measurements/?geometry_type=Polygon")
    assert filtered_resp.status_code == 200
    filtered_data = filtered_resp.json()
    assert filtered_data["total_features"] == 1
    assert len(filtered_data["features"]) == 1
    assert filtered_data["features"][0]["geometry_type"] == "Polygon"

    # 3. Test pagination limit=1, offset=1
    page_resp = client.get(f"/api/files/{file_id}/measurements/?limit=1&offset=1")
    assert page_resp.status_code == 200
    page_data = page_resp.json()
    assert page_data["limit"] == 1
    assert page_data["offset"] == 1
    assert page_data["total_features"] == 3
    assert len(page_data["features"]) == 1


def test_measurements_unknown_id_404(client: TestClient):
    """Verify measurements query for nonexistent file returns 404."""
    response = client.get("/api/files/88888/measurements/")
    assert response.status_code == 404
    data = response.json()
    assert data["error"]["code"] == "FILE_NOT_FOUND"


def test_measurements_not_completed_409(client: TestClient):
    """Verify measurements query for incomplete or failed file returns 409 Conflict."""
    # Seed an incomplete record in db
    override_fn = app.dependency_overrides[get_db]
    db_gen = override_fn()
    db = next(db_gen)

    failed_file = UploadedFile(
        filename="corrupted.kml",
        stored_path="uploads/corrupted.kml",
        status=FileStatus.FAILED,
        error_message="Corrupt markup",
    )
    db.add(failed_file)
    db.commit()
    db.refresh(failed_file)

    response = client.get(f"/api/files/{failed_file.id}/measurements/")
    assert response.status_code == 409
    data = response.json()
    assert data["error"]["code"] == "FILE_NOT_READY"
    assert "FAILED" in data["error"]["message"]


def test_upload_invalid_extension_error_format(client: TestClient):
    """Verify invalid file extension returns standard error format."""
    response = client.post(
        "/api/files/",
        files={"file": ("data.geojson", b'{"type": "FeatureCollection"}', "application/geo+json")},
    )
    assert response.status_code == 400
    data = response.json()
    assert "error" in data
    assert data["error"]["code"] == "INVALID_FILE_EXTENSION"
    assert "Only .zip" in data["error"]["message"]


def test_upload_empty_file_error_format(client: TestClient):
    """Verify empty upload returns 400 and EMPTY_FILE error."""
    response = client.post(
        "/api/files/",
        files={"file": ("empty.kml", b"", "application/vnd.google-earth.kml+xml")},
    )
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "EMPTY_FILE"


def test_upload_corrupt_zip_error_format(client: TestClient):
    """Verify corrupt zip returns 400 and CORRUPT_ARCHIVE error."""
    response = client.post(
        "/api/files/",
        files={"file": ("corrupted.zip", b"PK\x03\x04bad_archive_bytes", "application/zip")},
    )
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "CORRUPT_ARCHIVE"


def test_upload_missing_crs_prj_error_format(client: TestClient):
    """Verify shapefile missing .prj returns 400 and MISSING_CRS error."""
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("test.shp", b"dummy shp")
        zf.writestr("test.shx", b"dummy shx")
        zf.writestr("test.dbf", b"dummy dbf")

    response = client.post(
        "/api/files/",
        files={"file": ("no_crs.zip", zip_buf.getvalue(), "application/zip")},
    )
    assert response.status_code == 400
    data = response.json()
    assert data["error"]["code"] == "MISSING_CRS"
