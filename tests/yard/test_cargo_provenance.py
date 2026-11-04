"""Sampled yard calls record the cargo semantics actually used for their draw."""

import pytest

from stowage.generate import cargo
from stowage.model.fixtures import build_instance
from stowage.ports import load_simulator_params
from stowage.rules.catalogue import rule_freeze_v1
from stowage.yard import simulator


def _sample(monkeypatch, template):
    # Provenance applies to a workload even when that call contains no bookings.
    monkeypatch.setattr(simulator, "generate_load_list", lambda *args, **kwargs: [])
    parameters = load_simulator_params("laem_chabang_like")
    service = parameters.services[0].model_copy(update={
        "calls_per_week": 1, "arrival_jitter_s": 0,
        "mean_load_boxes": 1, "mean_discharge_boxes": 1,
    })
    parameters = parameters.model_copy(update={"services": (service,)})
    calls, _ = simulator.sample_ship_calls(template, parameters, days=1, seed=41)
    assert len(calls) == 1
    return calls[0].instance


@pytest.mark.parametrize("enabled,expected", [(False, "7.0.0"), (True, "8.0.0")])
@pytest.mark.parametrize("prior", [None, "7.0.0"])
def test_sampled_call_stamps_effective_cargo_version_without_mutating_template(
    monkeypatch, enabled, expected, prior,
):
    template = build_instance()
    template.provenance.versions = {"other_component": "retained"}
    if prior is not None:
        template.provenance.versions["cargo_generator"] = prior
    before = template.model_dump_json()

    with rule_freeze_v1(enabled=enabled):
        sampled = _sample(monkeypatch, template)

    if enabled:
        assert sampled.provenance.versions["cargo_generator"] == expected
        assert sampled.provenance.versions["yard_simulator"] == "5.0.0"
    else:
        assert sampled.provenance == template.provenance
    assert sampled.provenance.versions["other_component"] == "retained"
    assert template.model_dump_json() == before
    assert sampled.provenance is not template.provenance
    assert sampled.provenance.versions is not template.provenance.versions


def test_sampled_call_resolves_cargo_version_from_helper_at_draw_time(monkeypatch):
    template = build_instance()
    monkeypatch.setattr(cargo, "effective_cargo_generator_version", lambda: "draw-time-version")

    with rule_freeze_v1():
        sampled = _sample(monkeypatch, template)

    assert sampled.provenance.versions["cargo_generator"] == "draw-time-version"


def test_active_sampled_call_uses_effective_yard_helper(monkeypatch):
    template = build_instance()
    monkeypatch.setattr(simulator, "effective_yard_simulator_version", lambda: "yard-draw-version", raising=False)
    with rule_freeze_v1():
        sampled = _sample(monkeypatch, template)
    assert sampled.provenance.versions["yard_simulator"] == "yard-draw-version"


def test_off_does_not_rewrite_existing_cargo_or_yard_provenance(monkeypatch):
    template = build_instance()
    template.provenance.versions.update(cargo_generator="archived-cargo", yard_simulator="archived-yard")
    with rule_freeze_v1(enabled=False):
        sampled = _sample(monkeypatch, template)
    assert sampled.provenance.model_dump_json() == template.provenance.model_dump_json()
