"""Pydantic schemas for feature measurements, geometries, and paginated responses."""

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class MeasurementDetail(BaseModel):
    """Calculated metric measurements for a spatial feature."""
    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)

    type: str = Field(description="Measurement metric classification ('area', 'length', 'point', or unsupported type)")
    value: float | None = Field(default=None, description="Planar measurement value in base metric units (m² or m)")
    unit: str | None = Field(default=None, description="Measurement unit ('m²', 'm', or None)")
    projected_crs: str | None = Field(default=None, description="EPSG code or projected CRS used for planar computation")
    supported: bool = Field(description="Indicates whether the geometry is supported for metric measurement")
    reason: str | None = Field(default=None, description="Explanation when measurement is unsupported or null")
    hectares: float | None = Field(default=None, description="Computed area in hectares (for polygons)")
    kilometers: float | None = Field(default=None, description="Computed length in kilometers (for lines)")


class FeatureMeasurementResponse(BaseModel):
    """Feature geometry, attributes, and corresponding measurement calculation."""
    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)

    feature_id: int = Field(description="Feature identifier")
    geometry_type: str = Field(description="Geometry primitive type (Polygon, LineString, Point, etc.)")
    geometry: dict[str, Any] | None = Field(default=None, description="GeoJSON geometry mapping")
    crs: str = Field(description="Source Coordinate Reference System")
    properties: dict[str, Any] = Field(default_factory=dict, description="Feature attributes and metadata")
    measurement: MeasurementDetail = Field(description="Calculated planar measurement details")
    warnings: list[str] = Field(default_factory=list, description="Any warnings emitted during calculation")


class PaginatedMeasurementResponse(BaseModel):
    """Paginated collection of spatial feature measurements."""
    model_config = ConfigDict(from_attributes=True, arbitrary_types_allowed=True)

    file_id: int = Field(description="ID of the parent uploaded file")
    filename: str = Field(description="Filename of the uploaded dataset")
    total_features: int = Field(default=0, description="Total count of features contained in the dataset")
    total: int = Field(default=0, description="Total matching features count")
    page: int = Field(default=1, description="Current page index (1-based)")
    page_size: int = Field(default=50, description="Number of feature records per page")
    total_pages: int = Field(default=1, description="Total calculated pages")
    limit: int = Field(default=50, description="Query limit applied")
    offset: int = Field(default=0, description="Query offset applied")
    features: list[FeatureMeasurementResponse] = Field(default_factory=list, description="List of feature measurement items")

    def model_post_init(self, __context):
        if self.total == 0 and self.total_features != 0:
            self.total = self.total_features
        elif self.total_features == 0 and self.total != 0:
            self.total_features = self.total
