"""Known stores/constants need explicit classification and retained evidence."""

import json

import pytest

from stowage.model import Vessel
from stowage.vessel import fleet


SOURCE = "synthetic loading manual: fixed stores/crew allowance"
ASSUMPTION = "APPROXIMATION: declared synthetic non-cargo stores/crew, not inferred from a point mass"


def _payload(name, record):
    text = fleet._parameter_bytes(name).decode()
    values = ", ".join(f"{key} = {json.dumps(value)}" for key, value in record.items())
    return text.replace("\n[hull]\n", "\nfixed_loads = [{"+values+"}]\n[hull]\n", 1).encode()


@pytest.mark.parametrize("name", ["feeder_500", "feeder_800", "feeder_1700"])
def test_non_cargo_parameter_load_retains_explicit_classification_and_evidence(name, monkeypatch):
    record = dict(x_m=10., y_m=0., z_m=2., mass_kg=1000., kind="non_cargo",
                  source=SOURCE, assumption=ASSUMPTION)
    payload = _payload(name, record)
    monkeypatch.setattr(fleet, "_parameter_bytes", lambda _: payload)
    vessel = fleet._build_vessel(payload, name, False)
    assert len(vessel.fixed_loads) == 1
    load = vessel.fixed_loads[0]
    assert load.kind == "non_cargo" and load.cargo_boxes is None
    assert tuple(load) == (10., 0., 2., 1000.)
    assert any(SOURCE in source and ASSUMPTION in source for source in vessel.sources)
    assert Vessel.model_validate_json(vessel.model_dump_json()) == vessel
    from stowage.vessel.fleet_slices import slice_preset
    for preset in ("tiny", "small", "medium"):
        part = slice_preset(vessel, preset)
        assert part.fixed_loads[0] == load
        assert any(SOURCE in source and ASSUMPTION in source for source in part.sources)
        assert Vessel.model_validate_json(part.model_dump_json()) == part


def test_non_cargo_parameter_load_requires_classification_provenance():
    record = dict(x_m=10., y_m=0., z_m=2., mass_kg=1000., kind="non_cargo")
    with pytest.raises(ValueError, match="non.cargo.*provenance"):
        fleet._build_vessel(_payload("feeder_500", record), "feeder_500", False)


@pytest.mark.parametrize("kind", ["unknown", "other"])
def test_ambiguous_fixed_load_is_never_inferred_non_cargo(kind):
    record = dict(x_m=10., y_m=0., z_m=2., mass_kg=1000., kind=kind)
    vessel = fleet._build_vessel(_payload("feeder_500", record), "feeder_500", False)
    assert vessel.fixed_loads[0].kind == kind
