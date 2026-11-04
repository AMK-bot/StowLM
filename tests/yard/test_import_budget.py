"""Accepted import volumes respect nominal TEU and physical cell budgets."""
import pytest

from stowage.conventions import make_container_id
from stowage.rules.catalogue import rule_freeze_v1
from stowage.yard import sample_ship_calls
from stowage.yard import simulator
from stowage.model.fixtures import build_instance
from stowage.model.port import PortCall, Rotation
from stowage.ports import load_simulator_params


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


def _imports(call):
    return [box for box in call.instance.containers if box.id in call.instance.discharge_list]


def test_real_import_draw_cannot_exceed_nominal_teu():
    # Review R3: seed zero drew one 40' import into a one-TEU vessel.
    with rule_freeze_v1():
        calls, _ = sample_ship_calls(_template(nominal_teu=1), _parameters(), days=1, seed=0)
    assert calls
    assert all(sum(box.length_ft / 20 for box in _imports(call)) <= 1 for call in calls)


@pytest.mark.parametrize("nominal_teu,expected_imports", [(100, 2), (4, 1), (2, 0)])
def test_45ft_imports_have_separate_teu_and_physical_cell_budgets(monkeypatch, nominal_teu, expected_imports):
    template = _template(nominal_teu)
    for cell in template.vessel.cells:
        cell.accepts_45 = True
    base = template.containers[0].model_copy(update={
        "iso_type": "L5G1", "length_ft": 45, "reefer": False,
        "reefer_setpoint_c": None, "dg": None, "special_stowage": [], "oog": None,
        "direct_delivery": False, "pod": "SGSIN"})

    def long_cargo(rotation, call_index, n_boxes, *, id_offset, **kwargs):
        return [base.model_copy(update={"id": make_container_id("QZXU", id_offset + i)})
                for i in range(n_boxes)]

    monkeypatch.setattr(simulator, "generate_load_list", long_cargo)
    with rule_freeze_v1():
        calls, _ = sample_ship_calls(template, _parameters(), days=1, seed=0)
    imports = _imports(calls[0])
    assert len(imports) == expected_imports
    assert sum(box.length_ft / 20 for box in imports) <= nominal_teu
    assert sum(1 if box.length_ft == 20 else 2 for box in imports) <= len(template.vessel.cells)


def test_active_workload_report_labels_export_lane_import_approximation():
    with rule_freeze_v1():
        calls, report = sample_ship_calls(_template(), _parameters(), days=1, seed=0)
    assert calls
    assert any("APPROXIMATION:" in note and "export-lane" in note
               and "equipment" in note and "DG" in note and "POD" in note
               for note in report.conditioning)
