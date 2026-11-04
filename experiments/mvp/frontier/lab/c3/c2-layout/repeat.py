"""Refresh the securing reference from the best freshly checked Cycle 3 plan."""
from pathlib import Path
import importlib.util,json,sys

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('_c3_repeat_core',HERE/'method_lp.py')
base=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=base
spec.loader.exec_module(base)
old_reference=base.reference


def reference(instance,context):
    fallback=old_reference(instance,context)
    choices=[]
    for run in ('f60-1','train4-lp'):
        path=HERE/'results'/run/f'{instance.id}.json'
        if path.exists():
            row=json.loads(path.read_text())
            if row.get('passed'):choices.append((row['lifts'],row,path))
    if not choices:return fallback
    _,row,path=min(choices,key=lambda q:q[0])
    settings=fallback[2] if fallback else {}
    return row['assignment'],base.MovePlan.model_validate(row['plan']),settings,str(path)


base.reference=reference
propose=base.propose
finish=base.finish
