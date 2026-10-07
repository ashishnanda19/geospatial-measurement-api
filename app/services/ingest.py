"""Orchestrates one upload: validate -> store -> parse -> measure -> persist."""

import logging
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from sqlalchemy import insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import EmptyUploadError, ProcessingError, UnsupportedFileTypeError, UploadTooLargeError
from app.models import Feature, FileStatus, FileType, UploadedFile
from app.services.archive import extract_zip
from app.services.measurement import Measurer
from app.services.parsing import ParsedLayer, geometry_to_geojson, parse_kml, parse_shapefiles

logger = logging.getLogger(__name__)

_EXTENSIONS = {".zip": FileType.SHAPEFILE, ".kml": FileType.KML}
_COPY_CHUNK = 1024 * 1024
_MAX_STORED_ERROR_CHARS = 1000
_MAX_FILENAME_CHARS = 255
MIXED_CRS = "MIXED"


def ingest_upload(db: Session, *, filename: str, stream: BinaryIO, settings: Settings) -> UploadedFile:
    """Process an uploaded file and return its stored record.

    Raises ``UnsupportedFileTypeError``, ``EmptyUploadError`` or ``UploadTooLargeError``
    for uploads rejected before a record exists. Once a record exists, a
    ``ProcessingError`` is stored as a FAILED record (returned, not raised);
    any other exception marks the record FAILED and propagates.
    """
    safe_name = _basename(filename)
    file_type = _detect_file_type(safe_name)

    with tempfile.TemporaryDirectory(prefix="geo-upload-") as tmp:
        workdir = Path(tmp)
        # The client-supplied name is never used as a path.
        source = workdir / f"upload{PurePosixPath(safe_name).suffix.lower()}"
        size = _copy_to_disk(stream, source, settings.max_upload_bytes)

        record = UploadedFile(
            filename=safe_name, file_type=file_type, size_bytes=size, status=FileStatus.PROCESSING
        )
        db.add(record)
        db.commit()
        file_id = record.id

        try:
            layers = _parse(source, file_type, workdir, settings)
            _persist(db, file_id, layers, settings)
        except ProcessingError as exc:
            logger.info("File %s failed processing: %s", file_id, exc.message)
            return _mark_failed(db, file_id, exc.message)
        except Exception:
            logger.exception("Unexpected error processing file %s", file_id)
            _mark_failed(db, file_id, "Internal error while processing the file")
            raise

    db.refresh(record)
    logger.info("File %s processed: %d features", file_id, record.feature_count)
    return record


def _basename(filename: str) -> str:
    name = PurePosixPath(filename.replace("\\", "/")).name
    return "".join(ch for ch in name if ch.isprintable())[:_MAX_FILENAME_CHARS]


def _detect_file_type(filename: str) -> FileType:
    suffix = PurePosixPath(filename).suffix.lower()
    try:
        return _EXTENSIONS[suffix]
    except KeyError:
        raise UnsupportedFileTypeError(
            "Unsupported file type; upload a .zip containing a Shapefile, or a .kml file"
        ) from None


def _copy_to_disk(stream: BinaryIO, dest: Path, max_bytes: int) -> int:
    size = 0
    with dest.open("wb") as out:
        while chunk := stream.read(_COPY_CHUNK):
            size += len(chunk)
            if size > max_bytes:
                raise UploadTooLargeError(f"The file exceeds the {max_bytes} byte upload limit")
            out.write(chunk)
    if size == 0:
        raise EmptyUploadError("The uploaded file is empty")
    return size


def _parse(source: Path, file_type: FileType, workdir: Path, settings: Settings) -> list[ParsedLayer]:
    if file_type is FileType.KML:
        return parse_kml(source, settings.max_features)

    extracted = workdir / "extracted"
    extracted.mkdir()
    extract_zip(
        source,
        extracted,
        max_members=settings.max_zip_members,
        max_total_bytes=settings.max_uncompressed_bytes,
    )
    return parse_shapefiles(extracted, settings.max_features)


def _persist(db: Session, file_id: str, layers: list[ParsedLayer], settings: Settings) -> None:
    """Measure and store all features, then flip the file to COMPLETED in one commit."""
    measurer = Measurer()
    chunk: list[dict[str, Any]] = []
    count = 0

    for layer in layers:
        for feature in layer.features:
            result = measurer.measure(feature.wgs84_geometry)
            chunk.append(
                {
                    "file_id": file_id,
                    "layer": layer.name,
                    "feature_index": feature.index,
                    "geometry_type": feature.geometry.geom_type if feature.geometry is not None else None,
                    "geometry": geometry_to_geojson(feature.geometry),
                    "crs": layer.crs,
                    "properties": feature.properties,
                    "measurement_status": result.status,
                    "area_sq_m": result.area_sq_m,
                    "length_m": result.length_m,
                    "projected_crs": result.projected_crs,
                    "message": result.message,
                    "warnings": list(result.warnings),
                }
            )
            count += 1
            if len(chunk) >= settings.insert_chunk_size:
                db.execute(insert(Feature), chunk)
                chunk = []
    if chunk:
        db.execute(insert(Feature), chunk)

    record = db.get(UploadedFile, file_id)
    record.feature_count = count
    record.crs = _file_crs(layers)
    record.status = FileStatus.COMPLETED
    db.commit()


def _file_crs(layers: list[ParsedLayer]) -> str | None:
    crs_values = {layer.crs for layer in layers}
    if not crs_values:
        return None
    return crs_values.pop() if len(crs_values) == 1 else MIXED_CRS


def _mark_failed(db: Session, file_id: str, message: str) -> UploadedFile:
    db.rollback()  # discards any features inserted before the failure
    record = db.get(UploadedFile, file_id)
    record.status = FileStatus.FAILED
    record.error = message[:_MAX_STORED_ERROR_CHARS]
    record.feature_count = 0
    record.crs = None
    db.commit()
    return record
