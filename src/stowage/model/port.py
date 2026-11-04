"""PortProfile and Rotation (plan 1.1; PortProfile built by 2.6 from ``params/ports/*.toml``)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_serializer, model_validator

from stowage.model.base import CallIndex, Locode, RuleId, Slug, StowModel
from stowage.model.yard import Yard


class Equipment(StowModel):
    """Yard handling equipment class at a port.

    Fields:
        id: e.g. ``"rtg"``. kind: equipment type. stack_tiers: max boxes per stack it can build; independent of
            the informational "1-over-N" clearance notation (passing over N stored boxes).
        count: machines available (simulator capacity, 3.4).
    """

    id: str
    kind: Literal["RTG", "RMG", "straddle_carrier", "reach_stacker", "other"]
    stack_tiers: int = Field(ge=1)
    count: int = Field(ge=0)


class QuayCraneSpec(StowModel):
    """Quay-crane limits at the berth (E3: per-port height limit, quay side).

    Fields:
        count: cranes per berth.
        max_on_deck_tiers: compatibility field for the port's governing max_crane_height_tiers (D62). A PortProfile
            with an explicit H requires equality. The move engine rejects a
            load/restow to an on-deck position whose tier count from the stack base (1 = lowest on-deck tier) exceeds
            this. ``PortCall.max_on_deck_tiers`` describes later calls with the same height semantics.
        max_lift_height_m: informational (spreader height above quay level, m); not used by the move engine in v1 —
            converting it to tiers needs draught and freeboard, so ``max_on_deck_tiers`` is the enforced form.
        outreach_rows: optional max rows reachable across the ship.
        gross_moves_per_hour: D74 gross lifts per hour per crane, not per berth.
        pre_operation_allowance_h, post_operation_allowance_h: declared pumping
            windows before the first lift and after the last lift, in hours.
            All timing inputs are optional/unknown for legacy profiles. None
            values are omitted from JSON so saved profiles retain their bytes.
    """

    count: int = Field(ge=1)
    max_on_deck_tiers: int = Field(ge=1)
    max_lift_height_m: float | None = None
    outreach_rows: int | None = None
    gross_moves_per_hour: float | None = Field(default=None, gt=0.0, allow_inf_nan=False, strict=True)
    pre_operation_allowance_h: float | None = Field(default=None, gt=0.0, allow_inf_nan=False, strict=True)
    post_operation_allowance_h: float | None = Field(default=None, gt=0.0, allow_inf_nan=False, strict=True)

    @property
    def count_per_berth(self) -> int:
        """D74's parameter spelling; ``count`` remains the serialized field."""
        return self.count

    @model_serializer(mode="wrap")
    def omit_unknown_timing(self, handler):
        data = handler(self)
        for name in ("gross_moves_per_hour", "pre_operation_allowance_h", "post_operation_allowance_h"):
            if getattr(self, name) is None:
                data.pop(name, None)
        return data


class PortRuleRef(StowModel):
    """A port-specific rule: a catalogue entry id plus this port's parameter values (units per the catalogue)."""

    rule_id: RuleId
    params: dict[str, Any] = Field(default_factory=dict)


class PortProfile(StowModel):
    """A port archetype instantiated for an instance.

    Fields:
        locode: UN/LOCODE the archetype stands in for (synthetic "X-like" port; the code is only a label).
        archetype: params file id, e.g. ``"laem_chabang_like"``.
        profile_kind: operational capability profile or next-call water environment only. Legacy profiles default
            to operational; density-only profiles cannot be used as an Instance's current port.
        yard: static yard layout.
        equipment: yard equipment classes.
        yard_max_tiers: port-wide yard stack-height limit (E3, yard side); effective block limit =
            min(block.max_tiers, this, the block's equipment stack_tiers), independently of quay height H.
            None only for an
            environment-only profile whose crane and yard limits are unknown.
        max_crane_height_tiers: quay-crane limit H >= 1 for on-deck ship tiers and ship-side crossing (D62 amendment,
            A8), or None for a legacy profile without an explicit quay-height declaration. H does not constrain
            yard, block or equipment limits. None retains the legacy quay field and never rewrites operating limits.
            Consumers must retain their legacy behavior until their L2 migration.
        berthing_side: side of the ship facing the quay, used for crossing geometry. Default port is synthetic.
        quay_crane: quay-side limits, or None for an environment-only profile (unknown, never a placeholder limit).
        port_rules: catalogue ids + parameters that apply at this port.
        water_density_kg_m3: dock water density (affects draught; 3.1).
        max_draught_m: positive finite effective berth draught limit, m, or None.
            D75 synthetic profiles use the declared berth depth with no tide or
            under-keel-clearance increment. It is not navigational permission.
        sources: params file path + sha256.
    """

    locode: Locode
    archetype: Slug
    profile_kind: Literal["operational", "next_call_environment"] = "operational"
    yard: Yard
    equipment: list[Equipment]
    yard_max_tiers: int | None = Field(default=None, ge=1)
    max_crane_height_tiers: int | None = Field(default=None, ge=1)
    berthing_side: Literal["port", "starboard"] = "port"
    quay_crane: QuayCraneSpec | None = None
    port_rules: list[PortRuleRef] = Field(default_factory=list)
    water_density_kg_m3: float = Field(default=1025.0, gt=0.0)
    max_draught_m: float | None = Field(default=None, gt=0.0, allow_inf_nan=False, strict=True)
    sources: list[str] = Field(default_factory=list)

    @property
    def berth_depth_m(self) -> float | None:
        """Compatibility view of the effective D75 berth draught limit.

        Raw profiles declare depth; their synthetic no-tide/no-UKC conversion
        is retained in sources. ``max_draught_m`` is the single serialized cap
        consumed by the existing checker, so this property adds no second cap.
        """
        return self.max_draught_m

    @model_validator(mode="after")
    def validate_crane_height(self) -> PortProfile:
        height = self.max_crane_height_tiers
        if self.profile_kind == "next_call_environment":
            if height is not None or self.quay_crane is not None or self.yard_max_tiers is not None:
                raise ValueError("environment-only profiles must have unknown crane and yard limits")
            return self
        if self.quay_crane is None or self.yard_max_tiers is None:
            raise ValueError("operational profile requires quay_crane and yard_max_tiers")
        if height is None:
            # L1 must load older snapshots without changing their independent operating limits.
            return self
        if self.quay_crane.max_on_deck_tiers != height:
            raise ValueError("quay_crane.max_on_deck_tiers must equal max_crane_height_tiers")
        return self

    def requires_direct_delivery(self, primary_class: str | None) -> bool:
        """Look up the current-port primary-class policy (D66); execution callers gate it with rule-freeze-v1.

        Subsidiary risks and additional DG entries are intentionally absent from this interface. All class-1
        divisions, including 1.4S, match a configured class 1 unless explicitly exempted.
        """
        if primary_class is None:
            return False
        rule = next((rule for rule in self.port_rules if rule.rule_id == "port.dg.direct_delivery"), None)
        if rule is None:
            return False
        primary = primary_class.upper()
        division = primary.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        identifiers = {primary, division, primary.partition(".")[0]}
        classes = {str(value).upper() for value in rule.params.get("classes", [])}
        exemptions = {str(value).upper() for value in rule.params.get("exempt_divisions", [])}
        return bool(identifiers & classes) and not bool(identifiers & exemptions)


    @model_validator(mode="after")
    def validate_equipment(self) -> PortProfile:
        ids = [equipment.id for equipment in self.equipment]
        if len(ids) != len(set(ids)):
            raise ValueError("port profile has duplicate equipment ids")
        for block in self.yard.blocks:
            if block.equipment_id not in ids:
                raise ValueError(f"yard block {block.id} references unknown equipment {block.equipment_id}")
        rule_ids = [rule.rule_id for rule in self.port_rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("port profile has duplicate port rule ids")
        self.equipment = sorted(self.equipment, key=lambda equipment: equipment.id)
        self.port_rules = sorted(self.port_rules, key=lambda rule: rule.rule_id)
        self.sources = sorted(set(self.sources))
        return self


class PortCall(StowModel):
    """One call in a rotation.

    Fields:
        locode: port. index: position in ``Rotation.calls`` (redundant, validated).
        archetype: port archetype id resolved through stowage.ports.resolve_port_call_profile for later-call port
            rules and height limits. None remains valid for legacy calls; unresolved limits are unknown.
        max_on_deck_tiers: the quay-crane limit at this call, if known — used only for *later* calls (restow
            projection, stage D); at ``Instance.call_index`` the port profile's ``quay_crane`` governs.
        eta_s: planned arrival, s from the rotation epoch (stage D forecasts; informational in A).
    """

    locode: Locode
    index: CallIndex
    archetype: Slug | None = None
    max_on_deck_tiers: int | None = Field(default=None, ge=1)
    eta_s: float | None = None


class Rotation(StowModel):
    """The ship's port calls in order: the **whole voyage**, including calls before ``Instance.call_index`` (where
    onboard boxes were loaded, stage B). A box's POD resolves to the first call with that LOCODE strictly after
    ``Instance.call_index`` (for a box on the load list or aboard); it must exist (1.1 validates). POL is
    informational (it may be a call before the rotation starts for transshipped cargo). Generators avoid repeated
    LOCODEs in stage A."""

    service: str
    calls: list[PortCall] = Field(min_length=1)


    @model_validator(mode="after")
    def validate_indexes(self) -> Rotation:
        if [call.index for call in self.calls] != list(range(len(self.calls))):
            raise ValueError("rotation call indexes must equal their positions (0-based)")
        return self
