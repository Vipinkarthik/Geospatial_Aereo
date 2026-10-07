"""Unit tests for geometric measurements, accuracy thresholds, and edge-case handling."""

import pyproj
import pytest
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiPolygon,
    Point,
    Polygon,
)

from app.services.measurement import measure_geometry


def test_tiruchengode_sample_polygon():
    """Verify Tiruchengode sample polygon yields ~12,111.29 m² within 1% in EPSG:32644."""
    # Matches the polygon in sample_data/sample.kml and sample_shapefile.zip
    poly = Polygon([
        (78.0, 11.0),
        (78.001, 11.0),
        (78.001, 11.001),
        (78.0, 11.001),
        (78.0, 11.0),
    ])
    result = measure_geometry(poly, source_crs="EPSG:4326")

    assert result["supported"] is True
    assert result["type"] == "area"
    assert result["unit"] == "m²"
    assert result["projected_crs"] == "EPSG:32644"
    assert pytest.approx(12111.29, rel=0.01) == result["value"]
    assert pytest.approx(1.211129, rel=0.01) == result["hectares"]


def test_tiruchengode_sample_line():
    """Verify Tiruchengode sample line yields ~1,093.89 m within 1% in EPSG:32644."""
    # Matches the line in sample_data/sample.kml
    line = LineString([(78.0, 11.0), (78.01, 11.0)])
    result = measure_geometry(line, source_crs="EPSG:4326")

    assert result["supported"] is True
    assert result["type"] == "length"
    assert result["unit"] == "m"
    assert result["projected_crs"] == "EPSG:32644"
    assert pytest.approx(1093.89, rel=0.01) == result["value"]
    assert pytest.approx(1.09389, rel=0.01) == result["kilometers"]


def test_square_near_equator_accuracy():
    """Verify ~100m x 100m square near equator measures ~10,000 m² within 1%."""
    # At equator: 100m in lon ~ 100 / 111319.49 degrees; 100m in lat ~ 100 / 110574.0 degrees
    d_lon = 100.0 / 111319.49
    d_lat = 100.0 / 110574.0
    equator_poly = Polygon([
        (0.1, 0.1),
        (0.1 + d_lon, 0.1),
        (0.1 + d_lon, 0.1 + d_lat),
        (0.1, 0.1 + d_lat),
        (0.1, 0.1),
    ])
    result = measure_geometry(equator_poly, source_crs="EPSG:4326")

    assert result["supported"] is True
    assert result["type"] == "area"
    assert result["projected_crs"] == "EPSG:32631"
    assert pytest.approx(10000.0, rel=0.01) == result["value"]


def test_square_at_high_latitude_accuracy():
    """Verify ~100m x 100m square at high latitude (70°N) measures ~10,000 m² within 1%."""
    # Use geodesic projection to construct exact 100m x 100m square on WGS84 ellipsoid
    geod = pyproj.Geod(ellps="WGS84")
    lon1, lat1 = 15.0, 70.0
    lon2, lat2, _ = geod.fwd(lon1, lat1, 90, 100)   # 100m East
    lon3, lat3, _ = geod.fwd(lon2, lat2, 0, 100)    # 100m North
    lon4, lat4, _ = geod.fwd(lon1, lat1, 0, 100)    # 100m North from pt 1

    high_lat_poly = Polygon([(lon1, lat1), (lon2, lat2), (lon3, lat3), (lon4, lat4), (lon1, lat1)])
    result = measure_geometry(high_lat_poly, source_crs="EPSG:4326")

    assert result["supported"] is True
    assert result["type"] == "area"
    assert result["projected_crs"] == "EPSG:32633"
    assert pytest.approx(10000.0, rel=0.01) == result["value"]


def test_one_kilometer_line_accuracy():
    """Verify roughly 1km line measures ~1,000m within 1%."""
    geod = pyproj.Geod(ellps="WGS84")
    lon_end, lat_end, _ = geod.fwd(78.0, 11.0, 90, 1000)  # 1,000m East from (78, 11)
    line_1km = LineString([(78.0, 11.0), (lon_end, lat_end)])

    result = measure_geometry(line_1km, source_crs="EPSG:4326")

    assert result["supported"] is True
    assert result["type"] == "length"
    assert result["projected_crs"] == "EPSG:32644"
    assert pytest.approx(1000.0, rel=0.01) == result["value"]
    assert pytest.approx(1.0, rel=0.01) == result["kilometers"]


def test_point_geometry_handling():
    """Verify Point/MultiPoint returns supported=True and value=None."""
    pt = Point(78.0, 11.0)
    result = measure_geometry(pt, source_crs="EPSG:4326")

    assert result["supported"] is True
    assert result["type"] == "point"
    assert result["value"] is None
    assert result["unit"] is None
    assert result["reason"] is not None


def test_geometry_collection_unsupported():
    """Verify GeometryCollection is handled gracefully as unsupported."""
    gc = GeometryCollection([Point(0, 0), LineString([(0, 0), (1, 1)])])
    result = measure_geometry(gc, source_crs="EPSG:4326")

    assert result["supported"] is False
    assert result["type"] == "GeometryCollection"
    assert result["value"] is None
    assert "heterogeneous" in result["reason"]


def test_empty_and_null_geometry():
    """Verify empty and null geometries are handled gracefully."""
    empty_poly = Polygon()
    result_empty = measure_geometry(empty_poly, source_crs="EPSG:4326")
    assert result_empty["supported"] is False
    assert result_empty["type"] == "empty"
    assert result_empty["value"] is None

    result_null = measure_geometry(None, source_crs="EPSG:4326")
    assert result_null["supported"] is False
    assert result_null["type"] == "null"
    assert result_null["value"] is None


def test_invalid_polygon_warning_and_repair():
    """Verify self-intersecting invalid polygon triggers make_valid repair and warning."""
    # Classic self-intersecting bow-tie polygon
    invalid_bow_tie = Polygon([(0.0, 0.0), (0.002, 0.002), (0.002, 0.0), (0.0, 0.002), (0.0, 0.0)])
    assert not invalid_bow_tie.is_valid

    result = measure_geometry(invalid_bow_tie, source_crs="EPSG:4326")

    assert result["supported"] is True
    assert result["value"] > 0
    assert any("Invalid geometry detected" in w and "repaired" in w for w in result["warnings"])


def test_polar_fallback_measurement():
    """Verify polygon near pole (> 84° latitude) uses EPSG:6933 and adds warning."""
    polar_poly = Polygon([(0.0, 85.0), (0.01, 85.0), (0.01, 85.01), (0.0, 85.01), (0.0, 85.0)])
    result = measure_geometry(polar_poly, source_crs="EPSG:4326")

    assert result["supported"] is True
    assert result["projected_crs"] == "EPSG:6933"
    assert any("exceeds standard UTM limits" in w for w in result["warnings"])
    assert result["value"] > 0


def test_three_dimensional_geometry():
    """Verify 3D geometry with Z elevation does not crash and computes planar XY measurements."""
    poly_3d = Polygon([
        (78.0, 11.0, 500.0),
        (78.001, 11.0, 520.0),
        (78.001, 11.001, 510.0),
        (78.0, 11.001, 490.0),
        (78.0, 11.0, 500.0),
    ])
    result = measure_geometry(poly_3d, source_crs="EPSG:4326")

    assert result["supported"] is True
    assert result["type"] == "area"
    # Matches the exact 2D planar area
    assert pytest.approx(12111.29, rel=0.01) == result["value"]
