"""No-planner D72 snapshot metrics and paired, production-yard admission probe."""
from dataclasses import asdict

import pytest

from stowage.bounds import components
from stowage.model.yard import YardStack, YardState
from stowage.ports import load_port, load_simulator_params
from stowage.rules.catalogue import rule_freeze_v1
from stowage.yard.yield_measurement import (
    measure_calls, measure_snapshot, production_yard_smoke_calls,
)


def test_snapshot_measures_targets_not_blockers_and_uses_shared_bound(monkeypatch):
    with rule_freeze_v1():
        calls = production_yard_smoke_calls("laem_chabang_like")
        instance = calls[0].instance
        a, b, c = instance.load_list[:3]
        foreign = calls[1].instance.load_list[0]
        instance = instance.model_copy(update={"yard": YardState(yard_id=instance.port.yard.id,
            stacks=[YardStack(key="A-001-01", length_ft=20, boxes=[a, b, foreign]),
                    YardStack(key="A-002-01", length_ft=20, boxes=[c])])})
        calls_seen = []
        def bound(value):
            calls_seen.append(value)
            return 71
        monkeypatch.setattr(components, "yard_rehandle_bound", bound)
        measured = measure_snapshot(instance)
    assert calls_seen == [instance]
    assert measured["foreign_blocked_exports"] == 2
    assert measured["foreign_blocked_export_share"] == 2 / len(instance.load_list)
    assert measured["foreign_blocked_present_export_share"] == 2 / 3
    assert measured["yard_rehandle_bound"] == 71
    assert measured["stack_height_histogram"] == {"1": 1, "3": 1}
    assert measured["stack_height_mean_nonempty"] == 2
    assert measured["stack_height_max"] == 3
    assert measured["exports_present"] == 3
    assert measured["exports_absent"] == len(instance.load_list) - 3


def test_smoke_preserves_production_yard_and_labels_unqualified_demand():
    with rule_freeze_v1():
        for port_name, height in (("laem_chabang_like", 5), ("small_feeder", 3)):
            calls = production_yard_smoke_calls(port_name)
            production = load_port(port_name)
            assert len(calls) == 2
            assert sum(len(call.instance.containers) for call in calls) == 24
            assert all(call.instance.port == production for call in calls)
            assert all(block.max_tiers == height for block in calls[0].instance.port.yard.blocks)
            assert "synthetic" in calls[0].id


def test_paired_measurement_keeps_noise_calls_and_global_rules_fixed():
    with rule_freeze_v1():
        calls = production_yard_smoke_calls("laem_chabang_like")
        before = asdict(calls[0])
        measured = measure_calls(calls, load_simulator_params("laem_chabang_like"),
                                 seed=3, noise=.5, until_s=1000)
    assert asdict(calls[0]) == before
    assert measured["qualification"] == "synthetic admission probe; not qualified production yield"
    off, on = measured["arms"]["d72_off"], measured["arms"]["d72_on"]
    assert off["rule_freeze_v1"] is on["rule_freeze_v1"] is True
    assert off["noise"] == on["noise"] == .5
    assert off["requested_congestion_mix_share"] == 0
    assert on["requested_congestion_mix_share"] == .25
    for arm in (off, on):
        assert len(arm["snapshots"]) == 1
        assert arm["snapshots"][0]["call_id"] == calls[0].id
        assert arm["snapshots"][0]["exports_present"] == len(calls[0].instance.load_list)
        assert arm["snapshots"][0]["stack_height_max"] <= 5
        assert arm["admission"]["requested_exports"] == 16
        assert arm["admission"]["unserved_loads"] == 0
        assert arm["admission"]["unserved_discharges"] == 0
        assert arm["admission"]["calls_not_started"] == 1
        assert arm["congestion_mixing"]["synthetic"] is True
    assert on["snapshots"][0]["foreign_blocked_exports"] > 0
    assert off["snapshots"][0]["foreign_blocked_exports"] == 0


def test_measure_calls_rejects_scaled_yard_instead_of_claiming_production():
    with rule_freeze_v1():
        calls = production_yard_smoke_calls("laem_chabang_like")
        for call in calls:
            call.instance.port.yard.blocks[0].n_bays = 1
        with pytest.raises(ValueError, match="production"):
            measure_calls(calls, load_simulator_params("laem_chabang_like"), seed=3, noise=.5)


def test_snapshot_direct_delivery_absence_and_empty_height_are_explicit():
    with rule_freeze_v1():
        instance = production_yard_smoke_calls()[0].instance
        instance.containers[0].direct_delivery = True
        instance.yard = YardState(yard_id=instance.port.yard.id)
        measured = measure_snapshot(instance)
    assert measured["direct_delivery_exports"] == 1
    assert measured["exports_present"] == 0
    assert measured["exports_absent"] == 8
    assert measured["foreign_blocked_export_share"] == 0
    assert measured["foreign_blocked_present_export_share"] == 0
    assert measured["yard_rehandle_bound"] == 0
    assert measured["stack_height_mean_nonempty"] == measured["stack_height_max"] == 0
    assert measured["stack_height_histogram"] == {}


def test_cli_consumes_existing_snapshot_without_simulation(tmp_path, monkeypatch):
    import json
    from stowage.yard import yield_measurement

    with rule_freeze_v1():
        instance = production_yard_smoke_calls()[0].instance
    source = tmp_path / "snapshot.json"
    source.write_text(instance.model_dump_json())
    output = tmp_path / "metrics.json"
    def forbidden(*args, **kwargs):
        raise AssertionError("saved snapshots must not rerun the simulator")
    monkeypatch.setattr(yield_measurement, "simulate_port", forbidden)
    report = yield_measurement.main(["--snapshot", str(source), "--output", str(output)])
    assert json.loads(output.read_text()) == report
    assert report["snapshots"][0]["instance_id"] == instance.id
    assert report["snapshots"][0]["production_yard_layout"] is True
    assert report["cases"] == []


def test_cli_consumes_preexisting_fixed_calls_and_retains_horizon(tmp_path):
    import json
    from stowage.yard.yield_measurement import main

    with rule_freeze_v1():
        calls = production_yard_smoke_calls()
    source = tmp_path / "calls.json"
    source.write_text(json.dumps({"calls": [asdict(call) for call in calls], "until_s": 1000},
                                default=lambda value: value.model_dump(mode="json")))
    report = main(["--calls", str(source), "--seeds", "3", "--noise", ".5",
                   "--output", str(tmp_path / "metrics.json")])
    assert len(report["cases"]) == 1
    assert report["cases"][0]["until_s"] == 1000
    assert report["cases"][0]["arms"]["d72_on"]["admission"]["calls_not_started"] == 1
