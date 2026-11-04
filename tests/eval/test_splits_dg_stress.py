"""D70's re-freeze is disjoint, explicit about cohorts, and never extra v2 draws."""
from collections import Counter
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from eval import splits

ROOT = Path(__file__).resolve().parents[2]
RELEASE = "dg-stress-v3"
PAIRS = ("flammable_water_reactive", "flammable_solid_flammable_liquid")
MAIN_BYTES_SHA256 = "e3fcf7559f5981118906e461e43079ae89e6dbcd653111881444c52de90f03dc"


def stress_spec(**changes):
    fields = dict(generator="random_yard", rung="small", vessel="feeder_500", slice="small",
                  port="laem_chabang_like", noise=None, blocking_ratio=0.2, seed=2**62 + 1,
                  cargo_profile="dg_stress", identity_version="v3", dg_stress_pair=None,
                  equipment_profile="dry20_dg_stress")
    return splits.InstanceSpec(**(fields | changes))


def test_stress_pair_selection_is_explicit_and_part_of_identity():
    mixed = stress_spec()
    assert splits.canonical_spec(mixed)["dg_stress_pair"] is None
    cohorts = [replace(mixed, dg_stress_pair=pair) for pair in PAIRS]
    assert len({splits.spec_key(spec) for spec in [mixed, *cohorts]}) == 3
    assert all(splits.split_of(spec) == "test" for spec in [mixed, *cohorts])
    assert all(splits.test_family(spec) == "dg_stress" for spec in [mixed, *cohorts])


@pytest.mark.parametrize("changes", [
    {"dg_stress_pair": "unknown"}, {"cargo_profile": "feeder_export"},
    {"identity_version": "v2", "dg_stress_pair": PAIRS[0]},
    {"generator": "simulated", "noise": 0.2, "blocking_ratio": None},
    {"equipment_profile": "full_mix"}, {"dense_20ft": True},
    {"tiny_dg_enabled": False}, {"without_dg": True},
])
def test_stress_v3_rejects_unrepresentable_or_misleading_controls(changes):
    with pytest.raises(ValueError):
        stress_spec(**changes)


def test_main_identity_and_manifest_bytes_do_not_change():
    raw = (ROOT / "experiments/splits/v2/manifest.json").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == MAIN_BYTES_SHA256
    manifest = json.loads(raw)
    for entry in manifest["entries"]:
        assert splits.canonical_spec(entry["spec"]) == entry["spec"]
        assert "dg_stress_pair" not in entry["spec"]


def test_stress_release_is_disjoint_balanced_test_only_and_pair_allocated():
    manifest = splits.load_manifest(RELEASE)
    assert manifest["version"] == RELEASE
    assert manifest["identity_version"] == "v3"
    assert len(manifest["entries"]) == 45
    main_inventory = splits.load_inventory()
    old = json.loads((ROOT / "experiments/splits/dg-stress-v1/manifest.json").read_text())
    old_v2 = json.loads((ROOT / "experiments/splits/dg-stress-v2/manifest.json").read_text())
    prior_seeds = {entry["spec"]["seed"] for m in (main_inventory.manifest, old, old_v2) for entry in m["entries"]}
    stress_seeds = set()
    for rung in ("tiny", "small"):
        entries = [entry for entry in manifest["entries"] if entry["spec"]["rung"] == rung]
        assert len(entries) == (15 if rung == "tiny" else 30)
        assert Counter(entry["spec"]["vessel"] for entry in entries) == {
            **{vessel: 5 if rung == "tiny" else 10 for vessel in ("feeder_500", "feeder_800", "feeder_1700")},
        }
        assert {entry["spec"]["generator"] for entry in entries} == {"random_yard"}
        assert {entry["spec"]["port"] for entry in entries} == {"laem_chabang_like", "singapore_like"}
        expected = {None: 15} if rung == "tiny" else {None: 14, PAIRS[0]: 8, PAIRS[1]: 8}
        assert Counter(entry["spec"]["dg_stress_pair"] for entry in entries) == expected
        for vessel in ("feeder_500", "feeder_800", "feeder_1700"):
            cells = [entry["spec"] for entry in entries if entry["spec"]["vessel"] == vessel]
            if rung == "tiny":
                assert Counter(spec["dg_stress_pair"] for spec in cells) == {None: 5}
            else:
                assert len(cells) == 10
                assert all(sum(spec["dg_stress_pair"] == pair for spec in cells) >= 2 for pair in PAIRS)
        for entry in entries:
            assert entry["split"] == "test"
            assert entry["test_family"] == "dg_stress"
            spec = splits.InstanceSpec(**entry["spec"])
            assert spec.cargo_profile == "dg_stress"
            assert spec.equipment_profile == "dry20_dg_stress"
            assert spec.seed > 2**62
            assert spec.tiny_dg_enabled and not spec.without_dg and not spec.dense_20ft
            assert spec.n_boxes is None
            stress_seeds.add(spec.seed)
            with pytest.raises(ValueError, match="not in"):
                main_inventory.require_membership(spec)
    assert len(stress_seeds) == 45
    assert stress_seeds.isdisjoint(prior_seeds)
    assert manifest["counts"] == {"tiny": {"test": {"dg_stress": 15}}, "small": {"test": {"dg_stress": 30}}}


@pytest.mark.parametrize("identity_tag", ("identity_version", "split_identity_version"))
def test_stress_generated_tags_roundtrip_the_pair_and_new_identity(identity_tag):
    for pair in (None, *PAIRS):
        spec = stress_spec(dg_stress_pair=pair)
        family = {"size": "small", "slice": "small", "vessel": spec.vessel, "port": spec.port,
                  "blocking_ratio": "0.2", "seed": str(spec.seed), "requested_n_boxes": "auto",
                  "cargo_profile": "dg_stress", identity_tag: "v3",
                  "equipment_profile": "dry20_dg_stress", "dg_stress_pair": pair or "mixed"}
        instance = SimpleNamespace(family=family, seed=spec.seed, size_class="small",
                                   provenance=SimpleNamespace(created_by="generate.random_yard"))
        assert splits.spec_from_instance(instance) == spec
        del family["dg_stress_pair"]
        with pytest.raises(ValueError, match="cohort tag"):
            splits.spec_from_instance(instance)


def test_stress_allocation_reproduces_without_generating_or_solving():
    path = ROOT / "experiments/splits" / RELEASE / "freeze.py"
    module_spec = importlib.util.spec_from_file_location("freeze_dg_stress", path)
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    assert module.build_manifest() == splits.load_manifest(RELEASE)


def test_stress_readme_preserves_amended_d70_contract():
    readme = " ".join((ROOT / "experiments/splits" / RELEASE / "README.md").read_text().split())
    for required in (
        "D70", "15 tiny and 30 small", "random-yard only", "cargo_profile: dg_stress",
        "never pool them with v2", "no training or validation", "SP377", "never backfill",
        "10 admitted test instances", "5 binding instances", "feasible-witnessed",
        "proven-infeasible", "generation-rejected", "unknown", "dg_stress_pair",
        "DG safety under stress, zero-shot transfer", "base Glimmer + mask",
        "dead-end/repair rates", "Wilson", "n = 15", "best passing plan",
        "exactly the minimum separation", "IMDG 7.2.4", "no slack",
        "Mere co-presence", "outside every frozen release", "eight small specs per pair",
        "more than 30%", "before regeneration", "random-yard only", "dry 20ft only",
        "every loaded box DG", "foreign yard boxes are never DG", "SG25",
        "UN1333", "UN1263", "pending",
    ):
        assert required in readme
    for version in ("dg-stress-v1", "dg-stress-v2"):
        old_readme = (ROOT / "experiments/splits" / version / "README.md").read_text().lower()
        assert "superseded" in old_readme and "never used" in old_readme


@pytest.mark.parametrize("checker_test_namespace", ["available", "unavailable"])
def test_stress_non_un1402_pair_is_emittable_and_requires_separation(
        monkeypatch, checker_test_namespace):
    """Real DG legality on a synthetic fixture; not whole-physics admission yield."""
    if checker_test_namespace == "unavailable":
        # importlib-mode collection can register `tests` before its checker
        # children exist (e.g. eval-c1 first). The fixture must stand alone.
        import sys
        monkeypatch.setitem(sys.modules, "tests.checker", None)
    from stowage.checker.rules_imdg import SHIP, STOWAGE, imdg_violations
    from stowage.generate import cargo
    from stowage.model.cargo import DangerousGoods, PackingForm
    from stowage.model.fixtures import build_instance
    from stowage.model.ship import Placement, ShipState
    from stowage.rules.catalogue import rule_freeze_v1
    from stowage.rules.imdg.dgl import lookup_dgl
    from stowage.rules.imdg.tables import general_segregation

    assert general_segregation("4.1", "3").value == "X"
    assert {"SG25", "SG26"} <= set(lookup_dgl(1333, "II").segregation_codes)
    assert lookup_dgl(1263, "II").segregation_codes == ()
    with rule_freeze_v1(enabled=True):
        # Own this geometry fixture rather than importing private builders from
        # other test modules: pytest's namespace depends on collection order.
        instance = build_instance(with_dg_doc=True)
        instance.schema_version = "1.9"
        instance.vessel.fixed_loads = []
        instance.vessel.heat_sources = []
        instance.vessel.tanks = []
        instance.vessel.bridge = None
        solid, liquid = instance.containers[1:3]
        instance.containers = [solid, liquid]
        ship = ShipState(vessel_id=instance.vessel.id)
        instance.rule_overrides[SHIP] = {
            "ship_type": "closed_holds",
            "slots": {"0010182": {"closed": True, "hold_id": "aft"},
                      "0030182": {"closed": True, "hold_id": "fore"}},
            "pairs": {"0010182|0030182": {
                "bulkheads": 1, "barriers_resistant_to_fire_and_liquids": True,
                "distance_from_intervening_bulkhead_m": 6}},
        }
        for cell in instance.vessel.cells:
            if cell.id.startswith("003"):
                cell.lcg_m += 100
        instance.rule_overrides[STOWAGE] = {}
        manifests = {}
        for box, un in ((solid, 1333), (liquid, 1263)):
            entry = lookup_dgl(un, "II")
            box.dg = DangerousGoods(
                un_number=f"{un:04d}", imdg_class=entry.hazard_class,
                subsidiary_risks=list(entry.subsidiary_risks), packing_group="II",
                stowage_category=entry.stowage_category,
                stowage_codes=list(entry.stowage_codes),
                segregation_codes=list(entry.segregation_codes))
            box.dg.packing_form = PackingForm.PACKAGED_GOODS_IN_CLOSED_FREIGHT_CONTAINERS
            box.dg.ctu_closed = True
            facts = cargo.declare_entry_scenarios(lookup_dgl(un, "II"), iso_type=box.iso_type)
            if facts:
                manifests[box.id] = {"primary": facts}
        declaration = manifests[solid.id]["primary"]
        assert declaration["synthetic"] is True
        assert declaration["scope"] == "shipment_entry"
        assert declaration["h1"]["packing_condition"] == "dry"
        assert "verified_segregation_codes" not in declaration
        report = cargo.CargoGenerationReport()
        report.realised = {"n_boxes": 2, "shipment_scenarios": manifests}
        for stack in instance.vessel.stacks:
            stack.dg_classes_allowed = ["4.1", "3"]
        for area in instance.vessel.dg_doc_areas:
            area.classes_allowed = ["4.1", "3"]
        ship.placements = [Placement(slot=slot, container_id=box.id)
                           for box, slot in zip((solid, liquid), ("0010182", "0030182"))]
        # Design-fixture geometry: satisfy both the strengthened level and SG26.
        for cell in instance.vessel.cells:
            if cell.id.startswith("003"):
                cell.tcg_m += 12
        bound = cargo.bind_shipment_scenarios(instance, report)
        assert imdg_violations(bound, ship, call_index=0) == []
        first = next(cell for cell in bound.vessel.cells if cell.id == "0010182")
        for cell in bound.vessel.cells:
            if cell.id.startswith("003"):
                cell.lcg_m, cell.tcg_m = first.lcg_m + 6.1, first.tcg_m
        violations = imdg_violations(bound, ship, call_index=0)
        assert {violation.rule_id for violation in violations} == {SHIP}
        assert any("level 2" in violation.message for violation in violations)
        assert any("SG26" in violation.message for violation in violations)
