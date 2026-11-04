"""Trace real saved stability inputs without changing a calculation or budget."""
import argparse
import json
from pathlib import Path
from time import perf_counter

from stowage.checker import stability, stability_tables
from stowage.io import load_instance
from stowage.model import BallastCondition, MovePlan
from stowage.moves import apply_plan

parser = argparse.ArgumentParser()
parser.add_argument('input', type=Path)
parser.add_argument('output', type=Path)
parser.add_argument('--runs', type=int, default=2)
args = parser.parse_args()
instance = load_instance(args.input)
plan = MovePlan(id=instance.id+'.background-control', instance_id=instance.id,
    call_index=instance.call_index, moves=[],
    ballast=BallastCondition(tank_masses_kg={}, source='planner'))
ship = apply_plan(instance, plan).final_state.ship
events = []
original_check = stability._check_stability
original_cached = stability_tables.cached_geometry
original_near = stability._near_table_limit
original_condition = stability_tables.TableGeometry._condition


def traced_check(*a, **kw):
    events.append(dict(event='check', force_direct=kw.get('_force_direct', False)))
    return original_check(*a, **kw)


def traced_cached(*a, **kw):
    table = original_cached(*a, **kw)
    events.append(dict(event='qualified_table', found=table is not None,
        path=str(table.cache_path) if table is not None else None))
    return table


def traced_near(metrics, margins):
    result = original_near(metrics, margins)
    events.append(dict(event='near_limit', result=result,
        triggers=[key for key, value in margins.items()
                  if original_near(metrics, {key: value})]))
    return result


def traced_condition(self, *a, **kw):
    try:
        return original_condition(self, *a, **kw)
    except Exception as exc:
        events.append(dict(event='table_condition_error', error=repr(exc),
            mass=float(a[0]), cg=list(a[1]), density=float(a[2]),
            heel=None if a[3] is None else float(a[3])))
        raise


stability._check_stability = traced_check
stability_tables.cached_geometry = traced_cached
stability._near_table_limit = traced_near
stability_tables.TableGeometry._condition = traced_condition
rows = []
for run in range(args.runs):
    events.clear()
    started = perf_counter()
    report = stability.check_stability(instance, ship, call_index=instance.call_index)
    rows.append(dict(run=run, wall_seconds=perf_counter()-started, events=list(events),
                     report=report.model_dump(mode='json')))
    args.output.write_text(json.dumps(rows, indent=2)+'\n')
    print(json.dumps(dict(run=run, wall_seconds=rows[-1]['wall_seconds'],
        ran=report.ran, error=report.error, events=events)), flush=True)
