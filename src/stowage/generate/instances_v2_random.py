"""Canonical-v2 adapter for the original random-yard generator.

The original generator remains the owner of two-cohort yard blocking (Tanaka &
Voss, 2019, doi:10.1016/j.ejor.2019.06.014). This adapter applies the same necessary
condition screen as simulated-yard, with candidate-level rejection accounting.
Passing a screen is not a feasibility certificate; the full checker still decides.
Loaded whole-vessel exports use random_export's mass/TEU design bands and
bounded ballast initializer with the actual sampled equipment profile. Explicit
small diagnostic quantities retain their previous tank initialization.
Scoped producer overrides are restored even on failure. Call this adapter only in
separate worker processes, never concurrently in threads.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import math
from time import perf_counter

from stowage.conventions import canonical_vessel_id
from stowage.generate import random_yard as random
from stowage.generate.instances_screen import count_unresolved_provisions, screen_load_list
from stowage.generate.instances_voyage import attach_default_voyage
from stowage.generate.random_export import booking_tank_masses, export_rejections
from stowage.model import Vessel
from stowage.rules.catalogue import rule_freeze_v1_enabled

RANDOM_V2_VERSION = "7.0.0"
RULE_FREEZE_RANDOM_V2_VERSION = "8.0.0"


def effective_random_v2_version() -> str:
    return RULE_FREEZE_RANDOM_V2_VERSION if rule_freeze_v1_enabled() else RANDOM_V2_VERSION


def select_tiny_region(vessel: Vessel) -> Vessel:
    """Keep six bottom-up dry20 cells and reciprocal hatch/DoC references.

    Prefer deck cells on one hatch, which also works on KCS bow slices without
    hold stacks. The whole hull, fixed loads and physical stack ratings remain.
    APPROXIMATION: excluded cells are unavailable to the planner and empty;
    this is a deliberately restricted six-box decision problem, not a new hull.
    """
    cells = {cell.id: cell for cell in vessel.cells}
    groups = [[stack for stack in vessel.stacks if stack.on_deck and stack.hatch_cover_id == cover.id]
              for cover in vessel.hatch_covers]
    groups.append(list(vessel.stacks))
    selected_stacks = []
    for group in groups:
        remaining = 6
        chosen = []
        for stack in group:
            ids = []
            for cid in stack.cell_ids:
                if not cells[cid].accepts_20 or (len(ids)+1)*2.591 > stack.max_height_m:
                    break
                ids.append(cid)
                if len(ids) == 1:
                    break
            if ids:
                chosen.append(stack.model_copy(update={"cell_ids": ids}))
                remaining -= len(ids)
            if not remaining:
                selected_stacks = chosen
                break
        if selected_stacks:
            break
    if not selected_stacks:
        raise ValueError("tiny decision region needs six physically accepting bottom-up dry20 cells")
    stack_ids = {stack.id for stack in selected_stacks}
    selected = {cid for stack in selected_stacks for cid in stack.cell_ids}
    payload = vessel.model_dump()
    payload.update(nominal_teu=6,
                   cells=[cell.model_dump() for cell in vessel.cells if cell.id in selected],
                   stacks=[stack.model_dump() for stack in selected_stacks],
                   hatch_covers=[{**cover.model_dump(),
                       "hold_stack_ids": [sid for sid in cover.hold_stack_ids if sid in stack_ids],
                       "deck_stack_ids": [sid for sid in cover.deck_stack_ids if sid in stack_ids]}
                       for cover in vessel.hatch_covers
                       if any(sid in stack_ids for sid in cover.hold_stack_ids + cover.deck_stack_ids)],
                   dg_doc_areas=[{**area.model_dump(), "stack_ids": [sid for sid in area.stack_ids if sid in stack_ids]}
                                 for area in vessel.dg_doc_areas if any(sid in stack_ids for sid in area.stack_ids)],
                   sources=[*vessel.sources, "APPROXIMATION: canonical tiny retains six bottom-up dry20 cells; omitted cells empty, whole hull and limits retained"])
    return Vessel.model_validate(payload)


def generate_random_v2(size, *, seed, noise, vessel_name, port_name,
                       n_boxes=None, dense_20ft=False, report=None, max_attempts=200,
                       tiny_dg_enabled=False, cargo_profile="feeder_export",
                       d62_crossing_share=.25, dg_stress_pair=None, instance_id=None,
                       booking_slack=None, booking_mass_fraction=None):
    """Generate random-yard v2 with the original generator's DG sampling.

    APPROXIMATION: dense profiles condition equipment to dry20 while retaining
    its original empty share, mass distribution and DG prevalence. This explicit
    demand profile enables realistic feeder box counts without increasing cells.
    ``noise`` is the requested two-cohort blocking ratio for this generator.
    ``tiny_dg_enabled`` remains a compatibility flag: the current original
    generator already retains the dry20 loaded/empty mix and DG sampling in
    its four-hold/two-deck six-cell region.

    Defaults inherit the original generator's capacity-derived demand, rather
    than treating the rung's box ceiling as an exact quantity. Explicit demand
    overrides the D36 region-scaled full-export booking policy, subject to physical capacity and
    the realised draw's TEU/per-length budgets; it is never silently reduced.
    Depth-0 C1 checks remain on. Activated medium additionally requests the
    producer's necessary-direction C2 screen (D58); unavailable C2 fails closed.
    ``instance_id`` supplies the final campaign identity before activated
    shipment provenance is bound; callers must not rename a bound instance.
    """
    random.validate_booking_slack(booking_slack)
    random.validate_booking_mass_fraction(booking_mass_fraction)
    vessel_name = canonical_vessel_id(vessel_name)
    if size not in random.SIZE_LADDER:
        raise ValueError(f"unknown size {size!r}")
    if n_boxes is not None and (type(n_boxes) is not int or n_boxes < 1):
        raise ValueError("n_boxes must be a positive integer or None")
    if type(dense_20ft) is not bool:
        raise ValueError("dense_20ft must be boolean")
    if type(tiny_dg_enabled) is not bool:
        raise ValueError("tiny_dg_enabled must be boolean")
    freeze = rule_freeze_v1_enabled()
    if cargo_profile not in {"feeder_export", "dg_stress"}:
        raise ValueError("cargo_profile must be feeder_export or dg_stress")
    if not freeze and (cargo_profile != "feeder_export" or dg_stress_pair is not None):
        raise ValueError("DG-stress profiles require rule-freeze-v1 activation")
    if dg_stress_pair is not None and cargo_profile != "dg_stress":
        raise ValueError("dg_stress_pair requires cargo_profile=dg_stress")
    if isinstance(d62_crossing_share, bool) or not math.isfinite(d62_crossing_share) or not 0 <= d62_crossing_share <= 1:
        raise ValueError("d62_crossing_share must be finite and in [0, 1]")
    from stowage.generate.instances import GenerationReport
    audit = report if report is not None else GenerationReport()
    # A caller may reuse a report across activated and legacy invocations.
    # Never retain the previous invocation's shipment, bucket or C2 evidence.
    for name in ("cargo_draws", "d62_crossing", "producer_report", "c2_errors"):
        setattr(audit, name, {})
    audit.c2_requested, audit.c2_checks, audit.c2_direction = False, 0, None
    if freeze:
        audit.c2_screened = False
    audit.requested_blocking_ratio, audit.realised_blocking_ratio = 0.0, 0.0
    audit.blocked_targets, audit.targets = 0, 0
    audit.attempts, audit.rejections, audit.provision_rejections = 0, {}, 0
    audit.accepted, audit.timings_s, audit.simulation_statistics = False, {}, {}
    audit.full_export = {}
    audit.booking_reference, audit.booking_utilisation = {}, {}
    audit.balancing_rejections = 0
    audit.conditioning = ["Canonical necessary-condition screen plus depth-0 C1; passing is not a feasibility certificate",
        "APPROXIMATION: original generator resamples individually impossible DG shipments after C1a rejection, then rechecks whole-list budgets and C1"]
    if dense_20ft:
        audit.conditioning.append("APPROXIMATION: dense profile conditions equipment to dry20, retaining original empty share, masses and DG sampling")
    if cargo_profile == "dg_stress":
        audit.conditioning.append("APPROXIMATION: canonical D70 stress demand conditions equipment to dry20 at every rung; pair cohorts retain cargo-owned class probabilities")
    rung = random.SIZE_LADDER[size]
    ceiling = rung.boxes if n_boxes is None else n_boxes
    blocks = max(rung.yard_blocks, math.ceil((ceiling+2)/((rung.yard_bays//2)*rung.yard_rows)))
    port = random.load_port(port_name)
    saved = {name: getattr(random, name) for name in ("SIZE_LADDER", "_tiny_region", "load_param_file", "screen_load_list", "slice_preset", "scaled_yard", "generate_load_list", "capacity_load_size")}
    old_params = random.load_param_file
    old_generate = random.generate_load_list
    original_empty_share = next(e for e in old_params("cargo", "feeder_export")["equipment"]
                                if e["id"] == "dry_20")["empty_share"]["value"]
    start = perf_counter()
    screening_s = 0.0
    active_params = None
    active_cargo = None
    original_prepare = None
    original_bind = None
    provision_attempts = set()

    def yard(source_port, *, blocks, bays, rows):
        nonlocal port
        # Respect the caller's expanded footprint, rather than returning the
        # adapter's initial rung-sized yard. Narrow source blocks keep their
        # bay/row limits and gain blocks for the requested ground area.
        yard_bays = min(bays, min(block.n_bays for block in source_port.yard.blocks))
        yard_rows = min(rows, min(block.n_rows for block in source_port.yard.blocks))
        yard_blocks = math.ceil(blocks * bays * rows / (yard_bays * yard_rows))
        expanded = source_port.model_copy(deep=True)
        originals = list(expanded.yard.blocks)
        ids = {block.id for block in originals}
        next_id = 0
        for index in range(max(0, yard_blocks - len(originals))):
            # The original generator reserves X... for its later export-yard
            # expansion. Keep adapter-created blocks in a separate namespace.
            while f"V{next_id:03d}" in ids:
                next_id += 1
            block_id = f"V{next_id:03d}"
            ids.add(block_id)
            # APPROXIMATION: repeat source storage geometry and its exact
            # DG/equipment/length/height limits; this is a synthetic footprint.
            expanded.yard.blocks.append(originals[-1].model_copy(update={
                "id": block_id,
                "x_m": originals[-1].x_m + (index + 1) * (bays * 6.058 + 20.0),
            }, deep=True))
        port = saved["scaled_yard"](expanded, blocks=yard_blocks,
                                     bays=yard_bays, rows=yard_rows)
        return port

    def capacity(vessel, requested_rung, params):
        if n_boxes is not None:
            requested_rung = replace(requested_rung, fill_fraction=1.0)
        target, teu_budget, length_budgets = saved["capacity_load_size"](vessel, requested_rung, params)
        if booking_slack is not None:
            target, teu_budget, length_budgets = random.apply_booking_slack(
                vessel, params, (target, teu_budget, length_budgets), booking_slack)
        if booking_mass_fraction is not None:
            target, teu_budget, length_budgets = random.apply_booking_mass_fraction(
                vessel, params, (target, teu_budget, length_budgets), booking_mass_fraction,
                full_vessel=random._full_vessel(vessel_name))
        if n_boxes is not None and target != n_boxes:
            raise ValueError(f"n_boxes={n_boxes} exceeds capacity-derived demand budget ({target} boxes); explicit quantities are never reduced")
        return target, teu_budget, length_budgets

    def parameters(*args, **kwargs):
        params = deepcopy(old_params(*args, **kwargs))
        if dense_20ft:
            params["equipment"] = [next(e for e in params["equipment"] if e["id"] == "dry_20")]
        return params

    def cargo(*args, **kwargs):
        nonlocal active_params
        # APPROXIMATION: canonical tiny conditions equipment to dry20, but restores
        # its source empty share so eligible loaded boxes retain DG sampling.
        if tiny_dg_enabled and size == "tiny":
            kwargs = dict(kwargs)
            params = deepcopy(kwargs["params"])
            next(e for e in params["equipment"] if e["id"] == "dry_20")["empty_share"]["value"] = original_empty_share
            kwargs["params"] = params
        active_params = kwargs.get("params")
        return old_generate(*args, **kwargs)

    def prepare_cargo(*args, **kwargs):
        nonlocal active_params
        params, rotation = original_prepare(*args, **kwargs)
        if dg_stress_pair is not None:
            from stowage.generate.cargo import dg_stress_profile
            selected = dg_stress_profile(pair_id=dg_stress_pair)
            # Retain prepare_random_cargo's actual enriched handling route.
            params["dg"] = selected["dg"]
            params["profile_id"] = selected["profile_id"]
        if dense_20ft or cargo_profile == "dg_stress":
            params["equipment"] = [next(e for e in params["equipment"] if e["id"] == "dry_20")]
        # The producer mutates this same recipe for tiny before drawing. Use
        # that exact object for booking tanks and the adapter's extra screen.
        active_params = params
        return params, rotation

    def bind_cargo(draws, instance):
        # Shipment provenance includes the instance identity. Campaign adapters
        # must supply their final id before pre-screen and final binding.
        if instance_id is not None:
            instance = instance.model_copy(update={"id": instance_id})
        return original_bind(draws, instance)

    def screen(vessel, boxes):
        nonlocal screening_s
        t0 = perf_counter()
        # Singleton DG probes belong to the same candidate attempt and must
        # not inflate the candidate-level provision rejection rate.
        if count_unresolved_provisions(boxes) > 0 and raw.attempts not in provision_attempts:
            provision_attempts.add(raw.attempts)
            audit.provision_rejections += 1
        # APPROXIMATION: full exports use bounded design-band ballast; small
        # diagnostics keep their previous empty-ballast initialization. Use the
        # actual sampled profile, including dense equipment conditioning.
        params = active_params if active_params is not None else parameters("cargo", "feeder_export")
        reasons = screen_load_list(vessel, boxes, port=port,
                                   tank_masses_kg=booking_tank_masses(vessel, boxes, params))
        reasons.extend(export_rejections(vessel, boxes, params,
            **({"minimum_utilisation": False} if booking_slack is not None or booking_mass_fraction is not None else {})))
        screening_s += perf_counter()-t0
        return reasons

    raw = random.RandomGenerationReport()
    try:
        random.SIZE_LADDER = {**random.SIZE_LADDER, size: replace(rung, boxes=ceiling, yard_blocks=blocks,
            fill_fraction=1.0 if n_boxes is not None else rung.fill_fraction)}
        if n_boxes is not None:
            random.capacity_load_size = capacity
        random.load_param_file = parameters
        random.screen_load_list = screen
        random.scaled_yard = yard
        random.generate_load_list = cargo
        if freeze:
            from stowage.generate import random_cargo
            active_cargo = random_cargo
            original_prepare = active_cargo.prepare_random_cargo
            original_bind = active_cargo.RandomCargoDraws.bind
            # The upstream API currently has no explicit equipment/parameter
            # argument. Keep its policy and route owner, changing only this
            # scoped adapter recipe; all hooks are restored even on failure.
            active_cargo.prepare_random_cargo = prepare_cargo
            active_cargo.RandomCargoDraws.bind = bind_cargo
        result = random.generate_random_instance(size, seed=seed, blocking_ratio=noise,
                    vessel_name=vessel_name, port_name=port_name, report=raw, max_attempts=max_attempts,
                    **({"booking_slack": booking_slack} if booking_slack is not None else {}),
                    **({"booking_mass_fraction": booking_mass_fraction} if booking_mass_fraction is not None else {}),
                    **(dict(cargo_profile=cargo_profile, d62_crossing_share=d62_crossing_share,
                            c2_screen=size == "medium") if freeze else {}))
        profile_label = "dg_stress" if cargo_profile == "dg_stress" else "dense_dry20_dg" if dense_20ft else "feeder_export"
        equipment_profile = "dry20_dg_stress" if cargo_profile == "dg_stress" else "dry20_with_dg" if size == "tiny" else "dense_20ft" if dense_20ft else "full_mix"
        controls = {"size": size, "seed": seed, "noise": float(noise),
            "vessel": vessel_name, "port": port_name, "n_boxes": n_boxes,
            "dense_20ft": dense_20ft, "tiny_dg_enabled": tiny_dg_enabled}
        if booking_slack is not None:
            controls["booking_slack"] = float(booking_slack)
        if booking_mass_fraction is not None:
            controls["booking_mass_fraction"] = float(booking_mass_fraction)
        if freeze:
            controls.update(cargo_profile=cargo_profile, d62_crossing_share=d62_crossing_share,
                            dg_stress_pair=dg_stress_pair, random_yard_v2=effective_random_v2_version())
        identity = hashlib.sha256(json.dumps(controls, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        result.family.update(noise=repr(float(noise)), n_boxes=str(len(result.load_list)),
            requested_n_boxes="auto" if n_boxes is None else str(n_boxes),
            dense_20ft=str(dense_20ft).lower(), tiny_dg_enabled=str(tiny_dg_enabled).lower(),
            cargo_profile=profile_label, equipment_profile=equipment_profile,
            generation_identity=identity)
        result.provenance.versions["random_yard_v2"] = effective_random_v2_version()
        result.provenance.versions.update(cargo_profile=profile_label,
            equipment_profile=equipment_profile, generation_identity=identity)
        if freeze:
            from stowage.generate.cargo import effective_cargo_generator_version
            from stowage.yard import effective_yard_simulator_version
            result.provenance.versions.update(cargo_generator=effective_cargo_generator_version(),
                yard_simulator=effective_yard_simulator_version(), random_yard=random.effective_random_yard_version())
            result.family.update(generator_family="random-yard",
                d62_crossing_bucket=result.family.get("d62_crossing_bucket", "none"),
                d62_ship_crossing="probed" if "ship_probe" in raw.d62_crossing else "not_probed")
            if cargo_profile == "dg_stress":
                result.family.update(identity_version="v3", dg_stress_pair=dg_stress_pair or "mixed")
        result = attach_default_voyage(result)
        audit.accepted = True
        return result
    except ValueError as error:
        # A failed physical background initializer produces no usable vessel.
        # Retain its candidate audit and let the campaign record the failed
        # shard; never replace the condition or bypass the fleet's bounds.
        if raw.attempts and str(error).startswith("cannot balance slice background within physical cargo capacities:"):
            reason = "expert.physical.slot_occupancy"
            raw.rejections[reason] = raw.rejections.get(reason, 0) + 1
            audit.balancing_rejections += 1
        raise
    finally:
        if active_cargo is not None:
            active_cargo.prepare_random_cargo = original_prepare
            active_cargo.RandomCargoDraws.bind = original_bind
        for name, value in saved.items():
            setattr(random, name, value)
        # Optional non-medium campaign C2 runs through completion_failures and
        # records on this outer report. The medium producer owns its own C2,
        # while the campaign hook is disabled, so these counts are disjoint.
        outer_c2 = {name: deepcopy(getattr(audit, name)) for name in
                    ("c2_requested", "c2_checks", "c2_screened", "c2_direction", "c2_errors")} if freeze else {}
        audit.attempts, audit.rejections = raw.attempts, dict(raw.rejections)
        for name in ("capacity_teu", "teu_budget", "length_slot_budgets", "requested_targets",
                     "realised_teu", "completion_checks", "completion_rejections",
                     "c2_screened", "shipment_redraws", "full_export",
                     "booking_reference", "booking_utilisation", "booking_slack", "booking_mass_fraction", "balancing_rejections", "background_checks", "background_cache_hits",
                     "background_seconds", "background_rejections", "background_unknowns", "background_last"):
            setattr(audit, name, deepcopy(getattr(raw, name)))
        if freeze:
            # Preserve the producer schema in full, including future fields,
            # instead of silently dropping new audits at this boundary.
            audit.producer_report = deepcopy(asdict(raw))
            for name, value in audit.producer_report.items():
                if name not in {"conditioning", "accepted"}:
                    setattr(audit, name, deepcopy(value))
            audit.c2_requested |= outer_c2["c2_requested"]
            audit.c2_screened |= outer_c2["c2_screened"]
            audit.c2_checks += outer_c2["c2_checks"]
            audit.c2_direction = outer_c2["c2_direction"] or audit.c2_direction
            for reason, count in outer_c2["c2_errors"].items():
                audit.c2_errors[reason] = audit.c2_errors.get(reason, 0) + count
        if tiny_dg_enabled and size == "tiny":
            if booking_slack is not None or booking_mass_fraction is not None:
                audit.conditioning.extend(item for item in raw.conditioning if item.startswith("ASSUMPTION:"))
            audit.conditioning.append("APPROXIMATION: canonical tiny conditions equipment to dry20 with original empty share and DG sampling; four hold and two deck physical decision cells")
        else:
            audit.conditioning.extend(raw.conditioning)
        audit.simulation_statistics = {"requested_blocking_ratio": raw.requested_blocking_ratio,
            "realised_blocking_ratio": raw.realised_blocking_ratio, "targets": raw.targets, "blocked_targets": raw.blocked_targets}
        audit.timings_s = {"screening_s": screening_s, "total_s": perf_counter()-start}
