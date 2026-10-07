"""Common schemas for standardized API error responses."""

from pydantic import BaseModel, ConfigDict, Field


class ErrorDetail(BaseModel):
    """Structured error payload containing a machine-readable code and explanatory message."""
    model_config = ConfigDict(from_attributes=True)

    code: str = Field(description="Machine-readable error identifier")
    message: str = Field(description="Human-readable explanation of the error")


class ErrorResponse(BaseModel):
    """Consistent application error wrapper."""
    model_config = ConfigDict(from_attributes=True)

    error: ErrorDetail = Field(description="Error body details")
