"""Reference comparisons explicitly declare their isolated synthetic policy."""
from pathlib import Path
import runpy
from types import SimpleNamespace as NS

import numpy as np


def test_reference_crosscheck_declares_policy_without_mutating_production_instance(monkeypatch):
    from stowage.model.fixtures import build_instance
    import stowage.checker.stability as stability
    import stowage.checker.strength as strength
    instance=build_instance()
    original=dict(instance.rule_overrides)
    def inspect(reference,ship,**kwargs):
        assert reference.rule_overrides['iscode.loading.trim']['minimum'] == -10.
        assert reference.rule_overrides['iscode.loading.displacement'] == {'minimum':1.,'maximum':1.e9}
        return NS(ran=False,error='isolated test stops before physical metrics')
    monkeypatch.setattr(stability,'check_stability',inspect)
    monkeypatch.setattr(strength,'check_strength',lambda *a,**k:NS(ran=True,error=None))
    helper=runpy.run_path(str(Path(__file__).resolve().parents[2]/'experiments/linear-model/narrow.py'))
    model=NS(cell_lcg_m=np.zeros(1),cell_tcg_m=np.zeros(1))
    helper['public_crosscheck'](model,instance,(np.zeros(1),np.zeros(len(instance.vessel.tanks)),np.zeros(1),{}))
    assert instance.rule_overrides == original


def test_reference_crosscheck_uses_declared_d40_bounds_instead_of_broad_test_defaults(monkeypatch):
    from stowage.model.fixtures import build_instance
    import stowage.checker.stability as stability
    import stowage.checker.strength as strength
    instance=build_instance()
    instance=instance.model_copy(update={'vessel':instance.vessel.model_copy(update={'hull':NS(
        min_trim_m=-.7,max_trim_m=.3,min_displacement_kg=100.,max_displacement_kg=200.)})})
    def inspect(reference,ship,**kwargs):
        assert reference.rule_overrides['iscode.loading.trim'] == {'minimum':-.7,'maximum':.3}
        assert reference.rule_overrides['iscode.loading.displacement'] == {'minimum':100.,'maximum':200.}
        return NS(ran=False,error='isolated test stops before metrics')
    monkeypatch.setattr(stability,'check_stability',inspect)
    monkeypatch.setattr(strength,'check_strength',lambda *a,**k:NS(ran=True,error=None))
    helper=runpy.run_path(str(Path(__file__).resolve().parents[2]/'experiments/linear-model/narrow.py'))
    model=NS(cell_lcg_m=np.zeros(1),cell_tcg_m=np.zeros(1))
    helper['public_crosscheck'](model,instance,(np.zeros(1),np.zeros(len(instance.vessel.tanks)),np.zeros(1),{}))
