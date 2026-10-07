"""Read Shapefiles and KML into plain, framework-free feature records."""

import logging
import math
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyogrio
import shapely
from pyogrio.errors import DataLayerError, DataSourceError, FieldError, GeometryError
from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.errors import ProcessingError
from app.services.crs import WGS84, crs_label

logger = logging.getLogger(__name__)

_READ_ERRORS = (DataSourceError, DataLayerError, FieldError, GeometryError)


@dataclass(frozen=True)
class ParsedFeature:
    """One feature, with its geometry both as stored (original CRS) and in WGS84."""

    layer: str
    index: int
    geometry: BaseGeometry | None
    wgs84_geometry: BaseGeometry | None
    properties: dict[str, Any]


@dataclass(frozen=True)
class ParsedLayer:
    name: str
    crs: str
    features: list[ParsedFeature]


def parse_kml(path: Path, max_features: int) -> list[ParsedLayer]:
    """Read every layer of a KML file (each KML folder becomes a layer).

    KML coordinates are WGS84 by specification, so a missing CRS means EPSG:4326.
    """
    try:
        layer_names = [str(name) for name, _ in pyogrio.list_layers(path)]
    except _READ_ERRORS as exc:
        raise ProcessingError(f"The KML file could not be read: {exc}") from exc
    return _read_layers(path, layer_names, max_features, default_crs=WGS84, source="KML file")


def parse_shapefiles(root: Path, max_features: int) -> list[ParsedLayer]:
    """Read every ``.shp`` found under ``root`` (an extracted zip).

    A Shapefile carries its CRS only in the ``.prj`` sidecar; without it the
    coordinates cannot be interpreted, so the upload is rejected.
    """
    shapefiles = sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".shp")
    if not shapefiles:
        raise ProcessingError("The zip archive does not contain a .shp file")

    layers: list[ParsedLayer] = []
    for shp in shapefiles:
        name = shp.relative_to(root).with_suffix("").as_posix()
        already_read = sum(len(layer.features) for layer in layers)
        layers.extend(
            _read_layers(
                shp,
                [None],
                max_features,
                already_read=already_read,
                default_crs=None,
                source=f"Shapefile '{name}'",
                name=name,
            )
        )
    return layers


def _read_layers(
    path: Path,
    layer_names: list[str | None],
    max_features: int,
    *,
    default_crs: str | None,
    source: str,
    name: str | None = None,
    already_read: int = 0,
) -> list[ParsedLayer]:
    layers: list[ParsedLayer] = []
    remaining = max_features - already_read
    for layer_name in layer_names:
        try:
            # Reading one more than allowed lets us detect an over-limit file
            # without loading all of it.
            gdf = pyogrio.read_dataframe(path, layer=layer_name, max_features=remaining + 1)
        except _READ_ERRORS as exc:
            raise ProcessingError(f"{source} could not be read: {exc}") from exc
        if len(gdf) > remaining:
            raise ProcessingError(f"The file has more than {max_features} features, which is the limit")
        if gdf.empty:
            continue

        resolved_name = name or layer_name or path.stem
        layers.append(_to_parsed_layer(gdf, resolved_name, default_crs, source))
        remaining -= len(gdf)
    return layers


def _to_parsed_layer(gdf: Any, name: str, default_crs: str | None, source: str) -> ParsedLayer:
    if gdf.crs is None:
        if default_crs is None:
            raise ProcessingError(
                f"{source} has no coordinate reference system (the .prj file is missing "
                "or unreadable), so its coordinates cannot be interpreted"
            )
        gdf = gdf.set_crs(default_crs)

    crs = CRS.from_user_input(gdf.crs)
    label = crs_label(crs)
    originals = list(gdf.geometry)
    if label == WGS84:
        wgs84 = originals
    else:
        try:
            wgs84 = list(gdf.to_crs(WGS84).geometry)
        except Exception as exc:  # noqa: BLE001 - pyproj raises several unrelated types
            raise ProcessingError(f"Could not reproject {source} from {label} to {WGS84}: {exc}") from exc

    attributes = gdf.drop(columns=gdf.geometry.name)
    columns = [str(c) for c in attributes.columns]
    rows = attributes.to_numpy(dtype=object)  # one (possibly empty) row per feature

    features = [
        ParsedFeature(
            layer=name,
            index=i,
            geometry=originals[i],
            wgs84_geometry=wgs84[i],
            properties={col: _json_safe(value) for col, value in zip(columns, rows[i], strict=True)},
        )
        for i in range(len(gdf))
    ]
    return ParsedLayer(name=name, crs=label, features=features)


def geometry_to_geojson(geometry: BaseGeometry | None) -> dict[str, Any] | None:
    """GeoJSON-style mapping in the geometry's own CRS; ``None`` if absent or unusable."""
    if geometry is None or geometry.is_empty:
        return None
    if not np.isfinite(shapely.get_coordinates(geometry, include_z=geometry.has_z)).all():
        return None  # NaN/inf are not valid JSON; the measurement step reports the problem
    return shapely.geometry.mapping(geometry)


def _json_safe(value: Any) -> Any:
    """Convert a pandas/numpy attribute value to something ``json.dumps`` accepts."""
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, np.bool_ | bool):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, float | np.floating):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, pd.Timestamp | datetime | date):
        return value.isoformat()
    if isinstance(value, np.datetime64):
        return None if np.isnat(value) else str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, list | tuple | np.ndarray):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, str | int):
        return value
    return str(value)
