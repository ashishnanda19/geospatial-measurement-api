"""Fixture builders: every test file is generated in code, nothing is downloaded."""

import io
import tempfile
import zipfile
from collections.abc import Iterable, Mapping
from pathlib import Path

import geopandas as gpd
from pyproj import Geod
from shapely.geometry import LinearRing, LineString, Polygon

_GEOD = Geod(ellps="WGS84")


def _ring_area(ring: LinearRing) -> float:
    return abs(_GEOD.geometry_area_perimeter(Polygon(ring))[0])


def geodesic_area(polygon: Polygon) -> float:
    """Ground-truth area in m^2 on the WGS84 ellipsoid.

    Rings are measured one by one because pyproj does not subtract a hole that
    is wound the same way as its shell.
    """
    return _ring_area(polygon.exterior) - sum(_ring_area(hole) for hole in polygon.interiors)


def geodesic_length(line: LineString) -> float:
    return _GEOD.geometry_length(line)


# --- KML ---------------------------------------------------------------------------------


def _coords(points: Iterable[tuple[float, ...]]) -> str:
    return " ".join(",".join(str(v) for v in p) for p in points)


def polygon_xml(outer: Iterable[tuple[float, ...]], holes: Iterable[Iterable[tuple[float, ...]]] = ()) -> str:
    inner = "".join(
        f"<innerBoundaryIs><LinearRing><coordinates>{_coords(h)}</coordinates></LinearRing></innerBoundaryIs>"
        for h in holes
    )
    return (
        "<Polygon><outerBoundaryIs><LinearRing>"
        f"<coordinates>{_coords(outer)}</coordinates>"
        f"</LinearRing></outerBoundaryIs>{inner}</Polygon>"
    )


def linestring_xml(points: Iterable[tuple[float, ...]]) -> str:
    return f"<LineString><coordinates>{_coords(points)}</coordinates></LineString>"


def point_xml(point: tuple[float, ...]) -> str:
    return f"<Point><coordinates>{_coords([point])}</coordinates></Point>"


def multigeometry_xml(*parts: str) -> str:
    return f"<MultiGeometry>{''.join(parts)}</MultiGeometry>"


def placemark(name: str, geometry: str = "", extended: Mapping[str, str] | None = None) -> str:
    data = ""
    if extended:
        items = "".join(f'<Data name="{k}"><value>{v}</value></Data>' for k, v in extended.items())
        data = f"<ExtendedData>{items}</ExtendedData>"
    return f"<Placemark><name>{name}</name>{data}{geometry}</Placemark>"


def folder(name: str, *placemarks: str) -> str:
    return f"<Folder><name>{name}</name>{''.join(placemarks)}</Folder>"


def kml(*children: str, name: str = "Survey") -> bytes:
    body = "".join(children)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>{name}</name>{body}</Document></kml>'
    ).encode()


def square(lon: float, lat: float, size: float = 0.01) -> list[tuple[float, float]]:
    """Closed ring of a ``size``-degree square with its south-west corner at (lon, lat)."""
    return [
        (lon, lat),
        (lon + size, lat),
        (lon + size, lat + size),
        (lon, lat + size),
        (lon, lat),
    ]


# --- Shapefile ---------------------------------------------------------------------------


def zip_bytes(members: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def shapefile_zip(layers: Mapping[str, gpd.GeoDataFrame], *, include_prj: bool = True) -> bytes:
    """Write each GeoDataFrame as a Shapefile and zip the result."""
    with tempfile.TemporaryDirectory() as tmp:
        for name, gdf in layers.items():
            gdf.to_file(Path(tmp) / f"{name}.shp", driver="ESRI Shapefile")
        members = {
            path.name: path.read_bytes()
            for path in sorted(Path(tmp).iterdir())
            if include_prj or path.suffix != ".prj"
        }
    return zip_bytes(members)
