"""Safely expose first cache miss; NEVER run the expensive direct reference.

Run single-threaded with warmed caches. A control-flow BaseException stops before
any restart or direct max-transfer integration. This is a diagnostic only, not a
checker verdict. Current production cases may differ from historical geometry.
"""
from __future__ import annotations
import argparse
import json
import traceback
from pathlib import Path
from time import perf_counter
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from cases import CASES, build_case


def probe(case):
    from stowage.checker import stability, stability_tank_transfer, stability_tables
    from stowage.checker.full import check
    events=[]
    class StopBeforeDirect(BaseException):
        pass
    original = stability._check_stability
    original_basis = stability_tables._basis
    original_transfer = stability_tank_transfer.max_transfer
    original_near = stability._near_table_limit
    original_equilibrium = stability_tables.TableGeometry.equilibrium
    def intercept(*args,**kwargs):
        if kwargs.get('_force_direct'):
            active = sys.exc_info()[1]
            events.append(dict(reason='whole-check direct restart',
                exception=str(active) if active else None,
                stack=traceback.format_stack(limit=8)))
            raise StopBeforeDirect()
        return original(*args,**kwargs)
    def forbid_transfer(*args,**kwargs):
        events.append(dict(reason='direct tank envelope',angle=args[1] if len(args)>1 else None,
            stack=traceback.format_stack(limit=8)))
        raise StopBeforeDirect()
    def near(metrics,margins):
        result=original_near(metrics,margins)
        if result:
            events.append(dict(reason="criterion interpolation guard",metrics=metrics,margins=margins))
        return result
    def equilibrium(self,*args,**kwargs):
        value=original_equilibrium(self,*args,**kwargs)
        events.append(dict(reason="table equilibrium iteration",mass=args[0],cg=list(args[1:4]),
            heel_rad=value.heel_rad,trim_m=value.trim_m,residual=value.residual_norm))
        return value
    def basis(axis,value):
        if value < axis[0]-1e-10 or value > axis[-1]+1e-10:
            events.append(dict(reason='basis domain miss', value=float(value),
                minimum=float(axis[0]),maximum=float(axis[-1])))
        return original_basis(axis,value)
    instance, plan, info = build_case(case)
    stability._check_stability=intercept
    stability_tank_transfer.max_transfer=forbid_transfer
    stability_tables._basis=basis
    stability._near_table_limit=near
    stability_tables.TableGeometry.equilibrium=equilibrium
    started=perf_counter()
    try:
        report=check(instance,plan)
        outcome=dict(passed=report.passed,metrics=report.parts['stability'].metrics,
                     error=report.parts['stability'].error)
    except StopBeforeDirect:
        outcome=dict(stopped_before_direct=True)
    finally:
        stability._check_stability=original
        stability_tank_transfer.max_transfer=original_transfer
        stability_tables._basis=original_basis
        stability._near_table_limit=original_near
        stability_tables.TableGeometry.equilibrium=original_equilibrium
    return dict(case=case,seconds=perf_counter()-started,construction=info,events=events,**outcome)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case',choices=CASES,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    result=probe(args.case)
    args.output.write_text(json.dumps(result,indent=2,default=str)+'\n')
    print(json.dumps({key:value for key,value in result.items() if key!='construction'},indent=2))
