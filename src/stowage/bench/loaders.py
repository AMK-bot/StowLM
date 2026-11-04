"""Read public block-relocation instances as inert text, without executing archives.

In BRP, boxes leave stacks in priority order; a relocation is an extra stack-to-
stack lift. Smaller numbers leave first and stacks here run from ground to top.
The Tanaka author catalog documents the integer file grammar:
https://sites.google.com/site/shunjitanaka/brp . See docs/domain.md for the
restricted/unrestricted distinction. Heights and the variant are supplied by the
caller: a filename's initial height is not a certified operational height.

The local Zhu archive has instances only, with no per-instance optimality
certificates or move sequences. The BF archive is CPMP (pre-marshalling), a
different objective, and is deliberately not accepted by this BRP loader.
Dataset rights remain unresolved; this module does not copy or redistribute data.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import re
from typing import Literal
from zipfile import ZipFile

Variant = Literal["restricted", "unrestricted"]
_MAX_MEMBER_BYTES = 1_000_000
_MAX_ARCHIVE_MEMBERS = 50_000
_INTEGER_LINE = re.compile(r"[0-9]+(?:\s+[0-9]+)*", re.ASCII)


@dataclass(frozen=True)
class BRPInstance:
    """Distinct priorities and bottom-to-top stacks, with explicit model choices."""

    name: str
    stacks: tuple[tuple[int, ...], ...]
    height_limit: int
    variant: Variant
    source: str = ""

    def __post_init__(self) -> None:
        if not self.name or self.variant not in ("restricted", "unrestricted"):
            raise ValueError("name and an explicit restricted/unrestricted variant are required")
        if type(self.height_limit) is not int or self.height_limit < 1:
            raise ValueError("height_limit must be a positive integer")
        if not self.stacks or any(len(stack) > self.height_limit for stack in self.stacks):
            raise ValueError("stacks must be nonempty as a collection and fit height_limit")
        values = [priority for stack in self.stacks for priority in stack]
        if any(type(priority) is not int or priority < 1 for priority in values):
            raise ValueError("priorities must be positive integers")
        if len(set(values)) != len(values):
            raise ValueError("this BRP loader requires distinct priorities")

    @property
    def container_count(self) -> int:
        return sum(map(len, self.stacks))

    @property
    def retrieval_order(self) -> tuple[int, ...]:
        return tuple(sorted(priority for stack in self.stacks for priority in stack))


def parse_brp_text(
    text: str, *, name: str, height_limit: int, variant: Variant, source: str = ""
) -> BRPInstance:
    """Parse the Tanaka/Zhu integer grammar; reject inconsistent or extra data.

    Format: ``stack_count container_count``, then exactly one line per stack,
    each beginning with its occupancy followed by bottom-to-top priorities.
    An empty stack is represented by the line ``0``. Blank lines are ignored.
    """
    if len(text) > _MAX_MEMBER_BYTES:
        raise ValueError("instance text exceeds safety size limit")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or any(_INTEGER_LINE.fullmatch(line) is None for line in lines):
        raise ValueError("BRP data must contain only nonnegative integer rows")
    rows = [tuple(map(int, line.split())) for line in lines]
    if len(rows[0]) != 2:
        raise ValueError("header must contain exactly stack and container counts")
    stack_count, container_count = rows[0]
    if stack_count < 1 or len(rows) != stack_count + 1:
        raise ValueError("stack count does not match the number of rows")
    stacks = []
    for row in rows[1:]:
        occupancy, *priorities = row
        if occupancy != len(priorities):
            raise ValueError("stack occupancy does not match its priority count")
        stacks.append(tuple(priorities))
    if sum(map(len, stacks)) != container_count:
        raise ValueError("container count does not match stack occupancies")
    return BRPInstance(name, tuple(stacks), height_limit, variant, source)


def iter_zhu_instances(
    archive: str | Path,
    *,
    height_limit: int | Callable[[str], int],
    variant: Variant,
) -> Iterator[BRPInstance]:
    """Yield sorted Zhu ZIP members without extracting anything to disk.

    A height callback receives the complete member name, allowing a documented
    class-dependent operational-height policy. No height is inferred silently.
    Files must lie under a ``initial_height-stacks-containers`` directory, and
    header counts and initial heights are cross-checked against that directory.
    """
    with ZipFile(archive) as zipped:
        infos = zipped.infolist()
        if len(infos) > _MAX_ARCHIVE_MEMBERS:
            raise ValueError("archive contains too many entries")
        names = [info.filename for info in infos]
        if len(set(names)) != len(names):
            raise ValueError("duplicate archive member names")
        for info in sorted(infos, key=lambda entry: entry.filename):
            if info.is_dir():
                continue
            member = PurePosixPath(info.filename)
            if member.is_absolute() or ".." in member.parts or "\\" in info.filename:
                raise ValueError("unsafe archive member name")
            if len(member.parts) != 2 or member.suffix != ".txt":
                raise ValueError("expected a Zhu class directory and .txt instance")
            match = re.fullmatch(r"([1-9][0-9]*)-([1-9][0-9]*)-([1-9][0-9]*)", member.parts[0])
            if match is None:
                raise ValueError("invalid Zhu class directory")
            if info.file_size > _MAX_MEMBER_BYTES or info.flag_bits & 1:
                raise ValueError("oversized or encrypted archive member")
            initial_height, stack_count, container_count = map(int, match.groups())
            limit = height_limit(info.filename) if callable(height_limit) else height_limit
            instance = parse_brp_text(
                zipped.read(info).decode("ascii"),
                name=info.filename,
                height_limit=limit,
                variant=variant,
                source=f"{Path(archive).name}:{info.filename}",
            )
            if len(instance.stacks) != stack_count or instance.container_count != container_count:
                raise ValueError("Zhu directory class disagrees with instance header")
            if max(map(len, instance.stacks)) > initial_height:
                raise ValueError("Zhu instance exceeds its class initial height")
            yield instance
