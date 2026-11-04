"""Import bookings reapply shared policy to their actual reversed handling leg."""
import pytest

from stowage.conventions import make_container_id
from stowage.generate import cargo_shipments
from stowage.model.fixtures import build_instance
from stowage.model.port import PortCall, PortRuleRef, Rotation
from stowage.ports import load_port, load_simulator_params
from stowage.rules.catalogue import rule_freeze_v1
from stowage.yard import simulator


def _sample(monkeypatch, *, current="laem_chabang_like", origin="singapore_like",
            primary="3", direct=False, current_rules=None, active=True):
    template = build_instance()
    template.port = load_port(current)
    if current_rules is not None:
        template.port.port_rules = current_rules
    origin_port = load_port(origin)
    template.rotation = Rotation(service="import-policy", calls=[
        PortCall(index=0, locode=template.port.locode, archetype=current),
        PortCall(index=1, locode=origin_port.locode, archetype=origin,
                 max_on_deck_tiers=2, eta_s=1234),
        PortCall(index=2, locode=origin_port.locode, archetype=origin, eta_s=5678),
    ])
    base = template.containers[1].model_copy(update={
        "pod": origin_port.locode, "pol": template.port.locode,
        "direct_delivery": direct, "category": None, "oog": None,
        "special_stowage": [],
        "dg": template.containers[1].dg.model_copy(update={"imdg_class": primary}),
    })

    def cargo(rotation, call_index, n_boxes, *, id_offset, **kwargs):
        return [base.model_copy(update={"id": make_container_id("QZXU", id_offset + i)})
                for i in range(n_boxes)]

    monkeypatch.setattr(simulator, "generate_load_list", cargo)
    parameters = load_simulator_params(current)
    service = parameters.services[0].model_copy(update={
        "calls_per_week": 1, "arrival_jitter_s": 0,
        "mean_load_boxes": 5, "mean_discharge_boxes": 5, "call_volume_cv": .01,
    })
    parameters = parameters.model_copy(update={
        "services": (service,),
        "traffic": parameters.traffic.model_copy(update={"transshipment_fraction": 0}),
    })
    before = template.model_dump_json()
    with rule_freeze_v1(enabled=active):
        calls, report = simulator.sample_ship_calls(template, parameters, days=1, seed=41)
    assert template.model_dump_json() == before
    assert len(calls) == 1 and report.raw_discharge_boxes > 0
    imports = [box for box in calls[0].instance.containers
               if box.id in calls[0].instance.discharge_list]
    return imports, report


@pytest.mark.parametrize("active", [False, True])
def test_import_refusal_checks_actual_current_destination_profile(monkeypatch, active):
    rules = [PortRuleRef(rule_id="port.dg.refused_class", params={"classes": ["3"]})]
    imports, report = _sample(monkeypatch, current_rules=rules, active=active)
    assert bool(imports) is not active
    if active:
        losses = [entry for entry in report.conditioning if "refused import" in entry]
        assert len(losses) == report.raw_discharge_boxes
        assert all("SGSIN->THLCH" in entry and "1263" in entry and "3" in entry for entry in losses)
    else:
        assert not any("refused import" in entry for entry in report.conditioning)


@pytest.mark.parametrize("active", [False, True])
def test_import_refusal_checks_actual_origin_profile(monkeypatch, active):
    imports, report = _sample(monkeypatch, current="singapore_like", origin="laem_chabang_like",
                              primary="7", active=active)
    assert bool(imports) is not active
    if active:
        assert any("refused import" in entry and "THLCH->SGSIN" in entry
                   for entry in report.conditioning)


@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("old_flag,new_flag", [(False, True), (True, False)])
def test_import_direct_delivery_is_derived_at_current_destination(monkeypatch, active, old_flag, new_flag):
    rules = [PortRuleRef(rule_id="port.dg.direct_delivery", params={"classes": ["3"]})] if new_flag else []
    imports, _ = _sample(monkeypatch, current_rules=rules, direct=old_flag, active=active)
    assert imports
    assert all(box.direct_delivery == (new_flag if active else old_flag) for box in imports)


def test_import_policy_reuses_helper_with_first_origin_metadata_and_actual_profile(monkeypatch):
    original = cargo_shipments.refused_handling_calls
    seen = []

    def recording(rotation, call_index, pod, primary, **kwargs):
        seen.append((rotation, call_index, pod, primary, kwargs))
        return original(rotation, call_index, pod, primary, **kwargs)

    monkeypatch.setattr(cargo_shipments, "refused_handling_calls", recording)
    imports, _ = _sample(monkeypatch)
    assert imports and len(seen) >= len(imports)
    for route, index, pod, primary, kwargs in seen:
        assert index == 0 and pod == "THLCH" and primary == "3"
        assert [(call.index, call.locode) for call in route.calls] == [(0, "SGSIN"), (1, "THLCH")]
        assert route.calls[0].archetype == "singapore_like"
        assert route.calls[0].eta_s == 1234 and route.calls[0].max_on_deck_tiers == 2
        assert route.calls[1].archetype is None
        assert kwargs["current_port"].locode == "THLCH"


def test_import_origin_with_unknown_handling_policy_fails_closed(monkeypatch):
    original = cargo_shipments.resolve_port_call_profile

    def unresolved_origin(call, **kwargs):
        return None if call.locode == "SGSIN" else original(call, **kwargs)

    monkeypatch.setattr(cargo_shipments, "resolve_port_call_profile", unresolved_origin)
    with pytest.raises(ValueError, match="Cannot resolve handling port SGSIN"):
        _sample(monkeypatch)
