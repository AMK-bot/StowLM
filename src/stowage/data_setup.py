"""Acquire the pinned, non-redistributed KCS geometry asset.

Run ``uv run python -m stowage.data_setup`` in a source checkout. For an
offline installation, add ``--archive /path/to/kcs_offsets.ZIP``. The ZIP is
treated as data: only its exact ``kcs.fix`` member is read, never executed.
Both archive and member must match the pinned SHA256 before any output changes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.error import URLError
from urllib.request import urlopen
import zipfile

KCS_SOURCE_URL = (
    "https://www.nmri.go.jp/study/research_organization/fluid_performance/"
    "cfd/cfdws05/gothenburg2000/data/KCS_data/kcs_offsets.ZIP"
)
KCS_ARCHIVE_SHA256 = "62d737346e0a4bf7a065d3e675522d97dc67444c472aea1d3b45179a49fdbf58"
KCS_MEMBER_SHA256 = "aafc7e4f4d773bc50452d397985554c6a20f054150b8865ef70d61f836809190"
KCS_MEMBER = "kcs.fix"
MAX_ARCHIVE_BYTES = 8 * 1024 * 1024
MAX_MEMBER_BYTES = 4 * 1024 * 1024
DEFAULT_ROOT = Path(__file__).resolve().parents[2]


class DataSetupError(RuntimeError):
    """An asset could not be acquired or failed integrity validation."""


def _read_bounded(stream, limit: int, description: str) -> bytes:
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise DataSetupError(f"{description} exceeds the {limit}-byte safety limit")
    return data


def _verify(data: bytes, expected: str, description: str) -> str:
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise DataSetupError(
            f"{description} SHA256 mismatch: expected {expected}, got {actual}; "
            "no asset was installed"
        )
    return actual


def _atomic_write(path: Path, data: bytes) -> None:
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".kcs-", delete=False) as out:
            temporary = out.name
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def setup_kcs(
    root: Path = DEFAULT_ROOT,
    *,
    archive: Path | None = None,
    source_url: str = KCS_SOURCE_URL,
    archive_sha256: str = KCS_ARCHIVE_SHA256,
    member_sha256: str = KCS_MEMBER_SHA256,
) -> dict[str, object]:
    """Install one verified member and its provenance under ``root``.

    Python callers may supply expected hashes for isolated mock-archive tests;
    the public CLI always uses the production pins. An invalid archive/member
    never changes an existing installation. No other ZIP members are extracted.
    """
    import io

    try:
        if archive is None:
            if not source_url.startswith("https://"):
                raise DataSetupError("KCS download URL must use HTTPS")
            with urlopen(source_url, timeout=30) as response:
                if not response.geturl().startswith("https://"):
                    raise DataSetupError("KCS download redirected away from HTTPS")
                archive_bytes = _read_bounded(response, MAX_ARCHIVE_BYTES, "KCS archive")
        else:
            with Path(archive).open("rb") as stream:
                archive_bytes = _read_bounded(stream, MAX_ARCHIVE_BYTES, "KCS archive")

        verified_archive = _verify(archive_bytes, archive_sha256, "KCS archive")
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as bundle:
            matches = [info for info in bundle.infolist() if info.filename == KCS_MEMBER]
            if len(matches) != 1:
                raise DataSetupError("KCS archive must contain exactly one root-level kcs.fix")
            info = matches[0]
            if info.file_size > MAX_MEMBER_BYTES:
                raise DataSetupError("KCS member exceeds the extraction safety limit")
            with bundle.open(info) as stream:
                member_bytes = _read_bounded(stream, MAX_MEMBER_BYTES, "KCS member")
        verified_member = _verify(member_bytes, member_sha256, "KCS member kcs.fix")
    except DataSetupError:
        raise
    except (OSError, URLError, zipfile.BadZipFile, RuntimeError, EOFError) as exc:
        raise DataSetupError(f"KCS acquisition failed; no asset was installed: {exc}") from exc

    manifest: dict[str, object] = {
        "source": source_url,
        "acquired_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": "local_archive" if archive is not None else "https_download",
        "archive_sha256": verified_archive,
        "archive_bytes": len(archive_bytes),
        "member": KCS_MEMBER,
        "member_sha256": verified_member,
        "member_bytes": len(member_bytes),
        "licence": "unstated; not redistributed",
        "redistributed": False,
    }
    destination = Path(root) / "data" / "raw" / "kcs"
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    try:
        destination.mkdir(parents=True, exist_ok=True)
        _atomic_write(destination / KCS_MEMBER, member_bytes)
        _atomic_write(destination / "SETUP-MANIFEST.json", manifest_bytes)
    except OSError as exc:
        raise DataSetupError(f"Could not install KCS asset/provenance in {destination}: {exc}") from exc
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Use a local ZIP instead of downloading")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Checkout root to install into")
    args = parser.parse_args(argv)
    try:
        manifest = setup_kcs(args.root, archive=args.archive)
    except DataSetupError as exc:
        print(f"data setup error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"installed": str(args.root / "data/raw/kcs/kcs.fix"), **manifest}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
