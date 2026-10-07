"""HTTP routes. Handlers stay thin: they delegate to the service layer.

Handlers are plain ``def`` (not ``async def``) on purpose: parsing and measuring
are blocking, CPU-bound work, and FastAPI runs sync handlers in a threadpool
instead of stalling the event loop.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import get_db
from app.models import FileStatus, UploadedFile
from app.schemas import ErrorResponse, FeaturesResponse, FileInfo, MeasurementsResponse
from app.services import ingest, reports

router = APIRouter(prefix="/api/files", tags=["files"])


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


DbDep = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
LimitQuery = Annotated[int, Query(ge=1, le=1000, description="Page size")]
OffsetQuery = Annotated[int, Query(ge=0, description="Number of items to skip")]
GeometryTypeQuery = Annotated[
    str | None, Query(description="Only features of this geometry type, e.g. Polygon")
]


@router.post(
    "/",
    status_code=201,
    response_model=FileInfo,
    summary="Upload and process a file",
    responses={
        400: {"model": ErrorResponse, "description": "Empty upload"},
        413: {"model": ErrorResponse, "description": "Upload exceeds the size limit"},
        415: {"model": ErrorResponse, "description": "Not a .zip or .kml file"},
        422: {
            "model": FileInfo,
            "description": "The file was accepted but could not be processed; "
            "the body is the stored FAILED record (see its `error` field)",
        },
    },
)
def upload_file(
    file: Annotated[UploadFile, File(description="A .zip containing a Shapefile, or a .kml file")],
    response: Response,
    db: DbDep,
    settings: SettingsDep,
) -> UploadedFile:
    record = ingest.ingest_upload(db, filename=file.filename or "", stream=file.file, settings=settings)
    if record.status is FileStatus.FAILED:
        response.status_code = 422
    return record


@router.get(
    "/{file_id}/",
    response_model=FileInfo,
    summary="File information",
    responses={404: {"model": ErrorResponse}},
)
def get_file_info(file_id: str, db: DbDep) -> UploadedFile:
    return reports.get_file(db, file_id)


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementsResponse,
    summary="Per-feature measurements plus a whole-file summary",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def get_measurements(
    file_id: str,
    db: DbDep,
    limit: LimitQuery = 100,
    offset: OffsetQuery = 0,
    geometry_type: GeometryTypeQuery = None,
) -> MeasurementsResponse:
    reports.get_completed_file(db, file_id)
    total, results = reports.list_measurements(
        db, file_id, limit=limit, offset=offset, geometry_type=geometry_type
    )
    return MeasurementsResponse(
        file_id=file_id,
        summary=reports.summarize(db, file_id),
        total=total,
        limit=limit,
        offset=offset,
        results=results,
    )


@router.get(
    "/{file_id}/features/",
    response_model=FeaturesResponse,
    summary="Features with geometry, CRS and properties",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def get_features(
    file_id: str,
    db: DbDep,
    limit: LimitQuery = 100,
    offset: OffsetQuery = 0,
    geometry_type: GeometryTypeQuery = None,
    layer: Annotated[str | None, Query(description="Only features from this layer")] = None,
) -> FeaturesResponse:
    reports.get_completed_file(db, file_id)
    total, results = reports.list_features(
        db, file_id, limit=limit, offset=offset, geometry_type=geometry_type, layer=layer
    )
    return FeaturesResponse(file_id=file_id, total=total, limit=limit, offset=offset, results=results)
