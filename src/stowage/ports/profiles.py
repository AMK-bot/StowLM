"""Synthetic berth groups and typed simulator inputs (plan 2.6).

A yard describes storage positions, not cargo: an empty ``YardState`` is supplied
by ``empty_yard_state``. Bays are 20-foot positions; long boxes occupy two bays.
Under the D62 amendment, effective yard storage and blocking height is the
minimum of block, port and equipment limits. Quay-crane H governs only the
on-deck storage and ship-side crossing limits. Equipment notation and nominal
clearance are informational; they do not change either threshold. Current-call
quay limits equal explicit H; staged legacy profiles retain their quay limits
until rule-freeze-v1 activation. Laem Chabang-like yard storage remains five.
Source: params/ports/README.md (consumer semantics) and model/port.py. Laem
Chabang-like H=8 and DG handling restrictions follow expert Q3/Q9 (28 Oct 2026);
the remaining operating parameters retain their explicit synthetic provenance.

The simulator inputs preserve the README's weekly arrivals, negative-binomial
volumes, gamma dwell, conditional gate-in bins and independent placement errors.
A coefficient of variation is standard deviation divided by mean. This module
only exposes those inputs; it does not sample activity or relax hard constraints.
Lightweight next-call profiles additionally supply declared D50 water-density
evidence, with no yard capacity or simulator parameters.
"""

from __future__ import annotations

from functools import lru_cache
from hashlib import sha256
from stowage.toml_cache import load_bytes as load_toml_bytes
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stowage.model.port import Equipment, PortCall, PortProfile, PortRuleRef, QuayCraneSpec
from stowage.model.base import Locode, Slug
from stowage.model.yard import Yard, YardBlock, YardState
from stowage.params import list_param_files, params_dir, quantity, is_quantity
from stowage.rules.catalogue import catalogue_snapshot


class SimulatorModel(BaseModel):
    """Immutable, validated values that simulation code can share safely."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class ServiceParameters(SimulatorModel):
    id: str
    kind: str
    calls_per_week: int = Field(gt=0)
    mean_load_boxes: float = Field(gt=0)
    mean_discharge_boxes: float = Field(gt=0)
    call_volume_cv: float = Field(gt=0)
    arrival_jitter_s: float = Field(ge=0)

    @property
    def calls_per_second(self) -> float:
        return self.calls_per_week / 604800


class GateInBin(SimulatorModel):
    start_before_cutoff_s: float = Field(gt=0)
    end_before_cutoff_s: float = Field(ge=0)
    share: float = Field(ge=0, le=1)


class GateInParameters(SimulatorModel):
    process: Literal["piecewise_uniform_before_cutoff"]
    window_before_cutoff_s: float = Field(gt=0)
    cutoff_before_arrival_s: float = Field(ge=0)
    bins: tuple[GateInBin, ...]

    @model_validator(mode="after")
    def partition(self) -> GateInParameters:
        bins = sorted(self.bins, key=lambda b: b.end_before_cutoff_s)
        if (not bins or bins[0].end_before_cutoff_s != 0
                or bins[-1].start_before_cutoff_s != self.window_before_cutoff_s
                or any(b.end_before_cutoff_s >= b.start_before_cutoff_s for b in bins)
                or any(a.start_before_cutoff_s != b.end_before_cutoff_s for a, b in zip(bins, bins[1:]))
                or abs(sum(b.share for b in bins) - 1) > 1e-9):
            raise ValueError("gate-in bins must partition the window with shares summing to one")
        return self


class DwellParameters(SimulatorModel):
    distribution: Literal["gamma"]
    import_mean_s: float = Field(gt=0)
    import_cv: float = Field(gt=0)
    transshipment_mean_s: float = Field(gt=0)
    transshipment_cv: float = Field(gt=0)


class PlacementParameters(SimulatorModel):
    cluster_keys: tuple[str, ...]
    preferred_stack_order: Literal["later_retrieval_below_earlier_retrieval"]
    noise_model: Literal["independent_cluster_and_order_errors"]
    cluster_misplacement_probability: float = Field(ge=0, le=1)
    retrieval_order_error_probability: float = Field(ge=0, le=1)


class SimulationPolicy(SimulatorModel):
    call_arrival_process: Literal["weekly_schedule_with_uniform_jitter"]
    volume_distribution: Literal["negative_binomial"]
    overflow_policy: Literal["hold_in_external_queue"]
    import_exit_process: Literal["sample_dwell_at_discharge"]
    transshipment_connection_policy: Literal["next_compatible_call_after_dwell"]


class TrafficParameters(SimulatorModel):
    transshipment_fraction: float = Field(ge=0, le=1)
    mean_teu_per_box: float = Field(ge=1, le=2)
    annual_reference_throughput_teu: float | None = Field(default=None, gt=0)


class SimulatorParameters(SimulatorModel):
    """All non-geometric simulator inputs; durations are seconds, volumes boxes.

    Initial occupancy targets TEU ground/storage positions, not occupied stacks.
    Public annual reference throughput is context, never per-call calibration.
    ``sources`` pins the exact input bytes and lists the added scenario defaults.
    """

    archetype: str
    berth_count: int = Field(gt=0)
    blocks_per_berth: int = Field(gt=0)
    initial_occupancy_fraction: float = Field(ge=0, le=1)
    equipment_stacking_notation: str = Field(min_length=1)
    equipment_clearance_height_tiers: int = Field(gt=0)
    services: tuple[ServiceParameters, ...]
    traffic: TrafficParameters
    gate_in: GateInParameters
    dwell: DwellParameters
    placement: PlacementParameters
    simulation: SimulationPolicy
    sources: tuple[str, ...]


class _CraneTimingParameters(SimulatorModel):
    """Raw D74 timing evidence; it does not supply an operational crane cap."""

    gross_moves_per_hour: float = Field(gt=0.0, strict=True)
    pre_operation_allowance_h: float = Field(gt=0.0, strict=True)
    post_operation_allowance_h: float = Field(gt=0.0, strict=True)


class _NextCallEnvironment(SimulatorModel):
    """Environmental evidence only; no terminal operating model is declared."""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: Slug
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    synthetic: Literal[True]
    profile_kind: Literal["next_call_environment"]
    locode: Locode
    schema_version: Literal[1]
    water_density_kg_m3: float = Field(gt=0, strict=True)
    berthing_side: Literal["port", "starboard"]
    berthing_side_assumption: str = Field(min_length=1)
    berth_depth_m: float | None = Field(default=None, gt=0.0, strict=True)
    berth_depth_assumption: str | None = None
    quay_crane: _CraneTimingParameters | None = None


# APPROXIMATION: missing parameter-file fields use explicit synthetic scenario
# defaults, not real terminal restrictions or surveyed operating conditions.
_DEFAULT_LOCODES = {
    "laem_chabang_like": "THLCH", "singapore_like": "SGSIN",
    "small_feeder": "THBKK", "us_west_coast": "USLAX",
}
# APPROXIMATION: every synthetic port uses 1025 kg/m3 seawater because no
# measured dock density is supplied. This affects displacement/draught and
# stability/strength calculations; replace with scenario-specific measured
# density before claiming validation for a real dock. Provenance below retains
# the explicit assumption label.
_ASSUMPTIONS = (
    "ASSUMPTION: LOCODE labels THLCH/SGSIN/THBKK/USLAX identify synthetic scenarios, not real terminals.",
    "ASSUMPTION Q9: row 1 in every block has reefer plugs; final block permits DG, including reefer DG; detailed DG segregation remains mandatory.",
    "ASSUMPTION: dock water density 1025 kg/m3 (seawater); no measured density is supplied.",
    "UNVERIFIED: berth/channel draught limit is unknown (None), not an assertion of unlimited draught.",
)

# Exact units from params/ports/README.md and the port parameter schema. No
# conversion is implicit: durations exposed by this module are seconds.
# Array indices are omitted so every service and gate-in bin follows the same
# contract. Unknown quantities fail closed rather than discarding their units.
_QUANTITY_UNITS = {
    "schema_version": "version",
    "water_density_kg_m3": "kg/m3",
    "max_crane_height_tiers": "tiers",
    "berth_depth_m": "m",
    "yard.berth_count": "berths",
    "yard.block_count": "blocks",
    "yard.blocks_per_berth": "blocks/berth",
    "yard.bays_per_block": "bays",
    "yard.rows_per_bay": "rows",
    "yard.max_stack_tiers": "tiers",
    "yard.initial_occupancy_fraction": "fraction",
    "equipment.stack_height_tiers": "tiers",
    "equipment.clearance_height_tiers": "tiers",
    "equipment.count": "machines",
    "quay_crane.max_on_deck_tier_count": "tiers",
    "quay_crane.count_per_berth": "cranes/berth",
    "quay_crane.gross_moves_per_hour": "moves/h/crane",
    "quay_crane.pre_operation_allowance_h": "h",
    "quay_crane.post_operation_allowance_h": "h",
    "traffic.transshipment_fraction": "fraction",
    "traffic.mean_teu_per_box": "TEU/box",
    "traffic.annual_reference_throughput_teu": "TEU/year",
    "services.calls_per_week": "calls/week",
    "services.mean_load_boxes": "boxes/call",
    "services.mean_discharge_boxes": "boxes/call",
    "services.call_volume_cv": "dimensionless",
    "services.arrival_jitter_s": "s",
    "gate_in.window_before_cutoff_s": "s",
    "gate_in.cutoff_before_arrival_s": "s",
    "gate_in.bins.start_before_cutoff_s": "s",
    "gate_in.bins.end_before_cutoff_s": "s",
    "gate_in.bins.share": "fraction",
    "dwell.import_mean_s": "s",
    "dwell.import_cv": "dimensionless",
    "dwell.transshipment_mean_s": "s",
    "dwell.transshipment_cv": "dimensionless",
    "placement.cluster_misplacement_probability": "fraction",
    "placement.retrieval_order_error_probability": "fraction",
    "legacy_height_limits.yard_max_tiers": "tiers",
    "legacy_height_limits.equipment_stack_tiers": "tiers",
    "legacy_height_limits.equipment_clearance_height_tiers": "tiers",
    "legacy_height_limits.quay_max_on_deck_tiers": "tiers",
}


def list_ports() -> list[str]:
    """Return operational and next-call-only profile IDs in stable order.

    Every ID loads through load_port; environment-only entries have no yard
    capacity and deliberately provide no simulator parameters.
    """
    return list_param_files("ports")


def _unwrap(node: Any, path: tuple[str | int, ...] = ()) -> Any:
    field = ".".join(part for part in path if isinstance(part, str))
    location = ".".join(map(str, path))
    expected = _QUANTITY_UNITS.get(field)
    if expected is not None:
        if not is_quantity(node):
            raise ValueError(f"{location}: expected quantity with unit {expected!r}")
        metric = quantity(node)
        if metric.unit != expected:
            raise ValueError(f"{location}: expected unit {expected!r}, got {metric.unit!r}")
        return metric.value
    if is_quantity(node):
        raise ValueError(f"{location}: no expected unit declared for quantity {node['unit']!r}")
    if isinstance(node, dict):
        return {key: _unwrap(value, (*path, key)) for key, value in node.items()}
    if isinstance(node, list):
        return [_unwrap(value, (*path, index)) for index, value in enumerate(node)]
    return node


def _input(archetype: str) -> bytes:
    if archetype not in list_ports():
        raise ValueError(f"unknown port archetype {archetype!r}; available: {', '.join(list_ports())}")
    return (params_dir("ports") / f"{archetype}.toml").read_bytes()


def _height_sources(document: dict) -> tuple[str, ...]:
    """Keep expert evidence and synthetic geometry labels in loaded profiles."""
    height = document.get("max_crane_height_tiers")
    if height is None:
        return (document["berthing_side_assumption"],)
    if "source" in height:
        source = height["source"]
        evidence = f"{source['document']}: expert {source['question']}, {source['read_date']}; {source['says']}"
    else:
        evidence = height["assumption"]
    return (evidence, document["berthing_side_assumption"])


def _depth_sources(document: dict, *, enabled: bool) -> tuple[str, ...]:
    """Keep the public depth anchor distinct from its synthetic draught cap."""
    entry = document.get("berth_depth_m")
    if not enabled or entry is None:
        return ()
    source = entry.get("source")
    evidence = (f"Berth depth context: {source['url']}; {source['says']}; read {source['read_date']}"
                if source is not None else entry["assumption"])
    return (evidence, document["berth_depth_assumption"])


def _environment_profile(archetype: str, document: dict, raw: dict, content: bytes,
                         *, freeze_enabled: bool) -> PortProfile:
    environment = _NextCallEnvironment.model_validate(raw)
    if environment.id != archetype:
        raise ValueError("port parameter id must match its filename")
    density = document["water_density_kg_m3"]
    assumption = density.get("assumption")
    if (not isinstance(assumption, str) or not assumption.strip().startswith("ASSUMPTION:")
            or "source" in density):
        raise ValueError("synthetic next-call density requires one labelled ASSUMPTION: provenance")
    # APPROXIMATION: these are declared synthetic next-call environments, not
    # measured berth densities or operational terminal models. In particular,
    # the VNSGN freshwater choice is conservative for draught (leader order);
    # it does not establish conservative stability or strength for every plan.
    # Unknown crane limits stay absent; density evidence never supplies an
    # operating envelope. Instance rejects current operational use explicitly.
    return PortProfile(
        locode=environment.locode, archetype=environment.id,
        profile_kind=environment.profile_kind,
        yard=Yard(id=f"{environment.id}.environment_only", blocks=[]), equipment=[],
        yard_max_tiers=None, max_crane_height_tiers=None,
        berthing_side=environment.berthing_side,
        quay_crane=None,
        water_density_kg_m3=environment.water_density_kg_m3,
        max_draught_m=environment.berth_depth_m if freeze_enabled else None,
        sources=[f"params/ports/{archetype}.toml sha256:{sha256(content).hexdigest()}",
                 assumption.strip(),
                 *_height_sources(document),
                 *_depth_sources(document, enabled=freeze_enabled),
                 *(["UNVERIFIED: berth/channel draught limit is unknown (None)."]
                   if not freeze_enabled or environment.berth_depth_m is None else []),
                 "Environment-only: no yard, crane capability or simulator model is declared; crane limits are unknown (None), and current operational Instance use is rejected."],
    )


@lru_cache(maxsize=32)
def _bundle(archetype: str, content: bytes, freeze_enabled: bool | None = None,
            catalogue_sha: str | None = None) -> tuple[PortProfile, SimulatorParameters | None]:
    if freeze_enabled is None:
        snapshot = catalogue_snapshot()
        freeze_enabled = snapshot.catalogue.activation.rule_freeze_v1
        catalogue_sha = snapshot.content_sha256
    document = load_toml_bytes(content)
    raw = _unwrap(document)
    if archetype not in _DEFAULT_LOCODES or "profile_kind" in raw:
        return _environment_profile(archetype, document, raw, content, freeze_enabled=freeze_enabled), None
    if raw["id"] != archetype:
        raise ValueError("port parameter id must match its filename")
    y, e, q = raw["yard"], raw["equipment"], raw["quay_crane"]
    height = raw["max_crane_height_tiers"]
    if not freeze_enabled and "legacy_height_limits" in raw:
        legacy = raw["legacy_height_limits"]
        # A8 withdraws yard-height staging. Only the quay's legacy cap changes
        # at activation; yard equipment and its descriptive metadata stay put.
        q["max_on_deck_tier_count"] = legacy["quay_max_on_deck_tiers"]
        height = None
    if y["block_count"] != y["berth_count"] * y["blocks_per_berth"]:
        raise ValueError("block count must equal berths times blocks per berth")
    sources = (f"params/ports/{archetype}.toml sha256:{sha256(content).hexdigest()}",
               *[note for note in _ASSUMPTIONS if not
                 (freeze_enabled and raw.get("berth_depth_m") is not None and note.startswith("UNVERIFIED: berth/channel"))],
               *_height_sources(document), y["truck_lane_side_assumption"],
               *_depth_sources(document, enabled=freeze_enabled),
               f"Rule freeze v1: {'on' if freeze_enabled else 'off'}; catalogue {catalogue_sha}",
               *([raw["port_rules_source"]] if freeze_enabled and "port_rules_source" in raw else []))
    kind = "straddle_carrier" if e["type"] == "straddle" else e["type"]
    equipment = Equipment(id=kind.lower(), kind=kind,
                          stack_tiers=e["stack_height_tiers"], count=e["count"])
    blocks = [YardBlock(id=f"B{i + 1:03d}", n_bays=y["bays_per_block"],
                        n_rows=y["rows_per_bay"],
                        max_tiers=min(y["max_stack_tiers"], equipment.stack_tiers),
                        truck_lane_side=y["truck_lane_side"],
                        equipment_id=equipment.id, reefer_rows=[1],
                        dg_block=i == y["block_count"] - 1)
              for i in range(y["block_count"])]
    profile = PortProfile(
        locode=_DEFAULT_LOCODES[archetype], archetype=archetype,
        yard=Yard(id=f"{archetype}.berth_group", blocks=blocks), equipment=[equipment],
        yard_max_tiers=y["max_stack_tiers"],
        max_crane_height_tiers=height, berthing_side=raw["berthing_side"],
        quay_crane=QuayCraneSpec(count=q["count_per_berth"],
                                max_on_deck_tiers=q["max_on_deck_tier_count"],
                                **({field: q.get(field) for field in (
                                    "gross_moves_per_hour", "pre_operation_allowance_h", "post_operation_allowance_h")}
                                   if freeze_enabled else {})),
        port_rules=[PortRuleRef(rule_id="port.e3.yard_height", params={"max_tiers": y["max_stack_tiers"]}),
                    # The catalogue's metre-based height parameter cannot be inferred
                    # from tier counts; the model's explicit tier limit is authoritative.
                    PortRuleRef(rule_id="port.e3.quay_height"),
                    *[PortRuleRef.model_validate(rule) for rule in raw.get("port_rules", []) if freeze_enabled]],
        water_density_kg_m3=1025,
        max_draught_m=raw.get("berth_depth_m") if freeze_enabled else None, sources=list(sources),
    )
    simulator = SimulatorParameters(
        archetype=archetype, berth_count=y["berth_count"], blocks_per_berth=y["blocks_per_berth"],
        initial_occupancy_fraction=y["initial_occupancy_fraction"],
        equipment_stacking_notation=e["stacking_notation"],
        equipment_clearance_height_tiers=e["clearance_height_tiers"],
        services=raw["services"], traffic=raw["traffic"], gate_in=raw["gate_in"],
        dwell=raw["dwell"], placement=raw["placement"], simulation=raw["simulation"], sources=sources,
    )
    return profile, simulator


def load_port(archetype: str) -> PortProfile:
    """Load a fresh profile with an empty static yard, retaining file hash provenance.

    Environment-only profiles declare arrival density and unknown crane limits.
    Layouts have no cargo inventory. Use ``empty_yard_state(profile)`` for the
    separate state contract. Parameter bytes, effective catalogue SHA and freeze
    activation key the parse cache, so same-process switches cannot reuse a stale
    operational profile.
    """
    snapshot = catalogue_snapshot()
    return _bundle(archetype, _input(archetype), snapshot.catalogue.activation.rule_freeze_v1,
                   snapshot.content_sha256)[0].model_copy(deep=True)


def load_simulator_params(archetype: str) -> SimulatorParameters:
    """Read typed immutable simulator inputs, sharing load_port's parsed cache."""
    snapshot = catalogue_snapshot()
    simulator = _bundle(archetype, _input(archetype), snapshot.catalogue.activation.rule_freeze_v1,
                        snapshot.content_sha256)[1]
    if simulator is None:
        raise ValueError(f"{archetype!r} is environment-only; no simulator parameters are declared")
    return simulator


def resolve_port_call_profile(call: PortCall, *, current_port: PortProfile | None = None) -> PortProfile | None:
    """Resolve explicit archetypes, then legacy current-profile/LOCODE lookup.

    Unknown LOCODEs return None; ambiguous matches and inconsistent explicit
    archetypes fail closed. Density-only matches retain their unknown limits.
    This uses the same lookup order as the voyage arrival profile lookup.
    """
    if call.archetype is not None:
        profile = load_port(call.archetype)
        if profile.locode != call.locode:
            raise ValueError("archetype profile LOCODE must match the call LOCODE")
        return profile
    if current_port is not None and current_port.locode == call.locode:
        return current_port.model_copy(deep=True)
    matches = [profile for archetype in list_ports()
               if (profile := load_port(archetype)).locode == call.locode]
    if len(matches) > 1:
        raise ValueError(f"ambiguous synthetic port profile for call {call.locode}; declare archetype")
    return matches[0] if matches else None


def crane_height_at_call(call: PortCall, *, current_port: PortProfile | None = None) -> int | None:
    """Return the minimum declared call/port cap; None means unknown.

    Environment-only profiles never provide crane rules, even if a legacy call
    carried a positive placeholder. Unknown profiles can retain an explicit cap.
    """
    profile = resolve_port_call_profile(call, current_port=current_port)
    if profile is not None and profile.profile_kind == "next_call_environment":
        return None
    if profile is None or profile.quay_crane is None:
        return call.max_on_deck_tiers
    port_height = profile.max_crane_height_tiers or profile.quay_crane.max_on_deck_tiers
    return min(call.max_on_deck_tiers, port_height) if call.max_on_deck_tiers is not None else port_height


def crane_limits_known_at_call(call: PortCall, *, current_port: PortProfile | None = None) -> bool:
    """Whether the call has a declared crane cap, rather than unknown limits."""
    return crane_height_at_call(call, current_port=current_port) is not None


def empty_yard_state(port: PortProfile) -> YardState:
    """Create an inventory-free, time-zero state for this layout."""
    return YardState(yard_id=port.yard.id)


def effective_yard_tiers(port: PortProfile, block: YardBlock | str) -> int:
    """Resolve yard storage independently of quay H; reject unknown references."""
    block_id = block if isinstance(block, str) else block.id
    selected = next((candidate for candidate in port.yard.blocks if candidate.id == block_id), None)
    if selected is None:
        raise ValueError(f"unknown yard block {block_id!r}")
    machine = next((machine for machine in port.equipment if machine.id == selected.equipment_id), None)
    if machine is None:
        raise ValueError(f"unknown equipment {selected.equipment_id!r}")
    return min(selected.max_tiers, port.yard_max_tiers, machine.stack_tiers)


def yard_blocking_tiers(port: PortProfile, block: YardBlock | str) -> int:
    """Height at which a yard stack blocks crossing (D62 amendment/A8).

    Storage and crossing share the effective yard limit. Quay-crane H and
    informational equipment-clearance metadata do not enter this threshold.
    """
    return effective_yard_tiers(port, block)


def scaled_yard(port: PortProfile, *, blocks: int, bays: int, rows: int) -> PortProfile:
    """Return an independent profile with a smaller empty yard, preserving limits.

    Keep the first blocks, but retain a DG-capable block when the original has
    one. Clip reefer rows to the smaller width; since defaults power row 1, even
    a one-row yard retains plugs. Dimensions may only shrink the original yard.
    Equipment counts and berth settings remain scenario capabilities, not scaled
    traffic assumptions; callers select simulation workload independently.
    """
    for name, value in (("blocks", blocks), ("bays", bays), ("rows", rows)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if blocks > len(port.yard.blocks):
        raise ValueError("scaled block count cannot exceed the source yard")
    chosen = list(port.yard.blocks[:blocks])
    if not any(b.dg_block for b in chosen) and any(b.dg_block for b in port.yard.blocks):
        chosen[-1] = next(b for b in port.yard.blocks if b.dg_block)
    reduced = []
    for block in chosen:
        if bays > block.n_bays or rows > block.n_rows:
            raise ValueError("scaled bays/rows cannot exceed the source blocks")
        reduced.append(block.model_copy(update={
            "n_bays": bays, "n_rows": rows, "reefer_rows": [r for r in block.reefer_rows if r <= rows],
            "max_tiers": effective_yard_tiers(port, block),
        }, deep=True))
    result = port.model_copy(deep=True)
    result.yard = Yard(id=f"{port.yard.id}.scaled_{blocks}_{bays}_{rows}", blocks=reduced)
    return PortProfile.model_validate(result.model_dump())
