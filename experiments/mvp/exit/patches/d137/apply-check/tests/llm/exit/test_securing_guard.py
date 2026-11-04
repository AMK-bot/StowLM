from types import SimpleNamespace

import pytest

from llm.exit.securing_guard import (GuardedSecuringScreen, SecuringUnavailable,
                                    guarded_factory, securing_metrics)


def screen(**changes):
    result = SimpleNamespace(bounds_available=True, unavailable_reason=None,
                             metrics={'evaluations': 2, 'calls': 3})
    result.__dict__.update(changes)
    return result


def decoder(s):
    return SimpleNamespace(engine=SimpleNamespace(screen=s, securing_screen=s))


def test_missing_artifact_bounds_refused_on_initialization():
    with pytest.raises(SecuringUnavailable, match='kcs.fix'):
        GuardedSecuringScreen(screen(bounds_available=False, unavailable_reason='missing data/raw/kcs/kcs.fix'))


@pytest.mark.parametrize('name', ['fail_open:bounds', 'fail_open:exception:FileNotFoundError',
                                  'fail_open:geometry', 'unavailable:ValueError'])
def test_screen_fail_open_is_error_even_if_result_passes(name):
    source = screen()
    def check(*args):
        source.metrics[name] = 1
        return SimpleNamespace(passed=True)
    source.check_state = check
    guarded = GuardedSecuringScreen(source)
    with pytest.raises(SecuringUnavailable, match='fail-open'):
        guarded.check_state(None)


def test_legitimate_prefix_exemptions_recorded_not_fail_open():
    source = screen(metrics={'evaluations': 1, 'prefix_future_repair_exemptions': 4,
                             'prefix_single_chain_exemptions': 2})
    metrics = securing_metrics([decoder(source)], require_evaluations=True)
    assert metrics['evaluations'] == 1 and metrics['fail_open'] == metrics['unavailable'] == 0
    assert metrics['prefix_future_repair_exemptions'] == 4


def test_samples_sharing_screen_do_not_multiply_evaluation_count():
    source = screen()
    metrics = securing_metrics([decoder(source), decoder(source)], require_evaluations=True)
    assert metrics['screens'] == 1 and metrics['evaluations'] == 2


def test_zero_only_refused_when_aggregate_preflight_requires_evaluations():
    ds = [decoder(screen(metrics={'evaluations': 0}))]
    assert securing_metrics(ds)['evaluations'] == 0
    with pytest.raises(SecuringUnavailable, match='zero force evaluations'):
        securing_metrics(ds, require_evaluations=True)


def test_missing_layer_cannot_claim_active():
    with pytest.raises(SecuringUnavailable, match='missing'):
        securing_metrics([SimpleNamespace(engine=SimpleNamespace())])


def test_factory_keeps_shared_guard_across_independent_engines_and_copies():
    source = screen()
    ds = [decoder(source), decoder(source)]
    def production(*args): return ds
    production.name = 'production'
    factory = guarded_factory(production)
    assert factory.name == 'production'
    assert factory(None,None,None,None,2) is ds
    assert isinstance(ds[0].engine.screen, GuardedSecuringScreen)
    assert ds[0].engine.screen is ds[1].engine.screen
    assert ds[0].engine.securing_screen is ds[0].engine.screen


def test_factory_refuses_missing_production_layer():
    def production(*args): return [SimpleNamespace(engine=SimpleNamespace())]
    production.name = 'broken'
    with pytest.raises(SecuringUnavailable, match='missing'):
        guarded_factory(production)(None,None,None,None,1)
