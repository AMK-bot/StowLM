"""D115 synthetic CPU regressions; no model, gold, validation or test inputs."""
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import ast

import pytest

from llm.securing import mask_filter
from llm.securing.mask_filter import SecuringEngine


def fixture_engine(ref='params/fleet/d115_synthetic.toml#hull_form'):
    vessel = SimpleNamespace(hull=SimpleNamespace(hull_offsets_ref=ref), cells=[])
    instance = SimpleNamespace(vessel=vessel, containers=[])
    return SimpleNamespace(instance=instance, actions=lambda: (), space=None)


def supplied_screen(engine):
    return SimpleNamespace(instance=engine.instance, bounds_available=True, metrics={})


def configure(monkeypatch, path, digest=None):
    # Patch only parameter lookup; the production SHA verifier reads real bytes.
    import stowage.vessel.fleet as fleet
    params = {'hull_form': {'family': 'stowlm_offsets_v1', 'offsets_path': str(path),
                           'offsets_sha256': digest or sha256(b'd115 public synthetic bytes').hexdigest()}}
    monkeypatch.setattr(fleet, 'load_ship_params', lambda _: params)
    try:
        from llm.securing import assets
    except ImportError:
        pass  # The same regression must execute against the pre-patch source.
    else:
        monkeypatch.setattr(assets, 'load_ship_params', lambda _: params)
    return params


@pytest.mark.parametrize('warm_supplied_screen', [False, True])
def test_missing_asset_raises_at_construction(monkeypatch, tmp_path, warm_supplied_screen):
    engine = fixture_engine()
    path = tmp_path/'missing.fix'
    configure(monkeypatch, path)
    # Simulate cached geometry: it must not excuse absent on-disk source bytes.
    from stowage.checker import visibility
    monkeypatch.setattr(visibility, '_geometry', lambda _: ('warm cached geometry', {}))
    screen = supplied_screen(engine) if warm_supplied_screen else None
    with pytest.raises(RuntimeError, match='Securing configuration error.*missing.fix'):
        SecuringEngine(engine, screen=screen, expander=SimpleNamespace())


@pytest.mark.parametrize('warm_supplied_screen', [False, True])
def test_checksum_mismatch_raises_at_construction(monkeypatch, tmp_path, warm_supplied_screen):
    engine = fixture_engine()
    path = tmp_path/'tampered.fix'
    path.write_bytes(b'wrong bytes')
    configure(monkeypatch, path)
    from stowage.checker import visibility
    monkeypatch.setattr(visibility, '_geometry', lambda _: ('warm cached geometry', {}))
    screen = supplied_screen(engine) if warm_supplied_screen else None
    with pytest.raises(RuntimeError, match='Securing configuration error.*checksum mismatch'):
        SecuringEngine(engine, screen=screen, expander=SimpleNamespace())


def test_valid_asset_normal_construction_preserves_supplied_screen(monkeypatch, tmp_path):
    engine = fixture_engine()
    path = tmp_path/'synthetic.fix'
    path.write_bytes(b'd115 public synthetic bytes')
    configure(monkeypatch, path)
    screen = supplied_screen(engine)
    result = SecuringEngine(engine, screen=screen, expander=SimpleNamespace())
    assert result.screen is screen
    assert result.inner is engine
    assert result.depth if hasattr(result, 'depth') else not result._actions


def test_unknown_instance_attributes_still_fail_open():
    engine = fixture_engine(ref=None)
    result = SecuringEngine(engine, expander=SimpleNamespace())
    assert not result.screen.bounds_available
    assert result.screen.check_state(SimpleNamespace()).passed
    assert result.screen.metrics['fail_open:bounds'] == 1


def test_counters_are_exact_and_shared_screens_are_counted_once():
    from llm.securing.metrics import securing_decode_metrics
    engine = fixture_engine(ref=None)
    screen = supplied_screen(engine)
    screen.metrics = dict(calls=9, evaluations=3, placement_calls=14,
                          **{'unavailable:ValueError': 1, 'fail_open:bounds': 2,
                             'fail_open:exception:ValueError': 3, 'cache_hits': 6})
    result = SecuringEngine(engine, screen=screen, expander=SimpleNamespace())
    decoders = [SimpleNamespace(engine=result), SimpleNamespace(engine=result)]
    assert securing_decode_metrics(decoders) == dict(securing_calls=9, evaluations=3,
        unavailable=1, fail_open=5, securing_screens=1, securing_active=True, securing_metrics_complete=True)
    assert result.securing_metrics()['fail_open'] == 5
    assert screen.metrics['calls'] == 9  # Snapshot performs no query/evaluation.
    assert securing_decode_metrics([]) == dict(securing_calls=0, evaluations=0,
        unavailable=0, fail_open=0, securing_screens=0, securing_active=False, securing_metrics_complete=True)




def test_remote_scope_deduplicates_and_missing_scope_never_reports_zero():
    from llm.securing.metrics import securing_decode_metrics
    record = dict(securing_calls=7, evaluations=2, unavailable=0, fail_open=0,
                  securing_active=True, metrics_scope='worker:screen')
    remote = SimpleNamespace(diagnostics=lambda: {'screens': {'securing_screen': record}})
    totals = securing_decode_metrics([remote, remote])
    assert totals['securing_calls'] == 7 and totals['evaluations'] == 2
    assert totals['securing_metrics_complete'] and totals['securing_active']
    missing = SimpleNamespace(diagnostics=lambda: {})
    totals = securing_decode_metrics([missing])
    assert not totals['securing_metrics_complete'] and not totals['securing_active']
    assert all(totals[k] is None for k in ('securing_calls', 'evaluations', 'unavailable', 'fail_open'))




def test_declared_missing_checksum_is_configuration_error(monkeypatch, tmp_path):
    engine=fixture_engine()
    path=tmp_path/'synthetic.fix';path.write_bytes(b'd115 public synthetic bytes')
    params=configure(monkeypatch,path)
    params['hull_form'].pop('offsets_sha256')
    with pytest.raises(RuntimeError,match='Securing configuration error.*offsets_sha256'):
        SecuringEngine(engine,screen=supplied_screen(engine),expander=SimpleNamespace())


@pytest.mark.parametrize('with_snapshot', [False, True])
def test_opaque_non_none_engine_uses_transport_or_reports_unknown(with_snapshot):
    from llm.securing.metrics import securing_decode_metrics
    record = dict(securing_calls=4, evaluations=2, unavailable=0, fail_open=1,
                  securing_active=True, metrics_scope='remote:opaque')
    diagnostics = (lambda: {'screens': {'securing_screen': record}}) if with_snapshot else (lambda: {})
    decoder = SimpleNamespace(engine=object(), diagnostics=diagnostics)
    result = securing_decode_metrics([decoder,decoder])
    assert result['securing_metrics_complete'] is with_snapshot
    assert result['securing_active'] is with_snapshot
    assert result['securing_calls'] == (4 if with_snapshot else None)


def test_explicit_no_securing_diagnostic_snapshot_reports_off():
    from llm.securing.metrics import securing_decode_metrics
    decoder=SimpleNamespace(engine=object(),diagnostics=lambda: {'screens': {}})
    result=securing_decode_metrics([decoder])
    assert result['securing_metrics_complete'] and not result['securing_active']
    assert all(result[k]==0 for k in ('securing_calls','evaluations','unavailable','fail_open'))


def test_stale_shared_scope_snapshots_use_counter_max_in_both_orders():
    from llm.securing.metrics import securing_decode_metrics
    def remote(calls,evaluations,unavailable,fail_open,active=True,scope='same-worker:screen'):
        record=dict(securing_calls=calls,evaluations=evaluations,unavailable=unavailable,
                    fail_open=fail_open,securing_active=active,metrics_scope=scope)
        return SimpleNamespace(engine=object(),diagnostics=lambda:{'screens':{'securing_screen':record}})
    early=remote(9,3,1,2)
    late=remote(13,5,1,4)
    other=remote(2,1,0,0,scope='different-worker:screen')
    for order in ([early,late],[late,early]):
        values=securing_decode_metrics(order)
        assert values['securing_calls']==13 and values['evaluations']==5
        assert values['unavailable']==1 and values['fail_open']==4
        assert values['securing_screens']==1 and values['securing_active']
        combined=securing_decode_metrics([*order,other])
        assert combined['securing_calls']==15 and combined['evaluations']==6
        assert combined['unavailable']==1 and combined['fail_open']==4
        assert combined['securing_screens']==2
    assert not securing_decode_metrics([late,remote(9,3,1,2,active=False)])['securing_active']
