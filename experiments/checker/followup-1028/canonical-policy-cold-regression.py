"""Exact saved 09 stability calls: empty vessel cache, unchanged default budget."""
import os
for variable in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS'):
    os.environ[variable]='1'
import json
import argparse
from pathlib import Path
import tempfile
import struct
from time import perf_counter

from stowage.io import load_instance
from stowage.model import BallastCondition,MovePlan
from stowage.moves import apply_plan
from stowage.checker import stability,stability_policy

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output-dir',type=Path,default=HERE)
options=parser.parse_args()
output=options.output_dir.resolve()
output.mkdir(parents=True,exist_ok=True)
os.environ.pop('STOWAGE_STABILITY_TIME_BUDGET_S',None)
cache=Path(tempfile.mkdtemp(prefix='canonical-fast-cold-',dir=output))
os.environ['STOWAGE_VESSEL_CACHE_DIR']=str(cache)
instance=load_instance(ROOT/'experiments/solver-diag-1028/inputs/09-random_yard-tiny-feeder_800.json')
plan=MovePlan(id=instance.id+'.background-control',instance_id=instance.id,call_index=instance.call_index,
    moves=[],ballast=BallastCondition(tank_masses_kg={},source='planner'))
ship=apply_plan(instance,plan).final_state.ship
rows=[]
for run in range(2):
    start=perf_counter()
    report=stability.check_stability(instance,ship,call_index=instance.call_index)
    rows.append(dict(run=run,seconds=perf_counter()-start,report=report.model_dump(mode='json')))
def bitwise_equal(left,right):
    if type(left) is not type(right):
        return False
    if isinstance(left,float):
        return struct.pack('>d',left)==struct.pack('>d',right)
    if isinstance(left,dict):
        return left.keys()==right.keys() and all(bitwise_equal(left[key],right[key]) for key in left)
    if isinstance(left,list):
        return len(left)==len(right) and all(bitwise_equal(a,b) for a,b in zip(left,right))
    return left==right
record=dict(input='experiments/solver-diag-1028/inputs/09-random_yard-tiny-feeder_800.json',
    default_budget_seconds=30.,vessel_cache=str(cache),vessel_cache_remains_empty=not any(cache.iterdir()),
    cold_warm_reports_bitwise_equal=bitwise_equal(rows[0]['report'],rows[1]['report']),runs=rows)
with tempfile.NamedTemporaryFile(mode='w',dir=output,prefix='canonical-cold-',suffix='.tmp',delete=False) as stream:
    stream.write(json.dumps(record,indent=2)+'\n')
    temporary=Path(stream.name)
temporary.replace(output/'canonical-policy-cold-regression.json')
print(json.dumps({k:v for k,v in record.items() if k!='runs'}))
for row in rows:
    print(json.dumps(dict(run=row['run'],seconds=row['seconds'],ran=row['report']['ran'],error=row['report']['error'])))
assert all(row['report']['ran'] and row['seconds']<30. for row in rows)
assert record['cold_warm_reports_bitwise_equal'] and record['vessel_cache_remains_empty']
