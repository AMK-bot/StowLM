"""Report D49 finite optimistic holdout observations without global claims."""
import hashlib
import json
from pathlib import Path
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
bank_path = ROOT/'src/stowage/linear/margin_policy_data.json'
bank = json.loads(bank_path.read_text())
metrics = ('gm_m','trim_m','draught_m','draught_aft_m','draught_forward_m','sf_n','bm_nm')
maxima = {side: {metric: 0. for metric in metrics} for side in ('lower','upper')}
cases = []
for ship in ('feeder_500','feeder_1000','feeder_1700'):
    for level in ('light','medium','full'):
        path = HERE/'epoch-final'/ship/f'holdout-{level}.json'
        record = json.loads(path.read_text())
        signed = record['diagnostics']['signed_errors']
        observed = {side: {metric: float(np.max(signed[side+'_conservatism'][metric]))
                          for metric in metrics} for side in maxima}
        for side in maxima:
            for metric in metrics:
                maxima[side][metric] = max(maxima[side][metric], observed[side][metric])
        cases.append(dict(ship=ship,level=level,samples=record['samples'],seed=record['seed'],
            coverage=record['diagnostics']['coverage'],observed_optimistic_max=observed,
            source=str(path.relative_to(ROOT)),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
qualifications = [entry['necessary']['holdout_qualification'] for entry in bank['entries'].values()
                  if 'necessary' in entry]
qualified_max = {side: {metric: max(float(np.max(q['holdout_unsafe'][side+'_unsafe'][metric]))
    for q in qualifications) for metric in metrics if not (side == 'upper' and metric == 'gm_m')}
    for side in maxima}
report = dict(qualified_holdout_max_including_reference_transfer=qualified_max, samples=sum(case['samples'] for case in cases),cases=cases,
    residual_definition='lower=max(exact-linear,0); upper=max(linear-exact,0)',
    optimistic_max=maxima,
    bank_sha256=hashlib.sha256(bank_path.read_bytes()).hexdigest(),
    all_qualified=all(q['independent_holdout_passed'] for q in qualifications),
    qualified_holdouts=sum(q['holdout_samples'] for q in qualifications),
    boundary_list=[dict(response=identity, boundary=entry['necessary']['boundary_list']['qualification'],
                        interior=entry['necessary']['boundary_list']['interior_qualification'])
                   for identity,entry in bank['entries'].items() if 'necessary' in entry],
    conservative_preserved=True,
    warning='Finite empirical observations on576 holdouts in9 ship/load domains; no universal guarantee. LIST uses independently qualified boundary moments; full exact checker remains sole certifier.')
old = json.loads((HERE/'promotion-final/candidate-bank.json').read_text())
for identity, entry in old['entries'].items():
    current = bank['entries'][identity]
    for key in ('physical_unsafe','reference_transfer','boundary_list','holdout_qualification'):
        if key in entry and entry[key] != current.get(key):
            raise ValueError('Conservative numerical policy changed: '+key)
(HERE/'d49-optimistic-summary.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
print(json.dumps(dict(samples=report['samples'],optimistic_max=maxima,all_qualified=report['all_qualified'],conservative_preserved=True)))
