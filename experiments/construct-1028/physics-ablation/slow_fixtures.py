"""Task-local cache hook for the two specifically authorized ballast fixtures."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from cache_runtime import redirect_cache
redirect_cache(Path(__file__).resolve().parent/'slow-fixture-runtime')
if __name__ == '__main__':
    if '--inspect' in sys.argv:
        import importlib.util,json
        spec=importlib.util.spec_from_file_location('ballast_fixture_tests','tests/solver/test_ballast_integration.py')
        tests=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tests)
        from stowage.checker.visibility import _loading_condition
        instance=tests._fleet_context('feeder_500')
        hull=instance.vessel.hull
        out=[]
        for height in (4.5,5.0,5.3,6.0):
            tests._balance_fixed_load(instance,draught=5.5,cargo_z=height)
            condition=_loading_condition(instance,instance.onboard)
            out.append(dict(cargo_z=height,design_gm=hull.max_gm_lashing_m,
                gm=condition.gm_m,draft_aft=condition.t_aft_m,draft_fore=condition.t_fwd_m,
                kg=condition.kg_m))
        print(json.dumps(out),flush=True)
    else:
        import pytest
        raise SystemExit(pytest.main(['-o','addopts=--import-mode=importlib','-q','-p','no:cacheprovider',
            '-m','slow','--basetemp',str(Path(__file__).resolve().parent/'slow-fixture-runtime/pytest-tmp'),
            'tests/solver/test_ballast_integration.py::test_fleet_legal_stow_exact_parts_and_call_timing[feeder_500]',
            'tests/solver/test_ballast_integration.py::test_ballast_corrects_exact_trim_for_a_legal_stow']))
