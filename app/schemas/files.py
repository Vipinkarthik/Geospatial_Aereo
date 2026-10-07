"""Pydantic schemas for file upload responses and detailed status views."""

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

from app.models.uploaded_file import FileStatus


class FileUploadResponse(BaseModel):
    """Returned on successful file ingestion (HTTP 201)."""
    model_config = ConfigDict(from_attributes=True)

    id: int | None = Field(default=None, description="Unique file identifier")
    filename: str = Field(description="Name of the uploaded file")
    feature_count: int | None = Field(default=None, description="Number of geospatial features")
    crs: str | None = Field(default=None, description="Coordinate Reference System")
    status: FileStatus = Field(description="File processing lifecycle status")
    file_id: int | None = Field(default=None, description="Alias for id for backwards compatibility")
    message: str | None = Field(default=None, description="Optional summary message")

    def model_post_init(self, __context):
        if self.id is None and self.file_id is not None:
            self.id = self.file_id
        elif self.file_id is None and self.id is not None:
            self.file_id = self.id


class FileDetailResponse(BaseModel):
    """Detailed file record metadata."""
    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Unique file identifier")
    filename: str = Field(description="Original filename")
    feature_count: int | None = Field(default=None, description="Total features processed")
    crs: str | None = Field(default=None, description="Source Coordinate Reference System")
    status: FileStatus = Field(description="Current status (PROCESSING, COMPLETED, FAILED)")
    error_message: str | None = Field(default=None, description="Error report if processing failed")
    created_at: datetime = Field(description="Timestamp when the file was uploaded")
