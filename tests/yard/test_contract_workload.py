"""Rule-freeze workload bounds preserve the exact legacy draws when disabled."""
from dataclasses import asdict
from hashlib import sha256
import json

import pytest

from stowage.model.fixtures import build_instance
from stowage.conventions import make_container_id
from stowage.model.port import PortCall, Rotation
from stowage.ports import load_simulator_params
from stowage.rules.catalogue import rule_freeze_v1
from stowage.yard import sample_ship_calls
from stowage.yard.simulator import _workload_teu_cap


def _template(nominal_teu=100):
    template = build_instance()
    template.rotation = Rotation(service="straits", calls=[
        PortCall(index=i, locode=p) for i, p in enumerate(("THLCH", "SGSIN", "MYPKG", "THLCH"))])
    return template.model_copy(update={"vessel": template.vessel.model_copy(update={
        "id": "foreign", "slice_of": None, "sources": [], "nominal_teu": nominal_teu})})


def _parameters():
    parameters = load_simulator_params("laem_chabang_like")
    service = parameters.services[0].model_copy(update={
        "calls_per_week": 1, "arrival_jitter_s": 0,
        "mean_load_boxes": 200, "mean_discharge_boxes": 30,
        "call_volume_cv": .01})
    return parameters.model_copy(update={
        "services": (service,),
        "traffic": parameters.traffic.model_copy(update={"transshipment_fraction": 0})})


def _digest(calls, report):
    payload = {"calls": [{"id": call.id, "instance": call.instance.model_dump(mode="json"),
                          "arrival_s": call.arrival_s, "cutoff_s": call.cutoff_s,
                          "gate_in_times": call.gate_in_times, "service_s": call.service_s,
                          "load_order": call.load_order} for call in calls],
               "report": asdict(report)}
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize("nominal_teu,expected", [(100, 100), (8, 8)])
def test_workload_teu_cap_keeps_nominal_units_separate_from_physical_cells(nominal_teu, expected):
    with rule_freeze_v1():
        assert _workload_teu_cap(_template(nominal_teu).vessel) == expected


def test_active_workload_cap_also_retains_current_fleet_nominal_cap(monkeypatch):
    from stowage.vessel import fleet

    monkeypatch.setattr(fleet, "list_fleet", lambda: ["feeder_800"])
    monkeypatch.setattr(fleet, "load_ship_params", lambda name: {"nominal_teu": 8})
    vessel = _template().vessel.model_copy(update={"id": "feeder_800"})
    with rule_freeze_v1():
        assert _workload_teu_cap(vessel) == 8


def test_active_sampled_exports_never_exceed_physical_cell_budget():
    with rule_freeze_v1():
        template = _template()
        before = template.model_dump_json()
        calls, report = sample_ship_calls(template, _parameters(), days=1, seed=41)
    assert calls and report.conditioning
    for call in calls:
        assert 0 < sum(1 if box.length_ft == 20 else 2 for box in call.instance.containers
                       if box.id in call.instance.load_list) <= len(template.vessel.cells)
    assert template.model_dump_json() == before


@pytest.mark.parametrize("nominal_teu,expected_boxes,expected_teu", [(100, 8, 18), (16, 7, 15.75)])
def test_active_workload_budget_separates_fractional_teu_and_physical_45ft_cells(
        monkeypatch, nominal_teu, expected_boxes, expected_teu):
    from stowage.yard import simulator

    template = _template(nominal_teu)
    base = template.containers[0].model_copy(update={
        "iso_type": "L5G1", "length_ft": 45, "reefer": False,
        "reefer_setpoint_c": None, "dg": None, "special_stowage": [], "oog": None})

    def long_cargo(rotation, call_index, n_boxes, *, id_offset, **kwargs):
        return [base.model_copy(update={"id": make_container_id("QZXU", id_offset + i)})
                for i in range(n_boxes)]

    monkeypatch.setattr(simulator, "generate_load_list", long_cargo)
    with rule_freeze_v1():
        calls, _ = sample_ship_calls(template, _parameters(), days=1, seed=41)
    assert len(calls[0].instance.load_list) == expected_boxes
    assert sum(box.length_ft / 20 for box in calls[0].instance.containers
               if box.id in calls[0].instance.load_list) == expected_teu


@pytest.mark.parametrize("seed,expected", [
    (41, "9885b8182dd2171541281246592ea4c964bc5d4e53a24ddd2c763454203a7563"),
    (2, "b9f9ee0e8ab5a9afb6fbc5af1e868080533b55b9e1ab4ef19e7407d7acc27d49"),
])
def test_switch_off_preserves_seeded_workload_bytes(seed, expected):
    with rule_freeze_v1(enabled=False):
        template = _template()
        # Freeze the INPUT epoch so unrelated component/catalogue edits cannot
        # rewrite this yard regression. Expected full bytes were captured using
        # the original pre-wave simulator with this exact input provenance.
        template.provenance.versions = {"fixture": "yard-legacy-v1"}
        # The shared model fixture later added berth-evidence text. Restore
        # this legacy INPUT's original source list, just as versions are pinned
        # above; keep every output byte and the original expected hashes.
        # Diagnostic: experiments/mvp/smoke/v54-workload-byte-root-cause.json.
        template.port.sources = ["synthetic fixture; no actual terminal operating limits"]
        calls, report = sample_ship_calls(template, _parameters(), days=1, seed=seed)
    assert _digest(calls, report) == expected
