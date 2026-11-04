"""Fresh-process cache dispatch is independent of the launch directory.

Small synthetic qualified tables exercise the real disk loaders, interpolation,
public stability check and loading margins without relying on warmed fleet
caches. Qualification numbers are test-only dispatch premises, as in the table
unit tests; this test does not certify the synthetic grids' physical accuracy.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from stowage.checker import stability_liquid, stability_liquid_tables, stability_tables


_PROBE = r'''
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np

from stowage.checker import stability_liquid_tables as tanks
from stowage.checker.stability_loading import CorrectionTank, LoadingCondition
from tests.checker.test_stability_tables import BoxGeometry
from stowage.vessel.tanks import TankGeometry
from stowage.vessel.hydrostatics import HullGeometry

root = Path(sys.argv[1])
def source_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, root/'src/stowage/checker'/filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

geometry = source_module('_cwd_geometry', 'stability_tables.py')
liquid = source_module('_cwd_liquid', 'stability_liquid.py')
expected = root/'experiments/cache/stability'
assert geometry.CACHE_DIR == liquid.CACHE_DIRECTORY == expected
# The tank-grid provider is unchanged; redirect it to the isolated test repo.
tanks.CACHE_DIRECTORY = expected
raw_tank = dict(id='cwd-tank', shape='hull_sections', x_m=[1., 3.],
    integration_weights_m=[2., 2.],
    sections_yz_m=[[[[0., 0.], [2., 0.], [2., 3.]]],
                   [[[0., 0.], [4., 0.], [4., 3.]]]],
    x_min_m=0., x_max_m=4., y_min_m=0., y_max_m=4., z_min_m=0., z_max_m=4.)
shape = TankGeometry.from_params(raw_tank)
direct = BoxGeometry()
params = {'hull': {'lightship_mass_kg': 1e6}, 'tanks': [raw_tank]}
volume = shape.capacity_m3*.5
row = shape.sounding(volume)
fsm = row.fsm_m4*1025.
mass = 1025.*80.*12.*5.
tank = CorrectionTank('cwd-tank', 1025., volume, fsm, shape)
condition = LoadingCondition(mass, 40., .02, 3., fsm/mass, fsm,
                             tanks=(tank,), actual_tanks=(tank,))
if sys.argv[2] == 'seed':
    table = geometry.load_table_geometry(direct, params, 1025., build=True,
        grid=dict(immersion_m=np.linspace(3., 7., 9),
                  trim_m=np.linspace(-2., 2., 5),
                  heel_rad=np.radians(np.linspace(0., 12., 7))))
    geometry.qualify_table(table, dict(max_l5_fraction=dict(gz=.1, area=.1, gm=.1),
                                      failure_count=0, sample_count=4))
    tanks.build(shape, fills=np.linspace(0., 1., 9),
                heels=np.linspace(-.3, .3, 9), trims=np.linspace(-.06, .06, 5))
    tanks.qualify(shape, dict(gz=.1, area=.1, gm=.1, samples=4))
    liquid.qualify_vessel(params, condition, dict(gz=.1, area=.1, gm=.1, samples=4))
else:
    from stowage.checker import stability, stability_tables, stability_liquid
    from stowage.model.fixtures import build_instance
    instance = build_instance()
    instance.vessel.hull.lpp_m = 80.
    instance.vessel.hull.beam_m = 12.
    instance.vessel.hull.depth_m = 12.
    instance.vessel.hull.max_draught_m = 7.
    instance.vessel.hull.max_list_rad = .1
    instance.port.max_draught_m = 7.
    instance.port.water_density_kg_m3 = 1025.
    direct.waterplane_normal = HullGeometry.waterplane_normal.__get__(direct)
    # Supply the analytic test hull/loading, then use production dispatch,
    # equilibrium, GM and margin calculation through the public checker.
    stability._canonical_fleet_policy = lambda vessel: None
    stability._context = lambda vessel: (direct, params)
    stability.loading_condition = lambda *args, **kwargs: condition
    stability_tables.cached_geometry = geometry.cached_geometry
    stability_liquid.attach_tables = liquid.attach_tables
    report = stability.check_stability(instance, instance.onboard, call_index=0,
                                       in_port=True)
    assert report.ran, report.error
    assert not report.violations, report.violations
    margins = report.margins
    hits = {name: report.metrics[name] for name in
            ('geometry_table_used', 'liquid_table_queries', 'liquid_direct_queries')}
    assert hits['geometry_table_used'] == 1.
    assert hits['liquid_table_queries'] > 0
    assert hits['liquid_direct_queries'] == 0
    print(json.dumps(dict(margins=margins, cache_hits=hits), sort_keys=True))
'''


def test_default_stability_cache_directories_are_repo_anchored():
    root = Path(__file__).resolve().parents[2]
    expected = root/'experiments/cache/stability'
    assert stability_tables.CACHE_DIR == expected
    assert stability_liquid.CACHE_DIRECTORY == expected
    assert stability_liquid_tables.CACHE_DIRECTORY == expected


def test_stability_table_check_from_another_cwd_preserves_margins_and_cache_hits(tmp_path):
    root = Path(__file__).resolve().parents[2]
    isolated = tmp_path/'repo'
    source = isolated/'src/stowage/checker'
    source.mkdir(parents=True)
    for module in (stability_tables, stability_liquid):
        shutil.copyfile(module.__file__, source/Path(module.__file__).name)
    unrelated = tmp_path/'elsewhere'
    unrelated.mkdir()
    env = dict(os.environ, OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1',
               PYTHONPATH=os.pathsep.join((str(root/'src'), str(root))))

    def probe(cwd, mode):
        result = subprocess.run([sys.executable, '-c', _PROBE, str(isolated), mode],
                                cwd=cwd, env=env, capture_output=True,
                                text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        return result.stdout

    probe(isolated, 'seed')
    original = probe(isolated, 'check')
    moved = probe(unrelated, 'check')
    assert original == moved  # Includes byte-identical margins and hit counts.
    assert json.loads(moved)['cache_hits']['liquid_table_queries'] > 0
