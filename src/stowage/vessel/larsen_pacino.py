"""Read the public Larsen–Pacino (2020) benchmark as inert geometry data.

Source: Larsen & Pacino, “A heuristic and a benchmark for the stowage planning
problem”, CC0 GitHub copy, ``data/raw/larsen-pacino/NOTES.md``. A benchmark
bay is a 40-foot position: its two 20-foot slots become two physical cells.
One absent odd bay separates benchmark bays, preventing the model from accepting
a 40-foot slot across two different benchmark bays.
A stack limit is the weight supported by one column; the published limit for
40-foot boxes is divided equally over the two physical columns.

APPROXIMATION: section identifiers 1/2, 3/4, ... are interpreted as the deck
and hold portions of a hatch group within each benchmark bay. Cover masses
are unavailable and are represented by zero; inferred covers are for access
geometry only. Coordinates retain relative distances, translating x so the
AP proxy lies half a 40-foot container aft of the last bay. This proxy is not
a measured AP. Reefer values 0/1/2 are interpreted as plug counts, assigned
to the first/both physical cells. Standard ISO container heights expand each section's supplied
base VCG into tier floors; the source's height limit remains authoritative.

This is a geometry import, not a complete naval-architecture reconstruction.
There are no GZ curves, conning position, tank density/free-surface tables or
verified strength-unit conversions. Hull, bridge and tanks therefore remain
absent; physics checkers must report missing vessel data. HydroPoints, tank
coverage, buoyancy, strength and constant-weight records are parsed for syntax
but not converted into invented physics. The original file path and digest
preserve access to those records. Neither 45-foot capability nor DG clearance
is supplied. The 20-foot weight limit is interpreted per physical column (the source
container records distinguish slots 1/2); this interpretation remains unverified.
APPROXIMATION: mixed-length stacks use the smaller of the two homogeneous
per-column limits because the benchmark provides no separate mixed limit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import math
from pathlib import Path
import re

from stowage.conventions import CONTAINER_HEIGHT_STD_M, CONTAINER_LENGTH_M, format_slot, stack_id
from stowage.model import Vessel
from stowage.model.vessel import Cell, HatchCover, Stack


@dataclass
class _Section:
    bay: int
    stack: int
    x: float
    y: float
    on_deck: bool
    identifier: int
    height: float
    weight20: float
    weight40: float
    base: float
    cells: list[tuple[int, int]] = field(default_factory=list)


_FIELDS = {"Ship": 4, "HydroPoints": 4, "Tanks": 5, "BayCoverage": 2,
           "Bay": 7, "BuoyancyPoints": 1, "Stack": 2,
           "AboveDeck": 5, "BelowDeck": 5, "Cell": 2}


def _integer(value: float, *, minimum: int = 0) -> int:
    if not value.is_integer() or value < minimum:
        raise ValueError(f"expected integer >= {minimum}, got {value}")
    return int(value)


def _read(data: str) -> tuple[list[_Section], list[float]]:
    """Strict numeric state machine; source text is never evaluated or executed."""
    sections: list[_Section] = []
    bay_x: list[float] = []
    bays: set[int] = set()
    stack_indices: dict[int, set[int]] = {}
    dims: tuple[int, int, int] | None = None
    mode = ""
    bay = stack = None
    x = y = 0.0
    active: _Section | None = None
    single = False
    for lineno, raw in enumerate(data.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        try:
            if line.startswith("#"):
                mode = line.lstrip("# ").split(":", 1)[0]
                if mode not in _FIELDS:
                    raise ValueError(f"unknown section {mode!r}")
                single = False
                continue
            values = [float(token) for token in line.split()]
            if mode not in _FIELDS or len(values) != _FIELDS[mode] or not all(map(math.isfinite, values)):
                raise ValueError(f"invalid {mode or 'unheaded'} numeric record")
            if mode in {"Ship", "Bay", "Stack", "AboveDeck", "BelowDeck", "Tanks"}:
                if single:
                    raise ValueError(f"multiple records under {mode} header")
                single = True
            if mode == "Ship":
                if dims is not None:
                    raise ValueError("duplicate Ship header")
                dims = tuple(_integer(v, minimum=1) for v in values[:3])
                if dims[0] > 167 or dims[1] > 99 or dims[2] > 49:
                    raise ValueError("dimensions exceed BBBRRTT address range")
            elif mode == "Bay":
                if dims is None:
                    raise ValueError("Bay precedes Ship")
                bay = _integer(values[0])
                if bay >= dims[0] or bay in bays:
                    raise ValueError("duplicate/out-of-range bay")
                bays.add(bay)
                stack_indices[bay] = set()
                x = values[1]
                bay_x.append(x)
                stack = active = None
            elif mode == "Stack":
                if bay is None or dims is None:
                    raise ValueError("Stack precedes Bay")
                stack = _integer(values[0])
                if stack >= dims[1] or stack in stack_indices[bay]:
                    raise ValueError("duplicate/out-of-range stack")
                stack_indices[bay].add(stack)
                y = values[1]
                active = None
            elif mode in {"AboveDeck", "BelowDeck"}:
                if stack is None or bay is None:
                    raise ValueError("deck section precedes Stack")
                identifier = _integer(values[0], minimum=1)
                if any(s.bay == bay and s.stack == stack and s.on_deck == (mode == "AboveDeck") for s in sections):
                    raise ValueError("duplicate deck section")
                if min(values[1:4]) <= 0:
                    raise ValueError("stack height and weights must be positive")
                active = _Section(bay, stack, x, y, mode == "AboveDeck", identifier, *values[1:])
                sections.append(active)
            elif mode == "Cell":
                if active is None or dims is None:
                    raise ValueError("Cell precedes deck section")
                tier, reefer = map(_integer, values)
                if tier >= dims[2] or reefer not in (0, 1, 2) or tier in {t for t, _ in active.cells}:
                    raise ValueError("invalid or duplicate cell tier/reefer flag")
                active.cells.append((tier, reefer))
        except ValueError as exc:
            raise ValueError(f"Larsen–Pacino line {lineno}: {exc}") from exc
    if dims is None or len(bays) != dims[0] or any(len(s) != dims[1] for s in stack_indices.values()):
        raise ValueError("Larsen–Pacino incomplete ship/bay/stack records")
    if not sections or any(not s.cells for s in sections):
        raise ValueError("Larsen–Pacino empty deck section or vessel")
    return sections, bay_x


def load_larsen_pacino(path: str | Path) -> Vessel:
    """Load one benchmark ``vessel_*.txt``; reject malformed numeric input.

    Missing physics is explicit (``hull=None``, ``bridge=None``), so this vessel
    can exercise geometry/moves but cannot pass the full departure checker.
    ``nominal_teu`` is the imported physical-cell count, not a marketing figure.
    """
    source = Path(path)
    if source.stat().st_size > 20_000_000:
        raise ValueError("Larsen–Pacino vessel file exceeds 20 MB")
    raw = source.read_bytes()
    sections, positions = _read(raw.decode("utf-8"))
    # APPROXIMATION: the source origin is midship; AP is unavailable. Shift the
    # aftmost bay centre by half the nominal 40-foot length, retaining distances.
    shift = CONTAINER_LENGTH_M[40] / 2 - min(positions)
    ys = sorted({abs(s.y) for s in sections if s.y != 0})
    row_by_y = {v: i + 1 for i, v in enumerate(ys)}
    cells: list[Cell] = []
    stacks: list[Stack] = []
    covers: dict[str, dict] = {}
    for section in sections:
        row = 0 if section.y == 0 else 2 * row_by_y[abs(section.y)] - (section.y < 0)
        # APPROXIMATION: adjacent odd/even section identifiers denote one cover.
        cover_id = f"HC-LP-{section.bay:03d}-{(section.identifier + 1) // 2}"
        cover = covers.setdefault(cover_id, {"hold": [], "deck": [], "bays": [], "x": section.x + shift, "ys": [], "z": []})
        cover["ys"].append(section.y)
        cover["z"].append(section.base)
        source_tiers = sorted(t for t, _ in section.cells)
        if source_tiers != list(range(source_tiers[0], source_tiers[-1] + 1)):
            raise ValueError("Larsen–Pacino section has gaps in its tiers")
        reefers = dict(section.cells)
        for half in range(2):
            bay = 6 * section.bay + 1 + 2 * half
            sid = stack_id(bay, row, section.on_deck)
            ids: list[str] = []
            for level, source_tier in enumerate(source_tiers):
                tier = (82 if section.on_deck else 2) + 2 * level
                cid = format_slot(bay, row, tier)
                ids.append(cid)
                cells.append(Cell(id=cid, stack_id=sid, accepts_20=True, accepts_40=True,
                                  accepts_45=False, reefer_plug=reefers[source_tier] > half,
                                  lcg_m=section.x + shift + (0.5 - half) * CONTAINER_LENGTH_M[20],
                                  tcg_m=section.y, vcg_base_m=section.base + level * CONTAINER_HEIGHT_STD_M))
            # APPROXIMATION: the benchmark gives whole-40-foot stack limits;
            # distribute half the 40-foot limit to each physical support column.
            mass20, mass40 = section.weight20 * 1000, section.weight40 * 500
            stacks.append(Stack(id=sid, bay=bay, row=row, on_deck=section.on_deck,
                                cell_ids=ids, max_mass_kg=min(mass20, mass40),
                                max_mass_20_kg=mass20, max_mass_40_kg=mass40,
                                max_height_m=section.height, base_z_m=section.base,
                                hatch_cover_id=cover_id))
            cover["deck" if section.on_deck else "hold"].append(sid)
            cover["bays"].append(bay)
    hatch_covers = [HatchCover(id=key, bays=sorted(set(c["bays"])),
                              hold_stack_ids=c["hold"], deck_stack_ids=c["deck"], mass_kg=0,
                              lcg_m=c["x"], tcg_m=sum(c["ys"]) / len(c["ys"]), vcg_m=max(c["z"]))
                    for key, c in sorted(covers.items())]
    slug = re.sub(r"[^a-z0-9_.-]+", "-", source.stem.lower()).strip("-.") or "vessel"
    return Vessel(id=f"larsen_pacino.{slug}", name=f"Larsen–Pacino {source.stem}",
                  nominal_teu=len(cells), cells=cells, stacks=stacks, hatch_covers=hatch_covers,
                  sources=[str(source), f"sha256:{sha256(raw).hexdigest()}",
                           "Larsen & Pacino (2020); CC0 GitHub benchmark",
                           "APPROXIMATION: inferred hatch groups, zero cover mass, AP proxy, ISO tier pitch, equal 40-foot weight split, reefer plug counts",
                           "INCOMPLETE PHYSICS: hull, bridge, tanks, heat sources, strength units unavailable"])
