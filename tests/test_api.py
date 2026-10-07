"""End-to-end tests through the HTTP API."""

from collections.abc import Callable

import geopandas as gpd
import pytest
from fastapi.testclient import TestClient
from pyproj import Transformer
from shapely import transform
from shapely.geometry import LineString, Polygon, box
from sqlalchemy import select

from app.models import FileStatus, UploadedFile
from tests.helpers import (
    folder,
    geodesic_area,
    geodesic_length,
    kml,
    linestring_xml,
    multigeometry_xml,
    placemark,
    point_xml,
    polygon_xml,
    shapefile_zip,
    square,
    zip_bytes,
)

KML_MIME = "application/vnd.google-earth.kml+xml"
ZIP_MIME = "application/zip"


def upload(client: TestClient, filename: str, data: bytes, mime: str = KML_MIME):
    return client.post("/api/files/", files={"file": (filename, data, mime)})


def measurements(client: TestClient, file_id: str, **params: object) -> dict:
    response = client.get(f"/api/files/{file_id}/measurements/", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def by_key(body: dict) -> dict[tuple[str, int], dict]:
    return {(r["layer"], r["feature_index"]): r for r in body["results"]}


# --- happy paths -------------------------------------------------------------------------


def test_multi_layer_kml_end_to_end(client: TestClient) -> None:
    outer = square(77.2, 28.6, 0.05)
    hole = [(77.215, 28.615), (77.235, 28.615), (77.235, 28.635), (77.215, 28.635), (77.215, 28.615)]
    road = [(77.2, 28.7), (77.3, 28.7)]
    data = kml(
        folder(
            "Plots",
            placemark("Plot A", polygon_xml(outer, [hole]), {"owner": "Singh", "crop": "wheat"}),
            placemark("Unmapped plot"),
        ),
        folder(
            "Roads",
            placemark(
                "R1", multigeometry_xml(linestring_xml(road), linestring_xml([(77.2, 28.8), (77.3, 28.8)]))
            ),
            placemark("Well", point_xml((77.25, 28.65))),
        ),
    )

    created = upload(client, "survey.kml", data)

    assert created.status_code == 201
    info = created.json()
    assert info["filename"] == "survey.kml"
    assert info["file_type"] == "kml"
    assert info["status"] == "COMPLETED"
    assert info["crs"] == "EPSG:4326"
    assert info["feature_count"] == 4
    assert client.get(f"/api/files/{info['id']}/").json() == info

    body = measurements(client, info["id"])
    rows = by_key(body)

    plot = rows[("Plots", 0)]
    assert plot["geometry_type"] == "Polygon"
    expected_area = geodesic_area(Polygon(outer, [hole]))
    assert plot["measurement"]["area_sq_m"] == pytest.approx(expected_area, rel=0.002)
    assert plot["measurement"]["area_hectares"] == pytest.approx(expected_area / 10_000, rel=0.002)
    assert plot["projected_crs"].startswith("+proj=laea")

    assert rows[("Plots", 1)]["status"] == "NO_GEOMETRY"

    roads = rows[("Roads", 0)]
    assert roads["geometry_type"] == "MultiLineString"
    expected_length = geodesic_length(LineString(road)) + geodesic_length(
        LineString([(77.2, 28.8), (77.3, 28.8)])
    )
    assert roads["measurement"]["length_m"] == pytest.approx(expected_length, rel=0.002)
    assert roads["projected_crs"] == "EPSG:32643"

    well = rows[("Roads", 1)]
    assert well["status"] == "NOT_APPLICABLE"
    assert well["measurement"] is None

    summary = body["summary"]
    assert summary["feature_count"] == 4
    assert summary["by_status"] == {"MEASURED": 2, "NO_GEOMETRY": 1, "NOT_APPLICABLE": 1}
    assert summary["by_geometry_type"] == {"Polygon": 1, "MultiLineString": 1, "Point": 1, "None": 1}
    assert summary["total_area_sq_m"] == pytest.approx(plot["measurement"]["area_sq_m"], abs=0.01)
    assert summary["total_length_m"] == pytest.approx(roads["measurement"]["length_m"], abs=0.01)


def test_features_endpoint_returns_geometry_crs_and_properties(client: TestClient) -> None:
    data = kml(placemark("Plot A", polygon_xml(square(77.2, 28.6)), {"owner": "Singh"}), placemark("Empty"))
    file_id = upload(client, "a.kml", data).json()["id"]

    body = client.get(f"/api/files/{file_id}/features/").json()

    assert body["total"] == 2
    plot, empty = body["results"]
    assert plot["geometry_type"] == "Polygon"
    assert plot["geometry"]["type"] == "Polygon"
    assert plot["geometry"]["coordinates"][0][0][:2] == [77.2, 28.6]
    assert plot["crs"] == "EPSG:4326"
    assert plot["properties"]["owner"] == "Singh"
    assert plot["properties"]["Name"] == "Plot A"
    assert empty["geometry"] is None


def test_shapefile_in_wgs84_with_two_layers(client: TestClient) -> None:
    parcels = gpd.GeoDataFrame(
        {"name": ["north", "south"], "owner": ["Singh", None]},
        geometry=[box(77.0, 28.0, 77.1, 28.1), box(77.0, 27.0, 77.1, 27.1)],
        crs=4326,
    )
    roads = gpd.GeoDataFrame(
        {"name": ["NH-1"]}, geometry=[LineString([(77.0, 28.0), (77.2, 28.1)])], crs=4326
    )

    created = upload(client, "layers.zip", shapefile_zip({"parcels": parcels, "roads": roads}), ZIP_MIME)

    assert created.status_code == 201, created.text
    info = created.json()
    assert info["file_type"] == "shapefile"
    assert info["crs"] == "EPSG:4326"
    assert info["feature_count"] == 3

    rows = by_key(measurements(client, info["id"]))
    assert rows[("parcels", 0)]["measurement"]["area_sq_m"] == pytest.approx(
        geodesic_area(parcels.geometry[0]), rel=0.002
    )
    assert rows[("parcels", 1)]["measurement"]["area_sq_m"] == pytest.approx(
        geodesic_area(parcels.geometry[1]), rel=0.002
    )
    assert rows[("roads", 0)]["measurement"]["length_m"] == pytest.approx(
        geodesic_length(roads.geometry[0]), rel=0.002
    )

    features = client.get(f"/api/files/{info['id']}/features/", params={"layer": "parcels"}).json()
    assert [f["properties"]["owner"] for f in features["results"]] == ["Singh", None]


def test_shapefile_in_projected_crs_is_measured_on_the_ground(client: TestClient) -> None:
    plot = box(500_000, 3_100_000, 500_300, 3_100_200)
    gdf = gpd.GeoDataFrame({"name": ["plot"]}, geometry=[plot], crs=32643)

    created = upload(client, "utm.zip", shapefile_zip({"utm": gdf}), ZIP_MIME)

    info = created.json()
    assert info["crs"] == "EPSG:32643"
    result = measurements(client, info["id"])["results"][0]

    # Independent truth: take the UTM polygon to lon/lat and measure it on the ellipsoid.
    to_wgs84 = Transformer.from_crs(32643, 4326, always_xy=True)
    lonlat = transform(plot, lambda xy: tuple(zip(*to_wgs84.transform(xy[:, 0], xy[:, 1]), strict=True)))
    assert result["measurement"]["area_sq_m"] == pytest.approx(geodesic_area(lonlat), rel=0.001)
    # UTM grid area is 60,000 m2; the true ground area differs by the scale factor, so a
    # service that measured the file's own coordinates would be slightly off.
    assert result["measurement"]["area_sq_m"] != pytest.approx(60_000, abs=1)

    geometry = client.get(f"/api/files/{info['id']}/features/").json()["results"][0]
    assert geometry["crs"] == "EPSG:32643"
    assert Polygon(geometry["geometry"]["coordinates"][0]).bounds == plot.bounds  # original CRS kept


def test_mixed_crs_layers_are_reported_as_mixed(client: TestClient) -> None:
    a = gpd.GeoDataFrame({"n": [1]}, geometry=[box(77.0, 28.0, 77.1, 28.1)], crs=4326)
    b = gpd.GeoDataFrame({"n": [1]}, geometry=[box(500_000, 3_100_000, 500_100, 3_100_100)], crs=32643)

    info = upload(client, "mixed.zip", shapefile_zip({"a": a, "b": b}), ZIP_MIME).json()

    assert info["crs"] == "MIXED"
    assert info["feature_count"] == 2


# --- graceful handling of odd geometries -------------------------------------------------


def test_odd_geometries_never_fail_the_file(client: TestClient) -> None:
    good = square(77.2, 28.6)
    data = kml(
        placemark("good", polygon_xml(good)),
        placemark("collection", multigeometry_xml(polygon_xml(good), point_xml((77.2, 28.6)))),
        placemark("bowtie", polygon_xml([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])),
        placemark("3d", polygon_xml([(x, y, 500) for x, y in good])),
        placemark("bad lat", point_xml((10, 95))),
        placemark("bad polygon", polygon_xml([(0, 0), (200, 0), (200, 10), (0, 0)])),
    )

    info = upload(client, "odd.kml", data).json()

    assert info["status"] == "COMPLETED"
    rows = by_key(measurements(client, info["id"]))
    layer = "Survey"
    assert rows[(layer, 0)]["status"] == "MEASURED"
    assert rows[(layer, 1)]["status"] == "UNSUPPORTED"
    assert rows[(layer, 1)]["geometry_type"] == "GeometryCollection"
    assert rows[(layer, 2)]["status"] == "MEASURED"
    assert any("Invalid geometry" in w for w in rows[(layer, 2)]["warnings"])
    assert rows[(layer, 3)]["measurement"]["area_sq_m"] == pytest.approx(
        rows[(layer, 0)]["measurement"]["area_sq_m"]
    )
    assert rows[(layer, 4)]["status"] == "ERROR"
    assert rows[(layer, 5)]["status"] == "ERROR"
    assert "outside the valid WGS84 range" in rows[(layer, 5)]["message"]


def test_one_unmeasurable_feature_does_not_affect_the_others(client: TestClient) -> None:
    data = kml(
        placemark("a", polygon_xml(square(10, 10))),
        placemark("bad", polygon_xml([(0, 0), (200, 0), (200, 10), (0, 0)])),
        placemark("b", polygon_xml(square(20, 20))),
    )

    body = measurements(client, upload(client, "x.kml", data).json()["id"])

    assert [r["status"] for r in body["results"]] == ["MEASURED", "ERROR", "MEASURED"]
    assert body["summary"]["by_status"] == {"MEASURED": 2, "ERROR": 1}


def test_extension_and_path_handling(client: TestClient) -> None:
    data = kml(placemark("p", point_xml((1, 1))))

    info = upload(client, "../../etc/SURVEY.KML", data).json()

    assert info["filename"] == "SURVEY.KML"  # basename only, extension matched case-insensitively
    assert info["status"] == "COMPLETED"


# --- rejected uploads --------------------------------------------------------------------


@pytest.mark.parametrize("filename", ["notes.txt", "data.geojson", "noextension", "archive.kmz"])
def test_unsupported_extension_is_415(client: TestClient, filename: str) -> None:
    response = upload(client, filename, b"data")

    assert response.status_code == 415
    assert "Unsupported file type" in response.json()["detail"]


def test_empty_upload_is_400(client: TestClient) -> None:
    response = upload(client, "empty.kml", b"")

    assert response.status_code == 400


def test_oversized_upload_is_413(make_client: Callable[..., TestClient]) -> None:
    client = make_client(max_upload_bytes=100)

    response = upload(client, "big.kml", b"x" * 101)

    assert response.status_code == 413


def test_missing_file_field_is_422_validation_error(client: TestClient) -> None:
    assert client.post("/api/files/", data={"other": "x"}).status_code == 422


# --- accepted but unprocessable: FAILED record + 422 --------------------------------------


def assert_failed(client: TestClient, response, *, error_contains: str) -> None:
    assert response.status_code == 422
    body = response.json()
    assert body["status"] == "FAILED"
    assert error_contains in body["error"]
    assert body["feature_count"] == 0
    # The failure is stored: the client can still look the file up afterwards.
    stored = client.get(f"/api/files/{body['id']}/")
    assert stored.status_code == 200
    assert stored.json()["status"] == "FAILED"
    # ...but there are no results to fetch.
    for path in ("measurements", "features"):
        blocked = client.get(f"/api/files/{body['id']}/{path}/")
        assert blocked.status_code == 409
        assert "FAILED" in blocked.json()["detail"]


def test_corrupt_zip(client: TestClient) -> None:
    assert_failed(
        client, upload(client, "bad.zip", b"definitely not a zip", ZIP_MIME), error_contains="not a valid zip"
    )


def test_zip_without_shapefile(client: TestClient) -> None:
    response = upload(client, "nothing.zip", zip_bytes({"readme.txt": b"hi"}), ZIP_MIME)

    assert_failed(client, response, error_contains=".shp")


def test_shapefile_without_prj(client: TestClient) -> None:
    gdf = gpd.GeoDataFrame({"n": [1]}, geometry=[box(0, 0, 1, 1)], crs=4326)

    response = upload(client, "noprj.zip", shapefile_zip({"p": gdf}, include_prj=False), ZIP_MIME)

    assert_failed(client, response, error_contains=".prj")


def test_zip_slip_entry(client: TestClient) -> None:
    response = upload(client, "evil.zip", zip_bytes({"../../evil.shp": b"x"}), ZIP_MIME)

    assert_failed(client, response, error_contains="unsafe path")


def test_zip_bomb(make_client: Callable[..., TestClient]) -> None:
    client = make_client(max_uncompressed_bytes=1_000_000)
    bomb = zip_bytes({"p.shp": bytes(5_000_000)})

    response = upload(client, "bomb.zip", bomb, ZIP_MIME)

    assert_failed(client, response, error_contains="expands to")


def test_kml_that_is_not_kml(client: TestClient) -> None:
    assert_failed(client, upload(client, "fake.kml", b"just some text"), error_contains="KML")


def test_feature_limit(make_client: Callable[..., TestClient]) -> None:
    client = make_client(max_features=2)
    data = kml(*(placemark(f"p{i}", point_xml((i, i))) for i in range(3)))

    assert_failed(client, upload(client, "many.kml", data), error_contains="more than 2 features")


def test_unexpected_error_marks_file_failed_and_returns_500(
    make_client: Callable[..., TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    client = make_client()

    def explode(*_: object, **__: object) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr("app.services.ingest.parse_kml", explode)

    response = upload(client, "a.kml", kml(placemark("p", point_xml((1, 1)))))

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}  # internals are not leaked
    with client.app.state.session_factory() as db:
        (record,) = db.scalars(select(UploadedFile)).all()
    assert record.status is FileStatus.FAILED
    assert record.error == "Internal error while processing the file"


# --- lookups, pagination and filters ------------------------------------------------------


def test_unknown_id_is_404(client: TestClient) -> None:
    for path in ("", "measurements/", "features/"):
        response = client.get(f"/api/files/doesnotexist/{path}")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"]


def test_pagination_and_geometry_type_filter(client: TestClient) -> None:
    placemarks = [placemark(f"poly{i}", polygon_xml(square(10 + i, 10))) for i in range(3)]
    placemarks += [placemark(f"pt{i}", point_xml((i, i))) for i in range(4)]
    file_id = upload(client, "many.kml", kml(*placemarks)).json()["id"]

    page = measurements(client, file_id, limit=3, offset=2)
    assert (page["total"], page["limit"], page["offset"], len(page["results"])) == (7, 3, 2, 3)

    polygons = measurements(client, file_id, geometry_type="polygon")  # case-insensitive
    assert polygons["total"] == 3
    assert {r["geometry_type"] for r in polygons["results"]} == {"Polygon"}
    # The summary always describes the whole file, not the page or the filter.
    assert polygons["summary"]["feature_count"] == 7
    assert polygons["summary"]["by_status"] == {"MEASURED": 3, "NOT_APPLICABLE": 4}

    assert measurements(client, file_id, offset=100)["results"] == []
    assert client.get(f"/api/files/{file_id}/features/", params={"limit": 2}).json()["total"] == 7


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 1001}, {"offset": -1}])
def test_invalid_pagination_is_422(client: TestClient, params: dict[str, int]) -> None:
    file_id = upload(client, "a.kml", kml(placemark("p", point_xml((1, 1))))).json()["id"]

    assert client.get(f"/api/files/{file_id}/measurements/", params=params).status_code == 422


def test_health_and_openapi_docs(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/docs").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/api/files/", "/api/files/{file_id}/", "/api/files/{file_id}/measurements/"} <= set(paths)


def test_points_only_file_has_zero_totals(client: TestClient) -> None:
    file_id = upload(client, "pts.kml", kml(placemark("a", point_xml((1, 1))))).json()["id"]

    summary = measurements(client, file_id)["summary"]

    assert summary["total_area_sq_m"] == 0
    assert summary["total_length_m"] == 0
