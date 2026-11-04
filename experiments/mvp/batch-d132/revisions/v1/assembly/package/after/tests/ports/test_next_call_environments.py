"""D50 next-call evidence uses explicit synthetic density declarations."""
from copy import deepcopy
from hashlib import sha256
from unittest.mock import patch

import pytest

from stowage.generate import instances_voyage, random_voyage
from stowage.model.port import PortCall, PortProfile, Rotation
from stowage.params import load_param_file, params_dir
from stowage.ports import list_ports, load_port, load_simulator_params
from stowage.ports import profiles


ENVIRONMENTS = [("cat_lai_like", "VNSGN", 1000),
                ("hong_kong_like", "HKHKG", 1025),
                ("port_klang_like", "MYPKG", 1025)]


@pytest.mark.parametrize("archetype,locode,density", ENVIRONMENTS)
def test_next_call_environment_resolves_in_both_d50_producers(archetype, locode, density):
    rotation = Rotation(service="test.synthetic", calls=[
        PortCall(locode="THLCH", index=0), PortCall(locode=locode, index=1)])
    departure = load_port("laem_chabang_like")
    assert archetype in list_ports()
    profile = load_port(archetype)
    assert profile.locode == locode
    assert profile.water_density_kg_m3 == density
    assert not profile.yard.blocks and not profile.equipment
    assert profile.max_draught_m is None
    assert profile.profile_kind == "next_call_environment"
    assert profile.max_crane_height_tiers is None
    assert profile.quay_crane is profile.yard_max_tiers is None
    assert profile.berthing_side == "port"
    assert PortProfile.model_validate_json(profile.model_dump_json()) == profile
    content = (params_dir("ports") / f"{archetype}.toml").read_bytes()
    assert f"params/ports/{archetype}.toml sha256:{sha256(content).hexdigest()}" in profile.sources
    assert any(source.startswith("ASSUMPTION:") and "density" in source for source in profile.sources)
    assert any("no measured" in source for source in profile.sources)
    declarations = [
        instances_voyage.build_voyage_spec(instance_id="synthetic.environment.test",
            rotation=rotation, port=departure, call_index=0),
        random_voyage.build_voyage_spec(rotation, 0, departure, seed=1),
    ]
    for spec in declarations:
        assert spec.arrival.locode == locode
        assert spec.arrival.water_density_kg_m3 == density
        assert set(profile.sources) <= set(spec.arrival.provenance.parents)
        assert spec.arrival.density_id != spec.departure.density_id
    # Archetype-explicit calls resolve through the same loader.
    rotation.calls[1].archetype = archetype
    assert instances_voyage.build_voyage_spec(instance_id="synthetic.environment.test",
        rotation=rotation, port=departure, call_index=0).arrival == declarations[0].arrival
    profile.sources.clear()
    assert load_port(archetype).sources
    with pytest.raises(ValueError, match="environment.only|environment-only"):
        load_simulator_params(archetype)


def test_unknown_next_call_still_fails_closed_in_both_producers():
    rotation = Rotation(service="test.unknown", calls=[
        PortCall(locode="THLCH", index=0), PortCall(locode="ZZUNK", index=1)])
    departure = load_port("laem_chabang_like")
    with pytest.raises(ValueError, match="environment unavailable.*ZZUNK"):
        instances_voyage.build_voyage_spec(instance_id="synthetic.unknown.test",
            rotation=rotation, port=departure, call_index=0)
    with pytest.raises(ValueError, match="no matching.*ZZUNK"):
        random_voyage.build_voyage_spec(rotation, 0, departure, seed=1)


def test_every_configured_cargo_route_leg_has_exact_environment_evidence():
    catalog = {}
    for name in list_ports():
        profile = load_port(name)
        assert profile.locode not in catalog
        catalog[profile.locode] = profile
    routes = load_param_file("cargo", "feeder_export")["rotations"]
    for route in routes:
        rotation = Rotation(service=route["id"], calls=[
            PortCall(locode=locode, index=index) for index, locode in enumerate(route["port_calls"])])
        for index, following in enumerate(rotation.calls[1:]):
            departure = catalog[rotation.calls[index].locode]
            for spec in (
                instances_voyage.build_voyage_spec(instance_id="synthetic.route.test",
                    rotation=rotation, port=departure, call_index=index),
                random_voyage.build_voyage_spec(rotation, index, departure, seed=1),
            ):
                assert spec.arrival.locode == following.locode
                assert spec.arrival.water_density_kg_m3 == catalog[following.locode].water_density_kg_m3
                assert set(catalog[following.locode].sources) <= set(spec.arrival.provenance.parents)


@pytest.mark.parametrize("mutation", ["unit", "no_unit", "no_provenance", "empty_provenance",
    "nonfinite", "infinite", "zero", "negative", "boolean", "both_provenance", "unlabelled_provenance",
    "no_density", "no_locode", "no_kind", "no_id", "no_label", "no_description", "no_synthetic",
    "no_schema_version", "blank_label", "no_berthing_side",
    "no_berthing_side_assumption"])
def test_next_call_environment_rejects_incomplete_or_invalid_evidence(mutation):
    import tomllib
    content = (params_dir("ports") / "cat_lai_like.toml").read_bytes()
    raw = deepcopy(tomllib.loads(content.decode()))
    density = raw["water_density_kg_m3"]
    if mutation == "unit":
        density["unit"] = "tonnes/m3"
    elif mutation == "no_unit":
        del density["unit"]
    elif mutation == "no_provenance":
        del density["assumption"]
    elif mutation == "empty_provenance":
        density["assumption"] = " "
    elif mutation == "nonfinite":
        density["value"] = float("nan")
    elif mutation == "infinite":
        density["value"] = float("inf")
    elif mutation == "zero":
        density["value"] = 0
    elif mutation == "negative":
        density["value"] = -1
    elif mutation == "boolean":
        density["value"] = True
    elif mutation == "both_provenance":
        density["source"] = "Not an actual measurement"
    elif mutation == "unlabelled_provenance":
        density["assumption"] = "unlabelled scenario input"
    elif mutation == "no_density":
        del raw["water_density_kg_m3"]
    elif mutation == "no_locode":
        del raw["locode"]
    elif mutation == "no_kind":
        del raw["profile_kind"]
    elif mutation == "blank_label":
        raw["label"] = " "
    elif mutation.startswith("no_"):
        del raw[mutation.removeprefix("no_")]
    with patch.object(profiles, "load_toml_bytes", return_value=raw):
        with pytest.raises(ValueError):
            profiles._bundle.__wrapped__("cat_lai_like", content)
