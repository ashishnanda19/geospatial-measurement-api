import pytest
from pyproj import CRS

from app.services.crs import crs_label, laea_crs_string, utm_epsg


@pytest.mark.parametrize(
    ("lon", "lat", "expected"),
    [
        (77.2, 28.6, 32643),  # Delhi, zone 43N
        (-0.1, 51.5, 32630),  # London, zone 30N
        (151.2, -33.9, 32756),  # Sydney, zone 56S
        (-180.0, 0.0, 32601),
        (180.0, 0.0, 32660),  # lon 180 must clamp to zone 60, not 61
        (0.0, 84.0, 32631),  # UTM still applies at exactly 84N
        (10.0, 85.0, 32661),  # UPS North
        (10.0, -85.0, 32761),  # UPS South
    ],
)
def test_utm_epsg(lon: float, lat: float, expected: int) -> None:
    assert utm_epsg(lon, lat) == expected


def test_laea_centre_is_snapped_to_a_grid() -> None:
    assert laea_crs_string(77.23, 28.64) == laea_crs_string(77.2, 28.6)
    assert "+lon_0=77.2" in laea_crs_string(77.23, 28.64)


def test_laea_never_emits_negative_zero() -> None:
    assert "-0.0" not in laea_crs_string(-0.01, -0.02)


def test_laea_string_is_a_valid_metric_crs() -> None:
    crs = CRS.from_user_input(laea_crs_string(77.2, 28.6))
    assert crs.is_projected
    assert crs.axis_info[0].unit_name == "metre"


@pytest.mark.parametrize(("crs", "label"), [("EPSG:4326", "EPSG:4326"), ("EPSG:32643", "EPSG:32643")])
def test_crs_label_for_known_crs(crs: str, label: str) -> None:
    assert crs_label(CRS.from_user_input(crs)) == label


def test_crs_label_falls_back_to_name_for_custom_crs() -> None:
    label = crs_label(CRS.from_user_input(laea_crs_string(10, 10)))
    assert label  # non-empty, and not mistaken for an EPSG code
    assert not label.startswith("EPSG:")
