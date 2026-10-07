from typing import Any, TYPE_CHECKING
from sqlalchemy import ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

if TYPE_CHECKING:
    from app.models.uploaded_file import UploadedFile


class FeatureRecord(Base):
    """Stores individual spatial features, geometry metadata, and GeoJSON representations."""

    __tablename__ = "feature_records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    uploaded_file_id: Mapped[int] = mapped_column(
        ForeignKey("uploaded_files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    feature_index: Mapped[int] = mapped_column(Integer, nullable=False)
    geometry_type: Mapped[str] = mapped_column(String(50), nullable=False)
    feature_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    # Relationships
    uploaded_file: Mapped["UploadedFile"] = relationship(
        back_populates="features",
    )
