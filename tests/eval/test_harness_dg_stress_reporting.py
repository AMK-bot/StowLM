"""D70 stress reports isolate releases and require measured coverage witnesses."""
from copy import deepcopy

import pytest

from eval import harness_metrics as metrics
from eval.splits import load_inventory, spec_key


def rows(release='dg-stress-v3', rung=None):
    inventory = load_inventory(release)
    return [{**entry, 'spec_key': spec_key(entry['spec']),
             'instance_id': spec_key(entry['spec']), 'rung': entry['spec']['rung'],
             'split_version': inventory.version, 'split_entries_sha256': inventory.entries_sha256,
             'allocation_only': True, 'feasibility_status': 'unknown', 'report': None}
            for entry in inventory.manifest['entries']
            if entry['split'] == 'test' and entry['spec']['rung'] in ('tiny', 'small')
            and (rung is None or entry['spec']['rung'] == rung)]


def test_mixed_release_aggregation_is_rejected():
    with pytest.raises(ValueError, match='release|split|inventory'):
        metrics.aggregate_results(rows('v2') + rows(), split='test', tiny_run=True)


def test_stress_only_summary_preserves_release_pin():
    inventory = load_inventory('dg-stress-v3')
    summary = metrics.aggregate_results(rows(), split='test', tiny_run=True)
    assert summary['split_version'] == inventory.version
    assert summary['split_entries_sha256'] == inventory.entries_sha256
    for rung in ('tiny', 'small'):
        total = {'tiny': 15, 'small': 30}[rung]
        result = summary['by_rung'][rung]
        assert result['split_version'] == inventory.version
        assert result['n_allocated'] == total
        assert result['feasibility_status_counts'] == {
            'feasible-witnessed': 0, 'proven-infeasible': 0, 'unknown': total, 'generation-rejected': 0}
        assert result['pass_rate_ci95'] == pytest.approx(metrics.wilson_interval(0, total))


def test_stress_render_includes_explicit_release_label():
    summary = metrics.aggregate_results(rows(), split='test', tiny_run=True)
    rendered = metrics.render_row(summary, tiny_run=True)
    assert 'dg-stress-v3' in rendered
    assert 'DG safety under stress, zero-shot transfer' in rendered
    assert 'tiny' in rendered and 'small' in rendered
    assert '15 allocated' in rendered and 'Wilson 95%' in rendered
    assert '30 allocated' in rendered
    assert 'D70' in rendered and 'not tested' in rendered


@pytest.mark.parametrize('partial', ['missing-version', 'missing-digest', 'one-unlabelled', 'digest-drift'])
@pytest.mark.parametrize('paired', [False, True])
def test_public_metrics_reject_partial_or_drifting_release_pins(partial, paired):
    selected = rows(rung='tiny')[:2]
    if partial == 'missing-version':
        del selected[0]['split_version']
    elif partial == 'missing-digest':
        del selected[0]['split_entries_sha256']
    elif partial == 'one-unlabelled':
        del selected[0]['split_version'], selected[0]['split_entries_sha256']
    else:
        selected[0]['split_entries_sha256'] = 'f' * 64
    with pytest.raises(ValueError, match='release|split|inventory'):
        (metrics.paired_comparison(selected, deepcopy(selected), n_bootstrap=2) if paired
         else metrics.aggregate_results(selected))


def test_pairing_rejects_cross_release_and_retains_pin_in_rendered_comparison():
    stress = rows(rung='tiny')[:1]
    main = [{**stress[0], 'split_version': 'v2',
             'split_entries_sha256': load_inventory('v2').entries_sha256}]
    with pytest.raises(ValueError, match='release|split|inventory'):
        metrics.paired_comparison(stress, main, n_bootstrap=2)
    comparison = metrics.paired_comparison(stress, deepcopy(stress), n_bootstrap=2)
    assert comparison['split_version'] == 'dg-stress-v3'
    assert comparison['split_entries_sha256'] == stress[0]['split_entries_sha256']
    rendered = metrics.render_paired_comparison(comparison, first_method='StowLM', second_method='base Glimmer + mask')
    assert 'dg-stress-v3' in rendered and 'DG safety under stress, zero-shot transfer' in rendered


def measured_rows(n_class=10, n_binding=5):
    from eval.dg_coverage import BINDING_DEFINITION
    selected = rows(rung='small')
    for index, row in enumerate(selected):
        if index < n_class:
            row['allocation_only'] = False
            row['dg_coverage'] = {'admitted_class_uns': {'3': ['1263'], '4.3': ['1402']}}
        if index < n_binding:
            row['reference_solver'] = {'witness': {
                'report': {'passed': True}, 'report_binding_valid': True,
                'result': {'planner': 'solver.exact'},
                'dg_binding': {'definition': BINDING_DEFINITION, 'plan_selection': 'best_passing',
                               'pairs': [['3', '4.3']]}}}
    return selected


@pytest.mark.parametrize('n_class,n_binding,tested_class,tested_pair',
                         [(9, 4, False, False), (10, 5, True, True)])
def test_d70_coverage_thresholds_count_instances_and_print_uns(n_class, n_binding, tested_class, tested_pair):
    selected = measured_rows(n_class, n_binding)
    summary = metrics.aggregate_results(selected)
    coverage = summary['dg_coverage']
    assert coverage['classes']['4.3']['n_instances'] == n_class
    assert coverage['classes']['4.3']['uns'] == ['1402']
    assert coverage['classes']['4.3']['tested'] is tested_class
    pair = coverage['pairs']['3/4.3']
    assert pair['n_copresent'] == n_class
    assert pair['n_binding'] == n_binding and pair['tested'] is tested_pair
    rendered = metrics.render_row(summary)
    assert f'4.3={n_class}' in rendered and '1402' in rendered
    assert f'3/4.3={n_binding}' in rendered
    assert summary['n_allocated'] == 30


@pytest.mark.parametrize('bad_reference', ['failed', 'wrong-definition', 'not-best', 'copresence-only',
                                          'model-plan', 'detached-report'])
def test_d70_does_not_substitute_copresence_or_unqualified_reference(bad_reference):
    selected = measured_rows(10, 5)
    for row in selected[:5]:
        witness = row['reference_solver']['witness']
        if bad_reference == 'failed':
            witness['report']['passed'] = False
        elif bad_reference == 'wrong-definition':
            witness['dg_binding']['definition'] = 'class co-presence'
        elif bad_reference == 'not-best':
            witness['dg_binding']['plan_selection'] = 'any_passing'
        elif bad_reference == 'model-plan':
            witness['result']['planner'] = 'stowlm'
        elif bad_reference == 'detached-report':
            witness['report_binding_valid'] = False
        else:
            del witness['dg_binding']
    coverage = metrics.aggregate_results(selected)['dg_coverage']
    assert coverage['pairs']['3/4.3']['n_copresent'] == 10
    assert coverage['pairs']['3/4.3']['n_binding'] == 0
    assert coverage['pairs']['3/4.3']['tested'] is False
    assert coverage['n_binding_unmeasured'] == 30


def test_admitted_class_measurement_excludes_unrelated_yard_cargo_and_counts_mixed_entries():
    from types import SimpleNamespace as NS
    from eval.dg_coverage import admitted_class_uns
    def dg(label, un, additional=()):
        return NS(imdg_class=label, un_number=un, additional=additional)
    instance = NS(load_list=['export'], onboard=NS(placements=[NS(container_id='onboard')]), containers=[
        NS(id='export', dg=dg('3', '1263', [dg('4.3', '1402')])),
        NS(id='onboard', dg=dg('8', '1789')),
        NS(id='yard-blocker', dg=dg('2.1', '1011'))])
    assert admitted_class_uns(instance) == {'3': ['1263'], '4.3': ['1402'], '8': ['1789']}


def test_d70_counts_distinct_instances_and_never_uses_request_family_tags():
    selected = measured_rows(1, 1)
    repeated = metrics.aggregate_results([selected[0]] * 15)['dg_coverage']
    assert repeated['classes']['4.3']['n_instances'] == 1
    assert repeated['pairs']['3/4.3']['n_binding'] == 1
    unmeasured = metrics.aggregate_results(rows())['dg_coverage']
    assert unmeasured['n_admitted_unmeasured'] == 45
    assert not any(value['tested'] for value in unmeasured['classes'].values())
    assert not any(value['tested'] for value in unmeasured['pairs'].values())


def test_stress_emitted_unattempted_specs_retain_full_denominator():
    selected = rows(rung='tiny')
    selected[0].update(allocation_only=False, report={'passed': True}, feasibility_status='feasible-witnessed')
    selected[1].update(emitted=True)
    selected[2]['feasibility_status'] = 'generation-rejected'
    selected[3]['feasibility_status'] = 'proven-infeasible'
    summary = metrics.aggregate_results(selected)
    assert summary['n_allocated'] == 15
    assert summary['n_emitted'] == 2 and summary['n_attempted'] == 1
    assert summary['feasibility_status_counts'] == {
        'feasible-witnessed': 1, 'proven-infeasible': 1, 'unknown': 12, 'generation-rejected': 1}
    assert summary['pass_rate_ci95'] == pytest.approx(metrics.wilson_interval(1, 15))
    assert summary['by_family_rung']['dg_stress']['tiny']['pass_rate_ci95'] == summary['pass_rate_ci95']
    rendered = metrics.render_row(summary)
    assert '2 emitted; 1 attempted' in rendered


def test_active_policy_target_pairs_visible_before_any_binding_witness():
    summary = metrics.aggregate_results(rows())
    assert set(summary['dg_coverage']['pairs']) == {'3/4.3', '3/4.1'}
    for pair in summary['dg_coverage']['pairs'].values():
        assert pair == {'n_copresent': 0, 'n_binding': 0, 'tested': False}
    assert summary['dg_coverage']['n_binding_unmeasured'] == 45


def test_main_release_class_coverage_remains_available_without_stress_cohort_policy():
    from eval.dg_coverage import coverage_summary
    measured = {'spec_key': 'main', 'split_version': 'v2',
                'dg_coverage': {'admitted_class_uns': {'4.1': ['1333']}}}
    result = coverage_summary([measured])
    assert result['classes']['4.1'] == {'n_instances': 1, 'uns': ['1333'], 'tested': False}
    assert result['n_binding_unmeasured'] == 1


def test_requested_pair_cohorts_are_separate_strata_with_exact_allocations():
    summary = metrics.aggregate_results(rows())
    cohorts = summary['by_dg_stress_pair']
    assert {cohort: value['n_allocated'] for cohort, value in cohorts.items()} == {
        'mixed': 29, 'flammable_water_reactive': 8, 'flammable_solid_flammable_liquid': 8}
    by_rung = summary['by_dg_stress_pair_rung']
    assert {rung: value['n_allocated'] for rung, value in by_rung['mixed'].items()} == {'tiny': 15, 'small': 14}
    for cohort in ('flammable_water_reactive', 'flammable_solid_flammable_liquid'):
        result = by_rung[cohort]['small']
        assert result['n_allocated'] == 8
        assert result['n_emitted'] == 0 and result['n_generation_rejected'] == 0
        assert result['feasibility_status_counts']['unknown'] == 8
        assert result['pass_rate_ci95'] == pytest.approx(metrics.wilson_interval(0, 8))
        assert all(not pair['tested'] for pair in result['dg_coverage']['pairs'].values())
        assert cohort in metrics.render_row(summary)


def test_cohort_counts_keep_rejection_and_unknown_specs_in_pass_denominator():
    selected = rows(rung='small')
    cohort = 'flammable_solid_flammable_liquid'
    allocated = [row for row in selected if row['spec']['dg_stress_pair'] == cohort]
    allocated[0].update(allocation_only=False, report={'passed': True}, feasibility_status='feasible-witnessed')
    allocated[1].update(allocation_only=False, feasibility_status='proven-infeasible')
    allocated[2]['feasibility_status'] = 'generation-rejected'
    result = metrics.aggregate_results(selected)['by_dg_stress_pair_rung'][cohort]['small']
    assert result['n_allocated'] == 8 and result['n_emitted'] == 2
    assert result['n_generation_rejected'] == 1 and result['n_not_attempted'] == 5
    assert result['feasibility_status_counts'] == {
        'feasible-witnessed': 1, 'proven-infeasible': 1, 'unknown': 5, 'generation-rejected': 1}
    assert result['pass_rate'] == 1 / 8
    assert result['pass_rate_ci95'] == pytest.approx(metrics.wilson_interval(1, 8))
    assert result['dg_coverage']['pairs']['3/4.1']['tested'] is False


def test_evaluator_persists_allocated_pair_selector(empty_ship_instance, monkeypatch):
    from stowage.model import PlannerResult
    from tests.eval.test_harness import Diagnostic
    from eval import harness
    selected = next(row for row in rows(rung='small')
                    if row['spec']['dg_stress_pair'] == 'flammable_solid_flammable_liquid')
    monkeypatch.setattr(harness, '_load_selected', lambda *args: empty_ship_instance)
    monkeypatch.setattr(harness, 'run_planner', lambda planner, instance, **kw:
        (PlannerResult(status='error', planner=planner.name, planner_version=planner.version), None))
    record = harness._evaluate(Diagnostic(), {**selected, 'input_sha256': 'synthetic'}, 0, None, True, None)
    assert record['dg_stress_pair'] == 'flammable_solid_flammable_liquid'
    assert metrics.aggregate_results([record])['by_dg_stress_pair']['flammable_solid_flammable_liquid']['n_allocated'] == 1


@pytest.mark.parametrize('n_class,n_binding,tested_class,tested_pair',
                         [(9, 4, False, False), (10, 5, True, True)])
def test_new_pair_keeps_d70_thresholds_and_prints_actual_class_uns(n_class, n_binding, tested_class, tested_pair):
    selected = measured_rows(n_class, n_binding)
    for row in selected[:n_class]:
        row['dg_coverage']['admitted_class_uns'] = {'3': ['1263'], '4.1': ['1333']}
    for row in selected[:n_binding]:
        row['reference_solver']['witness']['dg_binding']['pairs'] = [['4.1', '3']]
    summary = metrics.aggregate_results(selected)
    coverage = summary['dg_coverage']
    assert coverage['classes']['4.1'] == {'n_instances': n_class, 'uns': ['1333'], 'tested': tested_class}
    assert coverage['pairs']['3/4.1'] == {'n_copresent': n_class, 'n_binding': n_binding, 'tested': tested_pair}
    assert summary['n_allocated'] == 30
    rendered = metrics.render_row(summary)
    assert f'3/4.1={n_binding}' in rendered and '1333' in rendered


@pytest.mark.parametrize('version', ['dg-stress-v1', 'dg-stress-v2'])
@pytest.mark.parametrize('consumer', ['aggregate', 'paired', 'row', 'paired-row'])
def test_superseded_stress_refuses_even_for_plain_mapping_reporting(version, consumer):
    # Bypass the inventory loader deliberately: public mapping APIs must refuse too.
    from pathlib import Path
    import json
    manifest = json.loads((Path(__file__).resolve().parents[2] /
                           f'experiments/splits/{version}/manifest.json').read_text())
    archived = {'split_version': version, 'split_entries_sha256': manifest['entries_sha256'],
                'instance_id': 'archived', 'allocation_only': True, 'report': None}
    with pytest.raises(ValueError, match='superseded'):
        if consumer == 'aggregate':
            metrics.aggregate_results([archived])
        elif consumer == 'paired':
            metrics.paired_comparison([archived], [archived], n_bootstrap=2)
        elif consumer == 'row':
            metrics.render_row(archived)
        else:
            metrics.render_paired_comparison({**archived, 'source': 'paired_comparison'},
                                            first_method='A', second_method='B')
