"""Activated random-generator contracts, isolated from hull qualification.

The explicit geometry/gate diagnostic below verifies manifest construction and
telemetry only. It supplies no full-physics feasibility certificate.
"""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from stowage.generate import cargo, random_yard as random
from stowage.params import load_param_file
from stowage.rules.catalogue import rule_freeze_v1
from stowage.vessel.fleet_slices import load_fleet_preset
from stowage.yard import effective_yard_simulator_version


@pytest.fixture(scope="module")
def geometry():
    return load_fleet_preset("feeder_500", "full", with_hull=False)


@pytest.fixture(autouse=True)
def diagnostic_geometry(monkeypatch, geometry):
    monkeypatch.setattr(random, "_full_vessel", lambda name: geometry)
    monkeypatch.setattr(random, "accept_background_candidate", lambda instance, audit: instance)


def _profile(monkeypatch, *, dg_class=None):
    params = deepcopy(load_param_file("cargo", "feeder_export"))
    equipment = next(row for row in params["equipment"] if row["id"] == "dry_20")
    equipment["empty_share"]["value"] = 0
    params["equipment"] = [equipment]
    params["dg"]["share_of_eligible_loaded_boxes"]["value"] = int(dg_class is not None)
    params["special_stowage"]["share_of_loaded_boxes"]["value"] = 0
    params["rotations"][0]["pods"] = params["rotations"][0]["pods"][:1]
    if dg_class:
        params["dg"].pop("pending_rule_freeze_v1", None)
        params["dg"]["classes"] = [row for row in params["dg"]["classes"] if row["id"] == dg_class]
        if dg_class == "9":
            params["dg"]["classes"][0]["un_numbers"] = [
                row for row in params["dg"]["classes"][0]["un_numbers"]
                if int(row["un_number"]) == 3480]
    actual = random.load_param_file
    monkeypatch.setattr(random, "load_param_file", lambda kind, name: deepcopy(params)
                        if (kind, name) == ("cargo", "feeder_export") else actual(kind, name))
    monkeypatch.setattr(cargo, "load_param_file", lambda kind, name: deepcopy(params)
                        if (kind, name) == ("cargo", "feeder_export") else actual(kind, name))
    return params


# Historical evidence, not expectations against today's physical dependencies.
# The feeder parameter pin changed c7ce7a13... -> 97c31c65..., including actual
# fixed-cargo masses/CGs; catalogue and port/voyage pins also moved.
# Replacing these hashes would conflate a fleet migration with a 3.5 regression.
_HISTORICAL_OFF_DIGESTS = {
    1: '8246359d69b889928fe529ddef0bf656f99d1172c279d27dfd46600e5b632027',
    13: '5c4fa874d78c81b2144b8069d69ec861464fe72c1f64a4ba86eb984be093dbcb',
}


def _assert_off_matches_pre35(seed):
    # Freeze the old algorithm, not its old hull/parameter/physics dependencies.
    # The reference source is readable below and pinned independently of the
    # implementation being tested. All helper objects use the same current
    # module globals, including this module's explicit geometry/gate diagnostic.
    assert sha256(_PRE35_SOURCE.encode()).hexdigest() == _PRE35_SOURCE_SHA256
    namespace = dict(vars(random))
    # This is the historical OFF producer label; a later ON registry promotion
    # must not turn the old algorithm into a differently labelled generator.
    namespace['RANDOM_YARD_VERSION'] = random.LEGACY_RANDOM_YARD_VERSION
    exec(compile(_PRE35_SOURCE, '<frozen-pre35-generator>', 'exec', dont_inherit=True), namespace)
    with rule_freeze_v1(enabled=False):
        reference = namespace['generate_random_instance'](seed=seed)
        current = random.generate_random_instance(seed=seed)
    # No exclusions or label normalization: actual cargo, background masses,
    # geometry, tank fills, yard, voyage and all provenance bytes must match.
    expected = reference.model_dump_json().encode()
    actual = current.model_dump_json().encode()
    assert sha256(actual).hexdigest() == sha256(expected).hexdigest()
    assert actual == expected


@pytest.mark.parametrize('seed', _HISTORICAL_OFF_DIGESTS)
def test_off_seeded_serialization_is_unchanged(seed):
    _assert_off_matches_pre35(seed)


def test_off_reference_comparison_detects_actual_mass_regression(monkeypatch):
    original = random.generate_random_instance

    def changed_mass(*args, **kwargs):
        instance = original(*args, **kwargs)
        boxes = [box.model_copy(deep=True) for box in instance.containers]
        boxes[0].gross_mass_kg += 1.
        return instance.model_copy(update={'containers': boxes})

    monkeypatch.setattr(random, 'generate_random_instance', changed_mass)
    with pytest.raises(AssertionError):
        _assert_off_matches_pre35(1)


def test_active_calls_have_cargo_provider_archetypes():
    with rule_freeze_v1(enabled=True):
        instance = random.generate_random_instance(seed=1)
    assert all(call.archetype for call in instance.rotation.calls)
    assert instance.rotation.calls[instance.call_index].archetype == instance.port.archetype


def test_active_effective_versions_and_exact_whole_kg_categories(monkeypatch):
    _profile(monkeypatch)
    with rule_freeze_v1(enabled=True):
        instance = random.generate_random_instance(seed=13, blocking_ratio=1)
        assert instance.provenance.versions["cargo_generator"] == cargo.effective_cargo_generator_version()
        assert instance.provenance.versions["yard_simulator"] == effective_yard_simulator_version()
    assert all(box.gross_mass_kg.is_integer() for box in instance.containers)
    targets = [box for box in instance.containers if box.id in instance.load_list]
    assert all(f"mass_kg={box.gross_mass_kg.hex()}" in box.category for box in targets)
    assert all(not box.direct_delivery for box in instance.containers if box.id not in instance.load_list)


def test_active_shipment_scenarios_bound_before_completion_screen(monkeypatch):
    _profile(monkeypatch, dg_class="9")
    screened = []
    bound_ids = set()
    actual_bind = cargo.bind_shipment_scenarios
    actual_screen = random.screen_load_list

    def bind(instance, report):
        bound = actual_bind(instance, report)
        facts = bound.rule_overrides.get("imdg.stowage.3_2", {}).get("containers", {})
        bound_ids.update(facts)
        return bound

    def physical_screen(vessel, boxes):
        assert {box.id for box in boxes if box.dg} <= bound_ids, "Bind DG declarations before screening"
        return actual_screen(vessel, boxes)

    def completion(instance):
        boxes = [box for box in instance.containers if box.id in instance.load_list]
        assert boxes and all(box.dg and int(box.dg.un_number) == 3480 for box in boxes)
        facts = instance.rule_overrides.get("imdg.stowage.3_2", {}).get("containers", {})
        assert set(facts) >= {box.id for box in boxes}, "DG facts must be bound before C1"
        assert all(facts[box.id]["entry_assessments"] for box in boxes)
        screened.append(instance)
        return ()

    monkeypatch.setattr(random, "completion_failures", completion)
    monkeypatch.setattr(cargo, "bind_shipment_scenarios", bind)
    monkeypatch.setattr(random, "screen_load_list", physical_screen)
    with rule_freeze_v1(enabled=True):
        instance = random.generate_random_instance(seed=731, blocking_ratio=0)
    assert screened and instance.rule_overrides


def test_active_direct_delivery_exports_never_enter_yard(monkeypatch):
    params = _profile(monkeypatch, dg_class="1.4")
    params["dg"]["classes"][0]["id"] = "1.4S"
    params["dg"]["classes"][0]["un_numbers"][0]["un_number"] = "0012"
    # Placement clearance is deliberately outside this manifest-flow test.
    monkeypatch.setattr(random, "completion_failures", lambda instance: ())
    with rule_freeze_v1(enabled=True):
        instance = random.generate_random_instance(seed=731, blocking_ratio=1)
    direct = {box.id for box in instance.containers if box.direct_delivery}
    assert direct == set(instance.load_list)
    assert not direct.intersection(box_id for stack in instance.yard.stacks for box_id in stack.boxes)


def test_active_d70_profile_is_separate_and_labelled(monkeypatch):
    monkeypatch.setattr(random, "completion_failures", lambda instance: ())
    report = random.RandomGenerationReport()
    with rule_freeze_v1(enabled=True):
        instance = random.generate_random_instance(seed=710, cargo_profile="dg_stress", report=report)
    assert instance.family["cargo_profile"] == "dg_stress"
    assert all(box.dg for box in instance.containers if box.id in instance.load_list)
    assert any("D70" in text for text in report.conditioning)


def test_active_requested_audit_retains_rejected_candidate(monkeypatch):
    _profile(monkeypatch)
    calls = []

    def completion(instance):
        calls.append(instance)
        return ("c1f",) if len(calls) == 1 else ()

    monkeypatch.setattr(random, "completion_failures", completion)
    report = random.RandomGenerationReport()
    with rule_freeze_v1(enabled=True):
        instance = random.generate_random_instance(seed=13, report=report)
    # The forced C1 rejection is retained; D77-seeded screens may add others.
    assert report.rejections["completion.c1"] == 1
    assert report.attempts == 1 + sum(report.rejections.values())
    stages = report.cargo_draws["stages"]
    assert stages["requested"]["n_boxes"] == report.attempts * len(instance.load_list)
    assert stages["drawn"]["n_boxes"] >= stages["requested"]["n_boxes"]
    requested = stages["requested"]
    assert sum(requested["mass_bands"].values()) == requested["n_boxes"]
    assert requested["non_dg"] + sum(requested["dg_by_class"].values()) == requested["n_boxes"]
    assert sum(row["n_boxes"] for row in requested["by_pod"].values()) == requested["n_boxes"]
    assert all("reefer" in row and "oog" in row for row in requested["by_pod"].values())


def test_effective_random_version_follows_runtime_switch_and_ignores_registry_drift(monkeypatch):
    monkeypatch.setattr(random, "RANDOM_YARD_VERSION", "999.0.0")
    for enabled, expected in ((False, "8.0.0"), (True, "9.3.0"), (False, "8.0.0")):
        with rule_freeze_v1(enabled=enabled):
            assert random.effective_random_yard_version() == expected
            instance = random.generate_random_instance(seed=13, d62_crossing_share=0)
        assert instance.family["generator"] == expected
        assert instance.provenance.versions["random_yard"] == expected


@pytest.mark.parametrize("first,second", [("feeder_export", "dg_stress"), ("dg_stress", "feeder_export")])
def test_active_cli_profile_is_stamped_and_cross_profile_resume_is_refused(
    monkeypatch, tmp_path, capsys, first, second,
):
    monkeypatch.setattr(random, "completion_failures", lambda instance: ())
    # This test isolates CLI configuration and shard serialization. The complete
    # source-identity resume validation has its own production-path regressions.
    monkeypatch.setattr(random, "_resume_inputs", lambda vessel, port, **options: options)
    args = ["--size", "tiny", "--seed", "710", "--count", "1", "--output", str(tmp_path),
            "--d62-crossing-share", "0", "--cargo-profile"]
    with rule_freeze_v1(enabled=True):
        assert random.main([*args, first]) == 0
        assert json.loads(capsys.readouterr().out)["generated"] == 1
        path = tmp_path / "tiny" / "seed-00000710.json"
        before = path.read_bytes()
        instance = random.load_instance(path)
        assert instance.family["cargo_profile"] == first
        assert random.main([*args, first]) == 0
        assert json.loads(capsys.readouterr().out)["resumed"] == 1
        with pytest.raises(ValueError, match="configuration mismatch"):
            random.main([*args, second])
        assert path.read_bytes() == before


@pytest.mark.parametrize("enabled,profile,error", [
    (False, "dg_stress", "activation"),
    (False, "not_a_profile", "cargo_profile"),
    (True, "not_a_profile", "cargo_profile"),
])
def test_profile_validation_precedes_physics_build(monkeypatch, enabled, profile, error):
    monkeypatch.setattr(random, "_full_vessel", lambda name: pytest.fail("Invalid profile reached hull construction"))
    with rule_freeze_v1(enabled=enabled), pytest.raises(ValueError, match=error):
        random.generate_random_instance(seed=1, cargo_profile=profile)


def test_active_fractional_tare_blockers_round_up_without_copying_shipment_facts(monkeypatch):
    params = _profile(monkeypatch)
    params["equipment"][0]["tare_kg"]["value"] = 2350.4
    params["equipment"][0]["empty_weight_kg"]["value"]["value"] = 2350.4
    with rule_freeze_v1(enabled=True):
        instance = random.generate_random_instance(seed=13, blocking_ratio=1, d62_crossing_share=0)
    blockers = [box for box in instance.containers if box.id not in instance.load_list]
    assert blockers
    assert all(box.tare_kg == 2350.4 and box.gross_mass_kg == 2351 for box in blockers)
    assert all(f"mass_kg={box.gross_mass_kg.hex()}" in box.category for box in blockers)
    assert all(box.dg is None and not box.direct_delivery for box in blockers)


# Frozen pre-3.5 generate_random_instance, recorded before activation edits.
# SHA256 is the original /tmp/random_yard_pre35_function.py content identity.
# Keep this independent reference unchanged when editing the live generator.
_PRE35_SOURCE_SHA256 = 'a6a5efa0ea1d1d1f6f72520e039f2bcde243dc19dc064b2584cbc324322878f0'
_PRE35_SOURCE = r'''def generate_random_instance(
    size: str = "tiny", *, seed: int, blocking_ratio: float = 0.3,
    vessel_name: str = "feeder_500", port_name: str = "laem_chabang_like",
    report: RandomGenerationReport | None = None, max_attempts: int = 200,
    c2_screen: bool = False,
) -> Instance:
    """Generate one clean, deterministic instance, using 2.2/2.5/2.6 builders.

    Tiny has at most six 20ft targets in four hold/two deck cells and one hatch.
    The region-sized booking has at most 6! complete ship assignments and 6!
    loading orders; this finite bound does not prove physical solvability.
    Each blocker needs at least one relocation; moving
    it to either of two reserved empty stacks attains that yard lower bound.
    This bounds a canonical brute-force solver, not every cyclic move sequence.
    Tiny conditions equipment to dry20, retaining the source loaded/empty mix,
    DG prevalence, exact masses and checker physics. Other rungs retain the complete cargo mix;
    unsupported DGL draws and necessary-condition failures are reported/redrawn.
    ``c2_screen=True`` additionally requests D49's necessary-direction fractional
    completion LP. Missing qualification fails closed; a pass is not a plan.
    """
    vessel_name = canonical_vessel_id(vessel_name)
    if size not in SIZE_LADDER:
        raise ValueError(f"unknown size {size!r}; choose {tuple(SIZE_LADDER)}")
    if not math.isfinite(blocking_ratio) or not 0 <= blocking_ratio <= 1:
        raise ValueError("blocking_ratio must be finite and in [0, 1]")
    if type(seed) is not int or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts must be a positive integer")
    if type(c2_screen) is not bool:
        raise ValueError("c2_screen must be a boolean")
    rung = SIZE_LADDER[size]
    audit = report if report is not None else RandomGenerationReport()
    audit.__dict__.update(RandomGenerationReport().__dict__)
    audit.requested_blocking_ratio = blocking_ratio
    audit.c2_requested = c2_screen
    parent = _full_vessel(vessel_name)
    vessel = _decision_region(parent, size)
    if size == "tiny":
        vessel = _tiny_region(vessel)
    port = load_port(port_name)
    if size != "full":
        port = scaled_yard(port, blocks=rung.yard_blocks,
                           bays=rung.yard_bays, rows=rung.yard_rows)
    params = deepcopy(load_param_file("cargo", "feeder_export"))
    route = deepcopy(params["rotations"][0])
    calls = list(route["port_calls"])
    calls[0] = port.locode
    rotation = Rotation(service=route["id"], calls=[PortCall(locode=p, index=i) for i, p in enumerate(calls)])
    route["port_calls"] = calls
    params["rotations"] = [route]
    if size == "tiny":
        params["equipment"] = [next(e for e in params["equipment"] if e["id"] == "dry_20")]
        audit.conditioning.append("APPROXIMATION: tiny conditions equipment to dry20, retaining source loaded/empty mix and DG sampling; region-sized booking in four hold/two deck cells")
    # The canonical adapter installs a three-argument capacity hook for an
    # explicitly requested diagnostic quantity. Preserve that existing hook
    # contract; default bookings alone opt into D36's regional distribution.
    regional_policy = capacity_load_size is _DEFAULT_CAPACITY_LOAD_SIZE
    if regional_policy:
        target_count, teu_budget, length_budgets = capacity_load_size(vessel, rung, params, full_vessel=parent)
    else:
        target_count, teu_budget, length_budgets = capacity_load_size(vessel, rung, params)
    booking_policy = "D36_scaled_full_export_v1" if regional_policy else "explicit_capacity_override"
    reference = full_export_policy(parent, params)
    if reference is not None:
        audit.booking_reference = {"policy": booking_policy,
            "full_capacity_teu": reference.capacity_teu,
            "full_target_count": reference.target_count,
            "requested_teu_fraction": reference.target_count * reference.mean_teu / reference.capacity_teu,
            "region_capacity_teu": len(vessel.cells)}
    if size == "full" or target_count > rung.boxes:
        port = _export_yard(port, rung, target_count)
    audit.capacity_teu = len(vessel.cells)
    audit.teu_budget, audit.length_slot_budgets = teu_budget, length_budgets
    audit.requested_targets = target_count
    audit.conditioning.append(f"{booking_policy} demand: {target_count} targets, {teu_budget} TEU budget; C1 depth-0 on, C2 {'necessary (D49)' if c2_screen else 'off'}")
    # D50: the model derives 10% remaining consumables by the vessel's declared
    # group/order policy. Declare identities/environment, never arrival fills.
    voyage = build_voyage_spec(rotation, 0, port, seed=seed)
    from stowage.generate.cargo import UnsupportedCargoError
    redraw = None
    for attempt in range(max_attempts):
        audit.attempts += 1
        try:
            if redraw is None:
                boxes = generate_load_list(rotation, 0, target_count, seed=seed * 1_000_000 + attempt, params=params)
            else:
                boxes, positions = redraw
                boxes = list(boxes)
                for index in positions:
                    draw_seed = Random(f"{seed}:{attempt}:shipment:{index}").getrandbits(64)
                    replacement = generate_load_list(rotation, 0, 1, seed=draw_seed,
                                                     id_offset=index, params=params)[0]
                    boxes[index] = replacement.model_copy(update={"id": boxes[index].id})
                    audit.shipment_redraws += 1
            redraw = None
        except UnsupportedCargoError:
            key = "imdg.stowage.3_2"
            audit.rejections[key] = audit.rejections.get(key, 0) + 1
            continue
        # D29: always start from the immutable FINAL geometry. Screening hooks
        # must see this candidate's actual booking condition, not a preset mass.
        # Yard blockers are excluded; consumables stay in live tanks, not loads.
        try:
            candidate_vessel = recondition_slice(vessel,
                expected_load_mass_kg=sum(box.gross_mass_kg for box in boxes),
                seed=Random(f"{seed}:{attempt}:slice").getrandbits(64))
        except ValueError as error:
            if not str(error).startswith("cannot balance slice background within physical cargo capacities"):
                raise
            audit.balancing_rejections += 1
            key = "background.construction"
            audit.rejections[key] = audit.rejections.get(key, 0) + 1
            continue
        # Keep this booking recipe through screening and emission. The gate
        # may check a heavier empty-plan witness without consuming its headroom.
        candidate_tanks = booking_tank_masses(candidate_vessel, boxes, params)
        background_probe = Instance(id=f"random-background.{size}.{seed}", stage="A", size_class=size,
            seed=seed, vessel=candidate_vessel, rotation=rotation, port=port, call_index=0,
            yard=YardState(yard_id=port.yard.id),
            onboard=ShipState(vessel_id=candidate_vessel.id,
                tank_masses_kg=candidate_tanks),
            containers=[], load_list=[], voyage=voyage)
        reasons = screen_load_list(candidate_vessel, boxes)
        reasons.extend(export_rejections(candidate_vessel, boxes, params,
            full_vessel=parent if regional_policy else None))
        if sum(box.length_ft / 20 for box in boxes) > teu_budget:
            reasons.append("expert.physical.slot_occupancy")
        if any(sum(box.length_ft == length for box in boxes) > limit
               for length, limit in length_budgets.items()):
            reasons.append("expert.physical.size_fit")
        if reasons:
            for reason in set(reasons):
                audit.rejections[reason] = audit.rejections.get(reason, 0) + 1
            if "imdg.stowage.3_2" in reasons:
                # APPROXIMATION: extend the same sequential shipment
                # conditioning to adapter screens that reject unsupported DG
                # before C1. A single-shipment screen identifies the offenders;
                # no declaration is edited, and the next complete candidate
                # must pass every adapter screen, mass budget and C1 bound.
                positions = [index for index, box in enumerate(boxes)
                             if box.dg is not None and "imdg.stowage.3_2" in
                             screen_load_list(candidate_vessel, [box])]
                if positions:
                    redraw = (boxes, positions)
            continue
        rng = Random(seed)
        rng.shuffle(boxes)
        n_blocked = int(math.floor(blocking_ratio * len(boxes) + 0.5))
        chosen = set(rng.sample([box.id for box in boxes], n_blocked))
        stacks = []
        background = []
        weight_classes = load_catalogue().weight_classes
        # Reserve two ground bays per target, avoiding all long-box overlaps. Row 1
        # is powered; final block permits DG. Keep capability checks on candidates.
        candidates = [(b, bay, row) for b in port.yard.blocks
                      for bay in range(1, b.n_bays, 2) for row in range(1, b.n_rows + 1)]
        rng.shuffle(candidates)
        for box in sorted(boxes, key=lambda b: (not b.reefer, b.dg is None, b.id)):
            match = next((i for i, (b, _, row) in enumerate(candidates)
                          if box.length_ft in b.allowed_lengths_ft
                          and (not box.reefer or row in b.reefer_rows)
                          and (box.dg is None or b.dg_block)), None)
            if match is None:
                raise ValueError("expert.physical.slot_occupancy: no legal footprint for sampled cargo")
            block, bay, row = candidates.pop(match)
            contents = [box.id]
            if box.id in chosen:
                # APPROXIMATION: dry, empty background is a later vessel cohort.
                # Clone size/height/tare only; special cargo facts never propagate.
                blocker = box.model_copy(update={"id": make_container_id("QZWU", len(background)),
                    "iso_type": {20: "22G1", 40: "42G1", 45: "L5G1"}[box.length_ft],
                    "height_m": 2.896 if box.length_ft == 45 else 2.591,
                    "gross_mass_kg": box.tare_kg, "empty": True, "reefer": False,
                    "reefer_setpoint_c": None, "dg": None, "oog": None,
                    "special_stowage": [], "weight_class": weight_classes.classify(box.tare_kg),
                    "category": f"later:dry:{box.length_ft}:{box.tare_kg.hex()}"})
                background.append(blocker)
                contents.append(blocker.id)
            stacks.append(YardStack(key=f"{block.id}-{bay:03d}-{row:02d}", length_ft=box.length_ft, boxes=contents))
        provenance = current_provenance(seed=seed, created_by="generate.random_yard")
        provenance.versions["random_yard"] = RANDOM_YARD_VERSION
        teu = sum(box.length_ft / 20 for box in boxes)
        mass = sum(box.gross_mass_kg for box in boxes)
        utilisation = {"teu_fraction": teu / len(vessel.cells)}
        if reference is not None:
            scale = len(vessel.cells) / reference.capacity_teu
            utilisation["cargo_mass_fraction"] = mass / (
                scale * reference.cargo_budget_kg(mass / scale))
        family_booking = {"region_capacity_teu": str(len(vessel.cells)),
            "booking_policy": booking_policy,
            "booking_utilisation_teu": repr(utilisation["teu_fraction"])}
        if "cargo_mass_fraction" in utilisation:
            family_booking["booking_utilisation_mass"] = repr(utilisation["cargo_mass_fraction"])
            family_booking["booking_requested_teu_fraction"] = repr(audit.booking_reference["requested_teu_fraction"])
        instance = Instance(id=f"random.{size}.{seed}.{blocking_ratio:g}", stage="A", size_class=size,
            family={"vessel": vessel_name, "port": port_name, "size": size,
                    "blocking_ratio": repr(float(blocking_ratio)), "seed": str(seed),
                    "generator": RANDOM_YARD_VERSION, **family_booking,
                    **({"c2_screen": "necessary"} if c2_screen else {})}, seed=seed, vessel=candidate_vessel,
            rotation=rotation, port=port, call_index=0,
            yard=YardState(yard_id=port.yard.id, stacks=stacks),
            onboard=ShipState(vessel_id=candidate_vessel.id,
                tank_masses_kg=candidate_tanks), containers=boxes + background,
            load_list=[b.id for b in boxes], provenance=provenance, voyage=voyage)
        audit.completion_checks += 1
        failed = completion_failures(instance)
        if failed:
            for reason in failed:
                audit.completion_rejections[reason] = audit.completion_rejections.get(reason, 0) + 1
            # Completion bounds can reject DG/weight classes despite spare
            # TEU; they do not establish a physical slot-occupancy violation.
            key = "completion.c1"
            audit.rejections[key] = audit.rejections.get(key, 0) + 1
            if "c1a" in failed:
                # APPROXIMATION: sequential shipment conditioning, rather than
                # independent whole-list rejection. Keep compatible bookings
                # and redraw individually impossible DG shipments from 2.5.
                # No DG fact is changed or waived. The next whole candidate
                # must pass the original capacity budgets and every C1 bound.
                positions = []
                for index, box in enumerate(boxes):
                    if box.dg is None:
                        continue
                    audit.completion_checks += 1
                    single = instance.model_copy(update={"load_list": [box.id]})
                    if "c1a" in completion_failures(single):
                        positions.append(index)
                if positions:
                    redraw = (boxes, positions)
            continue
        if c2_screen:
            from stowage.generate.random_screen import necessary_completion_failures

            diagnostics = {}
            audit.c2_checks += 1
            audit.c2_screened = True
            audit.c2_direction = "necessary"
            c2_failed = necessary_completion_failures(instance, diagnostics=diagnostics)
            for reason in c2_failed:
                audit.completion_rejections[reason] = audit.completion_rejections.get(reason, 0) + 1
            if "c2.unavailable" in c2_failed:
                error = diagnostics.get("error", "necessary linear model unavailable")
                audit.c2_errors[error] = audit.c2_errors.get(error, 0) + 1
                key = "checker.missing_vessel_data"
                audit.rejections[key] = audit.rejections.get(key, 0) + 1
                raise ValueError(f"C2 unavailable: {error}")
            if c2_failed:
                # The joint necessary screen does not isolate a single rule.
                # Its separate c2 counter identifies this completion rejection.
                key = "expert.physical.slot_occupancy"
                audit.rejections[key] = audit.rejections.get(key, 0) + 1
                continue
        # Run the same empty-plan full check only after this candidate survives
        # the cargo and completion screens. Unique backgrounds from rejected
        # bookings cannot share acceptance cache entries, so checking them
        # earlier repeats expensive D57 physics without enabling emission.
        # The probe retains exactly this candidate's vessel and booking tanks;
        # its separate witness still must pass before any instance is returned.
        if accept_background_candidate(background_probe, audit) is None:
            continue
        audit.accepted = True
        audit.booking_utilisation = utilisation
        audit.realised_teu = sum(box.length_ft / 20 for box in boxes)
        policy = full_export_policy(candidate_vessel, params)
        if policy and policy.is_export_count(len(boxes)):
            audit.full_export = policy.audit(sum(box.gross_mass_kg for box in boxes), audit.realised_teu)
        audit.targets, audit.blocked_targets = len(boxes), n_blocked
        audit.realised_blocking_ratio = globals()["blocking_ratio"](instance)
        return instance
    raise ValueError(f"screening exhausted {max_attempts} attempts: {audit.rejections}; C1: {audit.completion_rejections}")
'''
