"""Shared conventions: units, identifiers, ship slot addresses (BBBRRTT) and yard addresses.

Owner: plan task 0.9 (frozen; changes go through X2). Everything here is real code, not a stub.

Units (SI everywhere inside the code; convert only at the edges, e.g. the UI):
    mass            kg          (never tonnes; VGM 24 000 kg, not 24 t)
    length          m           (container dimensions too: a 40' box is 12.192 m)
    time            s
    force           N           (shear force)
    moment          N·m         (bending moment; mass moments like Σ w·z are kg·m and named *_kgm)
    angle           rad         (heel, trim angle); trim and list as *lengths* are m (trim = draught aft − fore)
    volume          m³,  density kg/m³,  temperature °C (reefer set points only)
    Field names carry the unit as a suffix when it isn't obvious: ``mass_kg``, ``lcg_m``, ``bm_nm``.

Coordinate frame (vessel):
    x  longitudinal, metres forward of the aft perpendicular (AP)      -> LCG, LCB, LCF, stations
    y  transverse, metres from the centreline, **positive to port**     -> TCG (list to port is positive)
    z  vertical, metres above the baseline (keel)                       -> VCG/KG, KB, KM
    APPROXIMATION/assumption: positive-to-port follows the Larsen–Pacino files' sign; if 2.1 finds otherwise it
    changes this line and the sign in one X2 change.

Ship slot address ``BBBRRTT`` (BAPLIE / ISO 9711 practice, docs/domain.md "Slot addressing"):
    BBB  bay. Odd = a 20' position. Even = a 40' (or 45') box spanning the two odd bays either side (bay 02 =
         01 + 03). A 40' box is *addressed* by the even bay; it *occupies* two physical cells (one per odd bay).
    RR   row. 00 = centre row (only when the row count is odd); odd rows to starboard (01, 03, ...), even rows
         to port (02, 04, ...), numbered outward from the centreline.
    TT   tier. Below deck (in the hold) 02, 04, 06, ... upward; on deck from 82 upward (some ships start at 80).
         ``tier >= ON_DECK_TIER_MIN`` (80) means on deck.
    The physical *cell* (where weight sits) is always an odd-bay address; the *slot* a box is reported in is its
    BAPLIE address (even bay for 40'/45'). Helpers below convert between them.

Yard address ``BLOCK-BBB-RR-TT`` (our own convention; no public standard):
    BLOCK  block id, 1–4 upper-case letters/digits (e.g. ``A1``, ``R02``).
    BBB    ground-bay position along the block, 1-based, consecutive integers (each is one 20' ground slot,
           matching params/ports: "bays count twenty-foot longitudinal positions").
    RR     row across the block, 1-based.
    TT     tier, 1 = on the ground, counting up.
    A 40'/45' yard stack occupies ground bays ``bay`` and ``bay + 1`` and is addressed by ``bay`` (the lower
    number). Stacks are single-length: 20s and 40s are never mixed in one yard stack (common terminal practice;
    assumption, see docs/architecture.md "Conventions").

Other identifiers:
    container id  ISO 6346: 4 letters (owner code + category U/J/Z) + 6 digits + check digit. Synthetic data uses
                  owner codes from ``SYNTHETIC_OWNER_CODES`` (assumption: not registered BIC codes; unverified).
    port          UN/LOCODE, 5 characters (``THLCH``, ``SGSIN``).
    rule id       dotted lower-case catalogue id, e.g. ``imdg.segregation.7_4_3`` (owned by plan 1.2).
    vessel id     lower-case slug, e.g. ``feeder_800``; instance id: any slug, unique per dataset.
    call index    0-based position of a port call in ``Rotation.calls``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache, wraps
from typing import Callable, Final, Literal, TypeVar

_StringResult = TypeVar("_StringResult")


def _memoize_string(function: Callable[..., _StringResult]) -> Callable[..., _StringResult]:
    """Cache exact built-in strings only, retaining original behavior for every other input.

    The four decorated functions return only frozen addresses, tuples, or booleans.
    ``lru_cache`` retains successful results, never exceptions; the bound limits the
    lifetime of addresses from unrelated instances. Normalize keyword calls to the
    same complete-string key without changing the public signature or call errors.
    String subclasses bypass caching because their hashing and equality may differ
    from string content, or they may be unhashable.
    """
    cached = lru_cache(maxsize=65536)(function)
    parameter = function.__code__.co_varnames[0]

    @wraps(function)
    def wrapper(*args, **kwargs):
        if len(args) == 1 and not kwargs:
            value = args[0]
        elif not args and len(kwargs) == 1 and parameter in kwargs:
            value = kwargs[parameter]
        else:
            return function(*args, **kwargs)
        return cached(value) if type(value) is str else function(value)

    wrapper.cache_info = cached.cache_info
    wrapper.cache_clear = cached.cache_clear
    wrapper.cache_parameters = cached.cache_parameters
    return wrapper


# D33: resolve registry lookups only. Imported models keep their stored labels,
# so loading never alters the content identities of archived instances.
VESSEL_ID_ALIASES: Final = {"feeder_1000": "feeder_800", "realistic_1000": "realistic_800"}
LEGACY_FLEET_PARAMETER_PINS: Final = {
    "feeder_800": (
        "6a26959e2bbbacbdf6dc052bd6e110cccf7835a524ea45b4becffdea6a9d4b94",
        "3c7d97c20caf53fe5fbf903d72d4193be1ae017db0bf0f655df7dfacdb8a5f5c",
    ),
}


def canonical_vessel_id(vessel_id: str) -> str:
    """Resolve a fleet/profile alias, preserving any slice or condition suffix."""
    root, separator, suffix = vessel_id.partition(".")
    return VESSEL_ID_ALIASES.get(root, root) + separator + suffix


# --------------------------------------------------------------------------------------------------- physics
G_M_S2: Final = 9.80665  # standard gravity (ISO 80000-3)
SEAWATER_DENSITY_KG_M3: Final = 1025.0  # assumption: standard design sea water; per-port density may override
FRESHWATER_DENSITY_KG_M3: Final = 1000.0
FOOT_M: Final = 0.3048

# ISO 668 external dimensions, m (length by nominal size; height by code)
CONTAINER_LENGTH_M: Final = {20: 6.058, 40: 12.192, 45: 13.716}
CONTAINER_WIDTH_M: Final = 2.438
CONTAINER_HEIGHT_STD_M: Final = 2.591  # 8'6"
CONTAINER_HEIGHT_HC_M: Final = 2.896  # 9'6" high cube

# ---------------------------------------------------------------------------------------------- ship slots
ON_DECK_TIER_MIN: Final = 80
_SLOT_RE = re.compile(r"^(\d{3})(\d{2})(\d{2})$")


@dataclass(frozen=True, order=True, slots=True)
class SlotAddress:
    """A parsed ``BBBRRTT`` ship slot address."""

    bay: int
    row: int
    tier: int

    def __post_init__(self) -> None:
        if not (0 < self.bay <= 999 and 0 <= self.row <= 99 and 0 < self.tier <= 99):
            raise ValueError(f"slot out of range: bay={self.bay} row={self.row} tier={self.tier}")
        if self.tier % 2:
            raise ValueError(f"tiers are even numbers (02, 04, ... / 80, 82, ...): got {self.tier}")

    @property
    def on_deck(self) -> bool:
        return self.tier >= ON_DECK_TIER_MIN

    @property
    def is_20ft_bay(self) -> bool:
        return self.bay % 2 == 1

    def __str__(self) -> str:
        return format_slot(self.bay, self.row, self.tier)


def format_slot(bay: int, row: int, tier: int) -> str:
    """``format_slot(2, 1, 82) == "0020182"``. Validates ranges."""
    SlotAddress(bay, row, tier)
    return f"{bay:03d}{row:02d}{tier:02d}"


@_memoize_string
def parse_slot(slot_id: str) -> SlotAddress:
    """Parse ``"0020182"`` → ``SlotAddress(bay=2, row=1, tier=82)``. Raises ValueError on a malformed id."""
    m = _SLOT_RE.match(slot_id)
    if not m:
        raise ValueError(f"not a BBBRRTT slot id: {slot_id!r}")
    return SlotAddress(int(m[1]), int(m[2]), int(m[3]))


def is_on_deck(tier: int) -> bool:
    return tier >= ON_DECK_TIER_MIN


def forty_bay(odd_bay_fore_or_aft: int) -> tuple[int, int]:
    """The two even (40') bays an odd bay can be half of: bay 03 → (02, 04). For bay 01 the first candidate is 0,
    which is not a bay; the vessel's cell list decides which candidates exist."""
    if odd_bay_fore_or_aft % 2 == 0:
        raise ValueError(f"expected an odd (20') bay, got {odd_bay_fore_or_aft}")
    return odd_bay_fore_or_aft - 1, odd_bay_fore_or_aft + 1


def odd_bays_of(bay: int) -> tuple[int, ...]:
    """Physical 20' bays a box addressed at ``bay`` occupies: odd → (bay,), even → (bay-1, bay+1)."""
    if bay % 2:
        return (bay,)
    if bay < 2:
        raise ValueError(f"even bay must be >= 2, got {bay}")
    return (bay - 1, bay + 1)


@_memoize_string
def cells_of_slot(slot_id: str) -> tuple[str, ...]:
    """Physical cell ids (odd-bay ``BBBRRTT``) occupied by a box reported at ``slot_id``.
    ``cells_of_slot("0020182") == ("0010182", "0030182")``; a 20' slot maps to itself."""
    s = parse_slot(slot_id)
    return tuple(format_slot(b, s.row, s.tier) for b in odd_bays_of(s.bay))


def row_side(row: int) -> Literal["centre", "starboard", "port"]:
    if row == 0:
        return "centre"
    return "starboard" if row % 2 else "port"


def rows_port_to_starboard(rows: list[int]) -> list[int]:
    """Order rows physically across the ship, port side first: [.., 04, 02, 00, 01, 03, ..]."""
    port = sorted((r for r in rows if r and r % 2 == 0), reverse=True)
    stbd = sorted(r for r in rows if r % 2 == 1)
    return port + ([0] if 0 in rows else []) + stbd


def stack_id(bay: int, row: int, on_deck: bool) -> str:
    """Stack id = odd bay (3) + row (2) + ``D`` (on deck) or ``H`` (hold), e.g. ``"00101D"``. A stack is one
    vertical column of 20' cells; 40' boxes sit across two stacks (bays b and b+2) with the same row and deck."""
    if bay % 2 == 0:
        raise ValueError("stacks are identified by their odd (20') bay")
    return f"{bay:03d}{row:02d}{'D' if on_deck else 'H'}"


# ---------------------------------------------------------------------------------------------- yard
_YARD_RE = re.compile(r"^([A-Z0-9]{1,4})-(\d{3})-(\d{2})-(\d{2})$")


@dataclass(frozen=True, order=True, slots=True)
class YardAddress:
    """A position in the yard: ``BLOCK-BBB-RR-TT`` (bay, row, tier all 1-based; tier 1 = ground)."""

    block: str
    bay: int
    row: int
    tier: int

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Z0-9]{1,4}", self.block):
            raise ValueError(f"bad yard block id: {self.block!r}")
        if not (1 <= self.bay <= 999 and 1 <= self.row <= 99 and 1 <= self.tier <= 99):
            raise ValueError(f"yard position out of range: {self}")

    @property
    def ground(self) -> tuple[str, int, int]:
        """The (block, bay, row) ground slot = the yard stack this position belongs to."""
        return (self.block, self.bay, self.row)

    def __str__(self) -> str:
        return f"{self.block}-{self.bay:03d}-{self.row:02d}-{self.tier:02d}"


def format_yard(block: str, bay: int, row: int, tier: int) -> str:
    return str(YardAddress(block, bay, row, tier))


@_memoize_string
def parse_yard(address: str) -> YardAddress:
    """Parse ``"A1-012-03-02"``. Raises ValueError on a malformed address."""
    m = _YARD_RE.match(address)
    if not m:
        raise ValueError(f"not a BLOCK-BBB-RR-TT yard address: {address!r}")
    return YardAddress(m[1], int(m[2]), int(m[3]), int(m[4]))


def yard_stack_key(block: str, bay: int, row: int) -> str:
    """Key of a yard stack (ground slot): ``"A1-012-03"``."""
    YardAddress(block, bay, row, 1)
    return f"{block}-{bay:03d}-{row:02d}"


# ---------------------------------------------------------------------------------------------- ids
SYNTHETIC_OWNER_CODES: Final = ("QZXU", "QZYU", "QZWU")  # assumption: unregistered BIC prefixes (unverified)
_ISO6346_RE = re.compile(r"^[A-Z]{3}[UJZ]\d{7}$")
_LOCODE_RE = re.compile(r"^[A-Z]{2}[A-Z2-9]{3}$")
RULE_ID_RE: Final = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")


def _iso6346_letter_value(c: str) -> int:
    # A=10, skipping multiples of 11 (11, 22, 33): B=12 .. K=21, L=23 .. U=32, V=34 .. Z=38
    v = 10 + (ord(c) - ord("A"))
    return v + (v - 1) // 10 if v > 10 else v


def iso6346_check_digit(first10: str) -> int:
    """Check digit of an ISO 6346 code given its first 10 characters (owner+category+serial)."""
    if not re.fullmatch(r"[A-Z]{4}\d{6}", first10):
        raise ValueError(f"need 4 letters + 6 digits, got {first10!r}")
    total = sum((int(c) if c.isdigit() else _iso6346_letter_value(c)) * 2**i for i, c in enumerate(first10))
    return total % 11 % 10


@_memoize_string
def is_valid_container_id(cid: str) -> bool:
    return bool(_ISO6346_RE.match(cid)) and iso6346_check_digit(cid[:10]) == int(cid[10])


def make_container_id(owner: str, serial: int) -> str:
    """``make_container_id("QZXU", 1) -> "QZXU000001<d>"`` with the ISO 6346 check digit."""
    body = f"{owner}{serial:06d}"
    return body + str(iso6346_check_digit(body))


def is_valid_locode(code: str) -> bool:
    return bool(_LOCODE_RE.match(code))


def is_valid_rule_id(rule_id: str) -> bool:
    return bool(RULE_ID_RE.match(rule_id))
