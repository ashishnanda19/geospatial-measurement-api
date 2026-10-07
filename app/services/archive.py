"""Defensive extraction of user-supplied zip archives."""

import logging
import stat
import zipfile
from pathlib import Path, PurePosixPath

from app.errors import ProcessingError

logger = logging.getLogger(__name__)

_CHUNK = 64 * 1024


def extract_zip(archive: Path, dest: Path, *, max_members: int, max_total_bytes: int) -> None:
    """Extract ``archive`` into ``dest``, refusing anything suspicious.

    Guards against zip-slip (entries resolving outside ``dest``), zip bombs
    (too many members, or too many uncompressed bytes - both as declared in the
    headers and as actually written) and non-regular files such as symlinks.
    macOS metadata (``__MACOSX/``, ``._*``) is skipped.
    """
    try:
        with zipfile.ZipFile(archive) as zf:
            _extract_members(zf, dest, max_members, max_total_bytes)
    except zipfile.BadZipFile as exc:
        raise ProcessingError(f"The uploaded file is not a valid zip archive: {exc}") from exc
    except (NotImplementedError, RuntimeError) as exc:
        # Unsupported compression method, or an encrypted archive.
        raise ProcessingError(f"The zip archive cannot be read: {exc}") from exc


def _extract_members(zf: zipfile.ZipFile, dest: Path, max_members: int, max_total_bytes: int) -> None:
    infos = zf.infolist()
    if len(infos) > max_members:
        raise ProcessingError(f"The zip archive has {len(infos)} entries; the limit is {max_members}")
    declared = sum(info.file_size for info in infos)
    if declared > max_total_bytes:
        raise ProcessingError(f"The zip archive expands to {declared} bytes; the limit is {max_total_bytes}")

    root = dest.resolve()
    written = 0
    for info in infos:
        if info.is_dir() or _is_macos_metadata(info.filename):
            continue
        if not _is_regular_file(info):
            logger.warning("Skipping non-regular zip entry %r", info.filename)
            continue
        target = _safe_target(root, info.filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, target.open("wb") as out:
            while chunk := src.read(_CHUNK):
                written += len(chunk)
                if written > max_total_bytes:
                    raise ProcessingError(f"The zip archive expands beyond the {max_total_bytes} byte limit")
                out.write(chunk)


def _safe_target(root: Path, member_name: str) -> Path:
    # Zip names use "/", but some writers emit "\"; normalise so ".." is caught.
    normalised = member_name.replace("\\", "/")
    target = (root / normalised).resolve()
    if not target.is_relative_to(root) or target == root:
        raise ProcessingError(f"The zip archive contains an unsafe path: {member_name!r}")
    return target


def _is_macos_metadata(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    return "__MACOSX" in path.parts or path.name.startswith("._")


def _is_regular_file(info: zipfile.ZipInfo) -> bool:
    # Some writers (including Python's zipfile) store permission bits only, with no
    # file-type bits; such entries are ordinary files. Only an explicit non-regular
    # type (symlink, device, ...) is refused.
    return stat.S_IFMT(info.external_attr >> 16) in (0, stat.S_IFREG)
