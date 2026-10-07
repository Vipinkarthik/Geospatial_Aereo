"""Unit tests for feature extraction, JSON serialization, and geometry edge cases."""

from datetime import datetime
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, Polygon

from app.services.feature_extractor import (
    extract_feature,
    extract_features_from_geodataframe,
    sanitize_json_value,
)
from app.services.file_reader import read_geospatial_file

SAMPLE_KML = Path("sample_data/sample.kml")


def test_sanitize_json_primitives():
    """Verify conversion of non-standard NumPy and Pandas types to JSON primitives."""
    raw_dict = {
        "int_num": np.int64(100),
        "float_num": np.float64(45.67),
        "nan_val": np.nan,
        "nat_val": pd.NaT,
        "bool_val": np.bool_(True),
        "timestamp": pd.Timestamp("2026-10-07 10:00:00"),
        "date_val": datetime(2026, 10, 7, 10, 0, 0),
        "array_val": np.array([1, 2, 3]),
    }

    sanitized = sanitize_json_value(raw_dict)

    # Must be 100% serializable to standard JSON
    json_str = json.dumps(sanitized)
    deserialized = json.loads(json_str)

    assert deserialized["int_num"] == 100
    assert deserialized["float_num"] == 45.67
    assert deserialized["nan_val"] is None
    assert deserialized["nat_val"] is None
    assert deserialized["bool_val"] is True
    assert "2026-10-07" in deserialized["timestamp"]
    assert deserialized["array_val"] == [1, 2, 3]


def test_3d_geometry_extraction():
    """Verify 3D geometry extracts coordinates and GeoJSON without failure."""
    poly_3d = Polygon([
        (78.0, 11.0, 500.0),
        (78.001, 11.0, 520.0),
        (78.001, 11.001, 510.0),
        (78.0, 11.001, 490.0),
        (78.0, 11.0, 500.0),
    ])

    extracted = extract_feature(
        feature_index=0,
        geometry=poly_3d,
        properties={"elevation_mean": np.float64(505.0)},
        source_crs="EPSG:4326",
    )

    assert extracted["feature_index"] == 0
    assert extracted["geometry_type"] == "Polygon"
    assert extracted["geometry"]["type"] == "Polygon"
    assert extracted["geometry"]["coordinates"][0][0] == [78.0, 11.0, 500.0]

    # Verify JSON serializability
    json_output = json.dumps(extracted)
    assert "500.0" in json_output


def test_null_and_empty_geometries():
    """Verify null and empty geometries are cleanly processed."""
    null_feat = extract_feature(
        feature_index=1,
        geometry=None,
        properties={"note": "missing geometry"},
        source_crs="EPSG:4326",
    )
    assert null_feat["geometry_type"] == "null"
    assert null_feat["geometry"] is None

    empty_feat = extract_feature(
        feature_index=2,
        geometry=Polygon(),
        properties={"note": "empty polygon"},
        source_crs="EPSG:4326",
    )
    assert empty_feat["geometry_type"] == "empty"
    assert empty_feat["geometry"] is None


def test_extract_features_from_sample_kml():
    """Verify extraction from real KML produces complete JSON-safe feature list."""
    gdf, crs_str = read_geospatial_file(SAMPLE_KML)
    features = extract_features_from_geodataframe(gdf, source_crs=crs_str)

    assert len(features) == 3

    # All must serialize to JSON
    json_bytes = json.dumps(features)
    assert len(json_bytes) > 0

    first = features[0]
    assert first["geometry_type"] == "Polygon"
    assert first["source_crs"] == "EPSG:4326"
    assert first["properties"]["Name"] == "Plot A"
