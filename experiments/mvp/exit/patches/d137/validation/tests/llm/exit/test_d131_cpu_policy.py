"""D131 measured real-load CPU policy, with unchanged fleet capacity limits."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def policy():
    root=Path(__file__).resolve().parents[3]
    source=root/'experiments/mvp/exit/patches/d129/resources.py'
    spec=importlib.util.spec_from_file_location('_d131_resources',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_d131_accepts_measured_load15_with_four_cpu_cores(policy):
    assert policy._load_allowed('cpu_reverification_d131',False,4,{'load_1m':15})
    assert not policy._load_allowed('strict',False,4,{'load_1m':15})
    assert policy._load_allowed('cpu_reverification_d131',False,4,{'load_1m':18})
    assert not policy._load_allowed('cpu_reverification_d131',False,4,{'load_1m':18.01})


def test_d131_never_weakens_ledger_or_memory_limits(policy):
    assert policy._capacity_allowed(4,8,False,(12,40,False),{'memory_free_percent':50})
    assert not policy._capacity_allowed(4,8,False,(13,40,False),{'memory_free_percent':50})
    assert not policy._capacity_allowed(4,8,False,(12,105,False),{'memory_free_percent':50})
    assert not policy._capacity_allowed(4,8,False,(12,40,False),{'memory_free_percent':10})


@pytest.mark.parametrize('gpu,cores',[(True,4),(False,5),(False,0)])
def test_d131_policy_is_narrow_cpu_only(policy,gpu,cores):
    with pytest.raises(ValueError,match='D131 policy'):
        policy._load_allowed('cpu_reverification_d131',gpu,cores,{'load_1m':10})
