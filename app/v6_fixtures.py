"""Fixtures keep changing while the demo is built. Chapters read them through these, so a new shape costs a panel, not the chapter."""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


class Withheld(ValueError):
    """A stored plan or a package paper that hasn't cleared its gate. The message is the plain reason the page shows."""


def optional(load, default=None):
    """load(), or default when the fixture is missing or isn't the shape this reader expects."""
    try:
        return load()
    except Exception as error:  # noqa: BLE001 - any failure here is one absent panel, however the fixture broke
        log.info("v6 panel dropped: %s: %s", type(error).__name__, error)
        return default


def read_optional(path, default=None):
    return optional(lambda: json.loads(Path(path).read_text()), default)


def dig(data, *keys, default=None):
    """data[k1][k2]..., or default at the first step that isn't there."""
    for key in keys:
        if not isinstance(data, dict) or key not in data:
            return default
        data = data[key]
    return default if data is None else data


def dicts(value) -> list[dict]:
    """The dict rows of a list; anything else is no rows."""
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None
