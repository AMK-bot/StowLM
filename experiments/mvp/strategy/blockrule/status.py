"""Compact read-only campaign pulse for the owning executive."""
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent


def read(path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError,json.JSONDecodeError):
        return {}


def count(name):
    return sum(read(p).get('complete',False) for p in (HERE/'runs'/name/'maps').glob('*/summary.json'))


def passes(name):
    pairs=[r['selected']['compact'] for p in (HERE/'runs'/name/'maps').glob('*/summary.json')
           if (r:=read(p)).get('complete')]
    return f'{sum(p["default"]["passed"] for p in pairs)}:{sum(p["planner"]["passed"] for p in pairs)}'


state=read(HERE/'final-status.json')
changed=[]
for p in (HERE/'runs/final-medium-train/maps').glob('*/summary.json'):
    row=read(p)
    if row.get('capacity_epoch') and row.get('rule_mask_unchanged') is False and row.get('complete'):
        changed.append(row)
print(json.dumps(dict(phase=state.get('phase','unknown'),small=f'{count("small-train")}/30',
    medium=f'{count("medium-train")}/12',capacity=f'{len(changed)}/4',
    validation=f'{count("small-val")}/55',train_pass=passes('small-train'),
    medium_pass=passes('medium-train'),val_pass=passes('small-val'),
    error=state.get('error')),separators=(',',':')))
