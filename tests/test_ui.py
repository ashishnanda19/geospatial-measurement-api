"""The bundled test console: the page, its assets and the two sample downloads."""

import pytest
from fastapi.testclient import TestClient


def test_index_page_is_served(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Geospatial File Measurement API" in response.text
    assert 'id="file"' in response.text  # the upload input


@pytest.mark.parametrize("path", ["/static/app.js", "/static/app.css"])
def test_static_assets_are_served(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("name", ["survey.kml", "parcels_utm43.zip"])
def test_sample_files_can_be_downloaded(client: TestClient, name: str) -> None:
    response = client.get(f"/samples/{name}")

    assert response.status_code == 200
    assert len(response.content) > 100


@pytest.mark.parametrize("name", ["make_samples.py", "..%2FREADME.md", "missing.kml"])
def test_only_allowlisted_samples_are_served(client: TestClient, name: str) -> None:
    assert client.get(f"/samples/{name}").status_code == 404


def test_the_samples_upload_successfully_through_the_api(client: TestClient) -> None:
    """The console's sample buttons rely on these exact files and numbers."""
    kml_bytes = client.get("/samples/survey.kml").content
    zip_bytes = client.get("/samples/parcels_utm43.zip").content

    kml = client.post("/api/files/", files={"file": ("survey.kml", kml_bytes)}).json()
    zipped = client.post("/api/files/", files={"file": ("parcels_utm43.zip", zip_bytes)}).json()

    assert (kml["feature_count"], kml["crs"]) == (7, "EPSG:4326")
    assert (zipped["feature_count"], zipped["crs"]) == (3, "EPSG:32643")


def test_ui_routes_are_hidden_from_the_api_docs(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "/" not in paths
    assert not any(p.startswith(("/samples", "/static")) for p in paths)
