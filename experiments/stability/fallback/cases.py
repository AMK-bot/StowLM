"""Reconstruct the public synthetic convergence regression conditions.

The historical JSON uses rounded ballast masses and obsolete fleet parameters;
these builders deliberately apply the same fill/placement policy to CURRENT
production vessels. They are regression inputs, not historical certificates.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
REVIEW = ROOT / 'experiments/physics-review/convergence'


def _harness():
    # The review harness registers passive curve spies. It never alters physics.
    sys.path.insert(0, str(REVIEW))
    sys.path.insert(0, str(REVIEW/'post_d30'))
    import harness
    import harness2
    return harness, harness2


def build_case(case):
    """Return instance and declared departure plan, without any checker call."""
    from stowage.model import BallastCondition, MovePlan
    from stowage.vessel.fleet import load_vessel, load_ship_params
    h1, h2 = _harness()
    if case == 'listed_500_c1':
        name = 'feeder_500'
        vessel = load_vessel(name, with_hull=True)
        instance, info = h1.build_condition(name,vessel,load_ship_params(name),'C1')
        masses = {}
    else:
        options = {'peak_500_c2':('feeder_500',.5),
                   'peak_1700_c2':('feeder_1700',.75)}
        name, fill = options[case]
        vessel = load_vessel(name, with_hull=True)
        masses = {tank.id: tank.capacity_m3*tank.content_density_kg_m3*
                  (fill if tank.id.startswith('FPT') else
                   float(tank.id.startswith(('DB-','HEEL'))))
                  for tank in vessel.tanks if tank.adjustable}
        instance, info = h2.build(name,vessel,load_ship_params(name),'C2',sum(masses.values()))
    plan = MovePlan(id=instance.id+'.fallback_regression',instance_id=instance.id,
        call_index=instance.call_index,ballast=BallastCondition(source='planner',tank_masses_kg=masses))
    return instance, plan, info


CASES = ('listed_500_c1','peak_500_c2','peak_1700_c2')
