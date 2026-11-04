"""Hand-calculated regression for the experiment's interpolation error table."""
import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest
import numpy as np

_spec = importlib.util.spec_from_file_location('method1_error_benchmark', Path(__file__).with_name('benchmark.py'))
benchmark = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(benchmark)
_spec_tanks = importlib.util.spec_from_file_location('method1_error_tanks', Path(__file__).with_name('qualify_tanks.py'))
tanks = importlib.util.module_from_spec(_spec_tanks)
_spec_tanks.loader.exec_module(tanks)


def test_error_accounting_uses_absolute_l5_floor_and_common_domain():
    direct = dict(curve=dict(angles_rad=[0., .5, 1.], gz_m=[0., .5, 1.], negative_gz_m=[0.,-.5,-1.]),
                  metrics=dict(gm_m=1., area_0_40_mrad=.02))
    fast = dict(curve=dict(angles_rad=[0., .25, .5], gz_m=[0., .255, .51], negative_gz_m=[0.,-.255,-.51]),
                metrics=dict(gm_m=1.002, area_0_40_mrad=.0201))
    row = benchmark.compare(direct, fast)
    assert row['common_end_rad'] == .5
    assert row['max_absolute_gz_difference_m'] == pytest.approx(.01)
    assert row['max_gz_l5_fraction'] == pytest.approx(.2)
    assert row['max_area_l5_fraction'] == pytest.approx(.0001/.0012)
    assert row['gm_l5_fraction'] == pytest.approx(.002/.05)
    assert row['common_domain_signed_area_difference_mrad'] == pytest.approx(.0025)


def test_error_accounting_uses_larger_relative_tolerance():
    direct = dict(curve=dict(angles_rad=[0., .5], gz_m=[0., 2.], negative_gz_m=[0.,-2.]),
                  metrics=dict(gm_m=10., area_0_40_mrad=1.))
    fast = dict(curve=dict(angles_rad=[0., .5], gz_m=[0., 2.01], negative_gz_m=[0.,-2.01]),
                metrics=dict(gm_m=10.01, area_0_40_mrad=1.001))
    row = benchmark.compare(direct, fast)
    assert row['max_gz_l5_fraction'] == pytest.approx(.1)
    assert row['max_area_l5_fraction'] == pytest.approx(.02)
    assert row['gm_l5_fraction'] == pytest.approx(.1)


def test_error_accounting_records_actual_combined_reference_scope():
    report = dict(curve=dict(angles_rad=[0., .5], gz_m=[0., .5], negative_gz_m=[0.,-.5]),
                  metrics=dict(gm_m=1., area_0_40_mrad=.02))
    row = benchmark.compare(report, report, scope='direct hull AND tanks versus both interpolated')
    assert row['scope'] == 'direct hull AND tanks versus both interpolated'


def test_negative_only_error_controls_qualification_and_records_signed_area():
    direct=dict(curve=dict(angles_rad=[0.,.5,1.],gz_m=[0.,.5,1.],
                           negative_gz_m=[0.,-.5,-1.]),
                metrics=dict(gm_m=1.,area_0_40_mrad=.02))
    fast=deepcopy(direct)
    fast['curve']['negative_gz_m']=[0.,-.52,-1.]
    row=benchmark.compare(direct,fast)
    assert row['max_absolute_positive_gz_difference_m']==0.
    assert row['max_absolute_negative_gz_difference_m']==pytest.approx(.02)
    assert row['max_absolute_gz_difference_m']==pytest.approx(.02)
    assert row['max_gz_l5_fraction']==pytest.approx(.4)
    assert row['common_domain_signed_negative_area_difference_mrad']==pytest.approx(-.01)


def test_negative_error_uses_its_own_relative_tolerance_and_common_grid():
    direct=dict(curve=dict(angles_rad=[0.,.5,1.],gz_m=[0.,.1,.2],
                           negative_gz_m=[0.,-2.,-4.]),
                metrics=dict(gm_m=1.,area_0_40_mrad=.02))
    fast=deepcopy(direct)
    fast['curve']=dict(angles_rad=[0.,.25,.5],gz_m=[0.,.05,.1],
                       negative_gz_m=[0.,-1.01,-2.02])
    row=benchmark.compare(direct,fast)
    assert row['common_end_rad']==.5
    assert row['max_gz_l5_fraction']==pytest.approx(.2)
    assert row['common_domain_signed_negative_area_difference_mrad']==pytest.approx(-.005)


def migration_report():
    return dict(ran=True, error=None, metrics={key: value for key, value in (
        ('displacement_kg', 8_000_000.), ('kg_m', 7.), ('draught_mean_m', 6.),
        ('trim_m', .5), ('gm_m', 1.), ('list_rad', 0.), ('windage_area_m2', 900.),
        ('windage_lever_m', 8.))}, curve=dict(angles_rad=[0., .5, 1.],
            gz_m=[0., .4, .1], negative_gz_m=[0., -.4, -.1], flooding_angle_rad=1.))


def test_metadata_migration_requires_fresh_method2_physics_and_complete_curve():
    reference = migration_report()
    current = deepcopy(reference)
    current['metrics']['gm_m'] += 1e-9
    assert benchmark.physical_method2_guard(reference, current)['passed']
    current['curve']['negative_gz_m'][1] += 1e-5
    with pytest.raises(ValueError, match='negative_gz_m'):
        benchmark.physical_method2_guard(reference, current)


@pytest.mark.parametrize('key', ['displacement_kg', 'trim_m', 'list_rad', 'windage_area_m2', 'windage_lever_m'])
def test_metadata_migration_rejects_changed_physical_condition(key):
    reference = migration_report()
    current = deepcopy(reference)
    current['metrics'][key] += .001
    with pytest.raises(ValueError, match=key):
        benchmark.physical_method2_guard(reference, current)


def test_same_attitude_transfer_error_counts_transverse_shift_once():
    # At45deg and zero trim, x is longitudinal and contributes no heel lever.
    # A10kg liquid (rho1000*V.01) in a1000kg ship shifted by1cm in both y,z
    # changes the single transfer correction by(.01+.01)/sqrt(2)*10/1000.
    delta = np.array([10., .01, .01])
    difference = tanks.transfer_lever_difference(delta, .01, 1000., 1000., np.pi/4, 0.)
    assert difference == pytest.approx(.02/np.sqrt(2)*.01)


def envelope_evidence():
    manifest = {'FO-P': dict(sha256='same-fo-grid', fills=129, heels=25, trims=5)}
    current = dict(vessel='example', parameters_sha256='same-params', displacement_kg=10_000_000.,
        grid_manifest=manifest, source_sha256={'src/stowage/checker/stability_tank_transfer.py': 'same-oracle'})
    previous = deepcopy(current)
    previous.update(samples=16, envelope_samples=4, envelopes=[])
    for index in range(4):
        tank = dict(id='FO-P', heel_rad=.1+index/10, trim_rad=.005, direct_m4=1.,
            interpolated_m4=.99, oracle_upper_bound_excess_m4=1., gz_error_m=1e-6)
        previous['envelopes'].append(dict(index=index, aggregate_gz_error_m=1e-6, tanks=[tank]))
    return previous, current, ['FO-P'], {'FO-P': 1000.}


def test_fo_measurement_reuse_ignores_changed_ballast_table_but_preserves_values():
    previous, current, ids, density = envelope_evidence()
    previous['grid_manifest']['DB-M-P'] = dict(sha256='old-ballast')
    current['grid_manifest']['DB-M-P'] = dict(sha256='refined-ballast')
    reused = tanks.reusable_envelopes(previous, current, ids, density, requested=4)
    assert reused == previous['envelopes']


@pytest.mark.parametrize('change', ['params', 'fo_grid', 'oracle', 'mass', 'tolerance', 'value', 'nan', 'incomplete'])
def test_fo_measurement_reuse_rejects_changed_inputs_or_invalid_measurements(change):
    previous, current, ids, density = envelope_evidence()
    if change == 'params':
        current['parameters_sha256'] = 'changed'
    elif change == 'fo_grid':
        current['grid_manifest']['FO-P']['sha256'] = 'changed'
    elif change == 'oracle':
        current['source_sha256']['src/stowage/checker/stability_tank_transfer.py'] = 'changed'
    elif change == 'mass':
        current['displacement_kg'] += 1.
    elif change == 'tolerance':
        previous['envelopes'][0]['tanks'][0]['oracle_upper_bound_excess_m4'] += .001
    elif change == 'value':
        previous['envelopes'][0]['tanks'][0]['direct_m4'] += .001
    elif change == 'nan':
        previous['envelopes'][0]['tanks'][0]['heel_rad'] = float('nan')
    elif change == 'incomplete':
        previous['envelopes'].pop()
    with pytest.raises(ValueError):
        tanks.reusable_envelopes(previous, current, ids, density, requested=4)
