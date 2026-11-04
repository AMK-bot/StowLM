"""Summarize newly measured qualification; never relabel historical evidence."""
import hashlib
import json
from pathlib import Path
import numpy as np
from stowage.linear import dependencies

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EPOCH = HERE/'epoch-fingerprint'
BANK = ROOT/'src/stowage/linear/margin_policy_data.json'
METRICS = ('gm_m','trim_m','draught_m','draught_aft_m','draught_forward_m','sf_n','bm_nm')
SIDES = ('lower','upper')


def main():
    report = json.loads((HERE/'fingerprint-promotion/report.json').read_text())
    if report['passed'] is not True or report['applied'] is not True:
        raise ValueError('passing applied promotion required')
    bank = json.loads(BANK.read_text())
    active = {identity: bank['entries'][identity] for identity in report['response_identities']}
    if len(active) != 3:
        raise ValueError('three fresh promoted response identities required')
    observed = {direction: {side: {metric: 0. for metric in METRICS} for side in SIDES}
                for direction in ('conservative','necessary')}
    cases = []
    for ship in ('feeder_500','feeder_1000','feeder_1700'):
        for level in ('light','medium','full'):
            path = EPOCH/ship/f'holdout-{level}.json'
            case = json.loads(path.read_text())
            assert case['phase'] == 'holdout' and case['samples'] == 64
            signed = case['diagnostics']['signed_errors']
            for direction, suffix in (('conservative','unsafe'), ('necessary','conservatism')):
                for side in SIDES:
                    for metric in METRICS:
                        observed[direction][side][metric] = max(observed[direction][side][metric],
                            float(np.max(signed[side+'_'+suffix][metric])))
            cases.append(dict(ship=ship, level=level, samples=64, seed=case['seed'],
                coverage=case['diagnostics']['coverage'], source=str(path.relative_to(ROOT)),
                sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    qualifications = {}
    for direction in ('conservative','necessary'):
        records = [(entry if direction == 'conservative' else entry['necessary'])['holdout_qualification']
                   for entry in active.values()]
        excess = max(float(np.max(value)) for rec in records
                     for side in rec['max_excess'].values() for value in side.values())
        if excess > 0 or not all(r['independent_holdout_passed'] for r in records):
            raise ValueError('unsafe independent holdout breach')
        qualifications[direction] = dict(holdouts=sum(r['holdout_samples'] for r in records),
            independent_passed=True, max_excess=excess)
    boundaries = {}
    for direction in ('conservative','necessary'):
        boundaries[direction] = {}
        for identity, entry in active.items():
            boundary = (entry if direction == 'conservative' else entry['necessary'])['boundary_list']
            q = boundary['qualification']
            if not q['independent_holdout_passed'] or max(q['max_holdout_excess_kgm'].values()) > 0:
                raise ValueError('boundary holdout breach')
            boundaries[direction][identity] = q
    scope = json.loads((HERE/'fingerprint-scope-source.json').read_text())
    current = {kind: dependencies.dependency_fingerprint(kind) for kind in dependencies._PROFILES}
    if current != {kind: rec['fingerprint'] for kind,rec in scope['profiles'].items()}:
        raise ValueError('numerical fingerprint changed since fresh campaign admission')
    inclusion = json.loads((HERE/'fingerprint-holdout-audit/summary.json').read_text())
    warm = json.loads((HERE/'fingerprint-warm.execution.json').read_text())
    if not inclusion['passed'] or warm['exit_code'] != 0:
        raise ValueError('passing row inclusion and cache warm required')
    summary = dict(holdouts=576, hull_load_cases=9, qualifications=qualifications, boundary_qualifications=boundaries,
        observed_raw_holdout_residuals=observed, cases=cases,
        residual_definitions=dict(conservative='lower=max(linear-exact);upper=max(exact-linear)',
            necessary='lower=max(exact-linear);upper=max(linear-exact)'),
        unused='upper GM has no row; its reported residual is diagnostic only',
        row_inclusions=inclusion['totals']['all'], active_response_identities=sorted(active),
        bank_sha256=hashlib.sha256(BANK.read_bytes()).hexdigest(),
        numerical_scope_unchanged=True, fingerprints=current,
        cache_warm=warm, warning='Finite empirical evidence; full checker alone certifies plans.')
    (HERE/'fingerprint-report.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n')
    print(json.dumps(dict(holdouts=576,qualifications=qualifications,
        row_inclusions=summary['row_inclusions'],bank_sha256=summary['bank_sha256'],
        cache_warm_exit=warm['exit_code'],observed=observed)))


if __name__ == '__main__':
    main()
