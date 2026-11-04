"""Current N1 qualification and row-inclusion report from pinned observations."""
import hashlib
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
METRICS = ('gm_m','trim_m','draught_m','draught_aft_m','draught_forward_m','sf_n','bm_nm')


def read(path):
    return json.loads(path.read_text())


def main():
    promotion = read(HERE/'claude-promotion/report.json')
    bank_path = ROOT/'src/stowage/linear/margin_policy_data.json'
    bank = read(bank_path)
    baseline = read(HERE/'claude-baseline-bank.json')
    if not promotion['passed'] or not promotion['applied']:
        raise ValueError('passing applied current promotion required')
    entries = {key:bank['entries'][key] for key in promotion['response_identities']}
    conservative_fields = ('physical_unsafe','reference_transfer','holdout_qualification')
    conservative_unchanged = all(entries[key][field] == baseline['entries'][key][field]
        for key in entries for field in conservative_fields)
    boundary_fields = ('limit_rad','gm_min_m','measured_unsafe_kgm',
                       'reference_transfer_kgm','roundoff_kgm','physical_reference_transfer_kgm')
    conservative_unchanged &= all(entries[key]['boundary_list'][field] ==
        baseline['entries'][key]['boundary_list'][field]
        for key in entries for field in boundary_fields)
    if not conservative_unchanged:
        raise ValueError('ordinary conservative numerical content unexpectedly changed')
    hulls = {}
    for key, entry in entries.items():
        record = entry['necessary']
        physical, transfer = record['physical_optimistic'], record['reference_transfer']
        raw = read(HERE/'epoch-claude'/entry['vessel_id']/'raw.json') if 'vessel_id' in entry else None
        # The raw source is explicitly pinned in the parent evidence.
        if raw is None:
            sources = [ROOT/pin['path'] for pin in entry['evidence'] if pin['path'].endswith('/raw.json')]
            if len(sources) != 1:
                raise ValueError('ambiguous raw observation pin')
            raw = read(sources[0])
        from stowage.linear.margin_policy import optimistic_envelope
        family = optimistic_envelope(raw['diagnostics']['signed_errors'],len(entry['station_x_m']))
        limits = {}
        for side in ('lower_unsafe','upper_unsafe'):
            limits[side] = {metric:dict(
                observed_training=float(np.max(np.maximum(physical[side][metric],family[side][metric]))),
                reference_transfer=float(np.max(transfer[side][metric])),
                ordinary_holdout_observed=(float(np.max(record['holdout_qualification'][
                    'holdout_unsafe'][side][metric])) if metric in record[
                        'holdout_qualification']['holdout_unsafe'][side] else None),
                edge_holdout_observed=(float(np.max(record['edge_holdout_qualification'][
                    'holdout_unsafe'][side][metric])) if metric in record[
                        'edge_holdout_qualification']['holdout_unsafe'][side] else None),
                reserve=float(np.max(1.1*(np.maximum(physical[side][metric],family[side][metric])+
                                             np.asarray(transfer[side][metric])))))
                for metric in METRICS}
        for field in ('holdout_qualification','edge_holdout_qualification'):
            gate = record[field]
            if not gate['independent_holdout_passed'] or any(np.max(value)>0
                for row in gate['max_excess'].values() for value in row.values()):
                raise ValueError('necessary holdout reserve breach')
        hulls[raw['ship']] = dict(response_identity=key, optimistic_bounds=limits,
            ordinary_holdout=record['holdout_qualification'],
            edge_holdout=record['edge_holdout_qualification'],
            edge_coverage=record['edge_domain_coverage'],
            list_qualification=record['boundary_list']['qualification'],
            list_interior=record['boundary_list']['interior_qualification'])
    ordinary = read(HERE/'claude-holdout-audit/summary.json')
    edges = read(HERE/'claude-final-candidate-row-audit.json')
    if edges['bank']['sha256'] != hashlib.sha256(bank_path.read_bytes()).hexdigest():
        raise ValueError('public row audit candidate differs from promoted bank bytes')
    warm = read(HERE/'claude-warm.execution.json')
    residual_gate = read(HERE/'claude-final-residual-gate.json')
    if not ordinary['passed'] or not edges['passed'] or not residual_gate['passed'] or warm['exit_code']:
        raise ValueError('public row inclusions and cache warm must pass')
    from stowage.linear.dependencies import dependency_fingerprint
    current = {key:dependency_fingerprint(key) for key in baseline['fingerprints']}
    if current != baseline['fingerprints']:
        raise ValueError('numerical fingerprint changed since campaign admission')
    observed = {direction:{side:{metric:0. for metric in METRICS}
                for side in ('lower','upper')} for direction in ('conservative','necessary')}
    for ship in ('feeder_500','feeder_1000','feeder_1700'):
        for level in ('light','medium','full'):
            record = read(HERE/'epoch-claude'/ship/f'holdout-{level}.json')
            for direction,suffix in (('conservative','unsafe'),('necessary','conservatism')):
                for side in observed[direction]:
                    for metric in METRICS:
                        observed[direction][side][metric] = max(observed[direction][side][metric],
                            float(np.max(record['diagnostics']['signed_errors'][side+'_'+suffix][metric])))
        record = read(HERE/'epoch-claude-v3'/ship/'necessary-edges-holdout.json')
        for state in record['list_row_samples']:
            for metric in METRICS:
                if metric in ('draught_aft_m','draught_forward_m'):
                    sign = 1 if metric == 'draught_aft_m' else -1
                    residual = state['affine_draught_m']-state['exact_draught_m']+sign*.5*(
                        state['affine_trim_m']-state['exact_trim_m'])
                else:
                    residual = np.asarray(state['affine_'+metric])-state['exact_'+metric]
                for side,sign in (('lower',-1),('upper',1)):
                    observed['necessary'][side][metric] = max(observed['necessary'][side][metric],
                                                            float(np.max(np.maximum(sign*residual,0.))))
    report = dict(passed=True,conservative_numerics_unchanged=conservative_unchanged,
        bank_sha256=hashlib.sha256(bank_path.read_bytes()).hexdigest(), hulls=hulls,
        ordinary_row_inclusion=ordinary['totals']['all'],edge_rows=dict(
            samples=edges['samples'],holdouts=edges['holdout_samples'],passed=edges['passed'],
            strict_training_boundary_rejections=sum(len(case['production_rejected_boundary_roundoff'])
                                                    for case in edges['cases'])),
        all_observation_residual_gate=residual_gate,
        reproduction=read(HERE/'claude-reproduction-report.json'),
        numerical_fingerprints=current,numerical_scope_unchanged=True,
        observed_raw_holdout_residuals=observed,
        observed_holdout_scopes=dict(conservative=576,necessary=576+sum(
            hull['edge_holdout']['holdout_samples'] for hull in hulls.values())),
        unused_upper_gm='Diagnostic only; no upper GM constraint exists',
        margin='1.1 times measured training maximum plus reference transfer, per side and station',
        warning='Finite empirical coverage only; exact checker remains the plan certifier.',
        cache_warm=warm)
    (HERE/'claude-report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps({key:report[key] for key in ('passed','bank_sha256','conservative_numerics_unchanged',
                                               'ordinary_row_inclusion','edge_rows')}))


if __name__ == '__main__':
    main()
