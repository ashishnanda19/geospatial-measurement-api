import stat
import zipfile
from pathlib import Path

import pytest

from app.errors import ProcessingError
from app.services.archive import extract_zip
from tests.helpers import zip_bytes

LIMITS = {"max_members": 50, "max_total_bytes": 1_000_000}


def _extract(tmp_path: Path, data: bytes, **overrides: int) -> Path:
    archive = tmp_path / "in.zip"
    archive.write_bytes(data)
    dest = tmp_path / "out"
    dest.mkdir()
    extract_zip(archive, dest, **{**LIMITS, **overrides})
    return dest


def test_extracts_regular_files_including_nested_directories(tmp_path: Path) -> None:
    dest = _extract(tmp_path, zip_bytes({"a.shp": b"1", "sub/b.dbf": b"2"}))

    assert (dest / "a.shp").read_bytes() == b"1"
    assert (dest / "sub" / "b.dbf").read_bytes() == b"2"


@pytest.mark.parametrize(
    "name",
    ["../evil.txt", "a/../../evil.txt", "/tmp/evil.txt", "..\\evil.txt", "a\\..\\..\\evil.txt"],
)
def test_zip_slip_is_rejected(tmp_path: Path, name: str) -> None:
    with pytest.raises(ProcessingError, match="unsafe path"):
        _extract(tmp_path, zip_bytes({name: b"x"}))

    assert not (tmp_path / "evil.txt").exists()


def test_zip_bomb_is_rejected_by_declared_size(tmp_path: Path) -> None:
    bomb = zip_bytes({"zeros.bin": bytes(5_000_000)})  # compresses to a few KB
    assert len(bomb) < 100_000

    with pytest.raises(ProcessingError, match="expands to"):
        _extract(tmp_path, bomb)


def test_zip_with_understated_size_in_header_is_rejected(tmp_path: Path) -> None:
    """A bomb that lies about its size must not slip past the declared-size check."""
    data = bytearray(zip_bytes({"zeros.bin": bytes(5_000_000)}))
    central_directory = data.index(b"PK\x01\x02")
    data[central_directory + 24 : central_directory + 28] = (10).to_bytes(4, "little")  # uncompressed size

    with pytest.raises(ProcessingError, match="not a valid zip"):
        _extract(tmp_path, bytes(data))


def test_too_many_members_is_rejected(tmp_path: Path) -> None:
    data = zip_bytes({f"f{i}.txt": b"x" for i in range(10)})

    with pytest.raises(ProcessingError, match="10 entries"):
        _extract(tmp_path, data, max_members=5)


def test_macos_metadata_is_skipped(tmp_path: Path) -> None:
    dest = _extract(tmp_path, zip_bytes({"a.shp": b"1", "__MACOSX/._a.shp": b"junk", "sub/._b.dbf": b"junk"}))

    assert sorted(p.name for p in dest.rglob("*") if p.is_file()) == ["a.shp"]


def test_symlinks_are_not_extracted(tmp_path: Path) -> None:
    link = zipfile.ZipInfo("link.shp")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    archive = tmp_path / "in.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(link, "/etc/passwd")
        zf.writestr("real.shp", b"1")
    dest = tmp_path / "out"
    dest.mkdir()

    extract_zip(archive, dest, **LIMITS)

    assert sorted(p.name for p in dest.iterdir()) == ["real.shp"]


def test_corrupt_zip_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProcessingError, match="not a valid zip"):
        _extract(tmp_path, b"this is definitely not a zip file")
