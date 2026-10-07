"""Comprehensive Phase 6 integration and accuracy test suite.

Validates the complete system as a production geospatial API:
- FastAPI HTTP status codes and uniform error contracts
- Full upload validation and edge-case error envelopes
- Geometry handling across Point, LineString, Polygon, Multi-geometries, and GeometryCollection
- Metric measurement accuracy thresholds (equator, high latitude, 1km line, sample polygon)
- CRS reprojection, UTM zone selection, southern hemisphere, boundaries, and polar fallbacks
- Security protections (zip-slip, decompression bomb, filename sanitization)
- Database persistence verification without raw file reprocessing
"""

import io
import os
from pathlib import Path
import zipfile
from fastapi.testclient import TestClient
import pyproj
import pytest
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models.uploaded_file import FileStatus, UploadedFile
from app.services.crs import (
    determine_target_projected_crs,
    get_transformer,
    get_utm_epsg,
    get_utm_zone,
    is_geographic,
    is_projected,
)
from app.services.file_reader import read_geospatial_file
from app.services.measurement import measure_geometry
from app.services.storage import generate_safe_filename

SAMPLE_KML = Path("sample_data/sample.kml")
SAMPLE_SHAPEFILE = Path("sample_data/sample_shapefile.zip")


@pytest.fixture
def api_client() -> TestClient:
    """Provide TestClient with clean, isolated in-memory SQLite database per test."""
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
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


# ===========================================================================
# 1. UPLOAD LIFECYCLE & VALIDATION TESTS
# ===========================================================================

def test_upload_kml_success(api_client: TestClient):
    """Verify KML upload completes synchronously, returning HTTP 201 with metadata."""
    with open(SAMPLE_KML, "rb") as f:
        resp = api_client.post("/api/files/", files={"file": ("sample.kml", f, "application/xml")})
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] > 0
    assert data["filename"] == "sample.kml"
    assert data["feature_count"] == 3
    assert data["crs"] == "EPSG:4326"
    assert data["status"] == "COMPLETED"


def test_upload_shapefile_zip_success(api_client: TestClient):
    """Verify Shapefile ZIP upload completes synchronously, returning HTTP 201."""
    with open(SAMPLE_SHAPEFILE, "rb") as f:
        resp = api_client.post("/api/files/", files={"file": ("sample_shapefile.zip", f, "application/zip")})
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"] > 0
    assert data["filename"] == "sample_shapefile.zip"
    assert data["feature_count"] == 1
    assert data["crs"] == "EPSG:4326"
    assert data["status"] == "COMPLETED"


def test_upload_invalid_extension(api_client: TestClient):
    """Verify rejected extension returns HTTP 400 and standard error envelope."""
    resp = api_client.post("/api/files/", files={"file": ("data.geojson", b"{}", "application/json")})
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "INVALID_FILE_EXTENSION"
    assert "Only .zip" in err["message"]


def test_upload_empty_file(api_client: TestClient):
    """Verify 0-byte file returns HTTP 400 with EMPTY_FILE code."""
    resp = api_client.post("/api/files/", files={"file": ("empty.kml", b"", "application/xml")})
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "EMPTY_FILE"


def test_upload_corrupt_zip(api_client: TestClient):
    """Verify corrupt zip returns HTTP 400 with CORRUPT_ARCHIVE code."""
    resp = api_client.post("/api/files/", files={"file": ("corrupt.zip", b"PK\x03\x04broken_data", "application/zip")})
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "CORRUPT_ARCHIVE"


def test_upload_zip_missing_shp(api_client: TestClient):
    """Verify zip missing .shp returns HTTP 400 with MISSING_SHAPEFILE_COMPONENTS."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("test.shx", b"shx")
        zf.writestr("test.dbf", b"dbf")
        zf.writestr("test.prj", b"prj")

    resp = api_client.post("/api/files/", files={"file": ("missing_shp.zip", buf.getvalue(), "application/zip")})
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "MISSING_SHAPEFILE_COMPONENTS"


def test_upload_zip_missing_shx(api_client: TestClient):
    """Verify zip missing .shx returns HTTP 400 with MISSING_SHAPEFILE_COMPONENTS."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("test.shp", b"shp")
        zf.writestr("test.dbf", b"dbf")
        zf.writestr("test.prj", b"prj")

    resp = api_client.post("/api/files/", files={"file": ("missing_shx.zip", buf.getvalue(), "application/zip")})
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "MISSING_SHAPEFILE_COMPONENTS"


def test_upload_zip_missing_dbf(api_client: TestClient):
    """Verify zip missing .dbf returns HTTP 400 with MISSING_SHAPEFILE_COMPONENTS."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("test.shp", b"shp")
        zf.writestr("test.shx", b"shx")
        zf.writestr("test.prj", b"prj")

    resp = api_client.post("/api/files/", files={"file": ("missing_dbf.zip", buf.getvalue(), "application/zip")})
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "MISSING_SHAPEFILE_COMPONENTS"


def test_upload_zip_missing_crs_prj(api_client: TestClient):
    """Verify shapefile zip missing .prj fails with MISSING_CRS code."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("test.shp", b"shp")
        zf.writestr("test.shx", b"shx")
        zf.writestr("test.dbf", b"dbf")

    resp = api_client.post("/api/files/", files={"file": ("no_prj.zip", buf.getvalue(), "application/zip")})
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "MISSING_CRS"


def test_upload_oversized_file(api_client: TestClient, monkeypatch):
    """Verify upload exceeding maximum configured size returns HTTP 413."""
    # Temporarily lower maximum upload limit to 100 KB
    from app.config import settings
    monkeypatch.setattr(settings, "max_upload_size_bytes", 100 * 1024)

    oversized_data = b"X" * (150 * 1024)
    resp = api_client.post("/api/files/", files={"file": ("too_large.kml", oversized_data, "application/xml")})
    assert resp.status_code == 413
    err = resp.json()["error"]
    assert err["code"] == "FILE_TOO_LARGE"


def test_upload_zero_feature_kml(api_client: TestClient):
    """Verify valid XML/KML with zero spatial features returns HTTP 422 ZERO_FEATURES."""
    empty_doc = '<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document></Document></kml>'
    resp = api_client.post("/api/files/", files={"file": ("zero_feats.kml", empty_doc.encode("utf-8"), "application/xml")})
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "ZERO_FEATURES"


def test_upload_unreadable_kml(api_client: TestClient):
    """Verify malformed KML markup returns HTTP 400 and standard error code."""
    resp = api_client.post("/api/files/", files={"file": ("broken.kml", b"not xml at all", "application/xml")})
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] in ("UNREADABLE_KML", "INVALID_FILE_CONTENT")


# ===========================================================================
# 2. FILE ENDPOINT & STATUS TRANSITION TESTS
# ===========================================================================

def test_file_detail_successful_lookup(api_client: TestClient):
    """Verify GET /api/files/{id}/ returns exact file metadata and status."""
    with open(SAMPLE_KML, "rb") as f:
        post_resp = api_client.post("/api/files/", files={"file": ("sample.kml", f, "application/xml")})
    file_id = post_resp.json()["id"]

    resp = api_client.get(f"/api/files/{file_id}/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == file_id
    assert data["filename"] == "sample.kml"
    assert data["feature_count"] == 3
    assert data["crs"] == "EPSG:4326"
    assert data["status"] == "COMPLETED"
    assert data["error_message"] is None
    assert "created_at" in data


def test_file_detail_unknown_404(api_client: TestClient):
    """Verify GET /api/files/99999/ returns HTTP 404 with standard error envelope."""
    resp = api_client.get("/api/files/99999/")
    assert resp.status_code == 404
    err = resp.json()["error"]
    assert err["code"] == "FILE_NOT_FOUND"


# ===========================================================================
# 3. MEASUREMENTS RETRIEVAL, PAGINATION, AND PERSISTENCE TESTS
# ===========================================================================

def test_measurements_successful_retrieval(api_client: TestClient):
    """Verify GET /api/files/{id}/measurements/ returns per-feature records."""
    with open(SAMPLE_KML, "rb") as f:
        post_resp = api_client.post("/api/files/", files={"file": ("sample.kml", f, "application/xml")})
    file_id = post_resp.json()["id"]

    resp = api_client.get(f"/api/files/{file_id}/measurements/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["file_id"] == file_id
    assert data["total_features"] == 3
    assert len(data["features"]) == 3


def test_measurements_unknown_id_404(api_client: TestClient):
    """Verify measurements query for non-existent file ID returns 404."""
    resp = api_client.get("/api/files/88888/measurements/")
    assert resp.status_code == 404
    err = resp.json()["error"]
    assert err["code"] == "FILE_NOT_FOUND"


def test_measurements_incomplete_file_409(api_client: TestClient):
    """Verify measurements query for a non-COMPLETED file returns 409 Conflict."""
    override_fn = app.dependency_overrides[get_db]
    db = next(override_fn())

    incomplete_file = UploadedFile(
        filename="processing.zip",
        stored_path="uploads/mock.zip",
        status=FileStatus.PROCESSING,
    )
    db.add(incomplete_file)
    db.commit()
    db.refresh(incomplete_file)

    resp = api_client.get(f"/api/files/{incomplete_file.id}/measurements/")
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "FILE_NOT_READY"


def test_measurements_pagination(api_client: TestClient):
    """Verify limit and offset pagination parameters."""
    with open(SAMPLE_KML, "rb") as f:
        post_resp = api_client.post("/api/files/", files={"file": ("sample.kml", f, "application/xml")})
    file_id = post_resp.json()["id"]

    # Limit 1, offset 0 -> Page 1
    resp_p1 = api_client.get(f"/api/files/{file_id}/measurements/?limit=1&offset=0")
    assert resp_p1.status_code == 200
    data_p1 = resp_p1.json()
    assert data_p1["total_features"] == 3
    assert len(data_p1["features"]) == 1
    assert data_p1["features"][0]["feature_id"] == 1

    # Limit 1, offset 1 -> Page 2
    resp_p2 = api_client.get(f"/api/files/{file_id}/measurements/?limit=1&offset=1")
    assert resp_p2.status_code == 200
    data_p2 = resp_p2.json()
    assert len(data_p2["features"]) == 1
    assert data_p2["features"][0]["feature_id"] == 2


def test_measurements_geometry_type_filter(api_client: TestClient):
    """Verify filtering by geometry_type returns only matching primitives."""
    with open(SAMPLE_KML, "rb") as f:
        post_resp = api_client.post("/api/files/", files={"file": ("sample.kml", f, "application/xml")})
    file_id = post_resp.json()["id"]

    # Filter for LineString
    resp_lines = api_client.get(f"/api/files/{file_id}/measurements/?geometry_type=LineString")
    assert resp_lines.status_code == 200
    data_lines = resp_lines.json()
    assert data_lines["total_features"] == 1
    assert data_lines["features"][0]["geometry_type"] == "LineString"

    # Filter for Point
    resp_pts = api_client.get(f"/api/files/{file_id}/measurements/?geometry_type=Point")
    assert resp_pts.status_code == 200
    assert resp_pts.json()["total_features"] == 1


def test_measurements_returned_without_reprocessing(api_client: TestClient):
    """
    CRITICAL ARCHITECTURAL TEST:
    Verify that measurements are retrieved purely from persisted database JSON
    even after the physical file is deleted from disk.
    """
    with open(SAMPLE_KML, "rb") as f:
        post_resp = api_client.post("/api/files/", files={"file": ("sample.kml", f, "application/xml")})
    file_id = post_resp.json()["id"]

    # Get file record to find stored path on disk
    override_fn = app.dependency_overrides[get_db]
    db = next(override_fn())
    file_rec = db.get(UploadedFile, file_id)
    assert file_rec is not None
    stored_path = Path(file_rec.stored_path)

    # Physically delete the uploaded file from disk!
    if stored_path.exists():
        stored_path.unlink()
    assert not stored_path.exists()

    # Query measurements: must succeed without crashing or attempting to re-read the file!
    resp = api_client.get(f"/api/files/{file_id}/measurements/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_features"] == 3
    assert len(data["features"]) == 3
    assert data["features"][0]["measurement"]["value"] is not None


# ===========================================================================
# 4. MEASUREMENT ACCURACY TESTS (WITHIN 1%)
# ===========================================================================

def test_accuracy_tiruchengode_polygon(api_client: TestClient):
    """Verify Tiruchengode sample polygon measures ~12,111.29 m² within 1% in EPSG:32644."""
    with open(SAMPLE_KML, "rb") as f:
        post_resp = api_client.post("/api/files/", files={"file": ("sample.kml", f, "application/xml")})
    file_id = post_resp.json()["id"]

    resp = api_client.get(f"/api/files/{file_id}/measurements/?geometry_type=Polygon")
    poly_feat = resp.json()["features"][0]
    meas = poly_feat["measurement"]

    assert meas["type"] == "area"
    assert meas["unit"] == "m²"
    assert meas["projected_crs"] == "EPSG:32644"
    assert pytest.approx(12111.29, rel=0.01) == meas["value"]
    assert pytest.approx(1.211129, rel=0.01) == meas["hectares"]


def test_accuracy_tiruchengode_line(api_client: TestClient):
    """Verify Tiruchengode sample line measures ~1,093.89 m within 1% in EPSG:32644."""
    with open(SAMPLE_KML, "rb") as f:
        post_resp = api_client.post("/api/files/", files={"file": ("sample.kml", f, "application/xml")})
    file_id = post_resp.json()["id"]

    resp = api_client.get(f"/api/files/{file_id}/measurements/?geometry_type=LineString")
    line_feat = resp.json()["features"][0]
    meas = line_feat["measurement"]

    assert meas["type"] == "length"
    assert meas["unit"] == "m"
    assert meas["projected_crs"] == "EPSG:32644"
    assert pytest.approx(1093.89, rel=0.01) == meas["value"]
    assert pytest.approx(1.09389, rel=0.01) == meas["kilometers"]


def test_accuracy_equator_square():
    """Verify 100m x 100m square near equator measures ~10,000 m² within 1%."""
    d_lon = 100.0 / 111319.49
    d_lat = 100.0 / 110574.0
    poly = Polygon([(0.0, 0.0), (d_lon, 0.0), (d_lon, d_lat), (0.0, d_lat), (0.0, 0.0)])
    result = measure_geometry(poly, source_crs="EPSG:4326")

    assert result["supported"] is True
    assert result["type"] == "area"
    assert pytest.approx(10000.0, rel=0.01) == result["value"]


def test_accuracy_high_latitude_square():
    """Verify 100m x 100m square at high latitude (70°N) measures ~10,000 m² within 1%."""
    geod = pyproj.Geod(ellps="WGS84")
    lon1, lat1 = 15.0, 70.0
    lon2, lat2, _ = geod.fwd(lon1, lat1, 90, 100)
    lon3, lat3, _ = geod.fwd(lon2, lat2, 0, 100)
    lon4, lat4, _ = geod.fwd(lon1, lat1, 0, 100)
    poly = Polygon([(lon1, lat1), (lon2, lat2), (lon3, lat3), (lon4, lat4), (lon1, lat1)])

    result = measure_geometry(poly, source_crs="EPSG:4326")
    assert result["supported"] is True
    assert pytest.approx(10000.0, rel=0.01) == result["value"]


def test_accuracy_one_kilometer_line():
    """Verify 1km geodesic line measures ~1,000m within 1%."""
    geod = pyproj.Geod(ellps="WGS84")
    lon_end, lat_end, _ = geod.fwd(78.0, 11.0, 90, 1000)
    line = LineString([(78.0, 11.0), (lon_end, lat_end)])

    result = measure_geometry(line, source_crs="EPSG:4326")
    assert result["supported"] is True
    assert result["type"] == "length"
    assert pytest.approx(1000.0, rel=0.01) == result["value"]
    assert pytest.approx(1.0, rel=0.01) == result["kilometers"]


# ===========================================================================
# 5. GEOMETRY TYPES & SPECIAL CASES TESTS
# ===========================================================================

def test_geometry_point_and_multipoint():
    """Verify Point and MultiPoint return supported=True with value=None."""
    pt = Point(78.0, 11.0)
    res_pt = measure_geometry(pt, source_crs="EPSG:4326")
    assert res_pt["supported"] is True
    assert res_pt["value"] is None
    assert res_pt["type"] == "point"

    mpt = MultiPoint([(78.0, 11.0), (78.01, 11.01)])
    res_mpt = measure_geometry(mpt, source_crs="EPSG:4326")
    assert res_mpt["supported"] is True
    assert res_mpt["value"] is None


def test_geometry_multipolygon_and_multilinestring():
    """Verify MultiPolygon computes total area and MultiLineString computes total length."""
    p1 = Polygon([(78.0, 11.0), (78.001, 11.0), (78.001, 11.001), (78.0, 11.001), (78.0, 11.0)])
    p2 = Polygon([(78.01, 11.0), (78.011, 11.0), (78.011, 11.001), (78.01, 11.001), (78.01, 11.0)])
    mpoly = MultiPolygon([p1, p2])

    res_mpoly = measure_geometry(mpoly, source_crs="EPSG:4326")
    assert res_mpoly["supported"] is True
    assert res_mpoly["type"] == "area"
    # Combined area is roughly 2x single polygon area
    assert pytest.approx(24222.58, rel=0.01) == res_mpoly["value"]

    l1 = LineString([(78.0, 11.0), (78.01, 11.0)])
    l2 = LineString([(78.01, 11.0), (78.02, 11.0)])
    mline = MultiLineString([l1, l2])

    res_mline = measure_geometry(mline, source_crs="EPSG:4326")
    assert res_mline["supported"] is True
    assert res_mline["type"] == "length"
    # Combined length is roughly 2x single line length
    assert pytest.approx(2187.78, rel=0.01) == res_mline["value"]


def test_geometry_collection_graceful_unsupported():
    """Verify GeometryCollection is marked supported=False with explanation."""
    gc = GeometryCollection([Point(0, 0), LineString([(0, 0), (1, 1)])])
    res = measure_geometry(gc, source_crs="EPSG:4326")
    assert res["supported"] is False
    assert res["value"] is None
    assert "heterogeneous" in res["reason"]


def test_empty_geometry_graceful_unsupported():
    """Verify empty geometry returns supported=False."""
    res = measure_geometry(Polygon(), source_crs="EPSG:4326")
    assert res["supported"] is False
    assert res["type"] == "empty"


def test_invalid_polygon_repaired_with_warning():
    """Verify self-intersecting polygon is repaired via make_valid() with warning added."""
    bowtie = Polygon([(0.0, 0.0), (0.002, 0.002), (0.002, 0.0), (0.0, 0.002), (0.0, 0.0)])
    assert not bowtie.is_valid

    res = measure_geometry(bowtie, source_crs="EPSG:4326")
    assert res["supported"] is True
    assert res["value"] > 0
    assert any("Invalid geometry detected" in w for w in res["warnings"])


# ===========================================================================
# 6. CRS BEHAVIOR & PROJECTION ACCURACY TESTS
# ===========================================================================

def test_crs_geographic_vs_projected():
    """Verify geographic CRS is detected and never measured in degrees."""
    assert is_geographic("EPSG:4326") is True
    assert is_projected("EPSG:4326") is False
    assert is_projected("EPSG:32644") is True


def test_crs_utm_selection_and_hemisphere():
    """Verify UTM zone selection for northern and southern coordinates."""
    # Tiruchengode, India
    assert get_utm_epsg(lon=78.0, lat=11.0) == "EPSG:32644"
    # Southern hemisphere
    assert get_utm_epsg(lon=78.0, lat=-11.0) == "EPSG:32744"


def test_crs_projected_source_in_meters():
    """Verify source CRS already projected in meters is preserved."""
    target_crs, warnings = determine_target_projected_crs("EPSG:3857", centroid_lon=0.0, centroid_lat=0.0)
    assert target_crs == "EPSG:3857"
    assert len(warnings) == 0


def test_crs_projected_source_with_non_meter_units():
    """Verify projected CRS in feet (EPSG:2227) is converted to meters."""
    # NAD83 / California zone 3 (US Survey Foot)
    target_crs, warnings = determine_target_projected_crs("EPSG:2227", centroid_lon=-122.0, centroid_lat=37.5)
    assert target_crs == "EPSG:2227"
    assert any("scaled to meters" in w for w in warnings)


def test_crs_zone_boundary_warning():
    """Verify crossing UTM zone boundaries produces accuracy warning."""
    # Bounding box spanning lon 5.9 to 6.1 (crosses Zone 31 to Zone 32)
    bounds = (5.9, 45.0, 6.1, 46.0)
    _, warnings = determine_target_projected_crs("EPSG:4326", centroid_lon=6.0, centroid_lat=45.5, bounds=bounds)
    assert any("crosses UTM zone boundaries" in w for w in warnings)


def test_crs_polar_fallback_epsg_6933():
    """Verify latitude beyond 84° triggers fallback to EPSG:6933 with warning."""
    target_crs, warnings = determine_target_projected_crs("EPSG:4326", centroid_lon=0.0, centroid_lat=85.5)
    assert target_crs == "EPSG:6933"
    assert any("exceeds standard UTM limits" in w for w in warnings)


def test_projected_crs_included_in_every_measurement():
    """Verify every measurable feature contains projected_crs in response."""
    poly = Polygon([(78.0, 11.0), (78.001, 11.0), (78.001, 11.001), (78.0, 11.001), (78.0, 11.0)])
    res = measure_geometry(poly, source_crs="EPSG:4326")
    assert res["projected_crs"] == "EPSG:32644"


# ===========================================================================
# 7. SECURITY & INTEGRITY TESTS
# ===========================================================================

def test_security_zip_slip_rejection(api_client: TestClient):
    """Verify zip archive with directory traversal payload is rejected."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../etc/cron.daily/malicious", b"rm -rf /")
        zf.writestr("test.shp", b"shp")
        zf.writestr("test.shx", b"shx")
        zf.writestr("test.dbf", b"dbf")
        zf.writestr("test.prj", b"prj")

    resp = api_client.post("/api/files/", files={"file": ("slip.zip", buf.getvalue(), "application/zip")})
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "SECURITY_VIOLATION"


def test_security_decompression_bomb_rejection(api_client: TestClient, monkeypatch):
    """Verify archive with excessively large uncompressed footprint is rejected."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("large.shp", b"\x00" * (2 * 1024 * 1024))
        zf.writestr("large.shx", b"shx")
        zf.writestr("large.dbf", b"dbf")
        zf.writestr("large.prj", b"prj")

    # Lower uncompressed threshold to 500 KB to simulate bomb protection
    from app.services import file_reader
    monkeypatch.setattr(file_reader, "MAX_UNCOMPRESSED_ARCHIVE_BYTES", 500 * 1024)

    resp = api_client.post("/api/files/", files={"file": ("bomb.zip", buf.getvalue(), "application/zip")})
    assert resp.status_code == 413
    err = resp.json()["error"]
    assert err["code"] == "ARCHIVE_TOO_LARGE"


def test_security_unsafe_filename_sanitization():
    """Verify unsafe client filenames are never preserved on disk."""
    safe_name = generate_safe_filename("../../../etc/passwd.kml")
    assert safe_name.endswith(".kml")
    assert "/" not in safe_name
    assert "\\" not in safe_name
    assert "passwd" not in safe_name
