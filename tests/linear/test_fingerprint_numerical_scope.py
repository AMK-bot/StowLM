"""Numerical cache identities exclude evidence while operation guards retain it."""
from copy import deepcopy

import pytest

from stowage.linear import cache
from stowage.linear.model import ValidityBand
from stowage.model.fixtures import build_instance


def identities(instance, parameters=None):
    parameters = {} if parameters is None else parameters
    band = ValidityBand(1., 2., .1, 1., .1, .1)
    return {
        "coefficient": cache.coefficient_family_key(instance, parameters, band),
        "model": cache.model_cache_key(instance, band, 64, parameters),
        "calibration": cache.cache_key("family_calibration", vessel_id=instance.vessel.id,
            params=parameters, inputs={"samples": 192}, band=band),
    }


@pytest.mark.parametrize("evidence", [
    {"kind": "unknown"},
    {"silhouette": {"x_min_m": 0., "x_max_m": 1.}},
    {"cargo_boxes": [{"provenance": {"parents": ["renewed-D42-evidence"]}}]},
])
def test_fixed_load_evidence_does_not_change_numerical_keys(evidence):
    instance = build_instance()
    before = identities(instance)
    strict_binding = cache.fixed_loads_fingerprint(instance)
    # Projection-unit mutations intentionally bypass geometry validation; these
    # records are inert inputs, never claimed to be checker-approved evidence.
    load = instance.vessel.fixed_loads[0].model_dump(mode="json")
    load.update(evidence)
    changed = instance.model_copy(deep=True)
    changed.vessel.fixed_loads = [load, *changed.vessel.fixed_loads[1:]]
    assert identities(changed) == before
    # An old assembled model must still reject changed full safety evidence.
    assert cache.fixed_loads_fingerprint(changed) != strict_binding


def test_legacy_point_and_named_background_have_same_numerical_identity():
    instance = build_instance()
    before = identities(instance)
    changed = instance.model_copy(deep=True)
    changed.vessel.fixed_loads = [tuple(load) for load in changed.vessel.fixed_loads]
    assert identities(changed) == before


def test_typed_d42_box_evidence_and_its_provenance_preserve_aggregate_numerics():
    from stowage.conventions import parse_slot
    from stowage.model import FixedCargoBox, FixedLoad

    instance = build_instance()
    before = identities(instance)
    load = instance.vessel.fixed_loads[0]
    cell = instance.vessel.cells[0]
    box = FixedCargoBox(cell_ids=[cell.id], stack_id=cell.stack_id,
        tier=parse_slot(cell.id).tier, length_ft=20, height_m=2.591,
        gross_mass_kg=load.mass_kg, x_m=load.x_m, y_m=load.y_m, z_m=load.z_m)
    # Typed aggregate evidence is valid on its own. This projection test does
    # not assert that the synthetic support describes a valid physical ship.
    changed = instance.model_copy(deep=True)
    changed.vessel.fixed_loads[0] = FixedLoad.model_validate({
        **load.model_dump(mode="json"), "kind": "cargo", "cargo_boxes": [box]})
    assert identities(changed) == before
    binding = cache.fixed_loads_fingerprint(changed)
    changed.vessel.fixed_loads[0].cargo_boxes[0].provenance.parents.append("renewed-D42-document")
    assert identities(changed) == before
    assert cache.fixed_loads_fingerprint(changed) != binding


@pytest.mark.parametrize("field", ["x_m", "y_m", "z_m", "mass_kg"])
def test_actual_background_mass_and_cg_invalidate_only_assembled_numerics(field):
    instance = build_instance()
    before = identities(instance)
    changed = instance.model_copy(deep=True)
    load = changed.vessel.fixed_loads[0]
    changed.vessel.fixed_loads[0] = load.model_copy(update={field: getattr(load, field)+1.})
    after = identities(changed)
    assert after["model"] != before["model"]
    assert after["coefficient"] == before["coefficient"]
    assert after["calibration"] == before["calibration"]


@pytest.mark.parametrize("field", ["min_trim_m_provenance", "max_trim_m_provenance",
    "min_displacement_kg_provenance", "max_displacement_kg_provenance"])
def test_d40_evidence_refresh_is_not_a_numerical_dependency(field):
    instance = build_instance()
    before = identities(instance)
    changed = instance.model_copy(deep=True)
    changed.vessel.hull = changed.vessel.hull.model_copy(update={
        field: {"source": "same-bound-renewed-document", "provenance": {"parents": ["new"]}}})
    assert identities(changed) == before


def test_d50_voyage_spec_and_policy_are_outside_departure_numerics():
    instance = build_instance()
    before = identities(instance)
    changed = instance.model_copy(update={"voyage": {"arrival": {"locode": "THLCH"}}}, deep=True)
    changed.vessel.voyage_policy = {"evidence": "renewed-voyage-consumption-policy"}
    assert identities(changed) == before


@pytest.mark.parametrize("field", ["min_trim_m", "max_trim_m", "min_displacement_kg", "max_displacement_kg"])
def test_actual_d40_endpoint_changes_invalidate_assembled_model(field):
    instance = build_instance()
    before = identities(instance)
    changed = instance.model_copy(deep=True)
    value = getattr(changed.vessel.hull, field)
    changed.vessel.hull = changed.vessel.hull.model_copy(update={field: 1. if value is None else value+1.})
    assert identities(changed)["model"] != before["model"]


@pytest.mark.parametrize("path", [
    ("hull", "lpp_m"), ("hull", "beam_m"), ("hull", "depth_m"),
    ("hull", "lightship_mass_kg"), ("hull", "lightship_lcg_m"),
    ("hull", "lightship_tcg_m"), ("hull", "lightship_vcg_m"),
    ("tanks", 0, "content_density_kg_m3"), ("tanks", 0, "capacity_m3"),
    ("cells", 0, "lcg_m"), ("stacks", 0, "base_z_m"),
])
def test_coefficient_and_assembled_keys_retain_actual_physical_inputs(path):
    instance = build_instance()
    before = identities(instance)
    changed = instance.model_copy(deep=True)
    target = changed.vessel
    for part in path[:-1]:
        target = target[part] if isinstance(part, int) else getattr(target, part)
    field = path[-1]
    setattr(target, field, getattr(target, field)+1.)
    after = identities(changed)
    assert after["coefficient"] != before["coefficient"]
    assert after["model"] != before["model"]


def test_geometry_parameters_bind_shape_and_density_but_not_evidence():
    instance = build_instance()
    parameters = {"hull": {"lpp_m": 100., "beam_m": 20.},
        "hull_form": {"family": "synthetic"},
        "tanks": [{"id": "T", "geometry": {"shape": "rectangular_prism", "x_min_m": 1.},
                   "content_density_kg_m3": 1025.}]}
    before = identities(instance, parameters)
    evidence = deepcopy(parameters)
    evidence["hull"]["min_trim_m_provenance"] = {"source": "D40 document"}
    evidence["tanks"][0]["provenance"] = {"parents": ["renewed"]}
    evidence["voyage_policy"] = {"source": "D50"}
    assert identities(instance, evidence) == before
    for field in ("geometry", "content_density_kg_m3"):
        changed = deepcopy(parameters)
        if field == "geometry":
            changed["tanks"][0][field]["x_min_m"] += 1.
        else:
            changed["tanks"][0][field] += 1.
        assert all(value != before[key] for key, value in identities(instance, changed).items())
