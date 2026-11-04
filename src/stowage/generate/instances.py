"""Stage-A ship problems whose loading-start yard is produced by event simulation.

A necessary condition is a cheap impossibility test: passing it does not prove
that a loading plan exists. Cargo is resampled when such a test fails; every
rejection is counted rather than quietly changing a box. The full checker still
owns departure feasibility. Sources: params/cargo/README.md, params/ports/README.md
and the event semantics documented in stowage.yard.simulator.

APPROXIMATION: a two-call export window represents terminal activity, rather
than a calibrated steady-state terminal. A later vessel's dry empty bookings
interleave with the target's arrivals. This exposes placement noise and retrieval
priorities without inventing unsupported transshipment discharges. No occupancy
calibration is claimed. Tiny uses the same six dry20 decision cells as
the current random-yard generator, and all rungs use the fleet owner's
actual decision slices with whole-hull fixed-load accounting. Dense full-feeder profiles explicitly condition
equipment to dry20 while preserving loaded-box DG probability and fail-closed
provision screening; physical cell counts remain unchanged. Tiny defaults to
its source dry20 loaded/empty mix and DG prevalence. Demand uses actual TEU and
per-length slot budgets, following random_yard.capacity_load_size. Depth-0 C1
bounds screen the completed snapshot; activated medium also uses the necessary
C2 relaxation. Action search remains off.

APPROXIMATION: after each booking draw, recondition_slice reserves its actual
load-list mass in the fleet owner's synthetic C3 background. Yard blockers are
excluded. Live consumable tanks start at 97%, following fleet_conditions.py;
loaded full exports use random_export's bounded ballast initializer aimed at
93% design displacement. Slice booking ballast follows the vessel builder's
reserved low-ballast recipe. The separate D58 empty-background witness may use
more ballast for its own acceptance check and never changes booking tanks.
Consumables are never added to fixed loads. With activation, emitted tanks are
the gate's certified arrival fills (berth trim and HEEL prefill); the audit keeps
the booked and witness vectors separately. Plans still require the ballast
finisher and full checker.

APPROXIMATION: individually impossible DG shipments identified by C1a are redrawn
from the cargo distribution while other bookings are retained. This sequential
conditioning is counted separately from whole-candidate rejection; it does not
waive DG facts or certify feasibility. C1a is Hall's matching condition (Hall,
1935), as documented in stowage.feasibility.capacity.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field, replace
import hashlib
import json
import math
from random import Random
from time import perf_counter

from stowage.conventions import canonical_vessel_id
from stowage.frozen import current_provenance
from stowage.generate.background_acceptance import accept_background_candidate
from stowage.generate.cargo import effective_cargo_generator_version, generate_load_list, UnsupportedCargoError
from stowage.generate.random_yard import (
    SIZE_LADDER, capacity_load_size, completion_failures, _decision_region, _tiny_region,
)
from stowage.generate.instances_screen import screen_load_list, count_unresolved_provisions
from stowage.generate.random_export import booking_tank_masses, export_rejections, full_export_policy
from stowage.generate.instances_voyage import build_voyage_spec
from stowage.model import Instance
from stowage.model.port import PortCall, Rotation
from stowage.model.ship import ShipState
from stowage.model.yard import YardState
from stowage.params import load_param_file
from stowage.rules.catalogue import rule_freeze_v1_enabled
from stowage.ports.profiles import load_port, load_simulator_params, scaled_yard
from stowage.vessel.fleet import load_vessel
from stowage.vessel.fleet_slices import recondition_slice
from stowage.vessel.fleet_conditions import departure_tank_masses
from stowage.versions import SIMULATED_INSTANCES_VERSION as INSTANCES_VERSION
from stowage.yard.simulator import effective_yard_simulator_version, ShipCall, simulate_port
from stowage.yard.dig_out import YardCapacityError

_STREAM_NAMESPACE = "simulated-instance-draw-v1"


def _full_vessel(name):
    """Let the vessel builder's content cache validate sources on every call."""
    return load_vessel(name, with_hull=True)


def _stream_seed(identity: str, purpose: str, *, attempt: int = 0, index: int | None = None) -> int:
    """Address deterministic random streams without arithmetic retry strides.

    Canonical JSON keeps tuple boundaries unambiguous; purpose separates cargo,
    gate, simulation and shipment draws even within one candidate. Retrying with
    a larger limit preserves the same candidate prefix.
    """
    payload = json.dumps([_STREAM_NAMESPACE, identity, purpose, attempt, index],
                         separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return int.from_bytes(hashlib.sha256(payload).digest(), "big")


class _SnapshotOnlyRetrieval:
    """The target's loading plan belongs to solvers, after this snapshot.

    The public simulator records the snapshot before invoking retrieval. Its
    replaceable hook can defer all target retrievals; no later call starts in
    this run. Thus no greedy ship-loading work affects the requested input.
    """
    def retrieve(self, instance, state, container_id):
        raise YardCapacityError("snapshot-only run: target retrieval deferred to planner")


@dataclass
class GenerationReport:
    """Candidate-level audit; reason rates may overlap and need not sum to one."""
    attempts: int = 0
    rejections: dict[str, int] = field(default_factory=dict)
    conditioning: list[str] = field(default_factory=list)
    simulation_statistics: dict[str, float] = field(default_factory=dict)
    congestion_mixing: dict = field(default_factory=dict)
    timings_s: dict[str, float] = field(default_factory=dict)
    accepted: bool = False
    provision_rejections: int = 0
    balancing_rejections: int = 0
    capacity_teu: int = 0
    teu_budget: int = 0
    length_slot_budgets: dict[int, int] = field(default_factory=dict)
    requested_targets: int = 0
    realised_teu: float = 0.0
    completion_checks: int = 0
    completion_rejections: dict[str, int] = field(default_factory=dict)
    c2_screened: bool = False
    c2_requested: bool = False
    c2_checks: int = 0
    c2_direction: str | None = None
    c2_errors: dict[str, int] = field(default_factory=dict)
    shipment_redraws: int = 0
    full_export: dict[str, float | bool] = field(default_factory=dict)
    booking_reference: dict = field(default_factory=dict)
    booking_utilisation: dict[str, float] = field(default_factory=dict)
    booking_slack: float | None = None
    booking_mass_fraction: float | None = None
    background_checks: int = 0
    background_cache_hits: int = 0
    background_seconds: float = 0.0
    background_rejections: dict[str, int] = field(default_factory=dict)
    background_unknowns: dict[str, int] = field(default_factory=dict)
    background_last: dict = field(default_factory=dict)
    requested_blocking_ratio: float = 0.0
    realised_blocking_ratio: float = 0.0
    blocked_targets: int = 0
    targets: int = 0
    cargo_draws: dict = field(default_factory=dict)
    d62_crossing: dict = field(default_factory=dict)
    producer_report: dict = field(default_factory=dict)

    @property
    def screening_rate(self):
        return (self.attempts - int(self.accepted)) / self.attempts if self.attempts else 0.0


def _empty_params(params):
    result = deepcopy(params)
    result["equipment"] = [next(e for e in result["equipment"] if e["id"] == "dry_20")]
    result["equipment"][0]["empty_share"]["value"] = 1.0
    return result


def _cargo_snapshot(draws):
    snapshot = draws.snapshot()
    snapshot["emission_definition"] = (
        "emitted counts raw cargo-generator returns, before simulated-instance screening; not admitted instances")
    return snapshot


def size_synthetic_yard(port_name, *, blocks, bays, rows):
    """Size an independent synthetic footprint while preserving port restrictions.

    Narrow ports keep their source bay/row dimensions and gain more blocks to
    preserve the requested ground area. The synthetic footprint is not a claim
    about actual terminal capacity. Source: model/yard.py storage conventions.
    """
    source_port = load_port(port_name).model_copy(deep=True)
    yard_bays = min(bays, min(b.n_bays for b in source_port.yard.blocks))
    yard_rows = min(rows, min(b.n_rows for b in source_port.yard.blocks))
    yard_blocks = math.ceil(blocks * bays * rows / (yard_bays * yard_rows))
    originals = list(source_port.yard.blocks)
    ids = {b.id for b in originals}
    for i in range(max(0, yard_blocks - len(originals))):
        # APPROXIMATION: repeat synthetic geometry with the original storage,
        # DG and equipment restrictions; distinct origins avoid overlap.
        block_id = f"X{i:03d}"
        if block_id in ids:
            raise ValueError("synthetic yard block id collision")
        source_port.yard.blocks.append(originals[-1].model_copy(update={
            "id": block_id, "x_m": originals[-1].x_m + (i + 1) * (bays * 6.058 + 20.0),
        }, deep=True))
    return scaled_yard(source_port, blocks=yard_blocks, bays=yard_bays, rows=yard_rows)


def generate_simulated_instance(
    size: str = "tiny", *, seed: int, noise: float = 0.2,
    vessel_name: str = "feeder_500", port_name: str = "laem_chabang_like",
    report: GenerationReport | None = None, max_attempts: int = 200, without_dg: bool = False,
    n_boxes: int | None = None,
    dense_20ft: bool = False,
    tiny_dg_enabled: bool = True,
    cargo_profile: str = "feeder_export",
    instance_id: str | None = None,
    congestion_mix_share: float = 0.25, booking_slack: float | None = None,
    booking_mass_fraction: float | None = None,
) -> Instance:
    """Draw a screened booking, gate it through the simulator, retain its snapshot.

    Seeds address independent candidate substreams, so a shard never depends on
    earlier shards or process scheduling. Whole-list rejection conditions the
    sampled distribution; sequential DG redraws also condition it. The audit
    records both and the exact screen reasons.
    The simulator may fail to place all booked exports: these candidates are
    rejected under the yard capacity rule, never shortened to fit.

    ``n_boxes`` changes the target volume without changing the ship decision
    region. Explicit requests override the default fill fraction but must fit
    physical TEU and per-length budgets; they are never silently reduced.
    ``congestion_mix_share`` explicitly selects the synthetic D72 mixing share
    under rule-freeze-v1; nondefault settings require activation. The requested
    share is part of the draw identity and simulator evidence records the
    realised share separately.
    """
    freeze = rule_freeze_v1_enabled()
    vessel_name = canonical_vessel_id(vessel_name)
    if cargo_profile not in {"feeder_export", "dg_stress"}:
        raise ValueError("cargo_profile must be feeder_export or dg_stress")
    if cargo_profile == "dg_stress" and not freeze:
        raise ValueError("dg_stress requires rule-freeze-v1 activation")
    if cargo_profile == "dg_stress" and (without_dg or not tiny_dg_enabled):
        raise ValueError("dg_stress cannot be combined with no-DG or empty-tiny conditioning")
    if size not in SIZE_LADDER:
        raise ValueError(f"unknown size {size!r}")
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    if instance_id is not None and (not isinstance(instance_id, str) or not instance_id.strip()):
        raise ValueError("instance_id must be a nonblank string or None")
    if instance_id is not None and not freeze:
        raise ValueError("explicit instance_id requires rule-freeze-v1 activation")
    target_instance_id = instance_id or f"sim.{size}.{seed}"
    background_instance_id = f"sim-background.{size}.{seed}"
    if target_instance_id == background_instance_id:
        raise ValueError("instance_id must differ from the background call identity")
    if not math.isfinite(noise) or not 0 <= noise <= 1:
        raise ValueError("noise must be finite and in [0, 1]")
    if (type(congestion_mix_share) not in (int, float)
            or not math.isfinite(congestion_mix_share) or not 0 <= congestion_mix_share <= 1):
        raise ValueError("congestion_mix_share must be finite and in [0, 1]")
    if not freeze and congestion_mix_share != 0.25:
        raise ValueError("nondefault congestion_mix_share requires rule-freeze-v1 activation")
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    if type(without_dg) is not bool:
        raise ValueError("without_dg must be a boolean")
    if n_boxes is not None and (type(n_boxes) is not int or n_boxes < 1):
        raise ValueError("n_boxes must be a positive integer or None")
    if type(dense_20ft) is not bool:
        raise ValueError("dense_20ft must be a boolean")
    if type(tiny_dg_enabled) is not bool:
        raise ValueError("tiny_dg_enabled must be a boolean")
    if booking_slack is not None:
        from stowage.generate.random_yard import validate_booking_slack
        validate_booking_slack(booking_slack)
    if booking_mass_fraction is not None:
        from stowage.generate.random_yard import validate_booking_mass_fraction
        validate_booking_mass_fraction(booking_mass_fraction)
    # Include demand conditioning as well as yard noise: a held-out noise level
    # must not share the route/cargo/gate draw with the training specification.
    # max_attempts is a work limit, not a different underlying draw identity.
    controls = dict(size=size, seed=seed, noise=repr(float(noise)),
                    vessel=vessel_name, port=port_name, n_boxes=n_boxes,
                    without_dg=without_dg, dense_20ft=dense_20ft,
                    tiny_dg_enabled=tiny_dg_enabled)
    if booking_slack is not None:
        controls["booking_slack"] = float(booking_slack)
    if booking_mass_fraction is not None:
        controls["booking_mass_fraction"] = float(booking_mass_fraction)
    if freeze:
        controls["cargo_profile"] = cargo_profile
        controls["congestion_mix_share"] = repr(float(congestion_mix_share))
    draw_identity = hashlib.sha256(json.dumps(
        [_STREAM_NAMESPACE, controls], sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    start = perf_counter()
    audit = report if report is not None else GenerationReport()
    audit.__dict__.update(GenerationReport().__dict__)
    audit.c2_requested = freeze and size == "medium"
    audit.conditioning = ["APPROXIMATION: two overlapping export calls start from an empty yard; background dry20 empty bookings = half target volume",
                          "APPROXIMATION: C3 background reconditioned on actual booking mass; live consumables 97%, loaded full exports use bounded design-band ballast initialization; slice booking ballast uses the builder recipe; a separate empty-background witness passes the full-checker acceptance gate without changing booking tanks",
                          "Whole-list rejection sampling by necessary conditions; passing is not a feasibility certificate"]
    rung = SIZE_LADDER[size]
    parent = _full_vessel(vessel_name)
    vessel = _decision_region(parent, size)
    if size == "tiny":
        vessel = _tiny_region(parent)
        if tiny_dg_enabled:
            audit.conditioning.append("APPROXIMATION: tiny conditions equipment to dry20 using six decision cells; original loaded/empty mix and eligible DG probability retained")
        else:
            audit.conditioning.append("APPROXIMATION: tiny conditions demand to empty dry20 targets (ineligible for DG sampling), using six decision cells")
    audit.timings_s["vessel_s"] = perf_counter() - start
    port = load_port(port_name)
    parameters = load_simulator_params(port_name)
    draws = None
    if freeze:
        from stowage.generate import cargo
        from stowage.generate.random_cargo import RandomCargoDraws

        params = cargo.dg_stress_profile() if cargo_profile == "dg_stress" else deepcopy(cargo._params("feeder_export"))
        draws = RandomCargoDraws()
        if cargo_profile == "dg_stress":
            audit.conditioning.append("APPROXIMATION: D70 separate DG-stress demand profile; not the main feeder-export mixture")
    else:
        params = deepcopy(load_param_file("cargo", "feeder_export"))
    dry20_with_dg = size == "tiny" and tiny_dg_enabled
    if dense_20ft or dry20_with_dg:
        # APPROXIMATION: dense feeder profiles condition equipment to dry20,
        # preserving its loaded/empty mix and eligible loaded-box DG prevalence.
        params["equipment"] = [next(e for e in params["equipment"] if e["id"] == "dry_20")]
        params["equipment"][0]["share"]["value"] = 1.0
        profile_name = "dry20_with_dg" if dry20_with_dg else "dense_20ft"
        audit.conditioning.append(f"APPROXIMATION: {profile_name} equipment profile; dry20 only, original loaded/empty mix and eligible DG probability retained")
    rng = Random(_stream_seed(draw_identity, "route"))
    route = deepcopy(rng.choices(params["rotations"], weights=[r["share"]["value"] for r in params["rotations"]], k=1)[0])
    route["port_calls"][0] = port.locode
    # A regional Thai-context rotation has 3--5 calls, including a return call.
    if not 3 <= len(route["port_calls"]) <= 5:
        raise ValueError("cargo profile must supply 3--5 rotation calls")
    rotation = Rotation(service=route["id"], calls=[PortCall(locode=p, index=i) for i, p in enumerate(route["port_calls"])])
    params["rotations"] = [route]
    if freeze:
        rotation, _ = cargo._route(rotation, params, rng)
    if without_dg:
        # APPROXIMATION: an explicit synthetic non-DG service replaces the raw
        # DG prevalence. This is demand conditioning, never permission to carry
        # unsupported DG or a change to any physical/checker limit.
        params["dg"]["share_of_eligible_loaded_boxes"]["value"] = 0.0
        params["dg"]["implied_share_of_all_boxes"]["value"] = 0.0
        audit.conditioning.append("APPROXIMATION: explicit no_dg service; DG prevalence zero by demand conditioning; raw full-mix DG demand remains available by default")
    target_params = _empty_params(params) if size == "tiny" and not tiny_dg_enabled else params
    sizing_rung = rung if n_boxes is None else replace(rung, boxes=n_boxes, fill_fraction=1.0)
    if n_boxes is None:
        # D36 transfers the class's full-export distribution to the actual
        # decision region; explicit quantities remain diagnostic requests.
        target_count, teu_budget, length_budgets = capacity_load_size(
            vessel, sizing_rung, target_params, full_vessel=parent)
    else:
        target_count, teu_budget, length_budgets = capacity_load_size(vessel, sizing_rung, target_params)
    if booking_slack is not None:
        from stowage.generate.random_yard import apply_booking_slack
        target_count, teu_budget, length_budgets = apply_booking_slack(
            vessel, target_params, (target_count, teu_budget, length_budgets), booking_slack)
        audit.booking_slack = float(booking_slack)
        audit.conditioning.append("ASSUMPTION: feeders may sail below full capacity; booking_slack caps synthetic demand against geometric decision-cell TEU, not checker-legal capacity")
    if booking_mass_fraction is not None:
        from stowage.generate.random_yard import apply_booking_mass_fraction
        target_count, teu_budget, length_budgets = apply_booking_mass_fraction(
            vessel, target_params, (target_count, teu_budget, length_budgets), booking_mass_fraction,
            full_vessel=parent)
        audit.booking_mass_fraction = float(booking_mass_fraction)
        audit.conditioning.append("ASSUMPTION: feeders may sail lighter; booking_mass_fraction conditions booking count and realised mass against the scaled full-export reference budget, not legal loading capacity")
    if n_boxes is not None and target_count != n_boxes:
        raise ValueError("expert.physical.slot_occupancy: explicit n_boxes exceeds capacity-derived demand budget")
    audit.capacity_teu = len(vessel.cells)
    audit.teu_budget, audit.length_slot_budgets = teu_budget, length_budgets
    audit.requested_targets = target_count
    booking_policy = "D36_scaled_full_export_v1" if n_boxes is None else "explicit_capacity_override"
    reference = full_export_policy(parent, target_params)
    if reference is not None:
        audit.booking_reference = {"policy": booking_policy,
            "full_capacity_teu": reference.capacity_teu,
            "full_target_count": reference.target_count,
            "requested_teu_fraction": reference.target_count * reference.mean_teu / reference.capacity_teu,
            "region_capacity_teu": len(vessel.cells)}
    audit.conditioning.append(f"{booking_policy} demand: {target_count} targets, {teu_budget} TEU budget; C1 depth-0 on, C2 {'necessary (D49)' if audit.c2_requested else 'off'}")
    # The rung ceiling bounds workload, not yard demand: dense exports may
    # need many more ground stacks than the original four-block full preset.
    port = size_synthetic_yard(port_name, blocks=max(
        rung.yard_blocks, math.ceil(rung.yard_blocks * target_count / rung.boxes),
        math.ceil((target_count + 2) / ((rung.yard_bays // 2) * rung.yard_rows))),
                               bays=rung.yard_bays, rows=rung.yard_rows)
    if len(port.yard.blocks) > len(load_port(port_name).yard.blocks):
        audit.conditioning.append("APPROXIMATION: demand-sized synthetic yard repeats source port block geometry and limits; not a real terminal footprint")
    if n_boxes is not None:
        audit.conditioning.append(f"APPROXIMATION: explicit target volume {target_count}; physical capacity unchanged")
    selected_vessel = vessel
    provenance = current_provenance(seed=seed, created_by="generate.instances")
    provenance.versions.update(instances=INSTANCES_VERSION, cargo_generator=effective_cargo_generator_version(), yard_simulator=effective_yard_simulator_version())
    empty_tiny = size == "tiny" and not tiny_dg_enabled
    cargo_profile = "empty_dry20" if empty_tiny else "no_dg" if without_dg else cargo_profile
    equipment_profile = ("empty_dry20" if empty_tiny else "dry20_with_dg" if dry20_with_dg
                         else "dense_20ft" if dense_20ft else "full_mix")
    provenance.versions.update(cargo_profile=cargo_profile, equipment_profile=equipment_profile,
                               generation_identity=draw_identity, random_streams=_STREAM_NAMESPACE)
    family = {"vessel": vessel_name, "port": port_name, "noise": repr(float(noise)), "size": size,
              "seed": str(seed), "generator": INSTANCES_VERSION, "cargo_profile": cargo_profile,
              "n_boxes": str(target_count), "equipment_profile": equipment_profile,
              "requested_n_boxes": "auto" if n_boxes is None else str(n_boxes),
              "without_dg": str(without_dg).lower(), "dense_20ft": str(dense_20ft).lower(),
              "tiny_dg_enabled": str(tiny_dg_enabled).lower(), "generation_identity": draw_identity,
              "random_streams": _STREAM_NAMESPACE, "booking_policy": booking_policy,
              "region_capacity_teu": str(len(vessel.cells))}
    if booking_slack is not None:
        family.update(booking_slack=repr(float(booking_slack)),
                      booking_capacity_basis="decision_cells_geometric_teu")
    if booking_mass_fraction is not None:
        from stowage.generate.random_yard import BOOKING_MASS_BASIS
        family.update(booking_mass_fraction=repr(float(booking_mass_fraction)),
                      booking_mass_basis=BOOKING_MASS_BASIS)
    if freeze:
        family["generator_kind"] = "simulated"
        family["congestion_mix_share"] = repr(float(congestion_mix_share))
        provenance.versions["generator_kind"] = "simulated"
        provenance.parents = sorted(set(provenance.parents) | {
            "APPROXIMATION: foreign yard boxes are non-DG; yard DG segregation not modelled"})
    if reference is not None:
        family["booking_requested_teu_fraction"] = repr(audit.booking_reference["requested_teu_fraction"])
    # Resolve actual immediate-next-call environment evidence before sampling
    # candidates. Missing route metadata is not repaired by changing the route.
    # The vessel builder alone supplies the D50 grouping/draw-down policy.
    voyages = {cid: build_voyage_spec(instance_id=cid, rotation=rotation, port=port,
                                    call_index=0, provenance=provenance)
               for cid in (target_instance_id, background_instance_id)}
    rejected = Counter()
    simulation_s = screening_s = 0.0
    redraw = None
    for attempt in range(max_attempts):
        audit.attempts += 1
        candidate_seed = _stream_seed(draw_identity, "cargo", attempt=attempt)
        screen_start = perf_counter()
        try:
            if redraw is None:
                boxes = (draws.draw(rotation, 0, target_count, seed=candidate_seed, params=target_params)
                         if freeze else generate_load_list(rotation, 0, target_count, seed=candidate_seed, params=target_params))
            else:
                boxes, positions = redraw
                boxes = list(boxes)
                for index in positions:
                    draw_seed = _stream_seed(draw_identity, "shipment", attempt=attempt, index=index)
                    replacement = (draws.draw(rotation, 0, 1, seed=draw_seed,
                        id_offset=index, params=target_params, kind="shipment_redraw")[0] if freeze else
                        generate_load_list(rotation, 0, 1, seed=draw_seed,
                                           id_offset=index, params=target_params)[0])
                    if freeze:
                        draws.replace_id(replacement.id, boxes[index].id)
                    boxes[index] = replacement.model_copy(update={"id": boxes[index].id})
                    audit.shipment_redraws += 1
            redraw = None
        except UnsupportedCargoError:
            redraw = None
            rejected["imdg.stowage.3_2"] += 1
            screening_s += perf_counter() - screen_start
            audit.rejections = dict(sorted(rejected.items()))
            continue
        finally:
            if freeze:
                audit.cargo_draws = _cargo_snapshot(draws)
        if booking_mass_fraction is not None:
            from stowage.generate.random_yard import regional_booking_mass_budget
            mass = sum(box.gross_mass_kg for box in boxes)
            if mass > booking_mass_fraction * regional_booking_mass_budget(selected_vessel, reference, mass):
                rejected["booking.mass_fraction"] += 1
                screening_s += perf_counter() - screen_start
                audit.rejections = dict(sorted(rejected.items()))
                continue
        # Rebuild from the final selected geometry on every candidate, including
        # shipment redraws. Only planned cargo contributes; yard blockers do not.
        try:
            vessel = recondition_slice(selected_vessel,
                                       expected_load_mass_kg=sum(box.gross_mass_kg for box in boxes),
                                       seed=_stream_seed(draw_identity, "slice", attempt=attempt))
        except ValueError as error:
            if not str(error).startswith("cannot balance slice background within physical cargo capacities"):
                raise
            # Reject this booking without changing the balancing physics or
            # accepting a partial background. Other construction errors surface.
            audit.balancing_rejections += 1
            rejected["expert.physical.slot_occupancy"] += 1
            audit.rejections = dict(sorted(rejected.items()))
            screening_s += perf_counter() - screen_start
            continue
        export = full_export_policy(vessel, target_params)
        initial_tanks = departure_tank_masses(vessel)
        tank_masses = (booking_tank_masses(vessel, boxes, target_params)
                       if export is not None and export.is_export_count(len(boxes))
                       else initial_tanks)
        background_probe = Instance(id=background_instance_id, stage="A", size_class=size,
            seed=seed, vessel=vessel, rotation=rotation, port=port, call_index=0,
            yard=YardState(yard_id=port.yard.id),
            onboard=ShipState(vessel_id=vessel.id, tank_masses_kg=tank_masses),
            containers=[], load_list=[], provenance=provenance,
            voyage=voyages[background_instance_id])
        if freeze:
            # Bind final identity/geometry before cheap cargo screens, then
            # retain the bound declarations on the completed yard snapshot.
            draws.bind(background_probe.model_copy(update={
                "id": target_instance_id, "containers": boxes,
                "voyage": voyages[target_instance_id]}, deep=True))
        audit.provision_rejections += int(count_unresolved_provisions(boxes) > 0)
        reasons = screen_load_list(vessel, boxes, port=port, tank_masses_kg=tank_masses)
        reasons.extend(export_rejections(vessel, boxes, target_params,
                                        full_vessel=parent if n_boxes is None else None,
                                        **({"minimum_utilisation": False} if booking_slack is not None or booking_mass_fraction is not None else {})))
        if sum(box.length_ft / 20 for box in boxes) > teu_budget:
            reasons.append("expert.physical.slot_occupancy")
        if any(sum(box.length_ft == length for box in boxes) > limit
               for length, limit in length_budgets.items()):
            reasons.append("expert.physical.size_fit")
        screening_s += perf_counter() - screen_start
        if reasons:
            rejected.update(set(reasons))
            audit.rejections = dict(sorted(rejected.items()))
            continue
        background_options = dict(seed=_stream_seed(draw_identity, "background", attempt=attempt),
                                  params=_empty_params(params), id_offset=target_count)
        try:
            background = (draws.draw(rotation, 0, max(2, target_count // 2), **background_options) if freeze else
                          generate_load_list(rotation, 0, max(2, target_count // 2), **background_options))
        finally:
            if freeze:
                audit.cargo_draws = _cargo_snapshot(draws)
        teu = sum(box.length_ft / 20 for box in boxes)
        mass = sum(box.gross_mass_kg for box in boxes)
        utilisation = {"teu_fraction": teu / len(vessel.cells)}
        if reference is not None:
            scale = len(vessel.cells) / reference.capacity_teu
            utilisation["cargo_mass_fraction"] = mass / (scale * reference.cargo_budget_kg(mass / scale))
            family["booking_utilisation_mass"] = repr(utilisation["cargo_mass_fraction"])
        if booking_mass_fraction is not None:
            mass_budget = regional_booking_mass_budget(vessel, reference, mass)
            utilisation.update(cargo_mass_kg=mass, cargo_mass_budget_kg=mass_budget,
                               booking_mass_cap_kg=booking_mass_fraction * mass_budget)
            family.update(booking_cargo_mass_kg=repr(mass), booking_mass_budget_kg=repr(mass_budget),
                          booking_mass_cap_kg=repr(booking_mass_fraction * mass_budget))
        family["booking_utilisation_teu"] = repr(utilisation["teu_fraction"])
        def template(cid, cargo, targets):
            # An engine context precedes gate arrivals, so is not a planning
            # snapshot (same convention as simulator.sample_ship_calls). Only
            # the final loading-start snapshot is fully validated and saved.
            return Instance(id=cid, stage="A", size_class=size, family=family, seed=seed, vessel=vessel,
                            rotation=rotation, port=port, call_index=0, yard=YardState(yard_id=port.yard.id),
                            onboard=ShipState(vessel_id=vessel.id, tank_masses_kg=tank_masses),
                            containers=cargo, load_list=[], provenance=provenance,
                            voyage=voyages[cid]).model_copy(update={"load_list": sorted(targets)})
        target = template(target_instance_id, boxes, [b.id for b in boxes])
        later = template(background_instance_id, background, [b.id for b in background])
        gate_rng = Random(_stream_seed(draw_identity, "gate", attempt=attempt))
        cutoff = -parameters.gate_in.cutoff_before_arrival_s
        def gates(cargo):
            result = {}
            for box in cargo:
                interval = gate_rng.choices(parameters.gate_in.bins, weights=[b.share for b in parameters.gate_in.bins], k=1)[0]
                result[box.id] = cutoff - gate_rng.uniform(interval.end_before_cutoff_s, interval.start_before_cutoff_s)
            return result
        calls = [ShipCall("target", target, 0.0, cutoff, gates(boxes)),
                 ShipCall("later", later, 86400.0, cutoff, gates(background))]
        simulation_start = perf_counter()
        mixing_options = {"congestion_mix_share": float(congestion_mix_share)} if freeze else {}
        simulation = simulate_port(calls, parameters,
                                   seed=_stream_seed(draw_identity, "simulation", attempt=attempt),
                                   noise=noise, until_s=0.0, dig_out=_SnapshotOnlyRetrieval(),
                                   **mixing_options)
        if freeze:
            audit.congestion_mixing = deepcopy(simulation.provenance.congestion_mixing)
        simulation_s += perf_counter() - simulation_start
        snapshot = next(s for s in simulation.snapshots if s.call_id == "target")
        present = {cid for stack in snapshot.yard.stacks for cid in stack.boxes}
        required_yard = {box.id for box in boxes if not (freeze and box.direct_delivery)}
        if not required_yard <= present:
            rejected["expert.physical.slot_occupancy"] += 1
            audit.rejections = dict(sorted(rejected.items()))
            continue
        payload = target.model_dump()
        payload.update(yard=snapshot.yard.model_dump(), containers=[b.model_dump() for b in boxes + background
            if b.id in present or (freeze and b.direct_delivery)])
        result = Instance.model_validate(payload)
        if freeze:
            # The model's base Provenance has no structured extension slot.
            # Preserve complete simulator input evidence as canonical lineage,
            # including future counters, while retaining byte-stable OFF data.
            evidence = json.dumps(audit.congestion_mixing, sort_keys=True, separators=(",", ":"))
            result.provenance.parents = sorted(set(result.provenance.parents) | {
                "yard.simulator.congestion_mixing:" + evidence})
            result.provenance.versions["yard_mixing_policy"] = str(audit.congestion_mixing["policy"])
            result.family["achieved_congestion_mix_share"] = repr(audit.congestion_mixing["achieved_share"])
            result = draws.bind(result)
        screen_start = perf_counter()
        audit.completion_checks += 1
        failed = completion_failures(result)
        if failed:
            for reason in failed:
                audit.completion_rejections[reason] = audit.completion_rejections.get(reason, 0) + 1
            rejected["completion.c1"] += 1
            audit.rejections = dict(sorted(rejected.items()))
            if "c1a" in failed:
                # APPROXIMATION: sequential shipment conditioning; compatible
                # bookings remain, impossible DG shipments are resampled.
                positions = []
                for index, box in enumerate(boxes):
                    if box.dg is None:
                        continue
                    audit.completion_checks += 1
                    single = result.model_copy(update={"load_list": [box.id]})
                    if "c1a" in completion_failures(single):
                        positions.append(index)
                if positions:
                    redraw = (boxes, positions)
            screening_s += perf_counter() - screen_start
            continue
        screening_s += perf_counter() - screen_start
        if audit.c2_requested:
            from stowage.generate.random_screen import necessary_completion_failures

            diagnostics = {}
            audit.c2_checks += 1
            audit.c2_screened = True
            audit.c2_direction = "necessary"
            c2_failed = necessary_completion_failures(result, diagnostics=diagnostics)
            for reason in c2_failed:
                audit.completion_rejections[reason] = audit.completion_rejections.get(reason, 0) + 1
            if "c2.unavailable" in c2_failed:
                error = diagnostics.get("error", "necessary linear model unavailable")
                audit.c2_errors[error] = audit.c2_errors.get(error, 0) + 1
                rejected["checker.missing_vessel_data"] += 1
                audit.rejections = dict(sorted(rejected.items()))
                raise ValueError(f"C2 unavailable: {error}")
            if c2_failed:
                rejected["completion.c2"] += 1
                audit.rejections = dict(sorted(rejected.items()))
                continue
        # Reject cargo/completion failures before spending a full three-state
        # physics check on their unique backgrounds. Activated emission uses
        # the actual candidate's certified arrival copy; witness departure
        # ballast stays separate in the audit. UNKNOWN must never redraw.
        certified = accept_background_candidate(result if freeze else background_probe, audit)
        if certified is None:
            rejected["background.acceptance"] += 1
            audit.rejections = dict(sorted(rejected.items()))
            continue
        if freeze:
            result = certified
        audit.realised_teu = sum(box.length_ft / 20 for box in boxes)
        export = full_export_policy(vessel, target_params)
        if export is not None and export.is_export_count(len(boxes)):
            audit.full_export = export.audit(sum(box.gross_mass_kg for box in boxes), audit.realised_teu)
        audit.accepted = True
        if freeze:
            from stowage.generate.random_cargo import admitted_cargo_audit

            audit.cargo_draws["admitted"] = admitted_cargo_audit(result, target_params)
        audit.booking_utilisation = utilisation
        audit.simulation_statistics = {**snapshot.statistics, "gate_lifts_before_snapshot": float(snapshot.moves_before),
                                       "queued_gate_events": float(simulation.queued_gate_events)}
        audit.rejections = dict(sorted(rejected.items()))
        audit.timings_s.update(simulation_s=simulation_s, screening_s=screening_s, total_s=perf_counter() - start)
        return result
    audit.timings_s.update(simulation_s=simulation_s, screening_s=screening_s, total_s=perf_counter() - start)
    raise ValueError(f"screening exhausted {max_attempts} attempts: {audit.rejections}; C1: {audit.completion_rejections}")


def main(argv=None):
    from stowage.generate.instances_cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
