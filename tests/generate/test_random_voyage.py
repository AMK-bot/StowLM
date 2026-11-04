"""D50 producer identities use actual, source-labelled synthetic port profiles."""
from __future__ import annotations

import pytest

from stowage.generate import random_voyage
from stowage.model import Provenance, VoyageSpec
from stowage.model.port import PortCall, Rotation
from stowage.ports.profiles import load_port


def _rotation(*, arrival="SGSIN", archetype=None):
    return Rotation(service="synthetic.regional", calls=[
        PortCall(locode="THLCH", index=0),
        PortCall(locode=arrival, index=1, archetype=archetype),
        PortCall(locode="THLCH", index=2),
    ])


def test_d50_producer_default_spec_binds_next_call_and_json_roundtrips():
    rotation = _rotation()
    port = load_port("laem_chabang_like")
    before = rotation.model_dump_json(), port.model_dump_json()
    spec = random_voyage.build_voyage_spec(rotation, 0, port, seed=17)
    assert spec.consumption is None
    assert spec.ballast_operations == []
    assert spec.departure.phase == "departure"
    assert spec.departure.call_index == 0
    assert spec.departure.locode == "THLCH"
    assert spec.arrival.phase == "arrival"
    assert spec.arrival.call_index == 1
    assert spec.arrival.locode == "SGSIN"
    assert spec.departure.id != spec.arrival.id
    assert spec.departure.environment_id != spec.arrival.environment_id
    assert spec.departure.density_id != spec.arrival.density_id
    assert spec.arrival.water_density_kg_m3 == load_port("singapore_like").water_density_kg_m3
    assert spec.departure.water_density_kg_m3 == port.water_density_kg_m3
    assert before == (rotation.model_dump_json(), port.model_dump_json())
    assert VoyageSpec.model_validate_json(spec.model_dump_json()) == spec
    assert random_voyage.build_voyage_spec(rotation, 0, port, seed=17) == spec
    parents = "\n".join(spec.provenance.parents)
    assert "params/ports/singapore_like.toml sha256:" in parents
    assert "params/ports/laem_chabang_like.toml sha256:" in parents
    assert "ASSUMPTION: dock water density 1025 kg/m3" in parents
    assert "D50" in parents
    assert spec.provenance.seed == 17


def test_d50_arrival_density_is_loaded_from_next_port_profile(monkeypatch):
    arrival = load_port("singapore_like")
    arrival.water_density_kg_m3 = 1013.0
    arrival.sources = ["synthetic arrival density scenario sha256:test", "ASSUMPTION: 1013 kg/m3 scenario"]
    monkeypatch.setattr(random_voyage, "load_port", lambda name: arrival if name == "singapore_like" else load_port(name))
    spec = random_voyage.build_voyage_spec(_rotation(archetype="singapore_like"), 0,
                                          load_port("laem_chabang_like"), seed=2)
    assert spec.arrival.water_density_kg_m3 == 1013.0
    assert "synthetic arrival density scenario sha256:test" in spec.arrival.provenance.parents
    assert "ASSUMPTION: 1013 kg/m3 scenario" in spec.arrival.provenance.parents
    assert spec.departure.water_density_kg_m3 == 1025.0
    prior_id = spec.arrival.density_id
    arrival.water_density_kg_m3 = 1014.0
    changed = random_voyage.build_voyage_spec(_rotation(archetype="singapore_like"), 0,
                                             load_port("laem_chabang_like"), seed=2)
    assert changed.arrival.density_id != prior_id


def test_d50_repeated_locodes_use_call_index_and_immediate_next_call():
    rotation = Rotation(service="synthetic.repeat", calls=[
        PortCall(locode="THLCH", index=0), PortCall(locode="SGSIN", index=1),
        PortCall(locode="THLCH", index=2), PortCall(locode="SGSIN", index=3),
    ])
    first = random_voyage.build_voyage_spec(rotation, 0, load_port("laem_chabang_like"), seed=1)
    second = random_voyage.build_voyage_spec(rotation, 2, load_port("laem_chabang_like"), seed=1)
    assert second.arrival.call_index == 3
    assert first.arrival.id != second.arrival.id
    assert first.departure.id != second.departure.id


def test_d50_unknown_arrival_has_no_fabricated_density():
    with pytest.raises(ValueError, match="no matching.*profile"):
        random_voyage.build_voyage_spec(_rotation(arrival="ZZUNK"), 0,
                                        load_port("laem_chabang_like"), seed=1)


@pytest.mark.parametrize("locode,archetype,density", [
    ("VNSGN", "cat_lai_like", 1000.0),
    ("HKHKG", "hong_kong_like", 1025.0),
    ("MYPKG", "port_klang_like", 1025.0),
])
@pytest.mark.parametrize("explicit_archetype", [False, True])
def test_d34_declared_next_call_environments_produce_valid_voyage(
    locode, archetype, density, explicit_archetype,
):
    rotation = _rotation(arrival=locode,
                         archetype=archetype if explicit_archetype else None)
    profile = load_port(archetype)
    spec = random_voyage.build_voyage_spec(rotation, 0,
                                         load_port("laem_chabang_like"), seed=1)
    assert isinstance(spec, VoyageSpec)
    assert spec.arrival.locode == locode
    assert spec.arrival.call_index == rotation.calls[1].index
    assert spec.arrival.water_density_kg_m3 == density
    assert spec.departure.water_density_kg_m3 == 1025.0
    assert set(profile.sources) <= set(spec.arrival.provenance.parents)
    assert any(f"params/ports/{archetype}.toml sha256:" in parent
               for parent in spec.arrival.provenance.parents)
    assert any("ASSUMPTION" in parent for parent in spec.arrival.provenance.parents)
    assert spec.consumption is None and spec.ballast_operations == []
    assert VoyageSpec.model_validate_json(spec.model_dump_json()) == spec


def test_d50_explicit_archetype_must_match_next_locode():
    with pytest.raises(ValueError, match="LOCODE"):
        random_voyage.build_voyage_spec(_rotation(archetype="small_feeder"), 0,
                                        load_port("laem_chabang_like"), seed=1)


def test_d50_ambiguous_arrival_profile_fails_closed(monkeypatch):
    arrival = load_port("singapore_like")
    monkeypatch.setattr(random_voyage, "list_ports", lambda: ["one", "two"])
    monkeypatch.setattr(random_voyage, "load_port", lambda _: arrival.model_copy(deep=True))
    with pytest.raises(ValueError, match="ambiguous"):
        random_voyage.build_voyage_spec(_rotation(), 0, load_port("laem_chabang_like"), seed=1)


@pytest.mark.parametrize("call_index", [-1, True, 2, 5])
def test_d50_requires_current_call_with_an_immediate_next_call(call_index):
    with pytest.raises(ValueError, match="call_index|next call"):
        random_voyage.build_voyage_spec(_rotation(), call_index,
                                        load_port("laem_chabang_like"), seed=1)


def test_d50_departure_profile_must_match_current_call():
    with pytest.raises(ValueError, match="departure.*LOCODE"):
        random_voyage.build_voyage_spec(_rotation(), 0, load_port("small_feeder"), seed=1)


def test_d50_spec_preserves_active_stub_taint(monkeypatch):
    monkeypatch.setattr(random_voyage, "current_provenance", lambda **kwargs:
                        Provenance(stubs={"fixture": "test"}, **kwargs))
    spec = random_voyage.build_voyage_spec(_rotation(), 0, load_port("laem_chabang_like"), seed=1)
    assert spec.provenance.tainted
    assert spec.departure.provenance.tainted
    assert spec.arrival.provenance.tainted
