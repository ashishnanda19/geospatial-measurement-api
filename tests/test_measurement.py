import math

import pytest
from shapely.geometry import GeometryCollection, LineString, MultiPoint, MultiPolygon, Point, Polygon, box

from app.models import MeasurementStatus
from app.services.measurement import Measurer
from tests.helpers import geodesic_area, geodesic_length

# (lon, lat) of the south-west corner of the test shapes: equator, mid latitudes,
# southern hemisphere, high latitude, and different UTM zones.
LOCATIONS = [
    (0.0, 0.0),
    (77.2, 28.6),
    (-100.0, 45.0),
    (151.2, -33.9),
    (10.0, 60.0),
    (25.0, 80.0),
    (-179.5, 10.0),
]


@pytest.mark.parametrize(("lon", "lat"), LOCATIONS)
def test_polygon_area_matches_geodesic(lon: float, lat: float) -> None:
    polygon = box(lon, lat, lon + 0.5, lat + 0.5)

    result = Measurer().measure(polygon)

    assert result.status is MeasurementStatus.MEASURED
    assert result.area_sq_m == pytest.approx(geodesic_area(polygon), rel=0.0005)
    assert result.length_m is None


@pytest.mark.parametrize(("lon", "lat"), LOCATIONS)
def test_linestring_length_matches_geodesic(lon: float, lat: float) -> None:
    line = LineString([(lon, lat), (lon + 0.3, lat + 0.1), (lon + 0.4, lat + 0.4)])

    result = Measurer().measure(line)

    assert result.status is MeasurementStatus.MEASURED
    assert result.length_m == pytest.approx(geodesic_length(line), rel=0.002)
    assert result.area_sq_m is None


def test_length_near_the_pole_uses_ups_and_stays_within_one_percent() -> None:
    line = LineString([(10.0, 86.0), (20.0, 86.2)])

    result = Measurer().measure(line)

    assert result.projected_crs == "EPSG:32661"
    assert result.length_m == pytest.approx(geodesic_length(line), rel=0.01)


def test_area_is_reported_with_its_projected_crs() -> None:
    result = Measurer().measure(box(77.2, 28.6, 77.3, 28.7))
    assert result.projected_crs is not None
    assert result.projected_crs.startswith("+proj=laea")


def test_line_length_is_reported_with_its_utm_zone() -> None:
    result = Measurer().measure(LineString([(77.2, 28.6), (77.3, 28.6)]))
    assert result.projected_crs == "EPSG:32643"


def test_naive_degree_area_would_be_wrong() -> None:
    """Why projection matters: the planar area of a lon/lat box is in 'square degrees'."""
    polygon = box(10.0, 60.0, 11.0, 61.0)

    assert polygon.area == 1.0  # the meaningless number a naive implementation returns
    correct = Measurer().measure(polygon).area_sq_m
    assert correct == pytest.approx(geodesic_area(polygon), rel=0.002)
    # Even converting degrees to metres at the equator's 111.32 km/degree overstates it by ~2x at 60N.
    naive_metres = polygon.area * 111_320.0**2
    assert naive_metres > 1.8 * correct


def test_polygon_hole_is_subtracted() -> None:
    outer = box(77.0, 28.0, 77.2, 28.2)
    hole = box(77.05, 28.05, 77.15, 28.15)
    with_hole = Polygon(outer.exterior.coords, [hole.exterior.coords])

    result = Measurer().measure(with_hole)

    assert result.area_sq_m == pytest.approx(geodesic_area(with_hole), rel=0.002)
    assert result.area_sq_m < Measurer().measure(outer).area_sq_m


def test_multipolygon_area_is_the_sum_of_its_parts() -> None:
    a, b = box(77.0, 28.0, 77.1, 28.1), box(77.5, 28.5, 77.6, 28.6)

    result = Measurer().measure(MultiPolygon([a, b]))

    assert result.area_sq_m == pytest.approx(geodesic_area(a) + geodesic_area(b), rel=0.002)


def test_three_dimensional_coordinates_are_measured_in_2d() -> None:
    flat = box(77.0, 28.0, 77.1, 28.1)
    raised = Polygon([(x, y, 500.0) for x, y in flat.exterior.coords])

    assert Measurer().measure(raised).area_sq_m == pytest.approx(Measurer().measure(flat).area_sq_m)


@pytest.mark.parametrize("geometry", [Point(77.2, 28.6), MultiPoint([(1, 1), (2, 2)])])
def test_points_need_no_measurement(geometry: Point) -> None:
    result = Measurer().measure(geometry)

    assert result.status is MeasurementStatus.NOT_APPLICABLE
    assert result.area_sq_m is None
    assert result.length_m is None


def test_geometry_collection_is_unsupported_not_an_error() -> None:
    result = Measurer().measure(GeometryCollection([Point(1, 1), box(0, 0, 1, 1)]))

    assert result.status is MeasurementStatus.UNSUPPORTED
    assert "GeometryCollection" in (result.message or "")


@pytest.mark.parametrize("geometry", [None, Polygon(), LineString()])
def test_missing_or_empty_geometry(geometry: object) -> None:
    assert Measurer().measure(geometry).status is MeasurementStatus.NO_GEOMETRY  # type: ignore[arg-type]


def test_invalid_polygon_is_measured_with_a_warning() -> None:
    bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])

    result = Measurer().measure(bowtie)

    assert result.status is MeasurementStatus.MEASURED
    assert any("Invalid geometry" in w and "Self-intersection" in w for w in result.warnings)


def test_geometry_spanning_over_180_degrees_gets_a_warning() -> None:
    result = Measurer().measure(LineString([(-170.0, 10.0), (170.0, 10.0)]))

    assert any("180 degrees" in w for w in result.warnings)


@pytest.mark.parametrize("geometry", [Point(10, 95), Polygon([(0, 0), (200, 0), (200, 10), (0, 0)])])
def test_coordinates_outside_wgs84_range_are_an_error(geometry: object) -> None:
    result = Measurer().measure(geometry)  # type: ignore[arg-type]

    assert result.status is MeasurementStatus.ERROR
    assert "outside the valid WGS84 range" in (result.message or "")


def test_non_finite_coordinates_are_an_error() -> None:
    result = Measurer().measure(LineString([(0, 0), (math.nan, 1)]))

    assert result.status is MeasurementStatus.ERROR
    assert "non-finite" in (result.message or "")


def test_unexpected_failure_becomes_an_error_result(monkeypatch: pytest.MonkeyPatch) -> None:
    measurer = Measurer()

    def boom(*_: object) -> None:
        raise RuntimeError("projection exploded")

    monkeypatch.setattr(measurer, "_project", boom)

    result = measurer.measure(box(0, 0, 1, 1))

    assert result.status is MeasurementStatus.ERROR
    assert "projection exploded" in (result.message or "")


def test_transformers_are_cached_per_projected_crs() -> None:
    measurer = Measurer()

    for dx in (0.0, 0.01, 0.02):  # centres snap to the same 0.1 degree LAEA cell
        measurer.measure(box(77.2 + dx, 28.6, 77.21 + dx, 28.61))

    assert len(measurer._transformers) == 1
