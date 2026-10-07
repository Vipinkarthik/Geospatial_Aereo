"""Pydantic schemas for file upload responses and detailed status views."""

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

from app.models.uploaded_file import FileStatus


class FileUploadResponse(BaseModel):
    """Payload returned immediately following file ingestion."""
    model_config = ConfigDict(from_attributes=True)

    file_id: int = Field(description="Database primary key identifier for the file")
    filename: str = Field(description="Original uploaded filename")
    status: FileStatus = Field(description="Lifecycle status: PROCESSING, COMPLETED, or FAILED")
    feature_count: int | None = Field(default=None, description="Number of extracted geospatial features")
    crs: str | None = Field(default=None, description="Source Coordinate Reference System (e.g. EPSG:4326)")
    message: str = Field(description="Status confirmation or summary message")


class FileDetailResponse(BaseModel):
    """Detailed file record inspection payload."""
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Unique file ID")
    filename: str = Field(description="Original filename")
    status: FileStatus = Field(description="Current processing status")
    feature_count: int | None = Field(default=None, description="Total features processed")
    crs: str | None = Field(default=None, description="Source Coordinate Reference System")
    error_message: str | None = Field(default=None, description="Diagnostic error report if processing failed")
    created_at: datetime = Field(description="Timestamp when the file was ingested")
