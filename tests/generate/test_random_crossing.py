"""Random-yard crossing buckets use the move engine's independent yard limit."""
from __future__ import annotations

import pytest

from stowage.generate.random_crossing import (
    condition_crossing_bucket, crossing_bucket_selected, crossing_probe, ship_crossing_probe,
)
from stowage.conventions import format_slot, make_container_id
from stowage.model import Instance
from stowage.model.fixtures import build_instance
from stowage.model.yard import YardStack, YardState
from stowage.ports import effective_yard_tiers, list_ports, load_port
from stowage.rules.catalogue import rule_freeze_v1


def _one_target(port_name):
    instance = build_instance()
    port = load_port(port_name)
    box = instance.containers[2].model_copy(update={
        "special_stowage": [], "pol": port.locode, "pod": "THSGZ",
    })
    instance.port = port
    instance.rotation.calls[0].locode = port.locode
    instance.rotation.calls[0].archetype = port_name
    instance.containers = [box]
    instance.load_list = [box.id]
    block = next(b for b in port.yard.blocks if 20 in b.allowed_lengths_ft)
    instance.yard = YardState(yard_id=port.yard.id, stacks=[
        YardStack(key=f"{block.id}-001-01", length_ft=20, boxes=[box.id]),
    ])
    return Instance.model_validate(instance.model_dump())


@pytest.mark.parametrize("port_name", [name for name in list_ports()
                                     if load_port(name).profile_kind == "operational"])
def test_bucket_binds_at_effective_yard_limit_via_real_replay(port_name):
    with rule_freeze_v1(enabled=True):
        original = _one_target(port_name)
        before = original.model_dump_json()
        conditioned, audit = condition_crossing_bucket(original)
        assert audit["applied"]
        assert original.model_dump_json() == before
        assert conditioned.load_list == original.load_list
        targets = {b.id: b.model_dump() for b in original.containers}
        assert {b.id: b.model_dump() for b in conditioned.containers if b.id in targets} == targets
        blocker = next(s for s in conditioned.yard.stacks if s.key == audit["yard_blocker"])
        block = next(b for b in conditioned.port.yard.blocks if b.id == blocker.key.split("-")[0])
        assert len(blocker.boxes) == effective_yard_tiers(conditioned.port, block)
        assert conditioned.vessel == original.vessel
        replay = crossing_probe(conditioned)
        assert replay["yard_lifts_at_or_above_h"] == 1
        assert replay["rejected_crossing_lifts"] == 1
        assert replay["probe_rule"] == "port.e3.yard_crossing"
        assert conditioned.family["d62_crossing_bucket"] == "yard_limit_v1"
        assert Instance.model_validate(conditioned.model_dump()) == conditioned


def test_switch_off_is_byte_preserving():
    with rule_freeze_v1(enabled=False):
        instance = _one_target("laem_chabang_like")
        before = instance.model_dump_json()
        conditioned, audit = condition_crossing_bucket(instance)
        assert conditioned is instance
        assert instance.model_dump_json() == before
        assert audit == {"applied": False, "reason": "switch_off"}
        assert not crossing_bucket_selected(12)


def test_deterministic_share_uses_independent_rng():
    with rule_freeze_v1(enabled=True):
        draws = [crossing_bucket_selected(seed) for seed in range(1000)]
        assert draws == [crossing_bucket_selected(seed) for seed in range(1000)]
        assert 0.20 <= sum(draws) / len(draws) <= 0.30
        assert all(crossing_bucket_selected(seed, share=1.0) for seed in range(10))
        assert not any(crossing_bucket_selected(seed, share=0.0) for seed in range(10))


def test_no_space_does_not_relax_yard_or_create_overlap():
    with rule_freeze_v1(enabled=True):
        instance = _one_target("small_feeder")
        for block in instance.port.yard.blocks:
            block.n_rows = 1
            block.reefer_rows = [r for r in block.reefer_rows if r <= 1]
        before = instance.model_dump_json()
        conditioned, audit = condition_crossing_bucket(instance)
        assert not audit["applied"]
        assert audit["reason"] == "no_compatible_crossing_footprint"
        assert conditioned is instance and conditioned.model_dump_json() == before


@pytest.mark.parametrize("length", [40, 45])
@pytest.mark.parametrize("lane", ["low", "high"])
def test_long_footprint_and_both_truck_lane_sides(length, lane):
    with rule_freeze_v1(enabled=True):
        instance = _one_target("laem_chabang_like")
        box = instance.containers[0]
        box.length_ft = length
        box.iso_type = "42G1" if length == 40 else "L5G1"
        if length == 45:
            box.height_m = 2.896
        instance.yard.stacks[0].length_ft = length
        for block in instance.port.yard.blocks:
            block.truck_lane_side = lane
        conditioned, audit = condition_crossing_bucket(instance)
        assert audit["applied"]
        assert Instance.model_validate(conditioned.model_dump()) == conditioned
        assert crossing_probe(conditioned)["yard_lifts_at_or_above_h"] == 1


def test_ship_probe_observes_bad_sequence_without_fabricating_fixed_cargo():
    with rule_freeze_v1(enabled=True):
        instance = _one_target("small_feeder")
        template = instance.containers[0]
        instance.containers = [template.model_copy(update={
            "id": make_container_id("QZXU", index), "gross_mass_kg": float(12_000 - index),
        }) for index in range(5)]
        instance.load_list = [box.id for box in instance.containers]
        block = instance.port.yard.blocks[0]
        instance.yard.stacks = [YardStack(key=f"{block.id}-{i + 1:03d}-01", length_ft=20,
                                        boxes=[box.id]) for i, box in enumerate(instance.containers)]
        for stack in instance.vessel.stacks:
            if not stack.on_deck:
                continue
            base = next(c for c in instance.vessel.cells if c.id == stack.cell_ids[0])
            for index, tier in enumerate((86, 88), 2):
                cid = format_slot(stack.bay, stack.row, tier)
                instance.vessel.cells.append(base.model_copy(update={
                    "id": cid, "vcg_base_m": stack.base_z_m + index * 2.591,
                }))
                stack.cell_ids.append(cid)
            stack.max_height_m = 12
        before = instance.model_dump_json()
        observed = ship_crossing_probe(instance)
        assert observed["ship_lifts_at_or_above_h"] == 1
        assert observed["rejected_crossing_lifts"] == 1
        assert observed["probe_rule"] == "port.e3.quay_crossing"
        assert not observed["probe_accepted_plan"]
        assert observed["source"] == "random_yard_probe"
        assert instance.model_dump_json() == before


@pytest.mark.parametrize("ratio", [0.0, 0.3, 1.0])
@pytest.mark.parametrize("size", ["tiny", "small"])
@pytest.mark.parametrize("port_name", ["laem_chabang_like", "singapore_like", "small_feeder", "us_west_coast"])
def test_generated_bucket_reports_actual_replay_observations(monkeypatch, size, port_name, ratio):
    """Real fleet geometry and manifest generator; explicitly no physics claim.

    D58 and C1 are separately covered. Their TEST-ONLY stubs isolate whether
    random generation emits the specified yard stress layout and replay audit.
    """
    from stowage.generate import random_yard as random
    from stowage.vessel.fleet_slices import load_fleet_preset

    geometry = load_fleet_preset("feeder_1700", "full", with_hull=False)
    monkeypatch.setattr(random, "_full_vessel", lambda name: geometry)
    monkeypatch.setattr(random, "accept_background_candidate", lambda instance, audit: instance)
    monkeypatch.setattr(random, "completion_failures", lambda *args: ())
    with rule_freeze_v1(enabled=True):
        audit = random.RandomGenerationReport()
        instance = random.generate_random_instance(size, seed=0, vessel_name="feeder_1700",
            port_name=port_name, report=audit, d62_crossing_share=1.0, blocking_ratio=ratio)
        assert audit.d62_crossing["selected"]
        assert audit.d62_crossing["yard_bucket"]["applied"]
        assert audit.d62_crossing["yard_probe"]["yard_lifts_at_or_above_h"] == 1
        assert crossing_probe(instance) == audit.d62_crossing["yard_probe"]
        # Half a box is the exact rounding bound; allow only float representation error.
        assert abs(random.blocking_ratio(instance)-ratio) <= .5/len(instance.load_list) + 1e-12
        assert audit.d62_crossing["yard_probe"]["source"] == "random_yard_probe"
        assert instance.family["d62_crossing_requested_share"] == "1.0"
        if size == "small" and port_name == "small_feeder" and ratio == 0.:
            assert audit.d62_crossing["ship_probe"]["ship_lifts_at_or_above_h"] == 1
            assert audit.d62_crossing["ship_probe"]["probe_rule"] == "port.e3.quay_crossing"


def _cohort_instance(port_name, blocking_ratio):
    """Ten existing exports, with the requested later-cohort stack blockers."""
    instance = _one_target(port_name)
    template = instance.containers[0]
    block = instance.port.yard.blocks[0]
    boxes, stacks, targets = [], [], []
    blocked = round(10 * blocking_ratio)
    for index in range(10):
        target = template.model_copy(update={'id': make_container_id('QZXU', index)})
        boxes.append(target)
        targets.append(target.id)
        column = [target.id]
        if index < blocked:
            blocker = template.model_copy(update={
                'id': make_container_id('QZWU', index), 'gross_mass_kg': template.tare_kg,
                'empty': True, 'category': f'later:{index}',
            })
            boxes.append(blocker)
            column.append(blocker.id)
        stacks.append(YardStack(key=f'{block.id}-{index + 1:03d}-01', length_ft=20, boxes=column))
    instance.containers = boxes
    instance.load_list = targets
    instance.yard.stacks = stacks
    return Instance.model_validate(instance.model_dump())


@pytest.mark.parametrize('port_name', ['laem_chabang_like', 'small_feeder'])
@pytest.mark.parametrize('ratio', [0.3, 1.0])
def test_selected_bucket_preserves_default_and_full_two_cohort_blocking(port_name, ratio):
    from stowage.generate.random_yard import blocking_ratio
    with rule_freeze_v1(enabled=True):
        original = _cohort_instance(port_name, ratio)
        before = original.model_dump_json()
        facts = {box.id: box.model_dump() for box in original.containers}
        conditioned, audit = condition_crossing_bucket(original)
        assert audit['applied'], audit
        assert original.model_dump_json() == before
        assert conditioned.load_list == original.load_list
        assert {box.id: box.model_dump() for box in conditioned.containers if box.id in facts} == facts
        assert blocking_ratio(conditioned) == blocking_ratio(original) == ratio
        result = crossing_probe(conditioned)
        assert result['yard_lifts_at_or_above_h'] == 1
        assert result['rejected_crossing_lifts'] == 1
        assert result['probe_rule'] == 'port.e3.yard_crossing'
        assert result['source'] == 'random_yard_probe'
        assert result['probe_accepted_plan'] is False
        assert Instance.model_validate(conditioned.model_dump()) == conditioned


def test_yard_diagnostic_source_does_not_impersonate_verified_plan_evidence():
    with rule_freeze_v1(enabled=True):
        instance, audit = condition_crossing_bucket(_one_target('laem_chabang_like'))
        assert audit['applied']
        probe = crossing_probe(instance)
    assert probe['source'] == 'random_yard_probe'
    assert probe['probe_accepted_plan'] is False
    assert not probe.get('verified')
    assert not probe.get('binding_actions')


@pytest.mark.parametrize('ratio', [.3, 1.])
def test_blocked_target_probe_relocates_its_real_top_blocker_and_binds_at_the_limit(ratio):
    with rule_freeze_v1(enabled=True):
        original = _cohort_instance('laem_chabang_like', ratio)
        conditioned, audit = condition_crossing_bucket(original)
        assert audit['applied'] and audit['preserved_column_height'] == 2
        blocked = crossing_probe(conditioned)
        assert blocked['probe_kind'] == 'relocate'
        assert blocked['rejected_crossing_lifts'] == 1
        assert blocked['probe_container_id'] not in original.load_list
        # Lower only the new tower by one box. Every original shipment and
        # cohort stays put; the exact same needed access lift now crosses.
        lower = conditioned.model_copy(deep=True)
        tower = next(stack for stack in lower.yard.stacks if stack.key == audit['yard_blocker'])
        removed = tower.boxes.pop()
        lower.containers = [box for box in lower.containers if box.id != removed]
        clear = crossing_probe(lower)
    assert clear['rejected_crossing_lifts'] == 0
    assert clear['yard_lifts_at_or_above_h'] == 0
    assert clear['probe_rule'] is None
    assert clear['observed'] is False
    assert clear['probe_attempted'] is True


def test_two_row_block_probes_legal_digout_before_target_crossing():
    with rule_freeze_v1(enabled=True):
        original = _cohort_instance('laem_chabang_like', 1.)
        for block in original.port.yard.blocks:
            block.n_rows = 2
            block.reefer_rows = [row for row in block.reefer_rows if row <= 2]
        conditioned, audit = condition_crossing_bucket(original)
        assert audit['applied']
        result = crossing_probe(conditioned)
    assert result['probe_prefix_relocations'] == 1
    assert result['probe_kind'] == 'load'
    assert result['probe_container_id'] in original.load_list
    assert result['observed'] is True
    assert result['yard_lifts_at_or_above_h'] == result['rejected_crossing_lifts'] == 1
    assert result['probe_rule'] == 'port.e3.yard_crossing'
