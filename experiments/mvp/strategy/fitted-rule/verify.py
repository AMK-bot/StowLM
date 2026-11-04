"""Audit frozen bytes, recorded rule equivalence, admission and checker artifacts."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def audit(final=False):
    errors = []
    def require(ok, message):
        if not ok:
            errors.append(message)
    freeze = json.loads((HERE/'FREEZE.json').read_text())
    for name, digest in freeze['sha256'].items():
        require(sha(HERE/'frozen-source'/name) == digest, 'frozen bytes changed: '+name)
    selected = freeze['sha256']['fitted.json']
    require(selected == 'da15edecf1061c2e2014317ccaf53190f6c2997b5f225f8ca39bd5174a6559d6', 'wrong selected fit')
    original = {}
    for mode, filename in [('greedy', 'rule-fitgreedy.jsonl'), ('stochastic', 'rule-fitT1.jsonl')]:
        original[mode] = {(r['id'], r['seed']): r for r in
                          (json.loads(line) for line in (HERE/'frozen-source'/filename).read_text().splitlines())}
    compared = dict(greedy=0, stochastic=0)
    for mode, paths in [('greedy', (HERE/'small/greedy').glob('*.json')),
                        ('stochastic', (HERE/'small/samples').glob('*/*.json'))]:
        for p in paths:
            row = json.loads(p.read_text())
            meta = row['metadata']
            old = original[mode].get((row['instance_id'], meta['seed']))
            if old is None:
                continue
            require(meta['status'] == old['status'], 'status mismatch: '+str(p))
            require(meta['rewinds'] == old['rew'], 'rewind mismatch: '+str(p))
            prep = row.get('evaluation') if mode == 'greedy' else row.get('prepared')
            if meta['status'] == 'complete' and prep:
                require(prep['gate_clear'] == old['clear'], 'gate mismatch: '+str(p))
                require(prep['cost'] == old['cost'], 'cost mismatch: '+str(p))
            compared[mode] += 1
    spec = importlib.util.spec_from_file_location('fitted_verify_admission', HERE.parent/'k1b/skill-check/admission.py')
    admission = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(admission)
    # Verify the live D206 module's boundary semantics without touching held-out inputs.
    for payload, admitted in [({'t':1000,'load1':45,'mask':255},True),
                              ({'t':1000,'load1':45.01,'mask':255},False),
                              ({'t':1000,'load1':45,'mask':127},False),
                              ({'t':900,'load1':45,'mask':255},False),
                              ({'t':1021,'load1':45,'mask':255},False)]:
        require(admission._assess(payload,1020,'synthetic')['admitted'] == admitted, 'D206 boundary regression')
    evaluations = passed = admission_checks = raw_semantic_checks = 0
    epochs = set()
    raw_digests = {}
    for base in (HERE/'small/greedy-evaluations', HERE/'small/sample-evaluations', HERE/'scale/maps'):
        for p in base.rglob('result.json'):
            row = json.loads(p.read_text())
            evaluations += 1
            bind = row['binding']
            epochs.add(bind['outcome_epoch'])
            require(bind['weights_sha256'] == selected, 'weight binding changed: '+str(p))
            require(bind['finisher_s'] == 600, 'wrong finisher budget: '+str(p))
            require(sha(ROOT/bind['input']) == bind['input_sha256'], 'input bytes changed: '+str(p))
            for name, digest in bind['code'].items():
                require(sha(ROOT/name) == digest, 'evaluation dependency bytes changed: '+name)
            for name,digest in row.get('artifacts', {}).items():
                require(sha(p.parent/name) == digest, 'checker artifact changed: '+str(p.parent/name))
            if row['complete']:
                raw = json.loads((p.parent/'raw-plan.json').read_text())
                if base.name == 'greedy-evaluations':
                    candidate = HERE/'small/greedy-candidates'/f'{row["instance_id"]}.json'
                    originals = [json.loads(candidate.read_text())['plan']]
                elif base.name == 'sample-evaluations':
                    originals = [s['plan'] for s in
                                 (json.loads(q.read_text()) for q in (HERE/'small/samples'/row['instance_id']).glob('sample-*.json'))
                                 if s.get('signature') == p.parent.name]
                    chosen = json.loads((p.parent/'chosen-plan.json').read_text())
                    chosen_signature = hashlib.sha256(json.dumps(chosen['moves'],sort_keys=True).encode()).hexdigest()
                    require(chosen_signature==p.parent.name, 'evaluated compacted moves differ from ranked signature: '+str(p))
                else:
                    originals = [json.loads((p.parent.parent/'decode.json').read_text())['plan']]
                require(any(raw == original for original in originals), 'raw plan differs semantically from decoded candidate: '+str(p))
                raw_semantic_checks += 1
                raw_digests[str((p.parent/'raw-plan.json').relative_to(HERE))] = hashlib.sha256(
                    json.dumps(raw,sort_keys=True,separators=(',',':')).encode()).hexdigest()
                a = row['admission']
                assessed = admission._assess(a['source_payload'], a['read_at'], a['source'])
                require(a['admitted'] is True and assessed['admitted'] is True, 'invalid recorded D206 admission: '+str(p))
                admission_checks += 1
            if row['passed']:
                passed += 1
                require(row.get('mask_replay') is True, 'passed without strict mask replay: '+str(p))
                report = json.loads((p.parent/'report.json').read_text())
                require(report['passed'] is True, 'passed without full checker artifact: '+str(p))
                require(report['count']['weighted_nonproductive'] == row['moves'], 'move count differs from checker: '+str(p))
    curve_points = 0
    for p in (HERE/'small/curves').glob('*.json'):
        curve = json.loads(p.read_text())
        samples = [json.loads(q.read_text()) for q in (HERE/'small/samples'/curve['instance_id']).glob('sample-*.json')]
        require(len(samples) == 16, 'curve lacks sixteen genuine samples: '+str(p))
        for k in (1,2,4,8,16):
            seen, ranked = set(), []
            for s in sorted((s for s in samples if s['sample'] < k and s['plan'] is not None),
                            key=lambda s:(s['prepared']['cost'],s['sample'])):
                if s['signature'] not in seen:
                    ranked.append(s)
                    seen.add(s['signature'])
            point = curve['points'][str(k)]
            require(point['eligible']==bool(ranked), 'curve eligibility differs from ranked samples: '+str(p))
            require(point['raw_best']==(ranked[0]['prepared']['cost'] if ranked else None), 'curve raw best differs from ranking: '+str(p))
            expected_moves = expected_winner = None
            tried = 0
            for s in ranked:
                verdict = HERE/'small/sample-evaluations'/curve['instance_id']/s['signature']/'result.json'
                require(verdict.exists(), 'ranked curve trial lacks verdict: '+str(verdict))
                if not verdict.exists():
                    break
                row = json.loads(verdict.read_text())
                tried += 1
                if row['passed']:
                    expected_moves, expected_winner = row['moves'], s['sample']
                    break
            require(point['moves'] == expected_moves and point['winner_sample'] == expected_winner,
                    'curve winner differs from ranked first pass: '+str(p)+' k='+str(k))
            require(point['judged'] == tried, 'curve trial count mismatch: '+str(p)+' k='+str(k))
            curve_points += 1
    current_epoch = None
    if final:
        greedy = [json.loads(p.read_text()) for p in (HERE/'small/greedy').glob('*.json')]
        samples = [json.loads(p.read_text()) for p in (HERE/'small/samples').glob('*/sample-*.json')]
        curves = list((HERE/'small/curves').glob('*.json'))
        require(len(greedy)==55 and len({r['instance_id'] for r in greedy})==55, 'final greedy coverage is not55')
        expected_ids = {r['instance_id'] for r in greedy}
        require(len(samples)==880 and {(r['instance_id'],r['sample']) for r in samples} ==
                {(iid,seed) for iid in expected_ids for seed in range(16)}, 'final stochastic coverage is not55x16')
        for row in samples:
            meta = row['metadata']
            require(meta['seed']==row['sample'] and meta['greedy'] is False and meta['temperature']==1,
                    'wrong stochastic seed or regime: '+row['instance_id'])
            require(meta['weights_sha256']==selected, 'stochastic weights changed: '+row['instance_id'])
            if row['plan'] is not None:
                require(meta['status']=='complete' and 'prepared' in row and 'signature' in row, 'unprepared complete stochastic sample')
                signature = hashlib.sha256(json.dumps(row['chosen_plan']['moves'],sort_keys=True).encode()).hexdigest()
                require(signature==row['signature'], 'sample chosen-move signature mismatch')
            else:
                require(meta['status']=='dead_end', 'null stochastic plan has wrong status')
        require(len(curves)==55 and curve_points==275, 'final k-curve coverage is not55x5')
        require(compared==dict(greedy=55,stochastic=110), 'not all archived reference draws were reproduced')
        scale = json.loads((HERE/'scale/summary.json').read_text())
        require(scale['evaluated']==18 and len(scale['rows'])==18, 'final scale coverage is not18')
        require(json.loads((HERE/'scale/verification.json').read_text()).get('verified') is True, 'scale audit did not pass')
        for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS'):
            os.environ[name] = '1'
        sys.path.insert(0,str(ROOT/'src'))
        from llm.exit import outcome_epoch
        current_epoch = outcome_epoch.epoch_id(outcome_epoch.snapshot())
        require(epochs=={current_epoch}, 'outcome epoch changed across evaluations or by final audit')
        require(json.loads((HERE/'small/manifest.json').read_text())['outcome_epoch']==current_epoch,
                'small decoder manifest outcome epoch differs from final audit')
    result = dict(passed=not errors, frozen_files=len(freeze['sha256']), archived_comparisons=compared,
                  evaluations=evaluations, passes=passed, admission_checks=admission_checks,
                  admission_boundary_checks=5, verified_curve_points=curve_points,
                  raw_plan_semantic_checks=raw_semantic_checks, final=final,
                  checker_epochs=sorted(epochs), current_checker_epoch=current_epoch, errors=errors)
    (HERE/'raw-plan-canonical-sha256.json').write_text(json.dumps(raw_digests,indent=2,sort_keys=True)+'\n')
    (HERE/'verification.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps(result))
    if errors:
        raise SystemExit(1)


if __name__ == '__main__':
    audit(final='--final' in sys.argv)
