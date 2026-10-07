"""Read-side queries behind the measurements and features endpoints."""

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.errors import FileNotReadyError, UploadNotFoundError
from app.models import Feature, FileStatus, UploadedFile
from app.schemas import FeatureMeasurement, FeatureOut, MeasurementSummary

_NO_GEOMETRY_KEY = "None"


def get_file(db: Session, file_id: str) -> UploadedFile:
    record = db.get(UploadedFile, file_id)
    if record is None:
        raise UploadNotFoundError(f"File '{file_id}' not found")
    return record


def get_completed_file(db: Session, file_id: str) -> UploadedFile:
    record = get_file(db, file_id)
    if record.status is not FileStatus.COMPLETED:
        detail = f"File '{file_id}' is {record.status.value}, so it has no results"
        if record.error:
            detail += f": {record.error}"
        raise FileNotReadyError(detail)
    return record


def _filtered(
    file_id: str, geometry_type: str | None = None, layer: str | None = None
) -> Select[tuple[Feature]]:
    query = select(Feature).where(Feature.file_id == file_id)
    if geometry_type is not None:
        query = query.where(func.lower(Feature.geometry_type) == geometry_type.lower())
    if layer is not None:
        query = query.where(Feature.layer == layer)
    return query


def _page(db: Session, query: Select[tuple[Feature]], limit: int, offset: int) -> tuple[int, list[Feature]]:
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Feature.id).limit(limit).offset(offset)).all()
    return total, list(rows)


def list_measurements(
    db: Session, file_id: str, *, limit: int, offset: int, geometry_type: str | None
) -> tuple[int, list[FeatureMeasurement]]:
    total, rows = _page(db, _filtered(file_id, geometry_type), limit, offset)
    return total, [_to_measurement(row) for row in rows]


def list_features(
    db: Session, file_id: str, *, limit: int, offset: int, geometry_type: str | None, layer: str | None
) -> tuple[int, list[FeatureOut]]:
    total, rows = _page(db, _filtered(file_id, geometry_type, layer), limit, offset)
    return total, [
        FeatureOut(
            feature_index=row.feature_index,
            layer=row.layer,
            geometry_type=row.geometry_type,
            geometry=row.geometry,
            crs=row.crs,
            properties=row.properties,
        )
        for row in rows
    ]


def summarize(db: Session, file_id: str) -> MeasurementSummary:
    """Totals and counts over the whole file, computed with SQL aggregates."""
    total_area, total_length = db.execute(
        select(
            func.coalesce(func.sum(Feature.area_sq_m), 0.0),
            func.coalesce(func.sum(Feature.length_m), 0.0),
        ).where(Feature.file_id == file_id)
    ).one()
    by_status = db.execute(
        select(Feature.measurement_status, func.count())
        .where(Feature.file_id == file_id)
        .group_by(Feature.measurement_status)
    ).all()
    by_type = db.execute(
        select(Feature.geometry_type, func.count())
        .where(Feature.file_id == file_id)
        .group_by(Feature.geometry_type)
    ).all()
    return MeasurementSummary(
        feature_count=sum(count for _, count in by_status),
        total_area_sq_m=round(total_area, 3),
        total_area_hectares=round(total_area / 10_000, 6),
        total_length_m=round(total_length, 3),
        total_length_km=round(total_length / 1_000, 6),
        by_status={status.value: count for status, count in by_status},
        by_geometry_type={(kind or _NO_GEOMETRY_KEY): count for kind, count in by_type},
    )


def _to_measurement(row: Feature) -> FeatureMeasurement:
    values: dict[str, float] | None = None
    if row.area_sq_m is not None:
        values = {"area_sq_m": round(row.area_sq_m, 3), "area_hectares": round(row.area_sq_m / 10_000, 6)}
    elif row.length_m is not None:
        values = {"length_m": round(row.length_m, 3), "length_km": round(row.length_m / 1_000, 6)}
    return FeatureMeasurement(
        feature_index=row.feature_index,
        layer=row.layer,
        geometry_type=row.geometry_type,
        status=row.measurement_status,
        measurement=values,
        projected_crs=row.projected_crs,
        message=row.message,
        warnings=row.warnings,
    )
