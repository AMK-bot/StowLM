"""D64/rev31 4.3: the ON random producer emits the gate's certified arrival copy.

ON: the complete final candidate (with its demand) is gated and the returned
prepared-arrival copy is emitted; booked tanks stay in ``background_last``.
UNKNOWN propagates and never redraws. OFF keeps the historical probe gate
(byte-pinned separately in test_random_activation.py).
"""
import pytest

from stowage.generate import background_acceptance as gate
from stowage.generate import random_yard as random
from stowage.generate.background_acceptance import (
    BackgroundAcceptance, BackgroundAcceptanceUnknown, accept_background_candidate)
from stowage.rules.catalogue import rule_freeze_v1
from stowage.vessel.fleet_slices import load_fleet_preset

_MARKER = {"D64-TEST-ARRIVAL-MARKER": 4321.}


@pytest.fixture(scope="module")
def geometry():
    return load_fleet_preset("feeder_500", "full", with_hull=False)


@pytest.fixture
def real_gate(monkeypatch, geometry):
    """Cheap geometry, but the real wrapper (undo conftest's geometry bypass)."""
    monkeypatch.setattr(random, "_full_vessel", lambda name: geometry)
    monkeypatch.setattr(random, "accept_background_candidate", accept_background_candidate)
    gate.clear_background_cache()
    calls = {"prepare": [], "evaluate": []}

    def prepare(instance):
        calls["prepare"].append(instance.model_dump_json())
        changed = dict(instance.onboard.tank_masses_kg, **_MARKER)
        prepared = instance.model_copy(update={"onboard": instance.onboard.model_copy(
            update={"tank_masses_kg": changed})})
        calls["prepared"] = prepared
        return prepared

    monkeypatch.setattr(gate, "_prepare_arrival_instance", prepare)
    return calls


def _accepted(calls):
    def evaluate(instance, *, arrival_instance=None, **kwargs):
        calls["evaluate"].append((instance, arrival_instance))
        return BackgroundAcceptance(True, (), "ok", {}, {}, 0.)
    return evaluate


def test_on_emits_exactly_the_gated_arrival_copy_with_demand(monkeypatch, real_gate):
    monkeypatch.setattr(gate, "evaluate_background", _accepted(real_gate))
    report = random.RandomGenerationReport()
    with rule_freeze_v1(enabled=True):
        emitted = random.generate_random_instance(seed=1, report=report)
    assert emitted is real_gate["prepared"]
    assert emitted.onboard.tank_masses_kg["D64-TEST-ARRIVAL-MARKER"] == 4321.
    (booked, arrival), = real_gate["evaluate"]
    assert arrival is emitted
    # The gate saw the final candidate with its actual demand, not a probe.
    assert booked.load_list and booked.load_list == emitted.load_list
    assert booked.id == emitted.id and not booked.id.startswith("random-background")
    assert "D64-TEST-ARRIVAL-MARKER" not in booked.onboard.tank_masses_kg
    # Booked context unchanged and retained separately in the audit.
    assert real_gate["prepare"] == [booked.model_dump_json()]
    assert report.background_last["booking_tank_masses_kg"] == booked.onboard.tank_masses_kg
    assert report.background_last["arrival_tank_masses_kg"] == emitted.onboard.tank_masses_kg
    assert report.background_checks == 1 and report.accepted
    with rule_freeze_v1(enabled=True):
        assert random._report_payload(report)["background_unknowns"] == {}


def test_on_unknown_propagates_without_redraw_and_is_counted(monkeypatch, real_gate):
    seen = []

    def unknown(instance, *, arrival_instance=None, **kwargs):
        seen.append((report.attempts, instance.load_list))
        return BackgroundAcceptance(False, ("background.compute.deadline",), "unknown",
                                    {}, {}, 0., unknown=True)

    monkeypatch.setattr(gate, "evaluate_background", unknown)
    report = random.RandomGenerationReport()
    with rule_freeze_v1(enabled=True), pytest.raises(BackgroundAcceptanceUnknown):
        random.generate_random_instance(seed=1, report=report, max_attempts=50)
    (attempts_at_gate, demand), = seen  # exactly one gate call: no next draw
    assert demand, "UNKNOWN must come from gating the candidate with its demand"
    assert report.attempts == attempts_at_gate
    assert report.background_unknowns == {"background.compute.deadline": 1}
    assert report.background_checks == 1
    assert "background.acceptance" not in report.rejections
    assert report.background_rejections == {} and not report.accepted
    assert report.background_last["status"] == "unknown"


def test_off_gates_historical_probe_and_discards_return(monkeypatch, geometry):
    monkeypatch.setattr(random, "_full_vessel", lambda name: geometry)
    seen = []

    def stub(instance, audit):
        seen.append(instance)
        return object()  # OFF must not emit the gate's return value

    monkeypatch.setattr(random, "accept_background_candidate", stub)
    report = random.RandomGenerationReport()
    with rule_freeze_v1(enabled=False):
        emitted = random.generate_random_instance(seed=1, report=report)
        payload = random._report_payload(report)
    probe, = seen
    assert probe.id.startswith("random-background.") and probe.load_list == [] and probe.containers == []
    assert emitted.id.startswith("random.") and emitted.load_list
    assert "background_unknowns" not in payload  # historical OFF audit keys


def test_on_real_prepare_arrival_on_real_tiny_instance(monkeypatch):
    """Real hull + real D64 prepare_arrival; only the D57 verdict is stubbed."""
    from stowage.vessel.fleet_berth import prepare_arrival
    monkeypatch.setattr(random, "accept_background_candidate", accept_background_candidate)
    gate.clear_background_cache()
    seen = []

    def evaluate(instance, *, arrival_instance=None, **kwargs):
        seen.append((instance, arrival_instance))
        return BackgroundAcceptance(True, (), "ok", {}, {}, 0.)

    monkeypatch.setattr(gate, "evaluate_background", evaluate)
    report = random.RandomGenerationReport()
    with rule_freeze_v1(enabled=True):
        emitted = random.generate_random_instance(seed=1, report=report)
        (booked, arrival), = seen
        expected = prepare_arrival(booked)
    assert arrival is emitted
    assert emitted.onboard.tank_masses_kg == expected.onboard.tank_masses_kg
    assert emitted.provenance.parents == expected.provenance.parents
    assert report.background_last["booking_tank_masses_kg"] == booked.onboard.tank_masses_kg
    tanks = {tank.id: tank for tank in emitted.vessel.tanks}
    pair = next(pair for pair in emitted.vessel.tank_pairs if pair.role == "anti_heeling")
    for tid in (pair.port_tank, pair.starboard_tank):
        assert emitted.onboard.tank_masses_kg[tid] == .5*tanks[tid].capacity_m3*tanks[tid].content_density_kg_m3
    for tid, mass in emitted.onboard.tank_masses_kg.items():
        assert 0. <= mass <= tanks[tid].capacity_m3*tanks[tid].content_density_kg_m3
