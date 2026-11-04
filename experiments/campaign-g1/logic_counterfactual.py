"""Run admission regressions against the former no-audit behavior, without editing source."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE=Path(__file__).resolve().parent


def main():
    spec=importlib.util.spec_from_file_location('logic_regression_counterfactual',HERE/'test_logic_admission.py')
    tests=importlib.util.module_from_spec(spec); spec.loader.exec_module(tests)
    actual=tests.module
    old=SimpleNamespace(audit_instance=lambda *a,**k:dict(passed=True),summarize_mix=lambda *a,**k:dict(accepted=1,rejected=1))
    tests.module=lambda name:old if name=='mix_audit' else actual(name)
    caught=[]
    for fault in ('generator','condition','campaign','mass','certificate','source','c2'):
        with pytest.MonkeyPatch.context() as patch:
            try: tests.test_post_regeneration_audit_rejects_stale_or_unwitnessed_instance(patch,fault)
            except BaseException as exc:
                if 'DID NOT RAISE' not in str(exc): raise
                caught.append(fault)
            else: raise AssertionError(f'regression did not reject old no-audit behavior: {fault}')
    try: tests.test_mix_audit_retains_allocated_rejected_missing_and_small_heldout_panel()
    except KeyError: caught.append('allocation/panel')
    else: raise AssertionError('population regression did not reject old absent population audit')
    print('counterfactual former no-audit behavior: ' + ', '.join(caught) + ' — 8 expected failures')
    return 0


if __name__=='__main__':raise SystemExit(main())
