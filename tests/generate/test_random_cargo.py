"""Activated random-yard delegates cargo policy and retains raw draw evidence."""
from copy import deepcopy

import pytest

from stowage.generate import cargo
from stowage.model.fixtures import build_instance
from stowage.ports.profiles import load_port
from stowage.rules.catalogue import rule_freeze_v1


def _adapter():
    from stowage.generate.random_cargo import RandomCargoDraws, prepare_random_cargo
    return RandomCargoDraws, prepare_random_cargo


def test_current_route_enriches_every_call_without_mutating_profile():
    _, prepare = _adapter()
    with rule_freeze_v1(enabled=True):
        params, route = prepare(load_port('small_feeder'))
    assert all(call.archetype for call in route.calls)
    assert route.calls[0].archetype == 'small_feeder'
    assert params['rotations'][0]['port_calls'] == [call.locode for call in route.calls]


def test_stress_is_a_separate_labelled_profile():
    Draws, prepare = _adapter()
    with rule_freeze_v1(enabled=True):
        params, route = prepare(load_port('laem_chabang_like'), cargo_profile='dg_stress')
        draws = Draws()
        boxes = draws.draw(route, 0, 80, seed=710, params=params)
    audit = draws.snapshot()
    assert all(box.dg and not box.empty for box in boxes)
    assert audit['calls'][0]['report']['profile'] == 'synthetic_dg_stress_d70'
    assert audit['calls'][0]['report']['requested']['dg_stress']['separate_panel'] is True
    assert audit['stages']['requested']['n_boxes'] == len(boxes)
    assert sum(audit['stages']['requested']['dg_by_class'].values()) == len(boxes)
    assert sum(audit['stages']['requested']['mass_bands'].values()) == len(boxes)
    assert sum(v['reefer'] for v in audit['stages']['drawn']['by_pod'].values()) == 0


def test_failed_call_retains_the_raw_requested_audit(monkeypatch):
    Draws, prepare = _adapter()
    def fail(*args, report, **kwargs):
        report.requested = {'booking_audit': {'requested': {'n_boxes': 1, 'dg_by_class': {'7': 1}},
                                              'drawn': {'n_boxes': 2, 'dg_by_class': {'7': 2}},
                                              'refused': {'n_boxes': 2, 'dg_by_class': {'7': 2}}}}
        report.rejections.append('unsupported demand')
        raise cargo.UnsupportedCargoError('unsupported demand')
    monkeypatch.setattr(cargo, 'generate_load_list', fail)
    with rule_freeze_v1(enabled=True):
        params, route = prepare(load_port('laem_chabang_like'))
        draws = Draws()
        with pytest.raises(cargo.UnsupportedCargoError):
            draws.draw(route, 0, 1, seed=1, params=params)
    audit = draws.snapshot()
    assert audit['calls'][0]['failed'] is True
    assert audit['stages']['requested']['dg_by_class'] == {'7': 1}
    assert audit['stages']['drawn']['dg_by_class'] == {'7': 2}
    assert audit['stages']['refused']['n_boxes'] == 2
    assert audit['stages']['emitted']['n_boxes'] == 0


def test_draws_use_cargo_policy_for_export_and_onboard(monkeypatch):
    Draws, prepare = _adapter()
    calls = []
    actual = cargo.refused_handling_calls
    def policy(*args, **kwargs):
        calls.append(kwargs.get('onboard', False))
        return actual(*args, **kwargs)
    monkeypatch.setattr(cargo, 'refused_handling_calls', policy)
    with rule_freeze_v1(enabled=True):
        params, route = prepare(load_port('laem_chabang_like'), cargo_profile='dg_stress')
        draws = Draws()
        export = draws.draw(route, 0, 5, seed=10, params=params)
        onboard = draws.draw(route, 0, 5, seed=11, params=params,
                             vessel=build_instance().vessel, leg='onboard')
    assert False in calls and True in calls
    assert {box.id for box in export}.isdisjoint(box.id for box in onboard)
    assert [call['leg'] for call in draws.snapshot()['calls']] == ['exports', 'onboard']


def test_binding_rekeys_replacements_and_filters_discarded_bookings(monkeypatch):
    Draws, prepare = _adapter()
    bound = []
    def bind(instance, report):
        bound.append(deepcopy(report.realised))
        return instance
    monkeypatch.setattr(cargo, 'bind_shipment_scenarios', bind)
    with rule_freeze_v1(enabled=True):
        params, route = prepare(load_port('laem_chabang_like'), cargo_profile='dg_stress')
        draws = Draws()
        boxes = draws.draw(route, 0, 100, seed=710, params=params)
        carrying = [box for box in boxes if box.id in draws.manifests]
        assert carrying
        kept = carrying[0]
        replacement = draws.draw(route, 0, 1, seed=32, params=params, id_offset=101)[0]
        # Use an authored manifest to isolate the rekey/filter contract.
        draws.manifests[replacement.id] = deepcopy(draws.manifests[kept.id])
        draws.replace_id(replacement.id, kept.id)
        instance = build_instance().model_copy(update={'containers': [replacement.model_copy(update={'id': kept.id})]})
        assert draws.bind(instance) is instance
    assert set(bound[0]['shipment_scenarios']) == {kept.id}
    assert bound[0]['n_boxes'] == 1
    assert draws.snapshot()['stages']['emitted']['n_boxes'] == 101


def test_whole_kg_and_exact_mass_categories_come_from_cargo():
    Draws, prepare = _adapter()
    with rule_freeze_v1(enabled=True):
        params, route = prepare(load_port('laem_chabang_like'))
        boxes = Draws().draw(route, 0, 200, seed=17, params=params)
    assert all(box.gross_mass_kg.is_integer() for box in boxes)
    ordinary = [box for box in boxes if box.category]
    assert ordinary
    assert all(f'mass_kg={box.gross_mass_kg.hex()}' in box.category for box in ordinary)


def test_replacement_generated_id_collision_keeps_other_shipment_facts():
    Draws, prepare = _adapter()
    with rule_freeze_v1(enabled=True):
        params, route = prepare(load_port('laem_chabang_like'), cargo_profile='dg_stress')
        draws = Draws()
        boxes = draws.draw(route, 0, 100, seed=710, params=params)
        declared = [box for box in boxes if box.id in draws.manifests]
        source, other = declared[:2]
        retained_other = deepcopy(draws.manifests[other.id])
        serial = next(i for i, box in enumerate(boxes) if box.id == other.id)
        generated = draws.draw(route, 0, 1, seed=32, params=params, id_offset=serial)[0]
        new_declaration = deepcopy(draws.manifests.get(generated.id))
        draws.replace_id(generated.id, source.id)
    assert draws.manifests[other.id] == retained_other
    if new_declaration is None:
        assert source.id not in draws.manifests
    else:
        assert draws.manifests[source.id] == new_declaration


def test_import_draw_delegates_the_explicit_actual_handling_leg(monkeypatch):
    Draws, prepare = _adapter()
    seen = []
    actual = cargo.generate_load_list
    def draw(rotation, call_index, **kwargs):
        seen.append((rotation, call_index))
        return actual(rotation, call_index, **kwargs)
    monkeypatch.setattr(cargo, 'generate_load_list', draw)
    with rule_freeze_v1(enabled=True):
        params, route = prepare(load_port('laem_chabang_like'), cargo_profile='dg_stress')
        draws = Draws()
        boxes = draws.draw(route, 1, 5, seed=17, params=params, leg='imports')
    assert seen == [(route, 1)]
    assert all(box.pol == route.calls[1].locode for box in boxes)
    assert draws.snapshot()['calls'][0]['leg'] == 'imports'


def test_individual_redraw_demand_is_labelled_separately():
    Draws, prepare = _adapter()
    with rule_freeze_v1(enabled=True):
        params, route = prepare(load_port('laem_chabang_like'))
        draws = Draws()
        draws.draw(route, 0, 4, seed=17, params=params)
        draws.draw(route, 0, 1, seed=18, params=params, kind='shipment_redraw')
    audit = draws.snapshot()
    assert audit['calls'][1]['kind'] == 'shipment_redraw'
    assert audit['initial_requested']['n_boxes'] == 4
    assert audit['shipment_redraw_drawn']['n_boxes'] == 1
    assert audit['stages']['requested']['n_boxes'] == 5


@pytest.mark.parametrize('leg', ['exports', 'imports', 'onboard'])
def test_all_legs_call_shared_refusal_and_direct_delivery_policies(monkeypatch, leg):
    from stowage.model.port import PortProfile
    Draws, prepare = _adapter()
    refusals, direct = [], []
    vessel = build_instance().vessel
    actual_refusal = cargo.refused_handling_calls
    actual_direct = PortProfile.requires_direct_delivery
    def refusal(*args, **kwargs):
        refusals.append(kwargs.get('onboard', False))
        return actual_refusal(*args, **kwargs)
    def delivery(port, primary_class):
        direct.append(primary_class)
        return actual_direct(port, primary_class)
    monkeypatch.setattr(cargo, 'refused_handling_calls', refusal)
    monkeypatch.setattr(PortProfile, 'requires_direct_delivery', delivery)
    with rule_freeze_v1(enabled=True):
        params, route = prepare(load_port('laem_chabang_like'), cargo_profile='dg_stress')
        params['dg']['classes'] = [row for row in params['dg']['classes'] if row['id'] == '1.4S']
        boxes = Draws().draw(route, 0, 3, seed=17, params=params, leg=leg,
                             vessel=vessel if leg == 'onboard' else None)
    assert refusals == [leg == 'onboard'] * 3
    assert direct == ['1.4S'] * 3
    assert all(box.direct_delivery for box in boxes)


@pytest.mark.parametrize('leg', ['exports', 'imports', 'onboard'])
def test_actual_cargo_manifest_binds_all_represented_dg_to_final_scope(leg):
    from stowage.generate.random_voyage import build_voyage_spec
    from stowage.rules.imdg.scenarios import voyage_identity
    Draws, prepare = _adapter()
    with rule_freeze_v1(enabled=True):
        port = load_port('laem_chabang_like')
        params, route = prepare(port, cargo_profile='dg_stress')
        draws = Draws()
        original = build_instance()
        boxes = draws.draw(route, 0, 100, seed=710, params=params, leg=leg,
                           vessel=original.vessel if leg == 'onboard' else None)
        instance = original.model_copy(update={
            'id': f'actual-{leg}-binding', 'rotation': route, 'containers': boxes,
            'voyage': build_voyage_spec(route, 0, port, seed=710)})
        before = instance.model_dump_json()
        bound = draws.bind(instance)
    assert instance.model_dump_json() == before
    config = bound.rule_overrides['imdg.stowage.3_2']
    assert set(config['containers']) == set(draws.manifests)
    assert config['assessment_scenarios']
    assert all(row['instance_id'] == instance.id and row['vessel_id'] == instance.vessel.id
               and row['voyage_id'] == voyage_identity(instance)
               for row in config['assessment_scenarios'].values())


def test_admitted_counts_exclude_prior_rejected_draws_and_yard_blockers():
    from stowage.generate.random_cargo import admitted_cargo_audit
    Draws, prepare = _adapter()
    with rule_freeze_v1(enabled=True):
        params, route = prepare(load_port('laem_chabang_like'))
        draws = Draws()
        draws.draw(route, 0, 11, seed=17, params=params)
        accepted = draws.draw(route, 0, 7, seed=18, params=params, id_offset=20)
        blocker = accepted[0].model_copy(update={'id': 'later-cohort-blocker', 'gross_mass_kg': 1000.})
        instance = build_instance().model_copy(update={
            'rotation': route, 'containers': accepted + [blocker],
            'load_list': [box.id for box in accepted]})
        actual = admitted_cargo_audit(instance, params)
    masses = sorted(box.gross_mass_kg for box in accepted)
    assert draws.snapshot()['stages']['emitted']['n_boxes'] == 18
    assert actual['n_boxes'] == 7
    assert sum(actual['mass_bands'].values()) == 7
    assert actual['mass_summary']['count'] == 7
    assert actual['mass_summary']['mean_kg'] == sum(masses) / 7
    assert actual['mass_summary']['p95_kg'] == masses[-1]
    assert sum(values['reefer'] for values in actual['by_pod'].values()) == sum(box.reefer for box in accepted)
    assert sum(values['oog'] for values in actual['by_pod'].values()) == sum(box.oog is not None for box in accepted)
    assert sum(actual['dg_by_class'].values()) == sum(box.dg is not None for box in accepted)


def test_replacing_a_shipment_with_the_same_id_preserves_new_manifest():
    Draws, prepare = _adapter()
    with rule_freeze_v1(enabled=True):
        params, route = prepare(load_port('laem_chabang_like'), cargo_profile='dg_stress')
        draws = Draws()
        boxes = draws.draw(route, 0, 100, seed=710, params=params)
        selected = next(box for box in boxes if box.id in draws.manifests)
        before = deepcopy(draws.manifests[selected.id])
        draws.replace_id(selected.id, selected.id)
    assert draws.manifests[selected.id] == before


def _authored_flow(kind, failed, **counts):
    return {'leg': 'exports', 'kind': kind, 'failed': failed, 'report': {
        'profile': 'authored-audit', 'requested': {'booking_audit': {
            name: {'n_boxes': count, 'dg_by_class': {'7': count},
                   'dg_by_un': {'2915': count}, 'mass_bands': {'<10000': count},
                   'by_pod': {'THBKK': {'n_boxes': count, 'reefer': 0, 'oog': 0}}}
            for name, count in counts.items()}}}}


def test_raw_rates_use_generator_attempts_not_cargo_calls_or_admissions():
    Draws, _ = _adapter()
    draws = Draws()
    # Three generator attempts: a failed full draw; a full draw screened later;
    # then two shipment replacements in a single attempt (one failed).
    draws.calls = [
        _authored_flow('initial', True, requested=2, drawn=2, refused=2),
        _authored_flow('initial', False, requested=4, drawn=5, redrawn=1, refused=1, emitted=4),
        _authored_flow('shipment_redraw', False, requested=1, drawn=2, redrawn=1, refused=1, emitted=1),
        _authored_flow('shipment_redraw', True, requested=1, drawn=3, redrawn=2, refused=3),
    ]
    before = deepcopy(draws.calls)
    audit = draws.snapshot(attempts=3)
    assert draws.calls == before
    assert audit['cargo_call_counts'] == {'total': 4, 'failed': 2, 'initial': 2, 'shipment_redraw': 2}
    normalized = audit['per_attempt']
    assert normalized['status'] == 'available'
    assert normalized['denominator'] == 3
    assert 'generator' in normalized['denominator_definition']
    assert normalized['initial_requested']['n_boxes'] == 2
    assert normalized['shipment_redraw_drawn']['n_boxes'] == 5 / 3
    for stage, count in {'requested': 8, 'drawn': 12, 'redrawn': 4,
                         'refused': 7, 'emitted': 5}.items():
        assert audit['stages'][stage]['n_boxes'] == count
        rates = normalized['stages'][stage]
        assert rates['n_boxes'] == count / 3
        assert rates['dg_by_class']['7'] == count / 3
        assert rates['dg_by_un']['2915'] == count / 3
        assert rates['mass_bands']['<10000'] == count / 3
        assert rates['by_pod']['THBKK']['n_boxes'] == count / 3
    # Counts per attempt can exceed one; these are not DG shares or admission rates.
    assert normalized['stages']['refused']['n_boxes'] > 1
    assert audit['initial_requested']['n_boxes'] == 6
    assert audit['shipment_redraw_drawn']['n_boxes'] == 5


def test_missing_attempt_denominator_is_explicit_and_never_inferred_from_calls():
    Draws, _ = _adapter()
    draws = Draws()
    draws.calls = [_authored_flow('initial', True, requested=2, drawn=2, refused=2)]
    normalized = draws.snapshot()['per_attempt']
    assert normalized['status'] == 'missing_generator_attempt_count'
    assert normalized['denominator'] is None
    assert normalized['initial_requested'] is None
    assert normalized['stages'] is None


def test_zero_attempts_has_undefined_rates_and_no_invented_draws():
    Draws, _ = _adapter()
    audit = Draws().snapshot(attempts=0)
    assert audit['per_attempt']['status'] == 'no_attempts'
    assert audit['per_attempt']['denominator'] == 0
    assert audit['per_attempt']['stages'] is None
    assert audit['stages']['requested']['n_boxes'] == 0


@pytest.mark.parametrize('attempts', [-1, True, 1.5, '3'])
def test_invalid_attempt_denominator_is_rejected(attempts):
    Draws, _ = _adapter()
    with pytest.raises(ValueError, match='attempts'):
        Draws().snapshot(attempts=attempts)


def test_zero_attempts_cannot_describe_recorded_cargo_calls():
    Draws, _ = _adapter()
    draws = Draws()
    draws.calls = [_authored_flow('initial', False, requested=1, drawn=1, emitted=1)]
    with pytest.raises(ValueError, match='attempts'):
        draws.snapshot(attempts=0)
