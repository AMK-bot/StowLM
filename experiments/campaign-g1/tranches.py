"""Stable, balanced prefixes of the immutable v2 allocation.

Counts are cumulative train targets per rung, not tranche increments. Each cell
(ship, generator, port, noise) consumes its archived specs in ascending seed
order. Round-robin cells keep their counts within one while a deterministic
axis-balancing cell order diversifies short prefixes. No spec is rewritten.
"""
from __future__ import annotations

from collections import defaultdict, deque
import hashlib
import json
from typing import Mapping

RUNGS = ("tiny", "small", "medium", "full")


def parse_train_counts(value: str) -> dict[str, int]:
    """Parse cumulative targets; omitted rungs explicitly receive zero."""
    counts = dict.fromkeys(RUNGS, 0)
    seen = set()
    if not value.strip():
        raise ValueError("train-per-rung must contain at least one rung=count")
    for item in value.split(","):
        pair = item.strip().split("=")
        if len(pair) != 2:
            raise ValueError(f"invalid train-per-rung item: {item!r}")
        rung, raw = (part.strip() for part in pair)
        if rung not in RUNGS or rung in seen:
            raise ValueError(f"unknown or duplicate train rung: {rung!r}")
        if not raw.isascii() or not raw.isdecimal():
            raise ValueError(f"train count must be a non-negative integer: {item!r}")
        counts[rung] = int(raw)
        seen.add(rung)
    return counts


def _stable(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _family(entry):
    return entry.get("test_family") or "in_distribution"


def _cell(entry):
    spec = entry["spec"]
    noise = spec.get("blocking_ratio") if spec["generator"] == "random_yard" else spec.get("noise")
    # Canonicalisation is intentionally unnecessary here: archived v2 identity
    # stays intact, and production calls resolve vessel aliases separately.
    return tuple(_stable(value) for value in (spec["vessel"], spec["generator"], spec["port"], noise))


def _cell_order(cells):
    """Greedy marginal balancing breaks deterministic ties with a hash.

    This changes ordering only, never allocation or physics. The round-robin
    below guarantees per-cell fairness; this order spreads early cells across
    axes instead of exhausting the lexicographically first ship or generator.
    """
    remaining = set(cells)
    marginals = [defaultdict(int) for _ in range(4)]
    ordered = []
    while remaining:
        def score(cell):
            counts = [marginals[index][value] for index, value in enumerate(cell)]
            return (sum(counts), max(counts), hashlib.sha256(_stable(cell).encode()).hexdigest(), cell)
        cell = min(remaining, key=score)
        ordered.append(cell)
        remaining.remove(cell)
        for index, value in enumerate(cell):
            marginals[index][value] += 1
    return ordered


def priority_entries(entries):
    """One stable priority order for a single rung/split/family stratum."""
    cells = defaultdict(list)
    for entry in entries:
        cells[_cell(entry)].append(entry)
    queues = {cell: deque(sorted(rows, key=lambda row: (int(row["spec"]["seed"]), _stable(row))))
              for cell, rows in cells.items()}
    order = _cell_order(cells)
    result = []
    while order:
        active = []
        for cell in order:
            result.append(queues[cell].popleft())
            if queues[cell]:
                active.append(cell)
        order = active
    return result


def _balanced_families(entries):
    families = defaultdict(list)
    for entry in entries:
        families[_family(entry)].append(entry)
    queues = {family: deque(priority_entries(rows)) for family, rows in families.items()}
    order = sorted(queues)
    result = []
    while order:
        active = []
        for family in order:
            result.append(queues[family].popleft())
            if queues[family]:
                active.append(family)
        order = active
    return result


def select_entries(release, traincounts: Mapping[str, int] | None,
                   diagnostic_eval_per_rung: int | None = None, diagnostic: bool = False):
    """Select a cumulative balanced allocation without changing trusted specs.

    ``None`` train counts selects all train specs. An explicit mapping defaults
    missing rungs to zero. Validation and test are each complete unless the
    diagnostic-only cap is supplied, in which case it applies to EACH split per
    rung (e.g. 1 means one val plus one test). Caps never create extra specs.
    """
    if diagnostic_eval_per_rung is not None:
        if not diagnostic:
            raise ValueError("diagnostic-eval-per-rung requires diagnostic mode")
        if type(diagnostic_eval_per_rung) is not int or diagnostic_eval_per_rung < 0:
            raise ValueError("diagnostic eval cap must be a non-negative integer")
    if traincounts is not None:
        if set(traincounts) - set(RUNGS):
            raise ValueError("unknown train rung")
        if any(type(value) is not int or value < 0 for value in traincounts.values()):
            raise ValueError("train targets must be non-negative integers")
    entries = release["entries"]
    if any(entry["spec"]["rung"] not in RUNGS or entry["split"] not in ("train", "val", "test")
           for entry in entries):
        raise ValueError("allocation has an unknown rung or split")
    grouped = defaultdict(list)
    for entry in entries:
        grouped[entry["spec"]["rung"], entry["split"]].append(entry)
    selected = []
    for rung in RUNGS:
        for split in ("train", "val", "test"):
            ordered = _balanced_families(grouped[rung, split])
            if split == "train":
                count = len(ordered) if traincounts is None else traincounts.get(rung, 0)
                if count > len(ordered):
                    raise ValueError(f"train target {rung}={count} exceeds v2 allocation {len(ordered)}")
            else:
                count = len(ordered) if diagnostic_eval_per_rung is None else diagnostic_eval_per_rung
            selected.extend(ordered[:count])
    return selected
