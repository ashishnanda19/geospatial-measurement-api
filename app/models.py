"""SQLAlchemy models and the enums they share with the API schemas."""

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class FileStatus(enum.StrEnum):
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class FileType(enum.StrEnum):
    SHAPEFILE = "shapefile"
    KML = "kml"


class MeasurementStatus(enum.StrEnum):
    MEASURED = "MEASURED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNSUPPORTED = "UNSUPPORTED"
    NO_GEOMETRY = "NO_GEOMETRY"
    ERROR = "ERROR"


class UtcDateTime(TypeDecorator[datetime]):
    """Store UTC datetimes; SQLite drops tzinfo, so re-attach it on load."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("naive datetime; use timezone-aware UTC")
        return value.astimezone(UTC) if value is not None else None

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[FileType] = mapped_column(Enum(FileType, native_enum=False, length=16))
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[FileStatus] = mapped_column(Enum(FileStatus, native_enum=False, length=16))
    feature_count: Mapped[int] = mapped_column(Integer, default=0)
    crs: Mapped[str | None] = mapped_column(String(255))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=_utcnow)


class Feature(Base):
    __tablename__ = "features"
    __table_args__ = (Index("ix_features_file_id_id", "file_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("uploaded_files.id", ondelete="CASCADE"))
    layer: Mapped[str] = mapped_column(String(255))
    feature_index: Mapped[int] = mapped_column(Integer)

    geometry_type: Mapped[str | None] = mapped_column(String(32))
    geometry: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    crs: Mapped[str] = mapped_column(String(255))
    properties: Mapped[dict[str, Any]] = mapped_column(JSON)

    measurement_status: Mapped[MeasurementStatus] = mapped_column(
        Enum(MeasurementStatus, native_enum=False, length=16)
    )
    area_sq_m: Mapped[float | None] = mapped_column(Float)
    length_m: Mapped[float | None] = mapped_column(Float)
    projected_crs: Mapped[str | None] = mapped_column(String(255))
    message: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
