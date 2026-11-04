"""Strict, repair-free text permissions for the D179 layout proposer.

The grammar grants permissions, not reservations or per-block cargo quotas.
Syntax/contract rejection consumes a proposal in the runner; neither this module
nor its caller may insert missing rows, widen a permission set, or fix spelling.
Static geometry and full operational feasibility remain solver/checker questions.
"""
from __future__ import annotations

import re

from experiments.mvp.strategy.headroom.layout import BlockLayout, BlockModel, booking_category

HEADER = "LAYOUT 1"
ATOM = r"[A-Za-z0-9_.:-]+"
BLOCK = rf"{ATOM}/(?:hold|deck)"
ROW = re.compile(rf"({ATOM})/({ATOM}) = ({BLOCK}(?:,{BLOCK})*)", re.ASCII)


class LayoutError(ValueError):
    """An unmodified model response is outside the layout contract."""


def categories(instance):
    """Only new load-list bookings, using exactly headroom's D25 classes."""
    from stowage.rules.catalogue import rule_freeze_v1
    loads = set(instance.load_list)
    # The study uses the pinned legacy epoch even when called from an ambient
    # freeze-enabled session. E2 class overrides must match the solver's epoch.
    with rule_freeze_v1(enabled=False):
        return frozenset(booking_category(box, instance) for box in instance.containers if box.id in loads)


def validate_layout(layout, instance, model=None):
    """Check the permission contract without claiming physical feasibility.

    Empty sets are invalid, even if a caller intended an impossible proposal.
    Capacity-deficient permissions are allowed through to the common evaluator:
    bounded solver failure is unknown, not a proof of physical infeasibility.
    Returns the original object, without modifying its metadata or permissions.
    """
    model = model or BlockModel.from_instance(instance)
    wanted, got = categories(instance), set(layout.allowed)
    if got != wanted:
        raise LayoutError(f"category mismatch: missing={sorted(wanted - got)}, unknown={sorted(got - wanted)}")
    known = set(model.block_ids)
    for category, blocks in sorted(layout.allowed.items()):
        if not blocks:
            raise LayoutError(f"empty permission set for {category}")
        unknown = set(blocks) - known
        if unknown:
            raise LayoutError(f"unknown blocks for {category}: {sorted(unknown)}")
    return layout


def parse_layout(text, instance, model=None):
    """Parse the entire response, rejecting prose, fences, aliases and repairs.

    Exactly one terminal LF is optional. Internal blank lines, CRLF, tabs,
    leading/trailing spaces and duplicate rows/block names are invalid. Rows
    and block lists may be in any order; their exact permissions are preserved.
    """
    if not isinstance(text, str):
        raise LayoutError("layout response must be text")
    content = text[:-1] if text.endswith("\n") else text
    lines = content.split("\n")
    if not lines or lines[0] != HEADER:
        raise LayoutError(f"first line must be exactly {HEADER!r}")
    allowed = {}
    for number, line in enumerate(lines[1:], 2):
        match = ROW.fullmatch(line)
        if match is None:
            raise LayoutError(f"line {number}: expected POD/CLASS = block-id,block-id")
        pod, weight, values = match.groups()
        category = (pod, weight)
        if category in allowed:
            raise LayoutError(f"line {number}: duplicate category {category}")
        blocks = values.split(",")
        if len(blocks) != len(set(blocks)):
            raise LayoutError(f"line {number}: duplicate block")
        allowed[category] = frozenset(blocks)
    layout = BlockLayout(allowed, "text-proposal", {"format": "LAYOUT 1"})
    return validate_layout(layout, instance, model)


def format_layout(layout, instance=None):
    """Emit canonical sorted text, optionally validating against an instance."""
    if instance is not None:
        validate_layout(layout, instance)
    rows = [HEADER]
    for (pod, weight), blocks in sorted(layout.allowed.items()):
        line = f"{pod}/{weight} = {','.join(sorted(blocks))}"
        if not blocks or ROW.fullmatch(line) is None:
            raise LayoutError(f"layout cannot be serialized: {(pod, weight)}")
        rows.append(line)
    return "\n".join(rows) + "\n"
