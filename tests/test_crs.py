"""Unit tests for CRS detection, UTM zone calculations, transformer caching, and boundary warnings."""

import pytest
import pyproj

from app.services.crs import (
    MissingCRSError,
    UnsupportedCRSError,
    determine_target_projected_crs,
    get_linear_unit_factor,
    get_transformer,
    get_utm_epsg,
    get_utm_zone,
    is_geographic,
    is_projected,
    parse_crs,
)


def test_crs_detection():
    """Verify detection of geographic vs projected CRS."""
    assert is_geographic("EPSG:4326") is True
    assert is_projected("EPSG:4326") is False

    assert is_projected("EPSG:32644") is True
    assert is_geographic("EPSG:32644") is False

    assert is_projected("EPSG:3857") is True


def test_utm_selection_tiruchengode():
    """Verify (78.0, 11.0) maps exactly to UTM Zone 44N (EPSG:32644)."""
    epsg = get_utm_epsg(lon=78.0, lat=11.0)
    assert epsg == "EPSG:32644"
    assert get_utm_zone(78.0) == 44


def test_utm_southern_hemisphere():
    """Verify southern hemisphere coordinates map to EPSG:327xx."""
    # Same longitude (78.0) but southern latitude (-11.0) -> EPSG:32744
    epsg_south = get_utm_epsg(lon=78.0, lat=-11.0)
    assert epsg_south == "EPSG:32744"

    # Sydney, Australia (lon=151.2, lat=-33.8) -> Zone 56S -> EPSG:32756
    assert get_utm_epsg(lon=151.2, lat=-33.8) == "EPSG:32756"

    # Rio de Janeiro, Brazil (lon=-43.2, lat=-22.9) -> Zone 23S -> EPSG:32723
    assert get_utm_epsg(lon=-43.2, lat=-22.9) == "EPSG:32723"


def test_utm_zone_boundaries():
    """Verify zone boundaries along the international date line and prime meridian."""
    assert get_utm_zone(-180.0) == 1
    assert get_utm_zone(-179.99) == 1
    assert get_utm_zone(-174.0) == 2
    assert get_utm_zone(0.0) == 31
    assert get_utm_zone(6.0) == 32
    assert get_utm_zone(179.99) == 60
    assert get_utm_zone(180.0) == 60


def test_polar_fallback_epsg_6933():
    """Verify latitude beyond 84 degrees falls back to EPSG:6933 with warning."""
    # North pole area (lat = 85.0)
    target_crs, warnings = determine_target_projected_crs(
        source_crs_input="EPSG:4326",
        centroid_lon=10.0,
        centroid_lat=85.0,
    )
    assert target_crs == "EPSG:6933"
    assert any("exceeds standard UTM limits" in w for w in warnings)

    # South pole area (lat = -86.5)
    target_crs_south, warnings_south = determine_target_projected_crs(
        source_crs_input="EPSG:4326",
        centroid_lon=0.0,
        centroid_lat=-86.5,
    )
    assert target_crs_south == "EPSG:6933"
    assert any("exceeds standard UTM limits" in w for w in warnings_south)


def test_utm_zone_boundary_crossing_warning():
    """Verify warning emitted when geometry bounding box spans multiple UTM zones."""
    # Bounding box spanning lon 5.5 to 6.5 (Zone 31 and Zone 32)
    bounds = (5.5, 50.0, 6.5, 51.0)
    target_crs, warnings = determine_target_projected_crs(
        source_crs_input="EPSG:4326",
        centroid_lon=6.0,
        centroid_lat=50.5,
        bounds=bounds,
    )
    assert target_crs == "EPSG:32632"
    assert any("crosses UTM zone boundaries" in w for w in warnings)


def test_transformer_caching_and_always_xy():
    """Verify Transformers are cached and follow (x, y) / (lon, lat) axis order."""
    t1 = get_transformer("EPSG:4326", "EPSG:32644")
    t2 = get_transformer("EPSG:4326", "EPSG:32644")

    # Cached instance identity
    assert t1 is t2

    # Verify always_xy: passing (lon, lat) produces expected easting, northing
    x, y = t1.transform(78.0, 11.0)
    assert round(x, 1) == 172128.6
    assert round(y, 1) == 1217618.4


def test_projected_source_crs_retained():
    """Verify already-projected CRS is kept without re-projecting to UTM."""
    target_crs, warnings = determine_target_projected_crs(
        source_crs_input="EPSG:3857",
        centroid_lon=0.0,
        centroid_lat=0.0,
    )
    assert target_crs == "EPSG:3857"
    assert len(warnings) == 0


def test_projected_linear_unit_conversion():
    """Verify non-meter projected units (e.g., US survey feet) report conversion factor."""
    # EPSG:2227 is NAD83 / California zone 3 (ftUS)
    crs_feet = parse_crs("EPSG:2227")
    factor, unit_name = get_linear_unit_factor(crs_feet)
    assert "foot" in unit_name.lower()
    assert pytest.approx(factor, rel=1e-4) == 0.3048006

    _, warnings = determine_target_projected_crs(
        source_crs_input="EPSG:2227",
        centroid_lon=-122.0,
        centroid_lat=37.5,
    )
    assert any("uses unit" in w and "scaled to meters" in w for w in warnings)


def test_missing_crs_raises_error():
    """Verify missing CRS raises explicit MissingCRSError."""
    with pytest.raises(MissingCRSError):
        parse_crs(None)

    with pytest.raises(MissingCRSError):
        determine_target_projected_crs(None, centroid_lon=78.0, centroid_lat=11.0)


def test_unsupported_crs_raises_error():
    """Verify invalid CRS string raises UnsupportedCRSError."""
    with pytest.raises(UnsupportedCRSError):
        parse_crs("NOT_A_VALID_CRS_STRING_XYZ")
