"""D50 generated voyage declarations survive IO and bind their input profiles."""
from copy import deepcopy

import pytest

from stowage.generate import random_yard
from stowage.io import load_instance, save_instance
from stowage.model import Instance
from stowage.ports.profiles import load_port
from stowage.vessel.fleet_slices import load_fleet_preset


def test_every_generated_instance_declares_next_call_and_default_rule(monkeypatch, tmp_path):
    parent = load_fleet_preset("feeder_500", "full", with_hull=False)
    monkeypatch.setattr(random_yard, "_full_vessel", lambda name: parent)
    instance = random_yard.generate_random_instance("tiny", seed=1)
    spec = instance.voyage
    assert spec is not None
    next_call = instance.rotation.calls[instance.call_index + 1]
    assert spec.departure.call_index == instance.call_index
    assert spec.departure.locode == instance.port.locode
    assert spec.departure.water_density_kg_m3 == instance.port.water_density_kg_m3
    assert spec.arrival.call_index == next_call.index
    assert spec.arrival.locode == next_call.locode
    assert spec.arrival.water_density_kg_m3 == load_port("singapore_like").water_density_kg_m3
    assert spec.departure.id != spec.arrival.id
    assert spec.consumption is None and spec.ballast_operations == []
    assert spec.provenance.parents and spec.arrival.provenance.parents
    assert not spec.provenance.tainted
    loaded = load_instance(save_instance(instance, tmp_path / "generated.json"))
    assert loaded == Instance.model_validate_json(instance.model_dump_json()) == instance
    assert loaded.voyage == spec


def test_resume_fingerprint_includes_next_port_parameters(monkeypatch):
    actual = random_yard.load_param_file
    altered = False
    def parameters(kind, name):
        value = deepcopy(actual(kind, name))
        if altered and kind == "ports" and name == "singapore_like":
            value["d50_test_source_revision"] = 1
        return value
    monkeypatch.setattr(random_yard, "load_param_file", parameters)
    before = random_yard._resume_inputs("feeder_500", "laem_chabang_like")
    altered = True
    after = random_yard._resume_inputs("feeder_500", "laem_chabang_like")
    assert before != after
