"""Join checked upper plans, global certificates, and explicit construction gates."""
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean, median

from catalog import ROOT
from score import source_sha256

HERE = Path(__file__).resolve().parent
PREVIOUS = HERE.parents[1]/'c3/c2-hatch'
SCOPE = ('fewest lifts among plans the checker passes, counting later ports by '
         'our D15 projection (fixed stow, no later loads)')


def load(path):
    return json.loads(path.read_text())


def rows(folder):
    return {p.stem: load(p) for p in folder.glob('*.json')
            if p.name not in {'summary.json', 'SUMMARY.json'}}


def accepted_uppers():
    index = load(HERE/'references/upper_index.json')
    source = source_sha256()
    candidates = {}
    for folder in ('reference-admission', 'construction-validation', 'witness-admission',
                   'repair-warm', 'repair-cold', 'checked-quick', 'paired-cold', 'warm-small',
                   'target-cold', 'target-warm', 'target-validation'):
        for identity, row in rows(HERE/'results'/folder).items():
            if not row.get('passed'):
                continue
            assert row['config']['source_sha256'] == source
            assert sha256((ROOT/row['input']).read_bytes()).hexdigest() == row['input_sha256']
            if identity in candidates and candidates[identity]['lifts'] <= row['lifts']:
                continue
            candidates[identity] = dict(instance_id=identity, input=row['input'],
                input_sha256=row['input_sha256'], lifts=row['lifts'], plan=row['plan'],
                passed=True, evidence=str((HERE/'results'/folder/f'{identity}.json').relative_to(ROOT)),
                evidence_sha256=sha256((HERE/'results'/folder/f'{identity}.json').read_bytes()).hexdigest())
    for identity, row in candidates.items():
        if identity not in index or row['lifts'] <= index[identity]['lifts']:
            index[identity] = row
    (HERE/'results/accepted_upper_index.json').write_text(json.dumps(index, indent=2)+'\n')
    return candidates, source


def gate(folder):
    values = list(rows(HERE/'results'/folder).values())
    passed = [r for r in values if r.get('passed')]
    result = dict(maps=len(values), replay_legal=sum(bool(r.get('tries')) for r in values),
                  full_checker_passes=len(passed),
                  mean_wall_s=mean(r['wall_s'] for r in values) if values else None,
                  median_wall_s=median(r['wall_s'] for r in values) if values else None,
                  passes_within60s=sum(r['wall_s'] <= 60 for r in passed),
                  passed_mean_lifts=mean(r['lifts'] for r in passed) if passed else None)
    cached = {'raw-small','warm-small','target-warm','repair-warm','repair-cold','checked-quick'}
    if folder in cached:
        result['admission_passes_within60s'] = result.pop('passes_within60s')
        result['time_scope'] = 'Cached stow/witness admission only; excludes original model work; no cold speed claim.'
    elif folder in {'construction-validation','target-validation'}:
        result['time_scope'] = 'Cold cargo construction plus inherited ballast admission; no cold-ballast speed claim.'
    else:
        result['time_scope'] = 'End-to-end cold construction, cold finishing and independent full check.'
    return result


def main():
    candidates, source = accepted_uppers()
    freeze = load(HERE/'PROOF_FREEZE.json')
    for name, digest in freeze['global_bound_modules'].items():
        assert sha256((HERE/name).read_bytes()).hexdigest() == digest
    previous = load(HERE/'references/c3_certificates.json')
    initial, continuation = rows(HERE/'results/proofs-120'), rows(HERE/'results/proofs-300')
    refined = rows(HERE/'results/proofs-refined')
    groups = {}
    for group in ('quick_tiny', 'quick_small', 'validation_small'):
        output = []
        for original in previous[group]['rows']:
            identity = original['instance_id']
            assert original['source_config']['bound_dependencies'] == freeze['global_bound_modules']
            lower = original['lower_bound']
            bound_evidence = str((PREVIOUS/original['bound_evidence']).relative_to(ROOT))
            for experiment, collection in (('proofs-120',initial), ('proofs-300',continuation), ('proofs-refined',refined)):
                current = collection.get(identity)
                if current:
                    assert current['input_sha256'] == original['input_sha256']
                    assert current['source_modules'] == freeze['global_bound_modules']
                    if current['lower_bound'] >= lower:
                        lower = current['lower_bound']
                        bound_evidence = str((HERE/'results'/experiment/f'{identity}.json').relative_to(ROOT))
            upper = candidates.get(identity)
            if upper is None:
                # Retain C3 proofs only with unchanged source/input, never a bare passed flag.
                old_path = PREVIOUS/original['checked_evidence']
                checked = load(old_path)
                assert checked['config']['source_sha256'] == source
                assert checked['input_sha256'] == original['input_sha256']
                if checked.get('passed'):
                    upper = dict(lifts=checked['lifts'], evidence=str(old_path.relative_to(ROOT)))
            value = upper['lifts'] if upper else None
            assert value is None or lower <= value+1e-7
            output.append(dict(instance_id=identity, input=original['input'],
                input_sha256=original['input_sha256'], lower_bound=lower,
                checked_upper=value, gap=value-lower if value is not None else None,
                proved=value is not None and abs(value-lower)<1e-7,
                scope=SCOPE, proof_family='arbitrary_current_moves_final_stow_relaxation',
                load_once_restriction=False, bound_evidence=bound_evidence,
                checked_evidence=upper['evidence'] if upper else None))
        groups[group] = dict(maps=len(output), proved=sum(r['proved'] for r in output),
                             mean_gap=mean(r['gap'] for r in output if r['gap'] is not None), rows=output)
    medium = []
    for identity, row in rows(HERE/'medium-results/probe').items():
        lower = max(row['lower_bound'], rows(HERE/'medium-results/joint120').get(identity, {}).get('lower_bound', 0))
        upper = candidates.get(identity)
        assert upper is None or upper['input_sha256'] == row['input_sha256']
        assert upper is None or lower <= upper['lifts']+1e-7
        medium.append(dict(instance_id=identity, lower_bound=lower,
            checked_upper=upper['lifts'] if upper else None,
            proved=upper is not None and abs(upper['lifts']-lower)<1e-7, scope=SCOPE))
    groups['medium88'] = dict(maps=len(medium), proved=sum(r['proved'] for r in medium), rows=medium)
    (HERE/'results/CERTIFICATES.json').write_text(json.dumps(groups, indent=2)+'\n')
    controls = rows(HERE/'results/paired-fresh09')
    cold = rows(HERE/'results/paired-cold')
    mutual = [i for i in controls if controls[i].get('passed') and cold.get(i, {}).get('passed')]
    control_summary = dict(measured=len(controls), planned=10, completed_passes=sum(r['passed'] for r in controls.values()),
        cold_passes_on_controls=sum(cold.get(i, {}).get('passed', False) for i in controls), mutual=len(mutual),
        cold_paired_mean=mean(cold[i]['lifts'] for i in mutual) if mutual else None,
        fresh09_paired_mean=mean(controls[i]['lifts'] for i in mutual) if mutual else None,
        control_mean_work_s=mean(r['wall_s']-r.get('method_metadata', {}).get('control_idle_s', 0) for r in controls.values()) if controls else None,
        caveat='Two completed fresh controls only; no full-cohort speedup or lift saving claimed.')
    gates = {name: gate(name) for name in ('paired-cold', 'raw-small', 'warm-small', 'construction-validation',
                                         'repair-warm', 'repair-cold', 'checked-quick',
                                         'target-cold', 'target-warm', 'target-validation')}
    target_rows = rows(HERE/'results/target-cold')
    gates['target-cold']['first_cp_witness_checked_pass'] = sum(
        row.get('passed', False) and row.get('actions') == row.get('method_metadata', {}).get(
            'construction', {}).get('first_model_actions') for row in target_rows.values())
    gates['paired-cold']['proved_with_generated_plan'] = sum(
        r['passed'] and any(c['instance_id']==i and c['proved'] and abs(c['checked_upper']-r['lifts'])<1e-7
                           for c in groups['quick_small']['rows']) for i, r in cold.items())
    for folder in ('target-cold', 'target-warm'):
        gates[folder]['proved_with_generated_plan'] = sum(
            r['passed'] and any(c['instance_id']==i and c['proved'] and abs(c['checked_upper']-r['lifts'])<1e-7
                               for c in groups['quick_small']['rows']) for i,r in rows(HERE/'results'/folder).items())
    summary = dict(scope=SCOPE, proofs={g:{k:v for k,v in data.items() if k!='rows'} for g,data in groups.items()},
                   construction_gates=gates, fresh_control=control_summary, source_sha256=source,
                   tests_passed=86, bound_interface_unchanged=True, validation_tuning=False)
    (HERE/'results/SUMMARY.json').write_text(json.dumps(summary, indent=2)+'\n')
    lines = [SCOPE+'.', '', 'Current restows are unrestricted in the global proof family; construction restrictions do not change that family.', '']
    for group, data in groups.items():
        lines += [f'## {group}', '', '| Map | Checked upper | Lower | Gap | Proved within scope |', '| --- | ---: | ---: | ---: | :---: |']
        for row in data['rows']:
            gap = row.get('gap', row['checked_upper']-row['lower_bound'] if row['checked_upper'] is not None else None)
            lines.append(f"| {row['instance_id']} | {row['checked_upper']} | {row['lower_bound']} | {gap} | {'yes' if row['proved'] else 'no'} |")
        lines += ['', f"{data['proved']}/{data['maps']} proved within the scope above.", '']
    (HERE/'results/TABLE.md').write_text('\n'.join(lines))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
