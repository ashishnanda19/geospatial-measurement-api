"""Regenerate the sample uploads in this directory.

    python samples/make_samples.py

* survey.kml          - WGS84 KML with two folders and a few awkward features
* parcels_utm43.zip   - two Shapefiles (polygons + lines) projected in EPSG:32643
"""

import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, Polygon, box

HERE = Path(__file__).parent

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Village survey</name>
    <Folder>
      <name>Plots</name>
      <Placemark>
        <name>Plot A (with a pond)</name>
        <ExtendedData>
          <Data name="owner"><value>Singh</value></Data>
          <Data name="crop"><value>wheat</value></Data>
        </ExtendedData>
        <Polygon>
          <outerBoundaryIs><LinearRing><coordinates>
            77.200,28.600,0 77.210,28.600,0 77.210,28.610,0 77.200,28.610,0 77.200,28.600,0
          </coordinates></LinearRing></outerBoundaryIs>
          <innerBoundaryIs><LinearRing><coordinates>
            77.203,28.603,0 77.207,28.603,0 77.207,28.607,0 77.203,28.607,0 77.203,28.603,0
          </coordinates></LinearRing></innerBoundaryIs>
        </Polygon>
      </Placemark>
      <Placemark>
        <name>Plot B</name>
        <ExtendedData>
          <Data name="owner"><value>Kaur</value></Data>
          <Data name="crop"><value>rice</value></Data>
        </ExtendedData>
        <Polygon>
          <outerBoundaryIs><LinearRing><coordinates>
            77.220,28.600 77.235,28.600 77.235,28.608 77.220,28.608 77.220,28.600
          </coordinates></LinearRing></outerBoundaryIs>
        </Polygon>
      </Placemark>
      <Placemark>
        <name>Disputed plot (boundary not surveyed yet)</name>
      </Placemark>
    </Folder>
    <Folder>
      <name>Infrastructure</name>
      <Placemark>
        <name>Canal</name>
        <LineString><coordinates>77.195,28.595 77.215,28.612 77.240,28.612</coordinates></LineString>
      </Placemark>
      <Placemark>
        <name>Village paths</name>
        <MultiGeometry>
          <LineString><coordinates>77.200,28.620 77.210,28.620</coordinates></LineString>
          <LineString><coordinates>77.210,28.620 77.210,28.630</coordinates></LineString>
        </MultiGeometry>
      </Placemark>
      <Placemark>
        <name>Well</name>
        <Point><coordinates>77.205,28.605,210</coordinates></Point>
      </Placemark>
      <Placemark>
        <name>Temple complex (polygon + marker)</name>
        <MultiGeometry>
          <Polygon><outerBoundaryIs><LinearRing><coordinates>
            77.250,28.600 77.252,28.600 77.252,28.602 77.250,28.602 77.250,28.600
          </coordinates></LinearRing></outerBoundaryIs></Polygon>
          <Point><coordinates>77.251,28.601</coordinates></Point>
        </MultiGeometry>
      </Placemark>
    </Folder>
  </Document>
</kml>
"""


def write_shapefile_zip(path: Path) -> None:
    parcels = gpd.GeoDataFrame(
        {"parcel_id": ["P-001", "P-002"], "owner": ["Singh", "Kaur"], "crop": ["wheat", "rice"]},
        geometry=[
            box(716_000, 3_165_000, 716_200, 3_165_150),
            Polygon(
                [(716_300, 3_165_000), (716_600, 3_165_000), (716_600, 3_165_100), (716_300, 3_165_100)],
                holes=[
                    [(716_400, 3_165_030), (716_500, 3_165_030), (716_500, 3_165_070), (716_400, 3_165_070)]
                ],
            ),
        ],
        crs="EPSG:32643",
    )
    canals = gpd.GeoDataFrame(
        {"name": ["Main canal"]},
        geometry=[LineString([(715_900, 3_164_900), (716_400, 3_165_300), (716_900, 3_165_300)])],
        crs="EPSG:32643",
    )
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        parcels.to_file(Path(tmp) / "parcels.shp")
        canals.to_file(Path(tmp) / "canals.shp")
        for member in sorted(Path(tmp).iterdir()):
            zf.write(member, member.name)


if __name__ == "__main__":
    (HERE / "survey.kml").write_text(KML, encoding="utf-8")
    write_shapefile_zip(HERE / "parcels_utm43.zip")
    print("wrote samples/survey.kml and samples/parcels_utm43.zip")
