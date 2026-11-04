"""New workloads use the current fleet cap without changing archived templates."""
from dataclasses import fields
from hashlib import sha256

import pytest

from stowage.io import load_instance
from stowage.ports import load_simulator_params
from stowage.yard import sample_ship_calls


@pytest.fixture
def legacy_template(repo_root):
    return load_instance(repo_root / "experiments/instances-v3/random_yard/full/seed-100300001.json")


def _high_demand(*, imports=False):
    parameters = load_simulator_params("laem_chabang_like")
    service = parameters.services[0].model_copy(update={
        "calls_per_week": 1, "arrival_jitter_s": 0,
        "mean_load_boxes": 2000, "mean_discharge_boxes": 2000 if imports else .01,
        "call_volume_cv": .01,
    })
    return parameters.model_copy(update={
        "services": (service,),
        "traffic": parameters.traffic.model_copy(update={"transshipment_fraction": 0}),
    })


def _workload(template):
    calls, report = sample_ship_calls(template, _high_demand(), days=1, seed=41)
    assert report.raw_load_boxes == 2032
    assert len(calls) == 1
    return calls, report


def _exports(call):
    return sum(box.length_ft // 20 for box in call.instance.containers
               if box.id in call.instance.load_list)


def _generated(calls, report):
    return ([{field.name: (call.instance.model_dump(
        exclude={"vessel": True, "onboard": {"vessel_id"}})
        if field.name == "instance" else getattr(call, field.name))
        for field in fields(call)} for call in calls], report)


@pytest.mark.parametrize("lineage", ["stored", "old_nominal", "dotted_id", "slice_of", "source_only"])
def test_legacy_full_workload_matches_current_cap_without_mutation(legacy_template, repo_root, lineage):
    path = repo_root / "experiments/instances-v3/random_yard/full/seed-100300001.json"
    file_digest = sha256(path.read_bytes()).hexdigest()
    changes = {
        "stored": {},
        "old_nominal": {"nominal_teu": 1000},
        "dotted_id": {"id": "feeder_1000.slice_b02-b10", "slice_of": None, "sources": []},
        "slice_of": {"id": "custom-slice", "slice_of": "feeder_1000", "sources": []},
        "source_only": {"id": "custom", "slice_of": None},
    }[lineage]
    template = legacy_template.model_copy(update={
        "vessel": legacy_template.vessel.model_copy(update=changes)})
    before = template.model_dump_json()
    canonical = template.model_copy(update={"vessel": template.vessel.model_copy(update={
        "id": "feeder_800", "slice_of": "feeder_800", "nominal_teu": 800})})
    calls, report = _workload(template)
    expected, expected_report = _workload(canonical)
    assert _exports(calls[0]) == _exports(expected[0]) == 798
    assert _generated(calls, report) == _generated(expected, expected_report)
    assert template.model_dump_json() == before
    assert sha256(path.read_bytes()).hexdigest() == file_digest


@pytest.mark.parametrize("label", ["feeder_1000", "feeder_800"])
def test_current_fleet_cap_preserves_smaller_slice_capacity(legacy_template, label):
    template = legacy_template.model_copy(update={"vessel": legacy_template.vessel.model_copy(update={
        "id": f"{label}.slice_b02-b10", "slice_of": label, "nominal_teu": 12})})
    calls, _ = _workload(template)
    assert 0 < _exports(calls[0]) <= 12
    assert template.vessel.nominal_teu == 12


def test_foreign_vessel_keeps_its_declared_cap(legacy_template):
    template = legacy_template.model_copy(update={"vessel": legacy_template.vessel.model_copy(update={
        "id": "foreign", "slice_of": None, "nominal_teu": 1000, "sources": []})})
    calls, _ = _workload(template)
    assert _exports(calls[0]) == 997


def test_legacy_import_and_export_draws_both_use_current_cap(legacy_template, monkeypatch):
    from stowage.yard import simulator

    requested_counts = []

    def capture(rotation, call_index, n_boxes, **kwargs):
        requested_counts.append(n_boxes)
        return []

    monkeypatch.setattr(simulator, "generate_load_list", capture)
    sample_ship_calls(legacy_template, _high_demand(imports=True), days=1, seed=41)
    assert requested_counts == [800, 800]
