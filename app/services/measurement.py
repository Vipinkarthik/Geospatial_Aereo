"""Geospatial measurement calculation engine.

Computes geodesic-projected planar areas (m² and hectares) and lengths (m and km)
with strict geometric validation, 2D planar projection handling, and graceful
unsupported geometry fallbacks.
"""

from typing import Any
import shapely
import shapely.ops
import shapely.validation

from app.services.crs import (
    determine_target_projected_crs,
    get_linear_unit_factor,
    get_transformer,
    is_geographic,
    parse_crs,
)


def measure_geometry(geometry: Any, source_crs: Any) -> dict[str, Any]:
    """
    Measure an individual Shapely geometry using an appropriate projected CRS.

    Returns a standardized dictionary containing:
        - type: measurement classification ("area", "length", "point", or failure type)
        - value: numerical value in standard SI base unit (m² for area, m for length, or None)
        - unit: "m²", "m", or None
        - projected_crs: EPSG code or CRS string used for planar computation
        - supported: True if measurable or valid Point, False otherwise
        - reason: explanation if measurement is unsupported or null
        - hectares: float value for areas, or None
        - kilometers: float value for lengths, or None
        - warnings: list of contextual warnings (invalid fixes, zone crossings, unit conversions)
    """
    warnings: list[str] = []

    # 1. Null geometry check
    if geometry is None:
        return {
            "type": "null",
            "value": None,
            "unit": None,
            "projected_crs": None,
            "supported": False,
            "reason": "Geometry is null (None).",
            "hectares": None,
            "kilometers": None,
            "warnings": warnings,
        }

    # 2. Empty geometry check
    if getattr(geometry, "is_empty", False):
        return {
            "type": "empty",
            "value": None,
            "unit": None,
            "projected_crs": None,
            "supported": False,
            "reason": "Geometry contains no coordinate points (empty).",
            "hectares": None,
            "kilometers": None,
            "warnings": warnings,
        }

    geom_type = getattr(geometry, "geom_type", type(geometry).__name__)

    # 3. GeometryCollection check (unsupported gracefully)
    if geom_type == "GeometryCollection":
        return {
            "type": "GeometryCollection",
            "value": None,
            "unit": None,
            "projected_crs": None,
            "supported": False,
            "reason": "GeometryCollection containing heterogeneous types is not supported for single-metric measurement.",
            "hectares": None,
            "kilometers": None,
            "warnings": warnings,
        }

    # 4. Point / MultiPoint check (supported=True, value=None)
    if geom_type in ("Point", "MultiPoint"):
        projected_crs_code = None
        if source_crs is not None:
            try:
                c = geometry.centroid
                projected_crs_code, crs_warn = determine_target_projected_crs(source_crs, c.x, c.y)
                warnings.extend(crs_warn)
            except Exception:
                pass

        return {
            "type": "point",
            "value": None,
            "unit": None,
            "projected_crs": projected_crs_code,
            "supported": True,
            "reason": "Points have zero area and zero length.",
            "hectares": None,
            "kilometers": None,
            "warnings": warnings,
        }

    # 5. Unsupported geometry types
    if geom_type not in ("Polygon", "MultiPolygon", "LineString", "MultiLineString"):
        return {
            "type": geom_type,
            "value": None,
            "unit": None,
            "projected_crs": None,
            "supported": False,
            "reason": f"Geometry type '{geom_type}' is not supported for measurement.",
            "hectares": None,
            "kilometers": None,
            "warnings": warnings,
        }

    # 6. Parse and validate source CRS
    parsed_source_crs = parse_crs(source_crs)

    # 7. 3D geometry handling (strip Z/M dimensions; calculate strictly on XY plane)
    geom = geometry
    if getattr(geom, "has_z", False):
        geom = shapely.force_2d(geom)

    # 8. Invalid geometry handling (try make_valid, record warning, and proceed)
    if not geom.is_valid:
        invalid_reason = shapely.validation.explain_validity(geom)
        fixed_geom = shapely.make_valid(geom)
        warnings.append(f"Invalid geometry detected ({invalid_reason}); repaired with make_valid().")
        geom = fixed_geom

        # Handle edge-case where make_valid yields a GeometryCollection
        if geom.geom_type == "GeometryCollection":
            if geom_type in ("Polygon", "MultiPolygon"):
                polys = [g for g in geom.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
                geom = shapely.ops.unary_union(polys) if polys else geom
            elif geom_type in ("LineString", "MultiLineString"):
                lines = [g for g in geom.geoms if g.geom_type in ("LineString", "MultiLineString")]
                geom = shapely.ops.unary_union(lines) if lines else geom

        if geom.is_empty:
            return {
                "type": "empty",
                "value": None,
                "unit": None,
                "projected_crs": None,
                "supported": False,
                "reason": "Geometry became empty after repairing topology.",
                "hectares": None,
                "kilometers": None,
                "warnings": warnings,
            }

    # 9. Target projected CRS determination
    centroid = geom.centroid
    bounds = geom.bounds
    target_crs, crs_warnings = determine_target_projected_crs(
        source_crs_input=parsed_source_crs,
        centroid_lon=centroid.x,
        centroid_lat=centroid.y,
        bounds=bounds,
    )
    warnings.extend(crs_warnings)

    # 10. Transformation to projected coordinates
    if is_geographic(parsed_source_crs):
        transformer = get_transformer(parsed_source_crs, target_crs)
        projected_geom = shapely.ops.transform(transformer.transform, geom)
        factor = 1.0  # Selected UTM or EPSG:6933 standard unit is already meters
    else:
        projected_geom = geom
        factor, _ = get_linear_unit_factor(parsed_source_crs)

    # 11. Measurement calculation
    current_type = projected_geom.geom_type

    if current_type in ("Polygon", "MultiPolygon"):
        area_m2 = projected_geom.area * (factor ** 2)
        area_ha = area_m2 / 10000.0
        return {
            "type": "area",
            "value": round(area_m2, 4),
            "unit": "m²",
            "projected_crs": target_crs,
            "supported": True,
            "reason": None,
            "hectares": round(area_ha, 6),
            "kilometers": None,
            "warnings": warnings,
        }

    if current_type in ("LineString", "MultiLineString"):
        length_m = projected_geom.length * factor
        length_km = length_m / 1000.0
        return {
            "type": "length",
            "value": round(length_m, 4),
            "unit": "m",
            "projected_crs": target_crs,
            "supported": True,
            "reason": None,
            "hectares": None,
            "kilometers": round(length_km, 6),
            "warnings": warnings,
        }

    return {
        "type": current_type,
        "value": None,
        "unit": None,
        "projected_crs": target_crs,
        "supported": False,
        "reason": f"Resulting geometry type '{current_type}' is unsupported.",
        "hectares": None,
        "kilometers": None,
        "warnings": warnings,
    }
