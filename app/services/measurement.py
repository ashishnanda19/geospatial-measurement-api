"""Per-feature area and length measurement."""

import logging
import math
from dataclasses import dataclass

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry.base import BaseGeometry

from app.models import MeasurementStatus
from app.services.crs import WGS84, laea_crs_string, utm_crs_string

logger = logging.getLogger(__name__)

_AREAL = frozenset({"Polygon", "MultiPolygon"})
_LINEAR = frozenset({"LineString", "MultiLineString"})
_POINTLIKE = frozenset({"Point", "MultiPoint"})

_MAX_LON_SPAN = 180.0


@dataclass(frozen=True)
class MeasurementResult:
    status: MeasurementStatus
    area_sq_m: float | None = None
    length_m: float | None = None
    projected_crs: str | None = None
    message: str | None = None
    warnings: tuple[str, ...] = ()


class Measurer:
    """Measures WGS84 (lon/lat) geometries by projecting them into a metric CRS.

    Holds a transformer cache. pyproj transformers are not guaranteed to be
    thread-safe, so create one ``Measurer`` per file/request instead of sharing.
    """

    def __init__(self) -> None:
        self._transformers: dict[str, Transformer] = {}

    def measure(self, geometry: BaseGeometry | None) -> MeasurementResult:
        """Measure one geometry. Never raises: failures become an ERROR result."""
        try:
            return self._measure(geometry)
        except Exception as exc:  # noqa: BLE001 - one bad feature must not fail the file
            logger.warning("Measurement failed", exc_info=True)
            return MeasurementResult(MeasurementStatus.ERROR, message=f"Measurement failed: {exc}")

    def _measure(self, geometry: BaseGeometry | None) -> MeasurementResult:
        if geometry is None or geometry.is_empty:
            return MeasurementResult(MeasurementStatus.NO_GEOMETRY, message="Feature has no geometry")

        kind = geometry.geom_type
        if kind not in _AREAL | _LINEAR | _POINTLIKE:
            return MeasurementResult(
                MeasurementStatus.UNSUPPORTED,
                message=f"Geometry type '{kind}' is not supported for measurement",
            )

        flat = shapely.force_2d(geometry)
        if (problem := _coordinate_problem(flat)) is not None:
            return MeasurementResult(MeasurementStatus.ERROR, message=problem)
        warnings = tuple(_quality_warnings(flat))

        if kind in _POINTLIKE:
            return MeasurementResult(
                MeasurementStatus.NOT_APPLICABLE,
                message="Points have no area or length",
                warnings=warnings,
            )

        minx, miny, maxx, maxy = flat.bounds
        centre_lon, centre_lat = (minx + maxx) / 2, (miny + maxy) / 2

        if kind in _AREAL:
            target = laea_crs_string(centre_lon, centre_lat)
            value = self._project(flat, target).area
            result = MeasurementResult(
                MeasurementStatus.MEASURED, area_sq_m=value, projected_crs=target, warnings=warnings
            )
        else:
            target = utm_crs_string(centre_lon, centre_lat)
            value = self._project(flat, target).length
            result = MeasurementResult(
                MeasurementStatus.MEASURED, length_m=value, projected_crs=target, warnings=warnings
            )

        if not math.isfinite(value):
            return MeasurementResult(
                MeasurementStatus.ERROR,
                projected_crs=target,
                message="Projection produced a non-finite result",
                warnings=warnings,
            )
        return result

    def _project(self, geometry: BaseGeometry, target_crs: str) -> BaseGeometry:
        transformer = self._transformers.get(target_crs)
        if transformer is None:
            transformer = Transformer.from_crs(WGS84, target_crs, always_xy=True)
            self._transformers[target_crs] = transformer
        coords = shapely.get_coordinates(geometry)
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return shapely.set_coordinates(geometry, np.column_stack((x, y)))


def _coordinate_problem(geometry: BaseGeometry) -> str | None:
    coords = shapely.get_coordinates(geometry)
    if not np.isfinite(coords).all():
        return "Geometry contains non-finite (NaN or infinite) coordinates"
    lon, lat = coords[:, 0], coords[:, 1]
    if (np.abs(lon) > 180).any() or (np.abs(lat) > 90).any():
        return "Coordinates are outside the valid WGS84 range (lon -180..180, lat -90..90)"
    return None


def _quality_warnings(geometry: BaseGeometry) -> list[str]:
    warnings: list[str] = []
    if not shapely.is_valid(geometry):
        warnings.append(f"Invalid geometry: {shapely.is_valid_reason(geometry)}")
    minx, _, maxx, _ = geometry.bounds
    if maxx - minx > _MAX_LON_SPAN:
        warnings.append(
            "Geometry spans more than 180 degrees of longitude; it may cross the "
            "antimeridian and the measurement may be unreliable"
        )
    return warnings
