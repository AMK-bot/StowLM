"""D72 compares rehandles above the yard bound, retaining mixing provenance."""
import json

import pytest

from eval import harness, harness_metrics as metrics


def row(key='i', *, total=6, bound=2, passed=True, repaired=False, mixing=None):
    value = {'instance_id': key, 'spec_key': key, 'input_sha256': key, 'rung': 'tiny',
             'report': {'passed': passed, 'count': {'relocations': total}},
             'result': {'repaired': repaired}, 'lower_bound': {'relocations': bound},
             'shippable': passed}
    if mixing is not None:
        value['instance'] = {'provenance': {'congestion_mixing': mixing}}
    return value


def mixing(achieved=.1, configured=.25):
    return {'policy': 'synthetic-independent-v2', 'congestion_mix_share': configured,
            'configured_share': configured, 'achieved_share': achieved, 'synthetic': True}


def test_yard_total_and_avoidable_are_reported_together_with_zero_certificate_note():
    summary = metrics.aggregate_results([row(), row('zero', total=5, bound=0)])
    yard = summary['yard_rehandles']
    assert yard['total']['mean'] == 5.5
    assert yard['lower_bound']['mean'] == 1
    assert yard['avoidable']['mean'] == 4.5
    assert yard['n_no_certificate'] == 1
    assert summary['moves']['avoidable_yard_rehandles']['mean'] == 4.5
    text = metrics.render_row(summary)
    assert 'yard total 5.5' in text and 'avoidable yard rehandles 4.5' in text
    assert 'no certificate' in text
    assert 'avoidable yard rehandles' in metrics.render_table_header()


def test_missing_yard_bound_is_unknown_and_failed_or_repaired_plans_do_not_enter_own_metric():
    selected = [row(), row('missing', bound=None), row('failed', total=1, bound=2, passed=False),
                row('repair', total=10, bound=2, repaired=True)]
    summary = metrics.aggregate_results(selected)
    assert summary['yard_rehandles']['n_missing_lower_bound'] == 1
    assert summary['yard_rehandles']['avoidable']['n'] == 1
    assert summary['yard_rehandles']['avoidable']['mean'] == 4
    assert summary['moves']['avoidable_yard_rehandles']['n'] == 1
    shipped = metrics.aggregate_results(selected, population='shipped')
    assert shipped['yard_rehandles']['avoidable']['mean'] == 6


def test_checked_plan_below_yard_bound_is_rejected_not_clamped_to_zero():
    with pytest.raises(ValueError, match='yard lower bound'):
        metrics.aggregate_results([row(total=1, bound=2)])


def test_mixing_groups_use_recorded_policy_and_configured_share_not_noise_or_achieved_share():
    first, second = row('a', mixing=mixing(.1)), row('b', mixing=mixing(.3))
    first['instance']['family'] = {'noise': '0.2'}
    second['instance']['family'] = {'noise': '0.8'}
    unmixed = row('off', mixing=mixing(0, 0))
    unknown = row('unknown')
    unknown['instance'] = {'family': {'noise': '0.8'}}
    summary = metrics.aggregate_results([first, second, unmixed, unknown])
    groups = summary['by_congestion_mixing']
    assert len(groups) == 3
    active = next(g for g in groups.values() if g['provenance'].get('configured_share') == .25)
    assert active['n_allocated'] == 2
    assert active['yard_rehandles']['avoidable']['mean'] == 4
    assert active['achieved_share']['mean'] == pytest.approx(.2)
    assert groups['unknown']['n_allocated'] == 1
    assert 'congestion mixing' in metrics.render_row(summary)


def test_family_mixing_tags_are_consumed_without_fabricating_achieved_share():
    selected = row()
    selected['instance'] = {'family': {'congestion_mix_share': '0.25',
        'congestion_mixing_policy': 'synthetic-independent-v2'}}
    groups = metrics.aggregate_results([selected])['by_congestion_mixing']
    group = next(iter(groups.values()))
    assert group['provenance']['congestion_mix_share'] == .25
    assert group['achieved_share']['n'] == 0


def test_paired_avoidable_values_require_both_passes_and_known_matching_yard_bounds():
    first = [row('a'), row('zero', bound=0), row('missing', bound=None), row('repair', repaired=True)]
    second = [row('a', total=8), row('zero', total=8, bound=0), row('missing'), row('repair')]
    compared = metrics.paired_comparison(first, second, metric='avoidable_yard_rehandles', n_bootstrap=10)
    assert compared['n_pairs'] == 2 and compared['n_excluded'] == 2
    assert compared['mean_difference'] == -2
    assert compared['per_instance'][0]['first'] == 4
    assert compared['per_instance'][0]['second'] == 6
    assert compared['n_no_certificate'] == 1
    text = metrics.render_paired_comparison(compared, first_method='A', second_method='B')
    assert 'avoidable yard rehandles' in text and 'no certificate' in text
    with pytest.raises(ValueError, match='yard lower bound'):
        metrics.paired_comparison([row()], [row(bound=3)], metric='avoidable_yard_rehandles', n_bootstrap=10)


def test_paired_avoidable_comparison_stratifies_recorded_mixing_and_refuses_conflicting_controls():
    first = [row('mixed', mixing=mixing()), row('off', mixing=mixing(0, 0))]
    second = [row('mixed', total=8, mixing=mixing()), row('off', total=10, mixing=mixing(0, 0))]
    compared = metrics.paired_comparison(first, second, metric='avoidable_yard_rehandles', n_bootstrap=10)
    assert len(compared['by_congestion_mixing']) == 2
    assert sorted(group['mean_difference'] for group in compared['by_congestion_mixing'].values()) == [-4, -2]
    assert all(group['n_pairs'] == 1 and group['n_excluded'] == 0
               for group in compared['by_congestion_mixing'].values())
    assert 'congestion mixing' in metrics.render_paired_comparison(compared, first_method='A', second_method='B')
    second[0]['instance']['provenance']['congestion_mixing']['configured_share'] = .5
    with pytest.raises(ValueError, match='congestion.mixing'):
        metrics.paired_comparison(first, second, metric='avoidable_yard_rehandles', n_bootstrap=10)


def test_evaluation_records_actual_bound_and_preserves_input_mixing(empty_ship_instance, monkeypatch):
    from stowage.bounds import lower_bound
    from stowage.model import CheckReport, MoveCount, PlannerResult
    from stowage.yard.simulator import YardSimulationProvenance
    from tests.eval.test_harness import Diagnostic
    instance = empty_ship_instance.model_copy(deep=True)
    instance.provenance = YardSimulationProvenance(**instance.provenance.model_dump(), congestion_mixing=mixing())
    monkeypatch.setattr(harness, '_load_selected', lambda *args: instance)
    monkeypatch.setattr(harness, 'run_planner', lambda planner, instance, **kw:
        (PlannerResult(status='error', planner=planner.name, planner_version=planner.version),
         CheckReport(passed=False, count=MoveCount(relocations=7))))
    source = {'spec_key': 'x', 'split': 'test', 'rung': 'tiny', 'input_sha256': 'x'}
    record = harness._evaluate(Diagnostic(), source, 0, None, True, None)
    assert record['yard_rehandles']['total'] == 7
    assert record['yard_rehandles']['lower_bound'] == lower_bound(instance).relocations
    assert record['yard_rehandles']['avoidable'] == 7 - lower_bound(instance).relocations
    assert record['congestion_mixing'] == mixing()


def test_paired_harness_publishes_avoidable_companion_next_to_primary_metric(tmp_path, empty_ship_instance, monkeypatch):
    from types import SimpleNamespace
    from stowage.model import PlannerResult
    from tests.eval.test_harness import Diagnostic, campaign
    root = campaign(tmp_path, empty_ship_instance)
    monkeypatch.setattr(harness, 'run_planner', lambda planner, instance, **kw:
        (PlannerResult(status='error', planner=planner.name, planner_version=planner.version), None))
    monkeypatch.setattr(harness, 'lower_bound', lambda instance: SimpleNamespace(model_dump=lambda **kw: {}))
    compared = harness.run_paired_evaluation(Diagnostic(), Diagnostic(), run_dir=tmp_path / 'paired',
        instances_dir=root, rungs=['tiny'], tiny_run=True, n_bootstrap=10)
    yard = compared['avoidable_yard_rehandles_comparison']
    assert yard['metric'] == 'avoidable_yard_rehandles'
    assert yard['n_pairs'] == 0 and yard['n_excluded'] > 0
    assert 'shipped_comparison' in yard
    saved = json.loads((tmp_path / 'paired/paired.json').read_text())
    assert saved['avoidable_yard_rehandles_comparison'] == yard
    assert 'avoidable yard rehandles' in (tmp_path / 'paired/ROW.md').read_text()
