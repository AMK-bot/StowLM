"""Vessel: slot geometry, capabilities, hatch covers, limits, tanks, heat sources, hull data (plan 1.1).

Filled by 2.2 (fleet, Larsen–Pacino loader, sub-ship slices) and 2.1 (hull + hydrostatics). Read by every checker
part, the linear model (3.7), the move engine (hatch covers, cell fit) and the solvers.

Geometry model in one paragraph: the ship is a set of **cells**, one per odd (20') bay × row × tier, each holding at
most one 20' box or one half of a 40'/45' box. Cells are grouped into **stacks** (one vertical column of cells in the
hold or on deck: same odd bay, row and deck). A 40' box occupies the two cells at the same row and tier in the odd
bays either side of its even bay. Each hold stack sits under exactly one **hatch cover**; each deck stack sits on
exactly one hatch cover (or on a fixed deck/pedestal, ``hatch_cover_id=None`` with ``on_deck=True``).
Coordinates: x forward of AP, y positive to port, z above baseline (``stowage.conventions``).
"""

from __future__ import annotations

from collections.abc import Iterator
import math
from typing import Any, Literal

from pydantic import Field, FiniteFloat, field_validator, model_serializer, model_validator

from stowage.conventions import CONTAINER_LENGTH_M, CONTAINER_WIDTH_M, parse_slot, stack_id
from stowage.model.base import CellId, HatchCoverId, Kg, Metres, PosFloat, PosMetres, Slug, StackId, StowModel
from stowage.model.provenance import Provenance
from stowage.model.voyage import VoyageTankPolicy, validate_voyage_policy


class Cell(StowModel):
    """One physical 20' cell.

    Fields:
        id: odd-bay ``BBBRRTT`` cell id.
        stack_id: the stack this cell belongs to (``conventions.stack_id``).
        accepts_20: a 20' box may occupy this cell.
        accepts_40: this cell may hold one half of a 40' box (the partner cell, same row/tier in the other odd bay of
            the even bay, must also accept 40).
        accepts_45: as ``accepts_40`` for 45' boxes (overhang allowed by the cell guides).
        reefer_plug: a reefer can be plugged in here. A 40'/45' reefer is plugged if **either** of its two cells has a
            plug (assumption, matches how L–P files list plugs per 40' position; verify).
        lcg_m, tcg_m: x, y of the cell centre, m.
        vcg_base_m: z of the cell floor (bottom of a box placed here, before boxes below are known): for tier 02 the
            tank top; above that, the cell floor assumes standard-height boxes below. The true VCG of a box =
            floor of its stack position computed from the actual heights below + box height/2 (2.1/3.1 compute it;
            high cubes shift everything above).
    """

    id: CellId
    stack_id: StackId
    accepts_20: bool = True
    accepts_40: bool = True
    accepts_45: bool = False
    reefer_plug: bool = False
    lcg_m: Metres
    tcg_m: Metres
    vcg_base_m: Metres


    @model_validator(mode="after")
    def validate_address(self) -> Cell:
        address = parse_slot(self.id)
        if not address.is_20ft_bay:
            raise ValueError("physical cell must have an odd bay")
        if self.stack_id != stack_id(address.bay, address.row, address.on_deck):
            raise ValueError(f"cell {self.id} and stack_id {self.stack_id} disagree")
        return self


class Stack(StowModel):
    """One vertical column of cells, bottom → top.

    Fields:
        id: ``conventions.stack_id`` (odd bay + row + D/H).
        bay: odd bay. row: row number. on_deck: True for a deck stack.
        cell_ids: cells bottom → top (tiers ascending). Boxes fill bottom-up with no gaps (catalogue
            ``moves.*`` physical rule; enforced by the move engine).
        max_mass_kg: total stack weight limit (sum of box masses resting on this stack's base), kg, from the loading
            manual (E7). For 40' boxes each of the two stacks carries half the box mass (assumption; 2.4 documents).
        max_mass_20_kg / max_mass_40_kg: Any column containing 20′ boxes uses its 20′ rating; 40′/45′-only columns
            use their 40′ rating; missing ratings fall back to the general limit. Spanning boxes contribute half
            their mass per support. (ASSUMPTION pending expert Q16.)
        max_height_m: maximum stack height above the stack base, m (tier limit incl. high cubes).
        base_z_m: z of the stack base (tank top or hatch-cover top), m.
        hatch_cover_id: deck stack → the cover it rests on (None = fixed deck/pedestal); hold stack → the cover
            above it (always set for hold stacks).
        dg_classes_allowed: IMDG classes/divisions permitted in this stack's space by the ship's SOLAS II-2/19
            Document of Compliance (e.g. ``["2.2", "3", "8", "9"]``); None = no restriction recorded. For synthetic
            ships 1.6/2.2 set it by hold/deck area — APPROXIMATION (no real DoC), labelled in the params file.
        dg_doc_area_id: reference to Vessel.dg_doc_areas; None means no area recorded. An empty vessel area
            collection means the structured document is unavailable, not unrestricted DG authorization.
        max_tiers_by_visibility: optional cap on the number of tiers from SOLAS V/22, precomputed by 3.3 for
            convenience; the checker recomputes from geometry and never trusts this cache.
        lashing_group: id used by the lashing model (3.3, D2) to find this stack's lashing parameters; None = default.
    """

    id: StackId
    bay: int
    row: int
    on_deck: bool
    cell_ids: list[CellId]
    max_mass_kg: Kg
    max_mass_20_kg: Kg | None = None
    max_mass_40_kg: Kg | None = None
    max_height_m: PosMetres
    base_z_m: Metres
    hatch_cover_id: HatchCoverId | None = None
    dg_classes_allowed: list[str] | None = None
    dg_doc_area_id: str | None = None
    max_tiers_by_visibility: int | None = None
    lashing_group: str | None = None


    @model_validator(mode="after")
    def validate_cells(self) -> Stack:
        if self.id != stack_id(self.bay, self.row, self.on_deck):
            raise ValueError(f"stack {self.id} disagrees with bay, row or deck")
        if len(self.cell_ids) != len(set(self.cell_ids)):
            raise ValueError(f"stack {self.id} has duplicate cell ids")
        addresses = [parse_slot(cid) for cid in self.cell_ids]
        if any((a.bay, a.row, a.on_deck) != (self.bay, self.row, self.on_deck) for a in addresses):
            raise ValueError(f"stack {self.id} contains a cell from another stack")
        if self.cell_ids != sorted(self.cell_ids):
            raise ValueError(f"stack {self.id} cell_ids must be bottom to top (ascending tiers)")
        if not self.on_deck and self.hatch_cover_id is None:
            raise ValueError(f"hold stack {self.id} requires a hatch cover")
        if self.dg_classes_allowed is not None:
            self.dg_classes_allowed = sorted(set(self.dg_classes_allowed))
        return self


class DgDocArea(StowModel):
    """A SOLAS II-2/19 Document-of-Compliance cargo area, matching fleet parameters.

    ``classes_allowed`` records permitted IMDG classes/divisions (including labels
    such as 1.4S). ``packing_forms_allowed`` uses the parameter vocabulary, e.g.
    ``packaged_goods_in_closed_freight_containers``; an empty permission list grants
    nothing. ``stack_ids`` is reciprocal with Stack.dg_doc_area_id when the vessel
    supplies structured areas. ``kind``, ``hold_id`` and ``even_bay`` retain space
    identity; empty areas are allowed for holds absent from a geometry slice.

    ``equipment_assumptions`` and optional ventilation rate describe the equipment
    premise, not proof that equipment complies. ``assumption``, ``sources`` (including
    inherited document source/guidance URLs) and ``provenance`` retain its evidence.
    Authorization and packing/equipment compliance are checked by task 2.4; these
    fields alone do not certify a vessel. Synthetic premises must be labelled.
    """

    id: str = Field(min_length=1)
    stack_ids: list[StackId] = Field(default_factory=list)
    classes_allowed: list[str] = Field(default_factory=list)
    packing_forms_allowed: list[str] = Field(default_factory=list)
    kind: Literal["deck", "hold"] | None = None
    hold_id: str | None = None
    even_bay: int | None = Field(default=None, ge=2, le=998, multiple_of=2)
    equipment_assumptions: str | None = None
    ventilation_air_changes_per_hour: float | None = Field(default=None, ge=0.0)
    assumption: str | None = None
    sources: list[str] = Field(default_factory=list)
    provenance: Provenance = Field(default_factory=Provenance)

    @field_validator("stack_ids", "classes_allowed", "packing_forms_allowed", "sources")
    @classmethod
    def canonical_sets(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("DG DoC area has duplicate entries in a set-like list")
        return sorted(value)


class HatchCover(StowModel):
    """A hatch cover (pontoon). To reach any hold stack under it, every box in ``deck_stack_ids`` must be off the
    ship (or shifted) and the cover lifted — each lift of the cover is one ``hatch_cover`` move (D11; whether it
    counts is expert Q1, weight in ``ObjectiveWeights``).

    Fields:
        id: e.g. ``"HC-02-P"`` (free text, unique per vessel).
        bays: the even/odd bays it spans (informational).
        hold_stack_ids: stacks underneath (access needs this cover off).
        deck_stack_ids: stacks resting on top (must be empty for the cover to be lifted).
        mass_kg: cover mass, kg (part of lightship when closed; 3.1 moves it only for intermediate states).
        lcg_m, tcg_m, vcg_m: centre of mass, m.
    """

    id: HatchCoverId
    bays: list[int]
    hold_stack_ids: list[StackId]
    deck_stack_ids: list[StackId]
    mass_kg: Kg
    lcg_m: Metres
    tcg_m: Metres
    vcg_m: Metres


    @model_validator(mode="after")
    def normalize_members(self) -> HatchCover:
        for field in ("bays", "hold_stack_ids", "deck_stack_ids"):
            values = getattr(self, field)
            if len(values) != len(set(values)):
                raise ValueError(f"hatch cover {self.id} has duplicate {field}")
            setattr(self, field, sorted(values))
        if set(self.hold_stack_ids) & set(self.deck_stack_ids):
            raise ValueError(f"hatch cover {self.id} lists a stack as both hold and deck")
        return self


class TankSoundingPoint(StowModel):
    """One row of a tank's capacity table: centroid and free-surface moment at a filling level."""

    volume_m3: float = Field(ge=0.0)
    lcg_m: Metres
    tcg_m: Metres
    vcg_m: Metres
    fsm_m4: float = Field(ge=0.0, description="transverse free-surface inertia i_t, m^4 (FSM = ρ·i_t, kg·m)")


class Tank(StowModel):
    """A tank (ballast, fuel, diesel, fresh water, ...). Ballast tanks are the finisher's (4.2) decision variables.

    Fields:
        id: unique per vessel. kind: purpose. content_density_kg_m3: density of its content.
        capacity_m3: 100 % volume. table: capacity table rows, ascending volume, first row 0 m³, last = capacity.
        heated: True if heated (a heat source for "away from heat", E8; also listed in ``Vessel.heat_sources``).
        x_aft_m, x_fore_m: longitudinal extent, m (for the strength weight distribution, 3.2).
        adjustable: True if the planner/finisher may change its content (ballast); False = fixed by the instance.
    """

    id: str
    kind: Literal["ballast", "fuel_oil", "diesel_oil", "fresh_water", "lube_oil", "stores", "sludge", "other"]
    content_density_kg_m3: PosFloat
    capacity_m3: PosFloat
    table: list[TankSoundingPoint]
    heated: bool = False
    x_aft_m: Metres
    x_fore_m: Metres
    adjustable: bool = False


    @model_validator(mode="after")
    def validate_table(self) -> Tank:
        volumes = [row.volume_m3 for row in self.table]
        if not volumes or volumes[0] != 0.0 or volumes[-1] != self.capacity_m3:
            raise ValueError(f"tank {self.id} table must run from zero to capacity_m3")
        if any(a >= b for a, b in zip(volumes, volumes[1:])):
            raise ValueError(f"tank {self.id} table volumes must strictly increase")
        if self.x_aft_m > self.x_fore_m:
            raise ValueError(f"tank {self.id} longitudinal extent is reversed")
        return self


class TankPair(StowModel):
    """D64 dedicated anti-heeling pair; contents and the arrival pre-fill are not model defaults.

    Tank references, port-positive/starboard-negative lever arms and symmetry are
    checked against the actual vessel tank catalog. This makes the closed-form
    transfer's common density and mirrored, potentially fill-dependent levers
    explicit. The pair declares physical identity, not an unconditional ballast
    invariant: D64 arrival/departure total conservation is enforced by the
    checker only when ``rule_freeze_v1`` is enabled. Off-state public ballast
    adapters retain legacy behavior, including emptying omitted adjustable
    tanks. Constructing a condition is not a safety certification.
    """

    id: str = Field(min_length=1)
    port_tank: str = Field(min_length=1)
    starboard_tank: str = Field(min_length=1)
    role: Literal["anti_heeling"] = Field(default="anti_heeling", description=(
        "D64 pair-total conservation is enforced only when rule_freeze_v1 is enabled; "
        "off-state ballast adapters retain legacy semantics, including emptying omitted adjustable tanks."))
    assumption: str | None = None


class BoxSourceGeometry(StowModel):
    """An explicitly rectangular physical source solid, in ship metres."""

    shape: Literal["rectangular_prism"] = "rectangular_prism"
    x_min_m: FiniteFloat
    x_max_m: FiniteFloat
    y_min_m: FiniteFloat
    y_max_m: FiniteFloat
    z_min_m: FiniteFloat
    z_max_m: FiniteFloat

    @model_validator(mode="after")
    def positive_extents(self) -> BoxSourceGeometry:
        if any(getattr(self, f"{axis}_min_m") >= getattr(self, f"{axis}_max_m") for axis in "xyz"):
            raise ValueError("source geometry bounds require finite positive extents")
        return self


class HullSectionsSourceGeometry(BoxSourceGeometry):
    """Actual recorded hull-following tank sections, not their bounding box.

    APPROXIMATION: the retained longitudinal Gauss quadrature is the existing
    liquid-geometry representation. It supplies no surface between stations;
    gaps and missing Gauss mates are permitted. Consumers must not invent
    connecting panels or claim a continuous three-dimensional distance from
    these samples. Source: ``stowage.vessel.hull_tanks`` representation contract.
    """

    shape: Literal["hull_sections"] = "hull_sections"
    x_m: list[FiniteFloat]
    integration_weights_m: list[FiniteFloat]
    sections_yz_m: list[list[list[tuple[FiniteFloat, FiniteFloat]]]]

    @model_validator(mode="after")
    def validate_sections(self) -> HullSectionsSourceGeometry:
        if not self.x_m or len(self.x_m) != len(self.integration_weights_m) or len(self.x_m) != len(self.sections_yz_m):
            raise ValueError("source sections require matching nonempty station, weight and polygon arrays")
        if any(a >= b for a, b in zip(self.x_m, self.x_m[1:])):
            raise ValueError("source section stations must strictly increase")
        if any(weight <= 0 for weight in self.integration_weights_m):
            raise ValueError("source section integration weights must be positive")
        for x, section in zip(self.x_m, self.sections_yz_m):
            if not self.x_min_m <= x <= self.x_max_m or not section:
                raise ValueError("source section station must lie within its bounds and contain polygons")
            for polygon in section:
                if len(polygon) < 3:
                    raise ValueError("source section polygons require at least three vertices")
                for y, z in polygon:
                    # Only accommodate floating-point endpoint representation;
                    # no physical clearance or eligibility tolerance changes.
                    if not (self.y_min_m - 1e-9 <= y <= self.y_max_m + 1e-9
                            and self.z_min_m - 1e-9 <= z <= self.z_max_m + 1e-9):
                        raise ValueError("source section vertices must lie within its bounds")
                area2 = math.fsum(a[0] * b[1] - b[0] * a[1]
                                 for a, b in zip(polygon, polygon[1:] + polygon[:1]))
                if not math.isfinite(area2) or area2 <= 0:
                    raise ValueError("source section polygons must be nondegenerate and counterclockwise")
        return self


class HeatSource(StowModel):
    """A heat source for "away from heat" (E8) and IMDG "protected from sources of heat": engine-room bulkheads,
    heated tanks, funnel casing. Bounds retain the legacy E8 envelope. ``actual_geometry`` retains the explicitly
    declared solid/sections for shape-aware consumers; None is unknown, never proof that the bounds are the solid.
    Heated sources link the same tank design through ``tank_id``; the loader checks the actual parameter geometry.
    The catalogue gives the required distance."""

    id: str
    kind: Literal["engine_room", "heated_tank", "funnel", "other"]
    x_min_m: Metres
    x_max_m: Metres
    y_min_m: Metres
    y_max_m: Metres
    z_min_m: Metres
    z_max_m: Metres
    tank_id: str | None = None
    liable_to_exceed_55_c: bool | None = Field(default=None, strict=True)
    actual_geometry: BoxSourceGeometry | HullSectionsSourceGeometry | None = Field(default=None, discriminator="shape")


    @model_validator(mode="after")
    def validate_bounds(self) -> HeatSource:
        if any(getattr(self, f"{axis}_min_m") > getattr(self, f"{axis}_max_m") for axis in "xyz"):
            raise ValueError(f"heat source {self.id} has reversed bounds")
        if self.actual_geometry is not None and any(
                getattr(self.actual_geometry, f"{axis}_{side}_m") != getattr(self, f"{axis}_{side}_m")
                for axis in "xyz" for side in ("min", "max")):
            raise ValueError(f"heat source {self.id} actual geometry must share the source bounds")
        return self

    def distance_to_box_m(self, bounds: FixedLoadSilhouette) -> float | None:
        """Exact Euclidean solid distance for an explicitly rectangular source.

        None means no continuous solid is declared: missing actual geometry and
        Gauss-section samples cannot certify a clearance. In particular this
        helper does not replace hull-following tanks with their bounding box,
        interpolate their samples or decide the SW1 verdict. The consumer must
        retain unknown until a continuous physical representation is supplied.
        """
        geometry = self.actual_geometry
        if geometry is None or geometry.shape != "rectangular_prism":
            return None
        gaps = [max(getattr(bounds, f"{axis}_min_m") - getattr(geometry, f"{axis}_max_m"),
                    getattr(geometry, f"{axis}_min_m") - getattr(bounds, f"{axis}_max_m"), 0.0)
                for axis in "xyz"]
        return math.hypot(*gaps)


class PlacementMapEvidence(StowModel):
    """Design source bound to a separately registered assessment scenario.

    A scenario identifier alone never establishes clearance; the assessor resolves
    it against the actual shipment, voyage and placement context.
    """

    document: str = Field(min_length=1)
    clause: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)

    @field_validator("document", "clause", "scenario_id")
    @classmethod
    def nonblank_evidence(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("placement map evidence must be nonblank")
        return value


class ExclusionRegionSource(StowModel):
    """Named SW2 role referencing one actual shared solid.

    Exactly one reference is supplied: ``region_id`` for the permanent named
    vessel exclusion catalog, or ``fixed_load_index`` for saved indexed
    non-cargo silhouettes. No second set of bounds exists here.
    """

    id: str = Field(min_length=1)
    kind: Literal["accommodation", "air_intake", "machinery_space", "enclosed_work_area"]
    fixed_load_index: int | None = Field(default=None, ge=0, strict=True)
    region_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def exactly_one_source(self) -> ExclusionRegionSource:
        if (self.fixed_load_index is None) == (self.region_id is None):
            raise ValueError("exclusion source requires exactly one fixed_load_index or region_id")
        return self

    @model_serializer(mode="wrap")
    def omit_absent_reference(self, handler):
        data = handler(self)
        for field in ("fixed_load_index", "region_id"):
            if getattr(self, field) is None:
                data.pop(field, None)
        return data


class HeatMapSource(StowModel):
    """SW1 applicability/evidence bound to an actual E8 HeatSource id."""

    id: str = Field(min_length=1)
    liable_to_exceed_55_c: bool | None = Field(default=None, strict=True)
    provenance: PlacementMapEvidence | None = None
    tank_id: str | None = None

    @model_serializer(mode="wrap")
    def omit_absent_tank_link(self, handler):
        # The assessor distinguishes an absent link from an explicitly bad link.
        data = handler(self)
        if self.tank_id is None:
            data.pop("tank_id", None)
        return data


class ImdgPlacementGeometry(StowModel):
    """D59 source catalog, compatible with ``geometry_from_vessel``.

    Geometry is referenced, never independently redrawn. Completeness and
    temperature liability are design declarations, not future observations.
    Unknown/missing evidence stays unknown at the assessor boundary.
    A vessel augmented with unclassified non-cargo/legacy fixed loads remains
    readable, but its inherited SW2 completeness is downgraded to False. The
    original design map cannot certify a changed component inventory.
    """

    living_quarters_sources: list[ExclusionRegionSource] = Field(default_factory=list)
    living_quarters_map_complete: bool | None = Field(default=None, strict=True)
    living_quarters_map_provenance: PlacementMapEvidence | None = None
    heat_sources: list[HeatMapSource] = Field(default_factory=list)
    heat_map_complete: bool | None = Field(default=None, strict=True)
    heat_map_provenance: PlacementMapEvidence | None = None


class HydrostaticRow(StowModel):
    """One row of the upright, even-keel hydrostatic table at a mean draught (2.1 computes)."""

    draught_m: PosMetres
    displacement_kg: PosFloat  # in sea water of the table density
    kb_m: Metres
    kmt_m: Metres  # KM transverse = KB + BMt
    lcb_m: Metres
    lcf_m: Metres
    tpc_kg_per_cm: PosFloat  # mass per cm immersion, kg/cm
    mct_nm_per_cm: PosFloat  # moment to change trim 1 cm, N·m/cm


class CrossCurve(StowModel):
    """KN values (m) at one heel angle over displacement, for the GZ curve: GZ = KN − KG·sin(φ)."""

    heel_rad: float
    displacement_kg: list[float]
    kn_m: list[float]


    @model_validator(mode="after")
    def validate_samples(self) -> CrossCurve:
        if len(self.displacement_kg) != len(self.kn_m):
            raise ValueError("cross curve displacement_kg and kn_m must have equal lengths")
        if any(value <= 0 for value in self.displacement_kg):
            raise ValueError("cross curve displacements must be positive")
        if any(a >= b for a, b in zip(self.displacement_kg, self.displacement_kg[1:])):
            raise ValueError("cross curve displacements must strictly increase")
        return self


class Station(StowModel):
    """A strength calculation station (frame) with permissible still-water values (IACS S11A, computed for our
    synthetic ships in 2.1/3.2). Signs: bending moment **hogging positive**, sagging negative; shear force
    SF(x) = g·∫_AP^x (buoyancy − weight) dx per unit length, i.e. the net upward force on the part aft of x.
    Permissible: min_* <= value <= max_*."""

    x_m: Metres
    max_shear_n: PosFloat
    min_shear_n: float
    max_bending_nm: PosFloat  # hogging limit
    min_bending_nm: float  # sagging limit (negative)
    max_torsion_nm: float | None = None


class VesselFieldProvenance(StowModel):
    """Source or explicit assumption for one vessel value, plus artifact lineage.

    A missing record means the value's evidence is unknown. A present record
    must identify a nonblank source or assumption; it does not certify the value.
    """

    source: str | None = None
    assumption: str | None = None
    provenance: Provenance = Field(default_factory=Provenance)

    @field_validator("source", "assumption")
    @classmethod
    def nonblank_evidence(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("source or assumption text must not be blank")
        return value

    @model_validator(mode="after")
    def evidence_present(self) -> VesselFieldProvenance:
        if self.source is None and self.assumption is None:
            raise ValueError("VesselFieldProvenance requires source or assumption")
        return self


class HullModel(StowModel):
    """Everything the exact physics needs (2.1 builds it; 3.1/3.2/3.3/3.7 read it).

    Fields:
        lpp_m, loa_m, beam_m, depth_m, design_draught_m, scantling_draught_m: principal dimensions, m.
        ap_x_m: x of the aft perpendicular (= 0 by convention), fp_x_m: x of the forward perpendicular (= lpp).
        water_density_kg_m3: density the hydrostatic tables are computed for.
        lightship_mass_kg, lightship_lcg_m, lightship_tcg_m, lightship_vcg_m: lightship.
        lightship_distribution: (x_aft_m, x_fore_m, mass_kg) blocks summing to ``lightship_mass_kg`` (for SF/BM).
        hydrostatics: rows ascending in draught. cross_curves: one per heel angle (0 < φ <= 60°+, includes 30°, 40°).
        stations: strength stations, aft → fore.
        hull_offsets_ref: optional path/id of the station offsets used to integrate buoyancy (2.1's parametric hull);
            None when the buoyancy curve comes from tabulated data (Larsen–Pacino).
        bonjean: optional sectional-area table (x_m → [(draught_m, area_m2)]) for the buoyancy distribution.
        windage_area_m2, windage_lever_m: lateral projected area above the waterline and its centroid height for the
            IS Code weather criterion at the design draught (3.1 may recompute with deck cargo).
        bilge_keel: legacy flag; it does not determine bilge_keel_area_m2.
        bilge_keel_area_m2: overall bilge/bar-keel area Ak, finite and >= 0 m². None means unknown;
            0 is an explicit no-keels declaration. Evidence is in bilge_keel_area_m2_provenance.
        max_draught_m, min_draught_fwd_m: draught limits (load line, slamming/propeller immersion).
        min_trim_m, max_trim_m: independent finite signed loading-manual limits (aft − forward draught, m).
        min_displacement_kg, max_displacement_kg: independent finite positive loading-manual limits, kg.
            Each of these four limits has an optional <field>_provenance record. Missing values/evidence mean
            unknown; neither trim bound is inferred from the other, and displacement limits are not table endpoints.
        max_list_rad: list operating limit, rad.
        min_gm_m: ship-specific GM minimum (>= IS Code 0.15 m; catalogue holds the code value).
        max_gm_lashing_m: optional finite positive design maximum GM for the securing arrangement, m (D56).
            None means unknown; max_gm_lashing_m_provenance retains its source or declared assumption.
    """

    lpp_m: PosMetres
    loa_m: PosMetres
    beam_m: PosMetres
    depth_m: PosMetres
    design_draught_m: PosMetres
    scantling_draught_m: PosMetres
    ap_x_m: Metres = 0.0
    fp_x_m: PosMetres
    water_density_kg_m3: PosFloat = 1025.0
    lightship_mass_kg: PosFloat
    lightship_lcg_m: Metres
    lightship_tcg_m: Metres = 0.0
    lightship_vcg_m: Metres
    lightship_distribution: list[tuple[float, float, float]]
    hydrostatics: list[HydrostaticRow]
    cross_curves: list[CrossCurve]
    stations: list[Station]
    hull_offsets_ref: str | None = None
    bonjean: dict[str, list[tuple[float, float]]] | None = None
    windage_area_m2: PosFloat
    windage_lever_m: Metres
    bilge_keel: bool = True
    # IS Code A2.3.4, data/raw/is-code/msc267-85.pdf, PDF p15 (printed p14):
    # Ak = "total overall area of bilge keels" plus the lateral projected bar-keel area.
    # Table 2.3.4-3 (PDF p16) uses Ak; the legacy boolean cannot supply this quantity.
    bilge_keel_area_m2: float | None = Field(default=None, ge=0.0, allow_inf_nan=False, strict=True)
    bilge_keel_area_m2_provenance: VesselFieldProvenance | None = None
    max_draught_m: PosMetres
    min_draught_fwd_m: float = 0.0
    # D40: loading-manual values are independently declared, never inferred.
    min_trim_m: FiniteFloat | None = Field(default=None, strict=True)
    max_trim_m: FiniteFloat | None = Field(default=None, strict=True)
    min_displacement_kg: float | None = Field(default=None, gt=0.0, allow_inf_nan=False, strict=True)
    max_displacement_kg: float | None = Field(default=None, gt=0.0, allow_inf_nan=False, strict=True)
    min_trim_m_provenance: VesselFieldProvenance | None = None
    max_trim_m_provenance: VesselFieldProvenance | None = None
    min_displacement_kg_provenance: VesselFieldProvenance | None = None
    max_displacement_kg_provenance: VesselFieldProvenance | None = None
    max_list_rad: float
    min_gm_m: float = 0.15
    max_gm_lashing_m: float | None = Field(default=None, gt=0.0, allow_inf_nan=False, strict=True)
    max_gm_lashing_m_provenance: VesselFieldProvenance | None = None


    @model_validator(mode="after")
    def validate_table_order(self) -> HullModel:
        for minimum, maximum in (("min_trim_m", "max_trim_m"),
                                 ("min_displacement_kg", "max_displacement_kg")):
            lower, upper = getattr(self, minimum), getattr(self, maximum)
            if lower is not None and upper is not None and lower > upper:
                raise ValueError(f"hull {minimum} must be less than or equal to {maximum}")
        for label, values in (
            ("hydrostatics draught", [row.draught_m for row in self.hydrostatics]),
            ("strength station x", [station.x_m for station in self.stations]),
        ):
            if any(a >= b for a, b in zip(values, values[1:])):
                raise ValueError(f"hull {label} values must strictly increase")
        heels = [curve.heel_rad for curve in self.cross_curves]
        if len(heels) != len(set(heels)):
            raise ValueError("hull has duplicate cross curve heel angles")
        self.cross_curves = sorted(self.cross_curves, key=lambda curve: curve.heel_rad)
        return self


class Bridge(StowModel):
    """Conning position for SOLAS V/22 (3.3): x of the bridge front, eye height above baseline, and the ship's bow
    x. Also stacks behind the bridge are unrestricted except by the aft rules 3.3 implements."""

    x_m: Metres
    eye_z_m: Metres
    bow_x_m: Metres


class FixedLoadSilhouette(StowModel):
    """An axis-aligned occupied cargo envelope in vessel coordinates, metres.

    Used for background on-deck windage and line-of-sight obstruction. This is
    the full block envelope, not a projected area or a centre-of-mass location.
    Consumers must account for overlaps/occlusion rather than blindly summing
    projected areas. Coordinates use x forward, y to port, z above baseline.
    """

    x_min_m: FiniteFloat
    x_max_m: FiniteFloat
    y_min_m: FiniteFloat
    y_max_m: FiniteFloat
    z_min_m: FiniteFloat
    z_max_m: FiniteFloat

    @model_validator(mode="after")
    def positive_extents(self) -> FixedLoadSilhouette:
        for axis in "xyz":
            if getattr(self, f"{axis}_min_m") >= getattr(self, f"{axis}_max_m"):
                raise ValueError(f"fixed load silhouette {axis}_min_m must be less than {axis}_max_m")
        return self


class ExclusionRegion(StowModel):
    """Named physical SW2 component and its shared design-source reference.

    Engine-room solids bind the E8 heat catalog exactly. Deckhouse, bridge and
    intake solids retain their source identities; the fleet loader checks the
    corresponding raw design geometry. Their synthetic transverse/vertical
    extensions must be explicit assumptions, never implicit point extrusions.
    A conning enclosure is not an extra opaque cargo obstacle or extra mass.
    """

    id: str = Field(min_length=1)
    kind: Literal["accommodation", "air_intake", "machinery_space", "enclosed_work_area"]
    source_kind: Literal["deckhouse", "bridge", "heat_source", "opening", "forecastle", "steering_gear", "machinery_space"]
    source_id: str = Field(min_length=1)
    bounds: FixedLoadSilhouette
    assumption: str = Field(min_length=1)

    @field_validator("assumption")
    @classmethod
    def nonblank_assumption(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("exclusion region assumption must be nonblank")
        return value


class FixedCargoBox(StowModel):
    """One non-decision background container, with absolute CG in ship axes.

    ``cell_ids`` are the physical odd-bay supports, sorted into canonical order;
    ``stack_id`` identifies the first support's stack. A long box also loads the
    second support stack. Vessel validation checks cell existence, length fit,
    occupied-stack continuity and CG inside the actual box envelope. Coordinates
    are x forward of AP, y to port and z above baseline, all in metres.
    """

    cell_ids: list[CellId]
    length_ft: Literal[20, 40, 45]
    height_m: float = Field(gt=0.0, allow_inf_nan=False, strict=True)
    gross_mass_kg: float = Field(gt=0.0, allow_inf_nan=False, strict=True)
    x_m: float = Field(allow_inf_nan=False, strict=True)
    y_m: float = Field(allow_inf_nan=False, strict=True)
    z_m: float = Field(allow_inf_nan=False, strict=True)
    stack_id: StackId
    tier: int = Field(ge=2, le=98, multiple_of=2, strict=True)
    provenance: Provenance = Field(default_factory=Provenance)

    @model_validator(mode="after")
    def validate_support_addresses(self) -> FixedCargoBox:
        expected_count = 1 if self.length_ft == 20 else 2
        if len(self.cell_ids) != expected_count or len(set(self.cell_ids)) != expected_count:
            raise ValueError(f"cargo_boxes cell_ids must contain {expected_count} distinct supports for length_ft")
        self.cell_ids = sorted(self.cell_ids)
        addresses = [parse_slot(cid) for cid in self.cell_ids]
        if any(not address.is_20ft_bay for address in addresses):
            raise ValueError("cargo_boxes cell_ids must use physical odd bays")
        first = addresses[0]
        if any(address.row != first.row or address.tier != first.tier for address in addresses):
            raise ValueError("cargo_boxes cell_ids must share row and tier")
        if len(addresses) == 2 and addresses[1].bay - first.bay != 2:
            raise ValueError("cargo_boxes cell_ids for long boxes must use adjacent odd bays")
        if self.tier != first.tier:
            raise ValueError("cargo_boxes tier must match cell_ids")
        if self.stack_id != stack_id(first.bay, first.row, first.on_deck):
            raise ValueError("cargo_boxes stack_id must match the first cell_ids support")
        return self


class FixedLoad(StowModel):
    """A mass always aboard besides lightship, optionally with an occupied envelope.

    x_m/y_m/z_m are its centre of mass; mass_kg is counted exactly once. ``kind``
    distinguishes background cargo from non-cargo stores/crew/constants. Missing
    kind is ``unknown``; explicit legacy ``other`` also means unknown. A silhouette
    supplies geometry, not additional mass. On-deck cargo blocks should supply
    one envelope per background stack or bay block; None means geometry is
    unavailable, never evidence of zero windage or no visibility obstruction.

    Legacy [x, y, z, mass] inputs become kind='unknown', silhouette=None because
    their cargo classification and geometry cannot be recovered. JSON writes
    named fields. Iteration/indexing/length and point-mass ordering preserve the
    old numerical consumers during migration; prefer named fields in new code.

    ``cargo_boxes=None`` preserves missing legacy evidence; it never means no
    securing is required. When supplied, per-box mass and mass-weighted CG must
    agree with the declared aggregate: relative tolerance 1e-9, absolute mass
    tolerance 1e-6 kg and absolute CG tolerance 1e-6 m. Empty cargo_boxes is valid
    only for zero aggregate mass, whose CG is immaterial. Mass is counted once.
    """

    x_m: FiniteFloat
    y_m: FiniteFloat
    z_m: FiniteFloat
    mass_kg: float = Field(ge=0.0, allow_inf_nan=False)
    kind: Literal["cargo", "non_cargo", "unknown", "other"] = "unknown"
    silhouette: FixedLoadSilhouette | None = None
    cargo_boxes: list[FixedCargoBox] | None = None

    @model_validator(mode="before")
    @classmethod
    def legacy_point_mass(cls, value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            if len(value) != 4:
                raise ValueError("legacy fixed load must contain exactly four values: x, y, z, mass")
            return dict(zip(("x_m", "y_m", "z_m", "mass_kg"), value))
        return value

    @model_validator(mode="after")
    def validate_cargo_aggregate(self) -> FixedLoad:
        if self.cargo_boxes is None:
            return self
        if self.kind != "cargo":
            raise ValueError("cargo_boxes require kind='cargo'")
        if self.cargo_boxes and self.mass_kg <= 0.0:
            raise ValueError("cargo_boxes require positive aggregate mass_kg")
        try:
            box_mass = math.fsum(box.gross_mass_kg for box in self.cargo_boxes)
        except OverflowError as exc:
            raise ValueError("cargo_boxes aggregate mass_kg must be finite") from exc
        if not math.isfinite(box_mass) or not math.isclose(box_mass, self.mass_kg, rel_tol=1e-9, abs_tol=1e-6):
            raise ValueError("cargo_boxes gross_mass_kg sum must equal fixed load mass_kg")
        if not self.cargo_boxes:
            if self.mass_kg != 0.0:
                raise ValueError("empty cargo_boxes require zero mass_kg")
            return self
        for coordinate in ("x_m", "y_m", "z_m"):
            # Normalize masses first: mass*CG could overflow for finite inputs.
            try:
                centroid = math.fsum((box.gross_mass_kg / box_mass) * getattr(box, coordinate)
                                     for box in self.cargo_boxes)
            except OverflowError as exc:
                raise ValueError(f"cargo_boxes aggregate {coordinate} must be finite") from exc
            if not math.isfinite(centroid) or not math.isclose(
                centroid, getattr(self, coordinate), rel_tol=1e-9, abs_tol=1e-6
            ):
                raise ValueError(f"cargo_boxes mass-weighted CG must equal fixed load {coordinate}")
        return self

    def as_point_mass(self) -> tuple[float, float, float, float]:
        """Legacy numerical view (x, y, z, mass), excluding kind and geometry."""
        return self.x_m, self.y_m, self.z_m, self.mass_kg

    def __iter__(self) -> Iterator[float]:
        return iter(self.as_point_mass())

    def __getitem__(self, index: int | slice) -> float | tuple[float, ...]:
        return self.as_point_mass()[index]

    def __len__(self) -> int:
        return 4

    def __lt__(self, other: Any) -> bool:
        if isinstance(other, FixedLoad):
            return self.as_point_mass() < other.as_point_mass()
        if isinstance(other, tuple) and len(other) == 4:
            return self.as_point_mass() < other
        return NotImplemented


def _validate_geometry_refs(cells, stacks, covers, *, field: str) -> None:
    """Reciprocal physical geometry for decision and fixed-load catalogs alike."""
    for cell in cells.values():
        if cell.stack_id not in stacks:
            raise ValueError(f"{field} cell {cell.id} references unknown stack {cell.stack_id}")
        if cell.id not in stacks[cell.stack_id].cell_ids:
            raise ValueError(f"{field} cell {cell.id} missing from stack {cell.stack_id} cell_ids")
    for stack in stacks.values():
        for cid in stack.cell_ids:
            if cid not in cells:
                raise ValueError(f"{field} stack {stack.id} references unknown cell {cid}")
            if cells[cid].stack_id != stack.id:
                raise ValueError(f"{field} stack {stack.id} and cell {cid} disagree")
        if stack.hatch_cover_id is not None:
            if stack.hatch_cover_id not in covers:
                raise ValueError(f"{field} stack {stack.id} references unknown hatch cover {stack.hatch_cover_id}")
            cover = covers[stack.hatch_cover_id]
            members = cover.deck_stack_ids if stack.on_deck else cover.hold_stack_ids
            if stack.id not in members:
                raise ValueError(f"{field} stack {stack.id} missing from hatch cover {cover.id}")
    for cover in covers.values():
        for on_deck, members in ((False, cover.hold_stack_ids), (True, cover.deck_stack_ids)):
            for sid in members:
                if sid not in stacks:
                    raise ValueError(f"{field} hatch cover {cover.id} references unknown stack {sid}")
                stack = stacks[sid]
                if stack.on_deck != on_deck or stack.hatch_cover_id != cover.id:
                    raise ValueError(f"{field} hatch cover {cover.id} and stack {sid} disagree")


def _validate_stack_occupancy(stacks, occupied_cells, *, field: str) -> None:
    """Require a prefix of each declared stack; hold/deck bases are independent."""
    for stack in stacks:
        gap = False
        for cell_id in stack.cell_ids:
            if cell_id not in occupied_cells:
                gap = True
            elif gap:
                raise ValueError(f"{field} has unsupported cell {cell_id} "
                                 f"in stack {stack.id}; stacks must be gap-free bottom-up")


class BallastSystem(StowModel):
    """D74 declared pump rates and working-crane limit, without numeric defaults.

    Rates are m³/hour and ``max_cranes`` is a positive integer count. The current
    fleet values are synthetic pending Q23. Missing legacy data remains unknown;
    it never implies an unlimited or default pumping capability.
    """

    ballast_pump_m3_h: float = Field(gt=0, allow_inf_nan=False, strict=True)
    heel_transfer_m3_h: float = Field(gt=0, allow_inf_nan=False, strict=True)
    max_cranes: int = Field(gt=0, strict=True)
    assumption: str | None = Field(default=None, min_length=1)
    provenance: str | None = Field(default=None, min_length=1)


class Vessel(StowModel):
    """A ship (or a sub-ship slice of one, plan 2.2).

    Fields:
        id: slug, e.g. ``feeder_800`` or ``feeder_800.slice_b02-b10``. name: display name.
        nominal_teu: nominal capacity (2.2's done check: cell count matches it).
        cells, stacks, hatch_covers: geometry above; every cell's stack exists, every stack's cells exist, every
            hold stack has a cover, cover stack lists agree with stacks (1.1 validates these invariants).
        tanks: all tanks. heat_sources: E8/IMDG heat sources (engine room etc. + heated tanks).
        imdg_ship_type: declared D59 segregation-table domain: closed_holds for a cellular containership with
            hatch covers (IMDG 7.4.3.2), hatchless for a hatchcoverless containership (7.4.3.3), or None for unknown.
            This authored fact is independent of rule-freeze-v1 and is never inferred from names or hatch geometry.
        dg_doc_areas: structured DG permissions and equipment premises. Empty = document unavailable
            (legacy JSON remains valid), not blanket permission. With areas present, stack membership is reciprocal.
        hull: exact-physics data. None only for a pure-geometry slice used by move-engine tests; any checker part
            that needs it reports violation ``checker.missing_vessel_data`` (hard) instead of passing.
        bridge: None means "no visibility check possible" → same missing-data violation.
        slice_of: parent vessel id if this is a slice; ``fixed_loads`` then carries the mass of the rest of the
            ship as fixed masses and cargo envelopes (2.2 defines; APPROXIMATION label if used).
        fixed_loads: FixedLoad items always aboard besides lightship (stores, crew,
            constants, the rest of the ship for slices). Legacy four-value arrays remain readable.
        fixed_load_cells, fixed_load_stacks, fixed_load_hatch_covers: non-decision physical support geometry
            retained for background cargo boxes. Cells/stacks are disjoint from the planner's catalogs; physical
            covers can appear in both catalogs with their respective stack memberships. Boxes never add mass
            beyond the aggregate fixed_loads, and never enter load lists or the move objective (D42).
        voyage_policy: optional D50 consumable groups and tank draw-down order. None means unknown policy;
            existing conditions remain readable, but cannot establish arrival-condition evidence.
        sources: provenance of the numbers (param file path, Larsen–Pacino file + sha256).
    """

    id: Slug
    name: str
    nominal_teu: int = Field(ge=0)
    cells: list[Cell]
    stacks: list[Stack]
    hatch_covers: list[HatchCover]
    tanks: list[Tank] = Field(default_factory=list)
    tank_pairs: list[TankPair] = Field(default_factory=list)
    ballast_system: BallastSystem | None = None
    heat_sources: list[HeatSource] = Field(default_factory=list)
    imdg_placement_geometry: ImdgPlacementGeometry | None = None
    imdg_ship_type: Literal["closed_holds", "hatchless"] | None = None
    exclusion_regions: list[ExclusionRegion] = Field(default_factory=list)
    dg_doc_areas: list[DgDocArea] = Field(default_factory=list)
    hull: HullModel | None = None
    bridge: Bridge | None = None
    slice_of: Slug | None = None
    fixed_loads: list[FixedLoad] = Field(default_factory=list)
    fixed_load_cells: list[Cell] = Field(default_factory=list)
    fixed_load_stacks: list[Stack] = Field(default_factory=list)
    fixed_load_hatch_covers: list[HatchCover] = Field(default_factory=list)
    voyage_policy: VoyageTankPolicy | None = None
    sources: list[str] = Field(default_factory=list)

    @model_serializer(mode="wrap")
    def omit_unknown_ballast_system(self, handler):
        # Preserve legacy/default-off serialized shape, including nested vessels.
        data = handler(self)
        if self.ballast_system is None:
            data.pop("ballast_system", None)
        if self.imdg_ship_type is None:
            data.pop("imdg_ship_type", None)
        return data


    @model_validator(mode="after")
    def validate_geometry(self) -> Vessel:
        catalogs = {}
        for field in ("cells", "stacks", "hatch_covers", "tanks", "tank_pairs", "heat_sources", "exclusion_regions", "dg_doc_areas",
                      "fixed_load_cells", "fixed_load_stacks", "fixed_load_hatch_covers"):
            items = getattr(self, field)
            ids = [item.id for item in items]
            if len(ids) != len(set(ids)):
                raise ValueError(f"vessel has duplicate {field} ids")
            setattr(self, field, sorted(items, key=lambda item: item.id))
            catalogs[field] = {item.id: item for item in items}
        self._validate_tank_pairs(catalogs["tanks"])
        self._validate_placement_sources(catalogs["tanks"], catalogs["heat_sources"])
        cells, stacks, covers = (catalogs[k] for k in ("cells", "stacks", "hatch_covers"))
        areas = catalogs["dg_doc_areas"]
        membership = {}
        for area in self.dg_doc_areas:
            for sid in area.stack_ids:
                if sid not in stacks:
                    raise ValueError(f"DG DoC area {area.id} references unknown stack {sid}")
                if sid in membership:
                    raise ValueError(f"stack {sid} belongs to multiple DG DoC areas")
                membership[sid] = area.id
                stack = stacks[sid]
                if stack.dg_doc_area_id != area.id:
                    raise ValueError(f"stack {sid} and DG DoC area {area.id} membership disagree")
                if area.kind is not None and stack.on_deck != (area.kind == "deck"):
                    raise ValueError(f"stack {sid} and DG DoC area {area.id} deck/hold kind disagree")
        if areas:
            for stack in self.stacks:
                if stack.dg_doc_area_id is not None:
                    if stack.dg_doc_area_id not in areas:
                        raise ValueError(f"stack {stack.id} references unknown DG DoC area {stack.dg_doc_area_id}")
                    if membership.get(stack.id) != stack.dg_doc_area_id:
                        raise ValueError(f"stack {stack.id} missing from DG DoC area {stack.dg_doc_area_id}")
        _validate_geometry_refs(cells, stacks, covers, field="vessel")
        fixed_cells, fixed_stacks, fixed_covers = (catalogs[f"fixed_load_{name}"]
                                                  for name in ("cells", "stacks", "hatch_covers"))
        if cells.keys() & fixed_cells.keys() or stacks.keys() & fixed_stacks.keys():
            raise ValueError("fixed_load_cells/fixed_load_stacks must be disjoint from decision geometry")
        _validate_geometry_refs(fixed_cells, fixed_stacks, fixed_covers, field="fixed_load_geometry")
        self._validate_fixed_cargo(fixed_cells, fixed_stacks)
        validate_voyage_policy(self)
        self.sources = sorted(set(self.sources))
        return self

    def _validate_tank_pairs(self, tanks: dict[str, Tank]) -> None:
        used = set()
        for pair in self.tank_pairs:
            refs = (pair.port_tank, pair.starboard_tank)
            if refs[0] == refs[1] or any(ref not in tanks for ref in refs):
                raise ValueError(f"tank pair {pair.id} requires two distinct existing tanks")
            if used.intersection(refs):
                raise ValueError(f"tank pair {pair.id} reuses a paired tank")
            used.update(refs)
            port, starboard = (tanks[ref] for ref in refs)
            if any(tank.kind != "ballast" or not tank.adjustable for tank in (port, starboard)):
                raise ValueError(f"anti-heeling tank pair {pair.id} requires adjustable ballast tanks")
            if any(not math.isclose(getattr(port, field), getattr(starboard, field),
                                    rel_tol=1e-9, abs_tol=1e-9)
                   for field in ("capacity_m3", "content_density_kg_m3", "x_aft_m", "x_fore_m")):
                raise ValueError(f"anti-heeling tank pair {pair.id} requires matching capacity, density and extent")
            if len(port.table) != len(starboard.table):
                raise ValueError(f"anti-heeling tank pair {pair.id} requires mirrored sounding tables")
            for prow, srow in zip(port.table, starboard.table):
                if not (prow.tcg_m > 0 and srow.tcg_m < 0):
                    raise ValueError(f"tank pair {pair.id} requires port-positive/starboard-negative tcg")
                if (not math.isclose(prow.tcg_m, -srow.tcg_m, rel_tol=1e-9, abs_tol=1e-9)
                        or any(not math.isclose(getattr(prow, field), getattr(srow, field),
                                                rel_tol=1e-9, abs_tol=1e-9)
                               for field in ("volume_m3", "lcg_m", "vcg_m", "fsm_m4"))):
                    raise ValueError(f"anti-heeling tank pair {pair.id} requires mirrored sounding tables")

    def _validate_placement_sources(self, tanks: dict[str, Tank], heat: dict[str, HeatSource]) -> None:
        roles = {"deckhouse": "accommodation", "bridge": "enclosed_work_area",
                 "heat_source": "machinery_space", "opening": "air_intake",
                 "forecastle": "enclosed_work_area", "steering_gear": "enclosed_work_area",
                 "machinery_space": "machinery_space"}
        for region in self.exclusion_regions:
            if region.kind != roles[region.source_kind]:
                raise ValueError(f"exclusion region {region.id} has an incompatible source kind/role")
            if region.source_kind == "heat_source":
                source = heat.get(region.source_id)
                if source is None or source.kind != "engine_room":
                    raise ValueError(f"exclusion region {region.id} requires an actual engine-room HeatSource")
                if any(getattr(region.bounds, key) != getattr(source, key)
                       for key in FixedLoadSilhouette.model_fields):
                    raise ValueError(f"exclusion region {region.id} must share its HeatSource bounds")
            elif region.source_kind == "bridge":
                if self.bridge is None or region.source_id != "bridge":
                    raise ValueError(f"exclusion region {region.id} requires the actual bridge")
                if not (region.bounds.x_min_m <= self.bridge.x_m <= region.bounds.x_max_m
                        and region.bounds.z_min_m <= self.bridge.eye_z_m <= region.bounds.z_max_m):
                    raise ValueError(f"exclusion region {region.id} must contain the bridge conning point")
            elif region.source_kind == "deckhouse" and region.source_id != "deckhouse":
                raise ValueError(f"exclusion region {region.id} requires the shared deckhouse source")
            elif region.source_kind in {"forecastle", "steering_gear"} and region.source_id != region.source_kind:
                raise ValueError(f"exclusion region {region.id} requires its shared named structure source")
            elif region.source_kind == "machinery_space":
                cores = [source for source in self.heat_sources if source.kind == "engine_room"]
                if region.source_id != "machinery_space" or not cores:
                    raise ValueError(f"exclusion region {region.id} requires the shared machinery room and hot core")
                if any(getattr(region.bounds, f"{axis}_min_m") > getattr(source, f"{axis}_min_m")
                       or getattr(region.bounds, f"{axis}_max_m") < getattr(source, f"{axis}_max_m")
                       for source in cores for axis in "xyz"):
                    raise ValueError(f"exclusion region {region.id} must enclose the actual hot machinery core")
        for source in self.heat_sources:
            if source.tank_id is None:
                continue
            tank = tanks.get(source.tank_id)
            if tank is None or not tank.heated or source.kind != "heated_tank":
                raise ValueError(f"heat source {source.id} tank_id must reference an actual heated tank")
            if source.x_min_m != tank.x_aft_m or source.x_max_m != tank.x_fore_m:
                raise ValueError(f"heat source {source.id} must share its tank's longitudinal extent")
        metadata = self.imdg_placement_geometry
        if metadata is None:
            return
        for name in ("living_quarters_sources", "heat_sources"):
            ids = [source.id for source in getattr(metadata, name)]
            if len(ids) != len(set(ids)):
                raise ValueError(f"IMDG placement geometry has duplicate {name} ids")
        represented = set()
        represented_named = set()
        named = {region.id: region for region in self.exclusion_regions}
        for source in metadata.living_quarters_sources:
            if source.region_id is not None:
                region = named.get(source.region_id)
                if region is None or region.kind != source.kind:
                    raise ValueError(f"exclusion source {source.id} requires a matching named region and role")
                if source.region_id in represented_named:
                    raise ValueError("exclusion sources repeat a named region reference")
                represented_named.add(source.region_id)
                continue
            if source.fixed_load_index >= len(self.fixed_loads):
                raise ValueError(f"exclusion source {source.id} references unknown fixed_load_index")
            component = self.fixed_loads[source.fixed_load_index]
            if component.kind != "non_cargo" or component.silhouette is None:
                raise ValueError(f"exclusion source {source.id} requires a shared non-cargo silhouette")
            represented.add(source.fixed_load_index)
        if metadata.living_quarters_map_complete is True:
            if metadata.living_quarters_map_provenance is None:
                raise ValueError("complete exclusion map requires provenance")
            if represented_named != named.keys():
                raise ValueError("complete exclusion map must classify every named exclusion region")
            if any(component.kind != "cargo" and (component.kind != "non_cargo"
                    or component.silhouette is None or index not in represented)
                   for index, component in enumerate(self.fixed_loads)):
                # Preserve legacy point loads and externally added stores while
                # denying affirmative SW2 clearance from an incomplete inventory.
                # Copy rather than mutating a metadata object shared by the caller.
                self.imdg_placement_geometry = metadata.model_copy(update={
                    "living_quarters_map_complete": False})
        linked = set()
        for source in metadata.heat_sources:
            actual = heat.get(source.id)
            if actual is None:
                raise ValueError(f"heat map source {source.id} references an unknown HeatSource")
            if source.liable_to_exceed_55_c != actual.liable_to_exceed_55_c:
                raise ValueError(f"heat map source {source.id} conflicts with its HeatSource temperature liability")
            if source.tank_id != actual.tank_id:
                raise ValueError(f"heat map source {source.id} conflicts with its HeatSource tank_id")
            if source.tank_id is not None:
                if source.tank_id in linked:
                    raise ValueError("heat map repeats a heated tank link")
                linked.add(source.tank_id)
        if metadata.heat_map_complete is True:
            if metadata.heat_map_provenance is None:
                raise ValueError("complete heat map requires provenance")
            if {source.id for source in metadata.heat_sources} != heat.keys():
                raise ValueError("complete heat map must bind every actual HeatSource")
            if any(source.liable_to_exceed_55_c is None or source.provenance is None
                   for source in metadata.heat_sources):
                raise ValueError("complete heat map requires source liability and provenance")
            if any(tank.heated and tank.id not in linked for tank in self.tanks):
                raise ValueError("complete heat map must bind every heated tank")

    def _validate_fixed_cargo(self, cells, stacks) -> None:
        occupied = {}
        for index, load in enumerate(self.fixed_loads):
            for box in load.cargo_boxes or []:
                for cid in box.cell_ids:
                    if cid not in cells:
                        raise ValueError(f"fixed_loads[{index}].cargo_boxes references unknown fixed_load_cells {cid}")
                    if not getattr(cells[cid], f"accepts_{box.length_ft}"):
                        raise ValueError(f"fixed_loads[{index}].cargo_boxes cell {cid} requires accepts_{box.length_ft}")
                    if cid in occupied:
                        raise ValueError(f"fixed_loads.cargo_boxes overlap physical cell {cid}")
                    occupied[cid] = box
        _validate_stack_occupancy(stacks.values(), occupied, field="fixed_loads.cargo_boxes")
        # Use the declared stack base and actual box heights, not nominal tier
        # pitch. This supplies physical envelopes without assuming a central CG.
        floors = {}
        for stack in stacks.values():
            floor = stack.base_z_m
            if not math.isfinite(floor):
                raise ValueError(f"fixed_load_stacks[{stack.id}].base_z_m must be finite")
            for cid in stack.cell_ids:
                box = occupied.get(cid)
                if box is None:
                    break
                floors[cid] = floor
                floor += box.height_m
                if not math.isfinite(floor):
                    raise ValueError(f"fixed_loads.cargo_boxes height_m overflows stack {stack.id}")
        for index, load in enumerate(self.fixed_loads):
            for box in load.cargo_boxes or []:
                label = f"fixed_loads[{index}].cargo_boxes"
                bases = [floors[cid] for cid in box.cell_ids]
                if not math.isclose(min(bases), max(bases), rel_tol=0.0, abs_tol=1e-6):
                    raise ValueError(f"{label} has unequal support levels for cell_ids {box.cell_ids}")
                support = [cells[cid] for cid in box.cell_ids]
                if any(not math.isfinite(getattr(cell, axis)) for cell in support
                       for axis in ("lcg_m", "tcg_m")):
                    raise ValueError(f"{label} support cell CG coordinates must be finite")
                x = math.fsum(cell.lcg_m / len(support) for cell in support)
                y = math.fsum(cell.tcg_m / len(support) for cell in support)
                half_length = CONTAINER_LENGTH_M[box.length_ft] / 2
                bounds = (("x_m", x - half_length, x + half_length),
                          ("y_m", y - CONTAINER_WIDTH_M / 2, y + CONTAINER_WIDTH_M / 2),
                          ("z_m", bases[0], bases[0] + box.height_m))
                for axis, low, high in bounds:
                    if not low - 1e-6 <= getattr(box, axis) <= high + 1e-6:
                        raise ValueError(f"{label}.{axis} CG is outside the supported box envelope")
