"""Routes for the bundled test UI: the page itself and the two sample uploads."""

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from app.errors import UploadNotFoundError

APP_DIR = Path(__file__).resolve().parents[1]
STATIC_DIR = APP_DIR / "static"
SAMPLES_DIR = APP_DIR.parent / "samples"

# An allowlist, not a directory mount: only these files are ever served from samples/.
SAMPLES = {
    "survey.kml": "application/vnd.google-earth.kml+xml",
    "parcels_utm43.zip": "application/zip",
}

router = APIRouter(include_in_schema=False)


@router.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/samples/{name}")
def sample(name: str) -> FileResponse:
    media_type = SAMPLES.get(name)
    if media_type is None or not (SAMPLES_DIR / name).is_file():
        raise UploadNotFoundError(f"Sample '{name}' not found")
    return FileResponse(SAMPLES_DIR / name, media_type=media_type, filename=name)
