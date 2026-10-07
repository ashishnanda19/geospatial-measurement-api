import math
from datetime import date, datetime
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

from app.errors import ProcessingError
from app.services.archive import extract_zip
from app.services.parsing import _json_safe, geometry_to_geojson, parse_kml, parse_shapefiles
from tests.helpers import kml, placemark, point_xml, polygon_xml, shapefile_zip, square


def _unzip(tmp_path: Path, data: bytes) -> Path:
    archive = tmp_path / "in.zip"
    archive.write_bytes(data)
    dest = tmp_path / "extracted"
    dest.mkdir()
    extract_zip(archive, dest, max_members=50, max_total_bytes=10_000_000)
    return dest


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (pd.NaT, None),
        (math.nan, None),
        (math.inf, None),
        (np.float64("nan"), None),
        (np.int64(7), 7),
        (np.float64(1.5), 1.5),
        (np.bool_(True), True),
        (pd.Timestamp("2024-05-01 12:30:00"), "2024-05-01T12:30:00"),
        (datetime(2024, 5, 1, 12, 30), "2024-05-01T12:30:00"),
        (date(2024, 5, 1), "2024-05-01"),
        (np.datetime64("2024-05-01"), "2024-05-01"),
        (np.datetime64("NaT", "s"), None),
        (b"caf\xc3\xa9", "café"),
        ("text", "text"),
        (3, 3),
        ([np.int64(1), math.nan], [1, None]),
        ({"a": np.float64(2.0)}, {"a": 2.0}),
    ],
)
def test_json_safe(value: object, expected: object) -> None:
    assert _json_safe(value) == expected


def test_geometry_to_geojson_handles_2d_3d_and_missing() -> None:
    assert geometry_to_geojson(Point(1, 2)) == {"type": "Point", "coordinates": (1.0, 2.0)}
    assert geometry_to_geojson(Point(1, 2, 3))["coordinates"] == (1.0, 2.0, 3.0)  # type: ignore[index]
    assert geometry_to_geojson(None) is None
    assert geometry_to_geojson(Point()) is None
    assert geometry_to_geojson(Point(math.nan, 1)) is None


def test_kml_folders_become_layers_and_crs_defaults_to_wgs84(tmp_path: Path) -> None:
    path = tmp_path / "a.kml"
    path.write_bytes(
        kml(
            "<Folder><name>Plots</name>"
            + placemark("p1", polygon_xml(square(77.2, 28.6)), {"owner": "Singh"})
            + "</Folder><Folder><name>Wells</name>"
            + placemark("w1", point_xml((77.2, 28.6)))
            + "</Folder>"
        )
    )

    layers = {layer.name: layer for layer in parse_kml(path, max_features=100)}

    assert set(layers) == {"Plots", "Wells"}
    assert layers["Plots"].crs == "EPSG:4326"
    assert layers["Plots"].features[0].properties["owner"] == "Singh"
    assert layers["Plots"].features[0].index == 0


def test_kml_that_is_not_kml_is_a_processing_error(tmp_path: Path) -> None:
    path = tmp_path / "a.kml"
    path.write_bytes(b"this is not xml at all")

    with pytest.raises(ProcessingError, match="KML"):
        parse_kml(path, max_features=100)


def test_projected_shapefile_is_reprojected_for_measuring_but_keeps_original_geometry(tmp_path: Path) -> None:
    gdf = gpd.GeoDataFrame(
        {"name": ["plot"]}, geometry=[box(500_000, 3_000_000, 500_100, 3_000_200)], crs=32643
    )
    root = _unzip(tmp_path, shapefile_zip({"parcels": gdf}))

    (layer,) = parse_shapefiles(root, max_features=100)
    feature = layer.features[0]

    assert layer.crs == "EPSG:32643"
    assert feature.geometry.bounds == (500_000, 3_000_000, 500_100, 3_000_200)  # untouched
    minx, miny, *_ = feature.wgs84_geometry.bounds
    assert 70 < minx < 80  # UTM zone 43 spans 72E-78E
    assert 27 < miny < 28


def test_shapefile_without_prj_is_rejected_with_a_clear_message(tmp_path: Path) -> None:
    gdf = gpd.GeoDataFrame({"name": ["p"]}, geometry=[box(0, 0, 1, 1)], crs=4326)
    root = _unzip(tmp_path, shapefile_zip({"parcels": gdf}, include_prj=False))

    with pytest.raises(ProcessingError, match=r"\.prj"):
        parse_shapefiles(root, max_features=100)


def test_zip_without_a_shapefile_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hello")

    with pytest.raises(ProcessingError, match=r"\.shp"):
        parse_shapefiles(tmp_path, max_features=100)


def test_feature_limit_is_enforced_across_shapefiles(tmp_path: Path) -> None:
    gdf = gpd.GeoDataFrame({"n": [1, 2, 3]}, geometry=[Point(0, i) for i in range(3)], crs=4326)
    root = _unzip(tmp_path, shapefile_zip({"a": gdf, "b": gdf}))

    assert len(parse_shapefiles(root, max_features=6)) == 2
    with pytest.raises(ProcessingError, match="more than 5 features"):
        parse_shapefiles(root, max_features=5)


def test_multiple_shapefiles_become_separate_layers(tmp_path: Path) -> None:
    polys = gpd.GeoDataFrame({"n": [1]}, geometry=[box(0, 0, 1, 1)], crs=4326)
    points = gpd.GeoDataFrame({"n": [1]}, geometry=[Point(0, 0)], crs=4326)
    root = _unzip(tmp_path, shapefile_zip({"polys": polys, "points": points}))

    assert [layer.name for layer in parse_shapefiles(root, max_features=10)] == ["points", "polys"]
