import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from sqlalchemy import DateTime, Enum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.feature_record import FeatureRecord


class FileStatus(str, enum.Enum):
    """File processing lifecycle states."""
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class UploadedFile(Base):
    """Stores metadata, file location, and processing status for geospatial files."""

    __tablename__ = "uploaded_files"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(500), nullable=False)
    feature_count: Mapped[int | None] = mapped_column(Integer, default=0, nullable=True)
    crs: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[FileStatus] = mapped_column(
        Enum(FileStatus, native_enum=False),
        default=FileStatus.PROCESSING,
        nullable=False,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    features: Mapped[list["FeatureRecord"]] = relationship(
        back_populates="uploaded_file",
        cascade="all, delete-orphan",
    )
