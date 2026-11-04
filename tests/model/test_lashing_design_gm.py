"""D56 vessel lashing validity limits remain optional and retain evidence."""

import json

import pytest
from pydantic import ValidationError

from stowage.frozen import StubTaintError
from stowage.io import load_instance, save_instance
from stowage.model import HullModel, Instance, VesselFieldProvenance
from stowage.model.fixtures import build_instance


FIELD = "max_gm_lashing_m"
EVIDENCE = f"{FIELD}_provenance"


def _hull_payload(**changes):
    payload = build_instance().model_dump(mode="json")["vessel"]["hull"]
    payload.pop(FIELD, None)
    payload.pop(EVIDENCE, None)
    return payload | changes


@pytest.mark.parametrize("value", [0.001, 1.5, 3])
def test_lashing_design_gm_accepts_finite_positive_metres(value):
    hull = HullModel.model_validate(_hull_payload(**{FIELD: value}))
    assert hull.max_gm_lashing_m == value
    assert hull.max_gm_lashing_m_provenance is None


@pytest.mark.parametrize("value", [0.0, -1.0, float("nan"), float("inf"),
                                  float("-inf"), False, True, "1.5"])
def test_lashing_design_gm_rejects_invalid_values(value):
    with pytest.raises(ValidationError, match=FIELD):
        HullModel.model_validate(_hull_payload(**{FIELD: value}))


def test_lashing_design_gm_missing_legacy_json_is_unknown(tmp_path):
    payload = build_instance().model_dump(mode="json")
    payload["vessel"]["hull"] = _hull_payload()
    path = tmp_path / "legacy-no-lashing-gm.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    hull = load_instance(path).vessel.hull
    assert hull.max_gm_lashing_m is None
    assert hull.max_gm_lashing_m_provenance is None


@pytest.mark.parametrize("record", [
    {"source": "synthetic vessel loading manual"},
    {"assumption": "APPROXIMATION: synthetic lashing design GM"},
])
def test_lashing_design_gm_typed_provenance_json_and_io_round_trip(tmp_path, record):
    payload = build_instance().model_dump(mode="json")
    payload["vessel"]["hull"] = _hull_payload(**{FIELD: 2.4, EVIDENCE: record})
    instance = Instance.model_validate(payload)
    hull = instance.vessel.hull
    assert isinstance(hull.max_gm_lashing_m_provenance, VesselFieldProvenance)
    assert HullModel.model_validate_json(hull.model_dump_json()) == hull
    path = save_instance(instance, tmp_path / "lashing-gm.json")
    assert load_instance(path) == instance
    serialized = json.loads(path.read_text())["vessel"]["hull"]
    assert serialized[FIELD] == 2.4
    for key, text in record.items():
        assert serialized[EVIDENCE][key] == text


def test_lashing_design_gm_nested_evidence_taints_instance_and_blocks_clean_io(tmp_path):
    marker = {"loading_manual.max_gm_lashing_m": "synthetic evidence stub"}
    record = {"source": "synthetic vessel loading manual",
              "provenance": {"stubs": marker, "parents": ["lashing-design-evidence"]}}
    payload = build_instance().model_dump(mode="json")
    payload["vessel"]["hull"] = _hull_payload(**{FIELD: 2.4, EVIDENCE: record})
    instance = Instance.model_validate(payload)
    assert instance.provenance.stubs == marker
    assert "lashing-design-evidence" in instance.provenance.parents
    with pytest.raises(StubTaintError, match=FIELD):
        save_instance(instance, tmp_path / "must-not-write.json")
    path = save_instance(instance, tmp_path / "tainted-lashing-gm.json", allow_stubs=True)
    assert load_instance(path) == instance
