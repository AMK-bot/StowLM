"""Cargo sampling tests; probability bands are six binomial SD plus two boxes.

Weights use 22 analytic CDF probes per equipment/POD stratum, including both
clipped boundary atoms (the upper atom is isolated just below the cap). The same six-SD bands apply to each probe. These are
explicit conservative bands, not a claim of measured demand calibration.
All statistical samples use the unmodified default profile and real DGL.
A separate missing-entry regression checks explicit failure without resampling.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import math

import pytest

from stowage.checker.rules_imdg import imdg_placement_violations
from stowage.conventions import is_valid_container_id
from stowage.generate.cargo import (
    CargoGenerationReport, UnsupportedCargoError, generate_load_list,
    generate_onboard_cargo,
)
from stowage.generate.cargo_shipments import shipment_requirements
from stowage.generate.cargo_limits import MAX_GENERATED_GROSS_MASS_KG
from stowage.model.cargo import PackingForm
from stowage.model.fixtures import build_instance
from stowage.model.port import PortCall, Rotation
from stowage.params import load_param_file
from stowage.rules.catalogue import Catalogue, load_catalogue
from stowage.rules.imdg.dgl import entries_for_un


def value(table, key):
    return float(table[key]["value"])


def missing_entries(params):
    return [(c["id"], u["un_number"]) for c in params["dg"]["classes"] for u in c["un_numbers"]
            if not any(e.hazard_class == c["id"] for e in entries_for_un(int(u["un_number"])))]


@pytest.fixture(scope="module")
def params():
    return load_param_file("cargo", "feeder_export")



def band(count, n, probability):
    assert abs(count - n * probability) <= 6 * math.sqrt(n * probability * (1 - probability)) + 2, (count, n, probability)


def test_default_dgl_integration(params):
    assert missing_entries(params) == []


def test_missing_dgl_entry_fails_explicitly(monkeypatch):
    monkeypatch.setattr("stowage.generate.cargo.entries_for_un", lambda un: ())
    audit = CargoGenerationReport()
    with pytest.raises(UnsupportedCargoError, match="DGL has no supported entry"):
        generate_load_list("straits", 0, 20_000, seed=25, report=audit)
    assert audit.rejections and "DGL" in audit.rejections[0]


@pytest.mark.parametrize("route_id", ["straits", "vietnam_straits", "south_china_vietnam"])
def test_20000_box_statistics(params, route_id):
    p = params
    assert missing_entries(p) == []
    audit = CargoGenerationReport()
    boxes = generate_load_list(route_id, 0, 20_000, seed=250526, params=p, report=audit)
    n = len(boxes)
    route = next(r for r in p["rotations"] if r["id"] == route_id)
    assert n == 20_000
    assert len({b.id for b in boxes}) == n
    assert all(is_valid_container_id(b.id) for b in boxes)
    assert audit.realised["n_boxes"] == n
    assert audit.requested["dg"] == value(p["dg"], "implied_share_of_all_boxes")
    shared = load_catalogue().weight_classes
    medium = shared.parameters["medium_min_kg"].value
    heavy = shared.parameters["heavy_min_kg"].value
    for box in boxes:
        expected_class = "L" if box.gross_mass_kg < medium else "M" if box.gross_mass_kg < heavy else "H"
        assert box.weight_class == expected_class
    assert audit.requested["weight_classes"] == {key: parameter.value for key, parameter in shared.parameters.items()}
    assert audit.realised["weight_classes_by_size"] == {
        str(length): {label: sum(box.length_ft == length and box.weight_class == label for box in boxes)
                      for label in ("L", "M", "H")}
        for length in (20, 40, 45)
    }
    equipment = Counter(b.iso_type for b in boxes)
    for e in p["equipment"]:
        pool = [b for b in boxes if b.iso_type == e["iso_size_type"]]
        band(equipment[e["iso_size_type"]], n, value(e, "share"))
        band(sum(b.empty for b in pool), len(pool), value(e, "empty_share"))
        assert all(b.tare_kg == value(e, "tare_kg") for b in pool)
        assert all(b.gross_mass_kg == value(e["empty_weight_kg"], "value") for b in pool if b.empty)
        assert all(b.tare_kg <= b.gross_mass_kg <= value(e, "max_gross_kg") for b in pool)
        for pod in route["pods"]:
            # POD/equipment independence and equipment-specific conditional CDF.
            band(sum(b.pod == pod["port"] for b in pool), len(pool), value(pod, "share"))
            loaded = [b for b in pool if not b.empty and b.pod == pod["port"]]
            assert loaded
            weights = e["loaded_weight_kg"]
            low, mode, high = [value(weights, key) for key in ("min", "mode", "max")]
            tare, multiplier = value(e, "tare_kg"), value(pod, "payload_weight_multiplier")
            assert all(low <= b.gross_mass_kg <= min(high, MAX_GENERATED_GROSS_MASS_KG) for b in loaded)
            probes = [low + i * (high - low) / 20 for i in range(21)]
            # Just below the cap isolates its clipping atom; x == high alone
            # merely verifies the upper bound rather than the mass piled there.
            probes.append(math.nextafter(high, -math.inf))
            for x in probes:
                # P(clipped transform(G) <= x), preserving atoms at both bounds.
                if x >= min(high, MAX_GENERATED_GROSS_MASS_KG):
                    probability = 1.0
                else:
                    raw = tare + (x - tare) / multiplier
                    if raw <= low:
                        probability = 0.0
                    elif raw >= high:
                        probability = 1.0
                    elif raw <= mode:
                        probability = (raw - low) ** 2 / ((high - low) * (mode - low))
                    else:
                        probability = 1 - (high - raw) ** 2 / ((high - low) * (high - mode))
                    # D60 truncates the original transformed distribution.
                    # Its accepted CDF is the old CDF divided by retained mass,
                    # rather than a triangular with a different maximum.
                    cap_raw = tare + (MAX_GENERATED_GROSS_MASS_KG - tare) / multiplier
                    if high > MAX_GENERATED_GROSS_MASS_KG and cap_raw < high:
                        if cap_raw <= mode:
                            retained = (cap_raw - low) ** 2 / ((high - low) * (mode - low))
                        else:
                            retained = 1 - (high - cap_raw) ** 2 / ((high - low) * (high - mode))
                        probability /= retained
                band(sum(b.gross_mass_kg <= x for b in loaded), len(loaded), probability)
    for pod in route["pods"]:
        band(sum(b.pod == pod["port"] for b in boxes), n, value(pod, "share"))
    for length, share in ((20, .33), (40, .66), (45, .01)):
        band(sum(b.length_ft == length for b in boxes), n, share)
    band(sum(b.height_m > 2.8 for b in boxes), n, .47)
    band(sum(b.empty for b in boxes), n, .2)
    band(sum(b.reefer for b in boxes), n, .04)
    for section, check in (("dg", lambda b: b.dg is not None), ("oog", lambda b: b.oog is not None),
                           ("special_stowage", lambda b: bool(b.special_stowage))):
        band(sum(check(b) for b in boxes), n, value(p[section], "implied_share_of_all_boxes"))
    dg_pool = [b for b in boxes if b.iso_type in {e["iso_size_type"] for e in p["equipment"] if e["id"] in p["dg"]["eligible_equipment_ids"]} and not b.empty]
    band(sum(b.dg is not None for b in dg_pool), len(dg_pool), value(p["dg"], "share_of_eligible_loaded_boxes"))
    dg = [b.dg for b in boxes if b.dg]
    assert all(d.packing_form == PackingForm.PACKAGED_GOODS_IN_CLOSED_FREIGHT_CONTAINERS and d.ctu_closed is True for d in dg)
    assert audit.realised["dg_packing_forms"] == {"packaged_goods_in_closed_freight_containers": len(dg)}
    assert audit.realised["dg_packing_forms_by_un"] == {
        un: {"packaged_goods_in_closed_freight_containers": count}
        for un, count in sorted(Counter(d.un_number for d in dg).items())
    }
    for box in boxes:
        if box.dg is not None:
            entry = next(e for e in entries_for_un(int(box.dg.un_number)) if e.packing_group == box.dg.packing_group)
            expected = shipment_requirements(entry)
            assert audit.shipment_requirements.get(box.id, []) == expected
            assert all(getattr(box.dg, field) is None for field in expected)
    class_total = sum(value(c, "share") for c in p["dg"]["classes"])
    for c in p["dg"]["classes"]:
        class_pool = [d for d in dg if d.imdg_class.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ") == c["id"]]
        band(len(class_pool), len(dg), value(c, "share") / class_total)
        for u in c["un_numbers"]:
            band(sum(d.un_number == u["un_number"] for d in class_pool), len(class_pool), value(u, "share"))
    for d in dg:
        entries = entries_for_un(int(d.un_number))
        entry = next(e for e in entries if e.packing_group == d.packing_group)
        assert d.stowage_category == entry.stowage_category
        assert d.imdg_class == entry.hazard_class + (entry.compatibility_group or "")
        assert d.subsidiary_risks == sorted(entry.subsidiary_risks)
        assert d.segregation_codes == sorted(entry.segregation_codes)
        assert d.stowage_codes == sorted(c for c in entry.stowage_codes if not c.startswith("H"))
        assert d.handling_codes == sorted(c for c in entry.stowage_codes if c.startswith("H"))
    expected_guards = {}
    for box in boxes:
        if box.dg is None:
            continue
        entry = next(e for e in entries_for_un(int(box.dg.un_number)) if e.packing_group == box.dg.packing_group)
        guards = [provision.code for provision in entry.unresolved_provisions]
        if entry.stowage_category not in {"A", "B", "C", "D", "E"}:
            guards.append(f"class_1_stowage_category:{entry.stowage_category}")
        if guards:
            expected_guards[box.id] = sorted(set(guards))
    assert audit.unresolved_provisions == expected_guards
    assert audit.realised["unresolved_provisions"] == len(expected_guards)
    observed_guards = Counter(code for codes in expected_guards.values() for code in codes)
    assert observed_guards["IMDG:7.1.4.5"] > 0
    assert observed_guards["class_1_stowage_category:02"] > 0
    # A sampled demand remains present but cannot clear the checker merely
    # because its frequency and declaration match the generator profile.
    fixture = build_instance()
    for tag, message in (("IMDG:7.1.4.5", "IMDG:7.1.4.5"),
                         ("class_1_stowage_category:02", "verified explosives arrangements")):
        box = next(b for b in boxes if tag in expected_guards.get(b.id, []))
        reconstructed = type(box).model_validate_json(box.model_dump_json())
        assert reconstructed == box
        if tag.startswith("IMDG:"):
            assert tag in reconstructed.dg.stowage_codes
        slot = "0010182" if reconstructed.length_ft == 20 else "0020182"
        violations = imdg_placement_violations(fixture, fixture.onboard, reconstructed, slot)
        assert any(v.severity == "hard" and message in v.message for v in violations)
    oog_eligible = [b for b in boxes if b.iso_type in {e["iso_size_type"] for e in p["equipment"] if e["id"] in p["oog"]["eligible_equipment_ids"]} and not b.empty]
    band(sum(b.oog is not None for b in oog_eligible), len(oog_eligible), value(p["oog"], "share_of_loaded_compatible_boxes"))
    oog = [b.oog for b in boxes if b.oog]
    for profile in p["oog"]["profiles"]:
        band(sum(o.over_height_m == value(profile, "over_height_m") and o.over_width_port_m == value(profile, "over_width_each_side_m") and o.over_length_fore_m == value(profile, "over_length_each_end_m") for o in oog), len(oog), value(profile, "share"))
    e8_eligible = [b for b in boxes if not b.empty and not b.oog]
    band(sum(bool(b.special_stowage) for b in e8_eligible), len(e8_eligible), value(p["special_stowage"], "share_of_loaded_boxes"))
    e8 = [b for b in boxes if b.special_stowage]
    for request in p["special_stowage"]["requests"]:
        band(sum(request["id"] in b.special_stowage for b in e8), len(e8), value(request, "share"))
    for b in boxes:
        if b.empty:
            assert not (b.dg or b.oog or b.special_stowage or b.reefer)
        if b.oog:
            assert not (b.dg or b.special_stowage or b.empty)
            assert b.oog.over_width_port_m == b.oog.over_width_starboard_m
            assert b.oog.over_length_fore_m == b.oog.over_length_aft_m
        if b.dg or b.oog or b.special_stowage or b.reefer:
            assert b.category is None


def test_seed_ids_categories_and_audit(params):
    a, b = CargoGenerationReport(), CargoGenerationReport()
    first = generate_load_list("straits", 0, 20_000, seed=71, params=params, report=a)
    second = generate_load_list("straits", 0, 20_000, seed=71, params=params, report=b)
    assert [box.model_dump_json() for box in first] == [box.model_dump_json() for box in second]
    assert a == b
    assert first != generate_load_list("straits", 0, 20_000, seed=72, params=params)
    offset = generate_load_list("straits", 0, 20_000, seed=71, params=params, id_offset=20_000)
    assert {box.id for box in first}.isdisjoint(box.id for box in offset)
    groups = {}
    group_masses = {}
    for box in first:
        if box.category:
            facts = (box.pod, box.iso_type, box.length_ft, box.height_m, box.empty, box.weight_class)
            assert groups.setdefault(box.category, facts) == facts
            group_masses.setdefault(box.category, set()).add(box.gross_mass_kg)
    # A retrieval group intentionally admits different exact masses; those
    # masses remain facts for the checker, never replaced by the class label.
    assert any(len(masses) > 1 for masses in group_masses.values())
    assert a.unchecked and a.conditioning


def test_route_sampling_shares(params):
    observed = Counter()
    for seed in range(2000):
        audit = CargoGenerationReport()
        generate_load_list(None, 0, 0, seed=seed, report=audit)
        observed[audit.rotation] += 1
    for route in params["rotations"]:
        band(observed[route["id"]], 2000, value(route, "share"))


def test_future_occurrences_and_onboard(params):
    route = Rotation(service="straits", calls=[PortCall(index=i, locode=p) for i, p in enumerate(["THLCH", "SGSIN", "MYPKG", "SGSIN", "THLCH"])])
    audit = CargoGenerationReport()
    boxes = generate_load_list(route, 1, 200, seed=10, params=params, report=audit)
    assert {b.pod for b in boxes} == {"SGSIN", "MYPKG"}  # first subsequent occurrence, not unique LOCODE set of past calls
    boxes = generate_load_list("straits", 1, 100, seed=10, params=params)
    assert {b.pod for b in boxes} == {"MYPKG"}
    vessel = build_instance().vessel
    onboard = generate_onboard_cargo(vessel, "straits", 1, seed=71, n_boxes=10, params=params, report=audit)
    assert {b.pod for b in onboard} == {"MYPKG"}
    assert {b.pol for b in onboard} == {"THLCH"}
    exports = generate_load_list("straits", 1, 10, seed=71, params=params)
    assert {b.id for b in onboard}.isdisjoint(b.id for b in exports)
    assert audit.conditioning and audit.unchecked
    with pytest.raises(UnsupportedCargoError, match="no supported POD"):
        generate_onboard_cargo(vessel, "straits", 3, seed=71, n_boxes=10, params=params)


def test_vessel_support_is_not_silently_resampled(params):
    p = deepcopy(params)
    p["equipment"] = [next(e for e in p["equipment"] if e["id"] == "high_cube_45")]
    vessel = build_instance().vessel
    for cell in vessel.cells:
        cell.accepts_45 = False
    audit = CargoGenerationReport()
    with pytest.raises(UnsupportedCargoError, match="45-foot"):
        generate_onboard_cargo(vessel, "straits", 0, seed=1, n_boxes=5, params=p, report=audit)
    assert audit.realised["n_boxes"] == 5 and audit.rejections


def test_teu_budget_is_explicit(params):
    audit = CargoGenerationReport()
    boxes = generate_load_list("straits", 0, seed=13, teu=100.0, params=params, report=audit)
    total = sum(b.length_ft / 20 for b in boxes)
    assert total <= 100 < total + 2.25
    assert total == audit.realised["teu"]
    assert any("TEU budget" in message for message in audit.conditioning)


@pytest.mark.parametrize("kwargs", [{}, {"n_boxes": 1, "teu": 2}, {"n_boxes": -1}, {"n_boxes": 1.5}, {"n_boxes": True}, {"teu": float("nan")}, {"teu": -1}, {"n_boxes": 2, "id_offset": 999999}])
def test_quantity_validation(kwargs):
    with pytest.raises(ValueError):
        generate_load_list("straits", 0, seed=1, **kwargs)


@pytest.mark.parametrize("index", [-1, 4, 1.5, True])
def test_call_validation(index):
    with pytest.raises(ValueError, match="call_index"):
        generate_load_list("straits", index, 0, seed=1)


@pytest.mark.parametrize("path", [
    ("equipment", "empty_share"),
    ("dg", "share_of_eligible_loaded_boxes"),
    ("oog", "share_of_loaded_compatible_boxes"),
    ("special_stowage", "share_of_loaded_boxes"),
])
@pytest.mark.parametrize("bad", [1.5, -0.1, math.nan, math.inf, -math.inf])
@pytest.mark.parametrize("named", [False, True])
def test_invalid_bernoulli_probabilities_fail_before_sampling(params, monkeypatch, path, bad, named):
    profile = deepcopy(params)
    section, field = path
    rows = profile[section] if section == "equipment" else [profile[section]]
    for row in rows:
        row[field]["value"] = bad
    if named:
        monkeypatch.setattr("stowage.generate.cargo.load_param_file", lambda *args: profile)
    audit = CargoGenerationReport()
    # Even an unused branch/zero-box request must reject the invalid profile.
    with pytest.raises(ValueError, match=field):
        generate_load_list("straits", 0, 0, seed=1, params="custom" if named else profile, report=audit)
    assert len(audit.rejections) == 1
    assert field in audit.rejections[0]
    assert audit.realised == {}


@pytest.mark.parametrize("failure", [
    "quantity", "teu_type", "id_offset", "route", "call", "parameter_file",
    "missing_parameter", "catalogue", "shares", "weight", "namespace", "container",
])
def test_reused_audit_records_early_and_mid_generation_failure(params, monkeypatch, failure):
    audit = CargoGenerationReport()
    generate_load_list("straits", 0, 2, seed=1, params=params, report=audit)
    audit.unresolved_provisions["stale"] = ["stale"]
    audit.shipment_requirements["stale"] = ["stale"]
    profile = deepcopy(params)
    kwargs = dict(rotation="straits", call_index=0, n_boxes=2, seed=2,
                  params=profile, report=audit)
    expected = ValueError
    if failure == "quantity":
        kwargs["n_boxes"] = -1
    elif failure == "teu_type":
        kwargs.update(n_boxes=None, teu="bad")
        expected = TypeError
    elif failure == "id_offset":
        kwargs["id_offset"] = -1
    elif failure == "route":
        kwargs["rotation"] = "unsupported"
    elif failure == "call":
        kwargs["call_index"] = -1
    elif failure == "parameter_file":
        def missing_file(*args):
            raise FileNotFoundError("missing custom profile")
        monkeypatch.setattr("stowage.generate.cargo.load_param_file", missing_file)
        kwargs["params"] = "missing"
        expected = FileNotFoundError
    elif failure == "missing_parameter":
        del profile["equipment"]
        expected = KeyError
    elif failure == "catalogue":
        def invalid_catalogue():
            raise ValueError("invalid catalogue")
        monkeypatch.setattr("stowage.generate.cargo.load_catalogue", invalid_catalogue)
    elif failure == "shares":
        for e in profile["equipment"]:
            e["share"]["value"] = 0
    elif failure in {"weight", "container"}:
        profile["equipment"] = [profile["equipment"][0]]
        e = profile["equipment"][0]
        e["empty_share"]["value"] = 0
        if failure == "weight":
            e["loaded_weight_kg"]["family"] = "invalid"
        else:
            e["height_m"]["value"] = 3.0
    elif failure == "namespace":
        kwargs["id_offset"] = 999999
    with pytest.raises(expected) as error:
        generate_load_list(**kwargs)
    assert audit.seed == 2
    assert audit.call_index == kwargs["call_index"]
    assert audit.realised == {}
    assert audit.rejections == [str(error.value)]
    assert "stale" not in audit.unresolved_provisions
    assert "stale" not in audit.shipment_requirements


def test_synthetic_csc_ratings_preserve_seeded_demand(params, monkeypatch):
    audit = CargoGenerationReport()
    boxes = generate_load_list("straits", 0, 400, seed=122, params=params, report=audit)
    ratings = {e["iso_size_type"]: value(e, "max_gross_kg") for e in params["equipment"]}
    assert {b.iso_type for b in boxes} == set(ratings)
    assert all(b.max_gross_mass_kg == ratings[b.iso_type] >= b.gross_mass_kg for b in boxes)
    policy = audit.requested["synthetic_csc_ratings"]
    assert policy["kind"] == "synthetic_assumed_individual_plate_ratings"
    assert policy["profile_id"] == params["profile_id"]
    assert policy["equipment"] == {
        e["id"]: {"iso_size_type": e["iso_size_type"], "max_gross_kg": e["max_gross_kg"]}
        for e in params["equipment"]
    }
    # D60 intentionally changes masses and their derived labels, but its exact
    # inverse-CDF truncation consumes one uniform just like the original draw:
    # equipment, empty/POD, DG and OOG demand are preserved for the same seed.
    with monkeypatch.context() as original_distribution:
        original_distribution.setattr("stowage.generate.cargo.MAX_GENERATED_GROSS_MASS_KG", math.inf)
        original = generate_load_list("straits", 0, 400, seed=122, params=params)
    excluded = {"gross_mass_kg", "weight_class", "category"}
    assert [b.model_dump(mode="json", exclude=excluded) for b in boxes] == [
        b.model_dump(mode="json", exclude=excluded) for b in original
    ]


def test_onboard_cargo_has_synthetic_ratings_and_fresh_failure_audit(params):
    vessel = build_instance().vessel
    audit = CargoGenerationReport()
    for equipment in params["equipment"]:
        profile = deepcopy(params)
        profile["equipment"] = [deepcopy(equipment)]
        box = generate_onboard_cargo(vessel, "straits", 0, seed=1, n_boxes=1,
                                     params=profile, report=audit)[0]
        assert box.max_gross_mass_kg == value(equipment, "max_gross_kg")
        assert audit.requested["synthetic_csc_ratings"]["kind"] == "synthetic_assumed_individual_plate_ratings"
    with pytest.raises(ValueError, match="n_boxes") as error:
        generate_onboard_cargo(vessel, "straits", 0, seed=2, n_boxes=-1,
                               params=params, report=audit)
    assert audit.seed == 2
    assert audit.realised == {}
    assert audit.rejections == [str(error.value)]


@pytest.mark.parametrize("failure", ["unsupported", "invalid_cells", "invalid_cell"])
def test_onboard_vessel_failure_reports_current_inventory(params, failure):
    vessel = build_instance().vessel
    profile = deepcopy(params)
    profile["equipment"] = [next(e for e in profile["equipment"] if e["id"] == "high_cube_45")]
    audit = CargoGenerationReport()
    generate_onboard_cargo(vessel, "straits", 0, seed=1, n_boxes=1,
                           params=profile, report=audit)
    if failure == "unsupported":
        for cell in vessel.cells:
            cell.accepts_45 = False
        expected = UnsupportedCargoError
    elif failure == "invalid_cells":
        vessel.cells = None
        expected = TypeError
    else:
        vessel.cells = [object()]
        expected = AttributeError
    with pytest.raises(expected):
        generate_onboard_cargo(vessel, "straits", 0, seed=2, n_boxes=2,
                               params=profile, report=audit)
    assert audit.seed == 2
    # Inventory was generated and then rejected by the vessel-support step.
    assert audit.realised["n_boxes"] == 2
    assert audit.rejections


def point_weight_profile(params, mass):
    """A controlled cargo scenario isolates the class cut without RNG rounding."""
    profile = deepcopy(params)
    equipment = next(e for e in profile["equipment"] if e["id"] == "dry_20")
    profile["equipment"] = [equipment]
    equipment["empty_share"]["value"] = 0
    for field in ("min", "mode", "max"):
        equipment["loaded_weight_kg"][field]["value"] = mass
    profile["dg"]["share_of_eligible_loaded_boxes"]["value"] = 0
    profile["special_stowage"]["share_of_loaded_boxes"]["value"] = 0
    for rotation in profile["rotations"]:
        for pod in rotation["pods"]:
            pod["payload_weight_multiplier"]["value"] = 1
    return profile


@pytest.mark.parametrize("mass,expected", [
    (math.nextafter(10_000.0, -math.inf), "L"),
    (10_000.0, "M"),
    (math.nextafter(20_000.0, -math.inf), "M"),
    (20_000.0, "H"),
])
def test_shared_weight_class_boundaries(params, mass, expected):
    boxes = generate_load_list("straits", 0, 1, seed=42,
                               params=point_weight_profile(params, mass))
    assert boxes[0].gross_mass_kg == mass
    assert boxes[0].weight_class == expected
    assert f":{expected}:" in boxes[0].category


def test_weight_classes_follow_catalogue_changes(params, monkeypatch):
    """Changing the one shared catalogue source changes the next generated list."""
    baseline = load_catalogue()
    raw = baseline.model_dump(mode="python")
    raw["weight_classes"]["parameters"]["medium_min_kg"]["value"] = 15_000
    raw["weight_classes"]["parameters"]["heavy_min_kg"]["value"] = 25_000
    revised = Catalogue.model_validate(raw)
    calls = []

    def catalogue_source():
        calls.append(True)
        return revised

    for mass, expected in ((12_000, "M"), (22_000, "H")):
        box = generate_load_list("straits", 0, 1, seed=42,
                                 params=point_weight_profile(params, mass))[0]
        assert box.weight_class == expected
    monkeypatch.setattr("stowage.generate.cargo.load_catalogue", catalogue_source)
    for mass, expected in ((12_000, "L"), (22_000, "M"), (26_000, "H")):
        audit = CargoGenerationReport()
        box = generate_load_list("straits", 0, 1, seed=42,
                                 params=point_weight_profile(params, mass), report=audit)[0]
        assert box.weight_class == expected
        assert box.gross_mass_kg == mass
        assert f":{expected}:" in box.category
        assert audit.requested["weight_classes"]["medium_min_kg"] == 15_000
        assert audit.requested["weight_classes"]["heavy_min_kg"] == 25_000
    assert len(calls) == 3  # per-call lookup, not cached initial boundaries
