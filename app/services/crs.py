"""Geospatial Coordinate Reference System (CRS) management, UTM selection, and transformation utilities.

Ensures spatial measurements are never computed in angular degrees and selects
appropriate conformal/equal-area projections.
"""

import functools
import math
from typing import Any
import pyproj


class MissingCRSError(ValueError):
    """Raised when spatial geometries or datasets do not specify a CRS."""
    pass


class UnsupportedCRSError(ValueError):
    """Raised when an unrecognized, corrupt, or unsupported CRS definition is provided."""
    pass


def parse_crs(crs_input: Any) -> pyproj.CRS:
    """
    Parse multiple CRS representations (EPSG code integer, string, or pyproj.CRS)
    into a canonical pyproj.CRS instance.
    """
    if crs_input is None:
        raise MissingCRSError("CRS is missing or undefined.")
    try:
        return pyproj.CRS.from_user_input(crs_input)
    except Exception as exc:
        raise UnsupportedCRSError(f"Failed to parse CRS from input '{crs_input}': {exc}") from exc


def is_geographic(crs_input: Any) -> bool:
    """Return True if the CRS is geographic (angular units such as degrees)."""
    return parse_crs(crs_input).is_geographic


def is_projected(crs_input: Any) -> bool:
    """Return True if the CRS is projected (planar Cartesian coordinates)."""
    return parse_crs(crs_input).is_projected


def get_utm_zone(lon: float) -> int:
    """
    Calculate the UTM longitudinal zone number (1-60).
    Standard formula: floor((lon + 180) / 6) + 1, clamped to [1, 60].
    """
    zone = int(math.floor((lon + 180.0) / 6.0)) + 1
    if zone > 60:
        return 60
    if zone < 1:
        return 1
    return zone


def get_utm_epsg(lon: float, lat: float) -> str:
    """
    Determine the standard WGS 84 UTM EPSG code for a representative lon/lat coordinate.
    Northern hemisphere: 32600 + zone
    Southern hemisphere: 32700 + zone
    """
    zone = get_utm_zone(lon)
    base_code = 32600 if lat >= 0 else 32700
    return f"EPSG:{base_code + zone}"


def get_linear_unit_factor(crs: pyproj.CRS) -> tuple[float, str]:
    """
    Extract the linear unit conversion factor (to meters) and the unit name for a projected CRS.
    Returns: (factor_to_meters, unit_name)
    """
    try:
        axis = crs.axis_info[0]
        factor = float(axis.unit_conversion_factor) if axis.unit_conversion_factor else 1.0
        unit_name = str(axis.unit_name) if axis.unit_name else "metre"
        return factor, unit_name
    except Exception:
        return 1.0, "metre"


@functools.lru_cache(maxsize=256)
def _cached_transformer(source_key: str, target_key: str) -> pyproj.Transformer:
    """Cached transformer factory using always_xy=True to enforce (x, y) / (lon, lat) order."""
    return pyproj.Transformer.from_crs(source_key, target_key, always_xy=True)


def get_transformer(source_crs: Any, target_crs: Any) -> pyproj.Transformer:
    """
    Retrieve a cached pyproj.Transformer for coordinate conversion with always_xy=True.
    """
    s_crs = parse_crs(source_crs)
    t_crs = parse_crs(target_crs)

    s_key = f"EPSG:{s_crs.to_epsg()}" if s_crs.to_epsg() else s_crs.to_string()
    t_key = f"EPSG:{t_crs.to_epsg()}" if t_crs.to_epsg() else t_crs.to_string()

    return _cached_transformer(s_key, t_key)


def determine_target_projected_crs(
    source_crs_input: Any,
    centroid_lon: float,
    centroid_lat: float,
    bounds: tuple[float, float, float, float] | None = None,
) -> tuple[str, list[str]]:
    """
    Determine the optimal projected CRS for planar metric measurement along with any warnings.
    - If source CRS is already projected: retains source CRS and checks linear unit.
    - If source CRS is geographic:
        - Absolute latitude > 84°: falls back to EPSG:6933 (Equal-Area Cylindrical) with warning.
        - Otherwise: selects the appropriate UTM zone (EPSG:326xx or EPSG:327xx).
        - If geometry crosses UTM zone boundaries: emits zone-crossing warning.
    """
    if source_crs_input is None:
        raise MissingCRSError("Source CRS is missing or undefined.")

    source_crs = parse_crs(source_crs_input)
    warnings: list[str] = []

    if source_crs.is_projected:
        epsg = source_crs.to_epsg()
        target_str = f"EPSG:{epsg}" if epsg else source_crs.to_string()
        factor, unit_name = get_linear_unit_factor(source_crs)
        if abs(factor - 1.0) > 1e-9:
            warnings.append(
                f"Source CRS '{target_str}' uses unit '{unit_name}' (conversion factor {factor:.6f} to meters); "
                f"measurements will be scaled to meters."
            )
        return target_str, warnings

    # Geographic CRS: angular degrees cannot be used for metric measurements directly
    if abs(centroid_lat) > 84.0:
        target_str = "EPSG:6933"
        warnings.append(
            f"Latitude {centroid_lat:.2f}° exceeds standard UTM limits (|lat| > 84°); "
            f"fell back to EPSG:6933 (Equal-Area Cylindrical)."
        )
    else:
        target_str = get_utm_epsg(centroid_lon, centroid_lat)

        if bounds is not None:
            minx, _, maxx, _ = bounds
            min_zone = get_utm_zone(minx)
            max_zone = get_utm_zone(maxx)
            if min_zone != max_zone:
                warnings.append(
                    f"Feature crosses UTM zone boundaries (Zone {min_zone} to Zone {max_zone}); "
                    f"minor projection distortion may occur."
                )

    return target_str, warnings
