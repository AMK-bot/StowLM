"""Verify public BRP source identity by complete canonical stack comparison.

Bacci's author page documents ``w h n`` headers, whereas the Tanaka copy uses
``w n``. Whitespace and that format difference preclude byte equality; equality
here means identical ordered, bottom-to-top stacks for every named instance.
No archive member is extracted or executed.
"""
from hashlib import sha256
import json
from pathlib import Path
from zipfile import ZipFile

from stowage.bench.loaders import iter_zhu_instances, parse_brp_text


def verify_zhu_source_identity(local_archive: str | Path, source_archive: str | Path) -> dict:
    """Reject missing, duplicate, extra, malformed, or content-different instances."""
    canonical = sha256()
    count = 0
    with ZipFile(source_archive) as source:
        infos = source.infolist()
        if len(infos) > 50_000 or len({i.filename for i in infos}) != len(infos):
            raise ValueError("invalid source archive member count or duplicate names")
        members = {i.filename: i for i in infos if not i.is_dir()}
        seen = set()
        for local in iter_zhu_instances(local_archive,
            height_limit=lambda name: int(name.split('-')[0]), variant="restricted"):
            name = "data" + local.name.replace("/", "-")
            if name not in members:
                raise ValueError(f"missing author source instance: {name}")
            info = members[name]
            if info.file_size > 1_000_000 or info.flag_bits & 1:
                raise ValueError("oversized or encrypted source member")
            rows = source.read(info).decode("ascii").splitlines()
            header = rows[0].split() if rows else []
            if len(header) != 3 or any(not x.isascii() or not x.isdecimal() for x in header):
                raise ValueError(f"invalid author source header: {name}")
            stacks, height, boxes = map(int, header)
            expected_height = int(local.name.split('-')[0])
            if (stacks, height, boxes) != (len(local.stacks), expected_height, local.container_count):
                raise ValueError(f"author source dimensions differ: {name}")
            text = f"{stacks} {boxes}\n" + "\n".join(rows[1:])
            original = parse_brp_text(text, name=name, height_limit=height, variant="restricted")
            if local.stacks != original.stacks:
                raise ValueError(f"author source stack contents differ: {name}")
            canonical.update((json.dumps([local.name, local.stacks], separators=(",", ":")) + "\n").encode("ascii"))
            seen.add(name)
            count += 1
        if seen != set(members):
            raise ValueError("author source has unmatched extra instances")
    if not count:
        raise ValueError("empty benchmark source comparison")
    return {
        "compared_instances": count, "mismatches": [],
        "comparison": "all ordered bottom-to-top stack priorities and header dimensions",
        "canonical_sha256": canonical.hexdigest(),
        "local_archive_sha256": sha256(Path(local_archive).read_bytes()).hexdigest(),
        "source_archive_sha256": sha256(Path(source_archive).read_bytes()).hexdigest(),
    }
