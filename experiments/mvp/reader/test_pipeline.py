"""Semantic binding audits must not confuse allocation choices with language."""
from types import SimpleNamespace as NS
from unittest.mock import patch

from experiments.mvp.reader.pipeline import intrinsic_violations, plan_signature
from experiments.mvp.reader.schema import OnePortPerHatch


def fixture(pod_b):
    stacks = [NS(id='hold', hatch_cover_id='HC'), NS(id='deck', hatch_cover_id='HC')]
    instance = NS(vessel=NS(stacks=stacks,
        cells=[NS(id='0010102', stack_id='hold'), NS(id='0010182', stack_id='deck')],
        hatch_covers=[NS(id='HC', hold_stack_ids=['hold'], deck_stack_ids=['deck'])]),
        containers=[NS(id='a', pod='SGSIN'), NS(id='b', pod=pod_b)])
    placements = [NS(container_id='a', slot='0010102'), NS(container_id='b', slot='0010182')]
    replay = NS(legal=True, final_state=NS(ship=NS(placements=placements)))
    return instance, replay


def test_binding_uses_intrinsic_cover_ports_across_both_zones():
    instance, replay = fixture('MYPKG')
    with patch('experiments.mvp.reader.pipeline.apply_plan', return_value=replay):
        assert intrinsic_violations(instance, OnePortPerHatch(type='one_port_per_hatch'), object())


def test_software_mapping_choice_does_not_make_single_port_baseline_violate():
    instance, replay = fixture('SGSIN')
    with patch('experiments.mvp.reader.pipeline.apply_plan', return_value=replay):
        assert not intrinsic_violations(instance, OnePortPerHatch(type='one_port_per_hatch'), object())


def test_ballast_metadata_alone_is_not_a_changed_container_plan():
    move = NS(model_dump=lambda **kwargs: {'kind': 'load', 'container_id': 'a', 'dst': '0010102'})
    before = NS(moves=[move], ballast={'tank': 1})
    after = NS(moves=[move], ballast={'tank': 2})
    assert plan_signature(before) == plan_signature(after)
