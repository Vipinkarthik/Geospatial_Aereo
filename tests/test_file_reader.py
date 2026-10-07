"""Unit tests for geospatial file reading, archive security, and format validation."""

from pathlib import Path
import zipfile
import pytest

from app.services.file_reader import (
    CorruptZipFileError,
    DecompressionBombError,
    InvalidFileContentError,
    InvalidFileExtensionError,
    MissingCRSFileError,
    MissingShapefileComponentError,
    ZeroFeaturesError,
    ZipSlipError,
    read_geospatial_file,
)

SAMPLE_KML = Path("sample_data/sample.kml")
SAMPLE_SHAPEFILE = Path("sample_data/sample_shapefile.zip")


def test_read_sample_kml_features_and_crs():
    """Verify sample KML reads 3 features, preserves EPSG:4326, and retains attributes."""
    gdf, crs_str = read_geospatial_file(SAMPLE_KML)

    assert len(gdf) == 3
    assert crs_str == "EPSG:4326"
    assert "Name" in gdf.columns

    # Verify attributes preserved
    names = set(gdf["Name"].dropna())
    assert "Plot A" in names
    assert "Road 1" in names
    assert "Marker 1" in names

    # Verify geometry types present
    geom_types = set(gdf.geom_type)
    assert {"Polygon", "LineString", "Point"}.issubset(geom_types)


def test_read_sample_shapefile_features_and_crs():
    """Verify sample shapefile reads 1 feature with EPSG:4326."""
    gdf, crs_str = read_geospatial_file(SAMPLE_SHAPEFILE)

    assert len(gdf) == 1
    assert crs_str == "EPSG:4326"
    assert "name" in gdf.columns
    assert gdf.iloc[0]["name"] == "Plot A"
    assert gdf.iloc[0].geometry.geom_type == "Polygon"


def test_invalid_file_extension(tmp_path):
    """Verify unsupported extensions (e.g., .geojson, .txt) are rejected."""
    unsupported = tmp_path / "data.geojson"
    unsupported.write_text('{"type": "FeatureCollection", "features": []}', encoding="utf-8")

    with pytest.raises(InvalidFileExtensionError):
        read_geospatial_file(unsupported)


def test_empty_kml_file(tmp_path):
    """Verify empty KML file is rejected."""
    empty_kml = tmp_path / "empty.kml"
    empty_kml.write_bytes(b"")

    with pytest.raises(InvalidFileContentError):
        read_geospatial_file(empty_kml)


def test_corrupt_zip_file(tmp_path):
    """Verify corrupt zip file is detected and rejected."""
    corrupt_zip = tmp_path / "corrupt.zip"
    # Valid magic bytes followed by truncated/garbage data
    corrupt_zip.write_bytes(b"PK\x03\x04corrupted_payload_data_here")

    with pytest.raises(CorruptZipFileError):
        read_geospatial_file(corrupt_zip)


def test_missing_shapefile_components(tmp_path):
    """Verify shapefile zip missing .shx or .dbf fails validation."""
    incomplete_zip = tmp_path / "missing_dbf.zip"
    with zipfile.ZipFile(incomplete_zip, "w") as zf:
        zf.writestr("test.shp", b"dummy shp")
        zf.writestr("test.shx", b"dummy shx")
        zf.writestr("test.prj", b"dummy prj")
        # .dbf is intentionally missing

    with pytest.raises(MissingShapefileComponentError):
        read_geospatial_file(incomplete_zip)


def test_missing_shapefile_crs_prj(tmp_path):
    """Verify shapefile missing .prj fails with explicit MissingCRSFileError."""
    no_prj_zip = tmp_path / "missing_prj.zip"
    with zipfile.ZipFile(no_prj_zip, "w") as zf:
        zf.writestr("data.shp", b"dummy shp")
        zf.writestr("data.shx", b"dummy shx")
        zf.writestr("data.dbf", b"dummy dbf")
        # .prj is intentionally missing

    with pytest.raises(MissingCRSFileError):
        read_geospatial_file(no_prj_zip)


def test_zip_slip_security_attempt(tmp_path):
    """Verify directory traversal (zip-slip) within archive triggers ZipSlipError."""
    malicious_zip = tmp_path / "zipslip.zip"
    with zipfile.ZipFile(malicious_zip, "w") as zf:
        zf.writestr("../../etc/cron.d/attack.sh", b"malicious script")
        zf.writestr("data.shp", b"shp")
        zf.writestr("data.shx", b"shx")
        zf.writestr("data.dbf", b"dbf")
        zf.writestr("data.prj", b"prj")

    with pytest.raises(ZipSlipError):
        read_geospatial_file(malicious_zip)


def test_decompression_bomb_protection(tmp_path):
    """Verify archives exceeding uncompressed size limits are blocked."""
    bomb_zip = tmp_path / "bomb.zip"
    with zipfile.ZipFile(bomb_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # 10 MB of zeros compresses to a few kilobytes
        zf.writestr("data.shp", b"\x00" * (10 * 1024 * 1024))
        zf.writestr("data.shx", b"shx")
        zf.writestr("data.dbf", b"dbf")
        zf.writestr("data.prj", b"prj")

    # Set threshold lower than 10MB to trigger protection
    with pytest.raises(DecompressionBombError):
        read_geospatial_file(bomb_zip, max_uncompressed_bytes=1024 * 1024)


def test_zero_features_kml_rejected(tmp_path):
    """Verify KML with no spatial features is rejected."""
    empty_features_kml = tmp_path / "zero_features.kml"
    empty_features_kml.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document></Document></kml>',
        encoding="utf-8",
    )

    with pytest.raises(ZeroFeaturesError):
        read_geospatial_file(empty_features_kml)
