"""CRS labelling and the choice of projected CRS used for measuring.

Measurements are never taken on latitude/longitude degrees. Geometries are
brought to WGS84 first and then projected into a metric CRS chosen per feature:

* area   -> a local Lambert Azimuthal Equal-Area projection (area preserving)
* length -> the UTM zone containing the feature (distances accurate to ~0.1%)
"""

from pyproj import CRS

WGS84 = "EPSG:4326"

_UPS_NORTH = 32661
_UPS_SOUTH = 32761
_UTM_NORTH_BASE = 32600
_UTM_SOUTH_BASE = 32700

# Snapping the LAEA centre to a grid lets one transformer serve many features.
# LAEA preserves area wherever it is centred, so this does not affect results.
_LAEA_CENTRE_DECIMALS = 1


def crs_label(crs: CRS) -> str:
    """Short human-readable identifier, e.g. ``EPSG:4326``; falls back to the CRS name."""
    epsg = crs.to_epsg(min_confidence=70)
    if epsg is not None:
        return f"EPSG:{epsg}"
    return crs.name or "UNKNOWN"


def utm_epsg(lon: float, lat: float) -> int:
    """EPSG code of the WGS84 UTM zone containing a point (UPS near the poles).

    Does not model the Norway/Svalbard zone exceptions; the error this causes
    is negligible for measuring.
    """
    if lat > 84:
        return _UPS_NORTH
    if lat < -80:
        return _UPS_SOUTH
    zone = min(int((lon + 180) // 6) + 1, 60)
    return (_UTM_NORTH_BASE if lat >= 0 else _UTM_SOUTH_BASE) + zone


def utm_crs_string(lon: float, lat: float) -> str:
    return f"EPSG:{utm_epsg(lon, lat)}"


def laea_crs_string(lon: float, lat: float) -> str:
    """PROJ string for a Lambert Azimuthal Equal-Area CRS centred near (lon, lat)."""
    lon_0 = round(lon, _LAEA_CENTRE_DECIMALS) + 0.0  # + 0.0 turns -0.0 into 0.0
    lat_0 = round(lat, _LAEA_CENTRE_DECIMALS) + 0.0
    return f"+proj=laea +lat_0={lat_0} +lon_0={lon_0} +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"
