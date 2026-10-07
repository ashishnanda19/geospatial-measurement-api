"""Pydantic response models."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.models import FileStatus, FileType, MeasurementStatus


class FileInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    file_type: FileType
    size_bytes: int
    feature_count: int
    crs: str | None
    status: FileStatus
    error: str | None
    created_at: datetime


class FeatureMeasurement(BaseModel):
    feature_index: int
    layer: str
    geometry_type: str | None
    status: MeasurementStatus
    measurement: dict[str, float] | None
    projected_crs: str | None
    message: str | None
    warnings: list[str]


class MeasurementSummary(BaseModel):
    feature_count: int
    total_area_sq_m: float
    total_area_hectares: float
    total_length_m: float
    total_length_km: float
    by_status: dict[str, int]
    by_geometry_type: dict[str, int]


class MeasurementsResponse(BaseModel):
    file_id: str
    summary: MeasurementSummary
    total: int
    limit: int
    offset: int
    results: list[FeatureMeasurement]


class FeatureOut(BaseModel):
    feature_index: int
    layer: str
    geometry_type: str | None
    geometry: dict[str, Any] | None
    crs: str
    properties: dict[str, Any]


class FeaturesResponse(BaseModel):
    file_id: str
    total: int
    limit: int
    offset: int
    results: list[FeatureOut]


class ErrorResponse(BaseModel):
    detail: str
