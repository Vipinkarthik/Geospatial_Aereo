"""Common schemas for standardized API error responses and pagination."""

from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class ErrorResponse(BaseModel):
    """Standardized API error response payload."""
    model_config = ConfigDict(from_attributes=True)

    error: str = Field(description="High-level error classification or title")
    detail: str | None = Field(default=None, description="Explanatory detail about the error")
    code: str | None = Field(default=None, description="Machine-readable error identifier")
