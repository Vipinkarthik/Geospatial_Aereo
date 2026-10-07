"""Feature extraction service converting GeoPandas rows and geometries into JSON-safe dictionaries.

Sanitizes non-standard data types (NaN, NaT, NumPy primitives, Pandas timestamps, 3D coordinates)
so feature metadata can be stored in the database and serialized to JSON without error.
"""

from datetime import date, datetime
import math
from typing import Any
import numpy as np
import pandas as pd
import shapely.geometry


def sanitize_json_value(val: Any) -> Any:
    """
    Recursively sanitize any value into standard Python primitives for JSON compliance.

    Handles:
    - NaN / NaT -> None
    - NumPy int/float/bool -> native int/float/bool
    - NumPy ndarray -> list
    - Pandas Timestamp / datetime / date -> ISO 8601 string
    - Nested dictionaries and sequences
    """
    if val is None:
        return None

    if isinstance(val, np.ndarray):
        return [sanitize_json_value(item) for item in val]

    if isinstance(val, dict):
        return {str(k): sanitize_json_value(v) for k, v in val.items()}

    if isinstance(val, (list, tuple, set)):
        return [sanitize_json_value(item) for item in val]

    # Handle pandas missing values (NaN, NaT) for scalar values
    if pd.isna(val):
        return None

    if isinstance(val, (bool, np.bool_)):
        return bool(val)

    if isinstance(val, (int, np.integer)):
        return int(val)

    if isinstance(val, (float, np.floating)):
        if math.isnan(val) or math.isinf(val):
            return None
        return float(val)

    if isinstance(val, (pd.Timestamp, datetime, date)):
        return val.isoformat()

    return str(val)


def extract_feature(
    feature_index: int,
    geometry: Any,
    properties: dict[str, Any],
    source_crs: str,
) -> dict[str, Any]:
    """
    Extract a single geospatial feature into a standardized, JSON-safe dictionary.

    Handles 3D coordinates without failing and supports null or empty geometries gracefully.
    """
    if geometry is None:
        geom_type = "null"
        geom_geojson = None
    elif getattr(geometry, "is_empty", False):
        geom_type = "empty"
        geom_geojson = None
    else:
        geom_type = getattr(geometry, "geom_type", type(geometry).__name__)
        raw_mapping = shapely.geometry.mapping(geometry)
        geom_geojson = sanitize_json_value(raw_mapping)

    clean_properties = {
        str(k): sanitize_json_value(v)
        for k, v in properties.items()
        if k != "geometry"
    }

    return {
        "feature_index": feature_index,
        "geometry_type": geom_type,
        "geometry": geom_geojson,
        "source_crs": source_crs,
        "properties": clean_properties,
    }


def extract_features_from_geodataframe(
    gdf: Any,
    source_crs: str | None = None,
) -> list[dict[str, Any]]:
    """
    Iterate over a GeoDataFrame and convert every feature row into a JSON-safe record.
    """
    if source_crs is None:
        if hasattr(gdf, "crs") and gdf.crs is not None:
            source_crs = f"EPSG:{gdf.crs.to_epsg()}" if gdf.crs.to_epsg() else gdf.crs.to_string()
        else:
            source_crs = "EPSG:4326"

    features: list[dict[str, Any]] = []

    for idx, row in gdf.iterrows():
        geom = row.get("geometry", None) if hasattr(row, "get") else getattr(row, "geometry", None)
        raw_props = {
            col: row[col]
            for col in gdf.columns
            if col != "geometry"
        }
        feature_dict = extract_feature(
            feature_index=int(idx),
            geometry=geom,
            properties=raw_props,
            source_crs=source_crs,
        )
        features.append(feature_dict)

    return features
