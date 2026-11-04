"""Separate a hazard division from a class-1 compatibility-group suffix.

IMDG 42-24 DGL column 3 records e.g. ``1.4G``; the sampled DGL stores
``hazard_class='1.4'`` and ``compatibility_group='G'`` separately. Compare
``division`` with that hazard class. Keep ``qualified_label`` for Document of
Compliance permissions and explosives compatibility: ``1.4S`` never grants
permission for ``1.4G``. Parsing a division does not assess class-1 arrangements.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


_CLASS_ONE = re.compile(r'(1\.[1-6])([ABCDEFGHJKLNS])?')
_OTHER_DIVISIONS = frozenset({
    '2.1', '2.2', '2.3', '3', '4.1', '4.2', '4.3', '5.1', '5.2',
    '6.1', '6.2', '7', '8', '9',
})


@dataclass(frozen=True)
class HazardLabel:
    """A declared label, with no inferred compatibility group or permission."""

    division: str
    compatibility_group: str | None
    qualified_label: str


def parse_hazard_label(label: str | None) -> HazardLabel | None:
    """Return label components, or None for missing/unrecognised declarations.

    A bare class-1 division remains unqualified: no group is inferred from the
    UN number or from a vessel's permissions. Whitespace and case errors are not
    repaired, so an invalid declaration cannot acquire a known hazard class.
    """
    if not isinstance(label, str):
        return None
    match = _CLASS_ONE.fullmatch(label)
    if match is not None:
        return HazardLabel(match[1], match[2], label)
    if label in _OTHER_DIVISIONS:
        return HazardLabel(label, None, label)
    return None


def hazard_division(label: str | None) -> str | None:
    """Access the declared hazard class/division without a class-1 group suffix."""
    parsed = parse_hazard_label(label)
    return parsed.division if parsed is not None else None
