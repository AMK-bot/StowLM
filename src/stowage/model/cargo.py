"""Container and its cargo attributes (plan 1.1; used by 2.4, 2.5, 3.8, 4.x).

Expert rules covered by fields here: E1/E2/E7 (weight order: ``gross_mass_kg``, ``weight_class``), E6 (heavy under
deck: ``gross_mass_kg`` against the catalogue threshold), E8 (``special_stowage``), E9 (``oog``), E11 (``dg``).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from stowage.model.base import ContainerId, Kg, Locode, StowModel
from stowage.model.provenance import Provenance


class SpecialStowage(StrEnum):
    """Per-box booking requests (E8). Binding (hard) until expert Q12 says otherwise."""

    UNDER_DECK = "under_deck"
    ON_DECK = "on_deck"
    AWAY_FROM_HEAT = "away_from_heat"  # distance rule + heat sources: catalogue entry + Vessel.heat_sources
    TOP_STOWAGE = "top_stowage"  # assumption (Q12): top tier of its stack at departure


class PackingForm(StrEnum):
    """Shipment packing labels used by synthetic SOLAS II-2/19 DoC permissions.

    The singular labels match params/fleet dg_document permissions/prohibitions.
    IBC describes an intermediate bulk container; it does not itself establish
    that the enclosing freight container is closed. No permission is inferred
    from ISO type, packing form or absence of a prohibition.
    """

    PACKAGED_GOODS_IN_CLOSED_FREIGHT_CONTAINERS = "packaged_goods_in_closed_freight_containers"
    OPEN_FREIGHT_CONTAINER = "open_freight_container"
    IBC = "ibc"
    PORTABLE_TANK = "portable_tank"
    TANK_CONTAINER = "tank_container"
    BULK = "bulk"


class DangerousGoods(StowModel):
    """IMDG 3.2 Dangerous Goods List attributes for one box (one UN entry; mixed loads: list the governing entry
    first and others in ``additional``). Data source: plan 1.3 (``stowage.rules.imdg``).

    Fields:
        un_number: 4-digit UN number as a string, e.g. ``"1263"``.
        imdg_class: class or division as written in 3.2, e.g. ``"3"``, ``"2.1"``, ``"5.1"``, ``"1.4S"``.
        subsidiary_risks: subsidiary hazard classes (column 4), e.g. ``["8"]``.
        packing_group: ``"I"``, ``"II"``, ``"III"`` or None (classes without PG).
        stowage_category: ``"A"``–``"E"`` (classes other than 1) or ``"01"``–``"05"`` (class 1), column 16a.
        stowage_codes: SW codes from column 16a, e.g. ``["SW1", "SW2"]``.
        segregation_codes: SG codes from column 16b, e.g. ``["SG35"]``; segregation groups ("acids", ...) too.
        handling_codes: H codes from column 16a.
        marine_pollutant: True if P/MP.
        limited_quantity: True if shipped as LQ (most stowage/segregation then doesn't apply; 1.2 decides).
        flashpoint_c: °C, class 3 only, optional.
        additional: further UN entries in the same box (same fields, ``additional`` empty).

    Shipment facts (schema 1.2): all default to None (unknown, never permission).
    Packing and CTU classification are declared facts, not derived from ISO type.
    ``special_provisions`` lists the shipment's actual numeric SP identifiers;
    None means unknown, [] means explicitly no applicable SPs. SW19 also needs
    the declared ``short_international_voyage`` classification. SW23 may need
    ``bulk_container_type`` (BK1/BK2/BK3); None is not evidence of a non-BK3 CTU.

    ``protected_from_heat`` (SW1), ``clear_living_quarters`` (SW2),
    ``ventilation_compliant`` (SW6 under deck), ``shaded`` / ``air_circulation``
    (SW11), ``dry`` (H1) and ``handling_compliant`` (H5) are externally assessed
    conditions for the intended handling/stowage context. They are not planner
    assertions or blanket clearances for every location; the checker must ensure
    the assessment applies to the actual placement. ``subsidiary_hazards_verified``
    records resolution of conditional DGL hazards, not absence of subsidiary risk.

    ``verified_provisions`` and ``verified_segregation_codes`` record identifiers
    cleared by an external verifier, with evidence in ``shipment_provenance``.
    In particular IMDG:7.1.4.5 requires qualified external radiological evaluation;
    no transport-index/dose or other radioactive clearance is inferred here.
    Class-1 arrangements/compatibility remain unsupported by the current checker;
    no flag here clears its explosives guard. Empty verification lists clear
    nothing. For mixed loads, record entry-specific facts on each additional DG
    entry too; shared CTU facts must agree (checker responsibility). Consumers
    migrating from rule_overrides must handle None explicitly rather than treating
    model_dump() key presence as evidence that a fact is known.
    """

    un_number: str = Field(pattern=r"^\d{4}$")
    imdg_class: str
    subsidiary_risks: list[str] = Field(default_factory=list)
    packing_group: Literal["I", "II", "III"] | None = None
    stowage_category: str
    stowage_codes: list[str] = Field(default_factory=list)
    segregation_codes: list[str] = Field(default_factory=list)
    handling_codes: list[str] = Field(default_factory=list)
    marine_pollutant: bool = False
    limited_quantity: bool = False
    flashpoint_c: float | None = None
    additional: list[DangerousGoods] = Field(default_factory=list)
    packing_form: PackingForm | None = None
    ctu_closed: StrictBool | None = None
    protected_from_heat: StrictBool | None = None
    clear_living_quarters: StrictBool | None = None
    ventilation_compliant: StrictBool | None = None
    shaded: StrictBool | None = None
    air_circulation: StrictBool | None = None
    dry: StrictBool | None = None
    handling_compliant: StrictBool | None = None
    subsidiary_hazards_verified: StrictBool | None = None
    special_provisions: list[str] | None = None
    short_international_voyage: StrictBool | None = None
    bulk_container_type: Literal["BK1", "BK2", "BK3"] | None = None
    verified_provisions: list[str] | None = None
    verified_segregation_codes: list[str] | None = None
    shipment_provenance: Provenance | None = None

    @field_validator("special_provisions", "verified_provisions", "verified_segregation_codes")
    @classmethod
    def canonical_shipment_codes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if any(not code or code != code.strip() for code in value):
            raise ValueError("shipment provision identifiers must be non-empty without surrounding whitespace")
        return sorted(set(value))

    @field_validator("special_provisions")
    @classmethod
    def numeric_special_provisions(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and any(not code.isascii() or not code.isdecimal() for code in value):
            raise ValueError("special_provisions must contain numeric SP identifiers, e.g. '376'")
        return value

    @field_validator("subsidiary_risks", "stowage_codes", "segregation_codes", "handling_codes")
    @classmethod
    def canonical_codes(cls, value: list[str]) -> list[str]:
        return sorted(set(value))


class OutOfGauge(StowModel):
    """Out-of-gauge dimensions (E9): how far the cargo sticks out beyond the ISO box envelope, in metres (>= 0).
    D61/C4 (expert Q13, 28 Oct 2026) permits on-deck or under-deck stowage wherever physically possible.
    Over-height kills slot(s) directly above; over-width/length kills neighbouring row/bay slots, with cell-guide
    constraints in celled holds. Killed slots must remain empty. Catalogue entries define these rules; enforcement
    belongs to the checker and move engine. Killed-slot geometry is computed from these dimensions and the vessel,
    not stored here. Additional numeric clearance remains provisional; special-stowage requests create no killed slots.
    """

    over_height_m: float = Field(default=0.0, ge=0.0)
    over_width_port_m: float = Field(default=0.0, ge=0.0)
    over_width_starboard_m: float = Field(default=0.0, ge=0.0)
    over_length_fore_m: float = Field(default=0.0, ge=0.0)
    over_length_aft_m: float = Field(default=0.0, ge=0.0)


class Container(StowModel):
    """One box. Immutable facts about the box and its booking; *where* it is lives in YardState / ShipState.

    Fields:
        id: ISO 6346 container id (``conventions.make_container_id``). Unique within an Instance.
        iso_type: ISO 6346 size-type code, e.g. ``"22G1"`` (20' std), ``"42G1"`` (40'), ``"45G1"`` (40' HC),
            ``"L5G1"`` (45' HC), ``"45R1"`` (40' HC reefer). Must agree with the derived fields below (1.1 validates).
        length_ft: 20, 40 or 45 (nominal). 45' boxes use 40' slots where the vessel cell allows (``accepts_45``).
        height_m: external height, m (2.591 standard, 2.896 high cube; ``conventions.CONTAINER_HEIGHT_*_M``).
        tare_kg: empty mass, kg (part of ``gross_mass_kg``; informational).
        gross_mass_kg: verified gross mass (SOLAS VI/2 VGM), finite kg, incl. tare. Positive and no greater than
            ``max_gross_mass_kg`` when that individual container's rating is supplied.
        max_gross_mass_kg: maximum operating gross mass from this container's CSC Safety Approval Plate, finite
            positive kg. None means unknown (legacy input), not proof of compliance. No rating is inferred from
            nominal length or ISO size-type code; the checker must handle missing rating evidence separately.
        weight_class: optional yard gate-in clustering label assigned by the generator (D25/D61). Ship weight
            ordering uses gross_mass_kg, never this label.
        pol: port of loading (UN/LOCODE).
        pod: port of discharge (UN/LOCODE). Resolves to a call after ``Instance.call_index`` (see ``Rotation``).
        empty: True for an empty box (still has tare mass and still counts as a move).
        reefer: True if the box must be plugged in on board (needs a cell with ``reefer_plug``). A reefer box carried
            unplugged ("non-operating reefer") has ``reefer=False``.
        reefer_setpoint_c: °C, only when ``reefer``.
        dg: dangerous goods, or None.
        direct_delivery: at this instance's current call, transfer directly between truck and ship (D61 C5), with
            no yard storage or yard source. Export boxes may occur on load_list without a yard position; import
            boxes may be onboard. Instance rejects a marked box anywhere in its yard snapshot, including quay_buffer.
            With rule-freeze-v1 enabled, Instance requires this flag to match the current port's primary-class DG
            direct-delivery rule and exempt_divisions (D66). Subsidiary risks/additional entries do not trigger it.
            APPROXIMATION: no truck timing is modelled; a direct-delivery box is available when its lift comes.
        special_stowage: sorted list of E8 requests (no duplicates).
        oog: out-of-gauge dimensions, or None (in gauge).
        category: optional interchangeability key filled by the generator or 3.8 (boxes with equal category may swap
            ship slots without changing any rule outcome). Never trusted by the checker.
    """

    id: ContainerId
    iso_type: str = Field(pattern=r"^[0-9A-Z]{4}$")
    length_ft: Literal[20, 40, 45]
    height_m: float = Field(gt=2.0, lt=3.5)
    tare_kg: Kg
    gross_mass_kg: float = Field(gt=0.0, allow_inf_nan=False)
    max_gross_mass_kg: float | None = Field(default=None, gt=0.0, allow_inf_nan=False)
    weight_class: str | None = None
    pol: Locode
    pod: Locode
    empty: bool = False
    reefer: bool = False
    reefer_setpoint_c: float | None = None
    dg: DangerousGoods | None = None
    direct_delivery: bool = False
    special_stowage: list[SpecialStowage] = Field(default_factory=list)
    oog: OutOfGauge | None = None
    category: str | None = None

    @field_validator("special_stowage")
    @classmethod
    def canonical_requests(cls, value: list[SpecialStowage]) -> list[SpecialStowage]:
        return sorted(set(value))

    @model_validator(mode="after")
    def consistent_attributes(self) -> Container:
        length = {"2": 20, "4": 40, "L": 45}.get(self.iso_type[0])
        if length != self.length_ft:
            raise ValueError("iso_type length code must agree with length_ft (supported: 2, 4, L)")
        height = {"2": 2.591, "5": 2.896}.get(self.iso_type[1])
        if height is not None and abs(self.height_m - height) > 0.001:
            raise ValueError("iso_type height code must agree with height_m")
        if self.tare_kg > self.gross_mass_kg:
            raise ValueError("gross_mass_kg must include tare_kg")
        # CSC Annex I, Reg. 1(2)(a): "Maximum operating gross mass (kg and lb)" is an individual plate field;
        # Reg. 1(1)(b) requires matching markings. Reproduced at IMDG 1.1.2.3.1,
        # data/raw/imdg-42-24/msc556-108.pdf, PDF p. 21. Do not infer the rating from ISO size alone.
        if self.max_gross_mass_kg is not None and self.gross_mass_kg > self.max_gross_mass_kg:
            raise ValueError("gross_mass_kg must not exceed max_gross_mass_kg")
        if self.reefer_setpoint_c is not None and not self.reefer:
            raise ValueError("reefer_setpoint_c requires reefer=True")
        return self
