"""Assemble the D217 report from persisted experimental results, without launching work."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def read(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def render():
    freeze = read(HERE/'FREEZE.json', {})
    small = read(HERE/'small/summary.json', {})
    scale = read(HERE/'scale/summary.json', {})
    smoke = read(HERE/'smoke.json', {})
    samples = [json.loads(p.read_text()) for p in (HERE/'small/samples').glob('*/sample-*.json')]
    complete_samples = [r for r in samples if r['plan'] is not None]
    if samples:
        small['stochastic'] = dict(samples=len(samples), complete=len(complete_samples),
                                  completion_rate=len(complete_samples)/len(samples),
                                  gate_audited=sum('prepared' in r for r in complete_samples),
                                  gate_clear=sum(r.get('prepared',{}).get('gate_clear') is True for r in complete_samples),
                                  mean_rewinds=sum(r['metadata']['rewinds'] for r in samples)/len(samples))
    gate_rule_counts = {}
    for row in complete_samples:
        for name in row.get('prepared',{}).get('gate_rules',[]):
            gate_rule_counts[name] = gate_rule_counts.get(name,0)+1
    errors = []
    for name, digest in freeze['sha256'].items():
        if hashlib.sha256((HERE/'frozen-source'/name).read_bytes()).hexdigest() != digest:
            errors.append('frozen source changed: '+name)
    digest = freeze['sha256']['fitted.json']
    g = small.get('greedy', {})
    s = small.get('stochastic', {})
    done = not small.get('pending', True) and scale.get('evaluated') == 18
    fmt = lambda n: 'pending' if n is None else f'{n:.2f}'
    lines = [
        '# D217: frozen 12-weight rule', '',
        ('Complete.' if done else '**Running; partial observations below are not final results.**'), '',
        f'Updated {datetime.now(timezone.utc).isoformat()}.', '',
        f'Frozen first-fit weights: `frozen-source/fitted.json`, SHA-256 **`{digest}`**. '
        'The second fit is archived only. All 34 original scratch files, including both fits, fit scripts, features, rule driver and original validation rows, '
        'are copied verbatim under `frozen-source/` and individually hashed in `FREEZE.json`. Archived files are read-only. '
        'The first fit was selected before these runs. No refit, temperature search or validation tuning is performed. These are the weights selected for the future test-v3 arm; no test inputs were opened.', '',
        'The durable decoder in `rule.py` uses the original feature calculation and first-fit weights, legal-action order, '
        'T=1 action softmax, deterministic first-maximum greedy choice, and rewind stopping checks at 32 rewinds or 64 previously rewound actions, with at most 8 actions per rewind (the final rewind can cross 64). '
        'It loads the production ForbiddingDecoder class verbatim from its Python AST to avoid importing unrelated MLX model helpers. '
        'No LLM inference or GPU is used. The source file digest is bound to each evaluation.', '',
        'Each completed plan receives K1b compaction and a gate check. Completed greedy plans and stochastic ranked candidates tried by each k receive '
        'the unchanged `compact.finish_and_check` used by `judge.py`, at 600 seconds with the instance seed, full checker and strict mask replay. '
        'Ranking stops at the first passing candidate; untried stochastic plans receive no finisher verdict. Tried gate failures also receive the finisher. '
        'Only the leader admission file can admit a finisher: D206 requires a nonfuture timestamp younger than 120 seconds, '
        'mask popcount at least 8 and load1 at most 45. Exact admission snapshots and checker artifacts are retained. '
        'At most two concurrent CPU workers, each processing trials serially, with one native thread per library, '
        'normal priority, offline uv with the workspace cache.', '',
        '## Small validation', '',
        'The same 55 input digests as K1b are fixed in `small/manifest.json`. Greedy is separate from all 16 T=1 stochastic draws '
        '(seeds 0–15). Historical K1b includes greedy sample0 and T=.7 thereafter; temperatures and historical judging windows differ.', '',
        '| Arm | Maps resolved | Complete | Gate clear | Full-checker passes | Mean final moves on own passes |',
        '|---|---:|---:|---:|---:|---:|',
        '| 8.2 historical k=1 | 55/55 | 49 | 45 | 46 | 24.20 |',
        f'| Frozen rule greedy | {g.get("maps",0)}/55 | {g.get("complete",0)} | {g.get("gate_clear",0)} | {g.get("passed",0)} | {fmt(g.get("mean_moves"))} |', '',
        f'Stochastic T=1: {s.get("complete",0)} complete of {s.get("samples",0)}/880 stored draws; '
        f'{s.get("gate_clear",0)} gate-clear among {s.get("gate_audited",0)} audited complete plans. '
        'Full k=1,2,4,8,16 final-move curves, paired means against K1b/K1r, K1c at k=16, and D207 companion completion/rewind rates are in [SMALL.md](SMALL.md). '
        'Ranking is by compacted raw moves, duplicate move signatures are judged once, and each k stops at its first passing candidate.', '',
        '## Scale', '',
        f'{scale.get("evaluated",0)}/18 ladder maps resolved: {scale.get("complete",0)} complete, '
        f'{scale.get("gate_clear",0)} gate-clear, {scale.get("passed",0)} full-checker passes. '
        'The exact 18 K2 TRAIN ladder inputs, 58–306 boxes, are pinned by path and digest in `scale/manifest.json`; '
        'they are evaluation-only here. The per-box-count table with decode/evaluation/plan seconds, completion, gate clearance and passes '
        'beside historical 8.2/two-step/block-rule is in [SCALE.md](SCALE.md). '
        '8.2 historically passed 0/18 using up to 16 samples and a 1200-second finisher, so the 600-second rule arm is not a matched speed benchmark.', '',
        '## Verification and assumptions', '',
        f'All frozen source hashes verified: {not errors}. '
        f'CPU smoke matches the archived first validation map on status, rewind count, gate and raw cost: {smoke.get("passed",False)}; '
        f'MLX imported in smoke: {smoke.get("mlx_imported","unknown")}. '
        'Per-trial input, code, plan, mask configuration and outcome-epoch bindings and finished-plan/report SHA-256 values are recorded.', '',
        'Assumptions: use the first reported fit rather than choosing between two validation-equivalent fits; '
        'interpret 16 samples at T=1 as 16 stochastic draws plus the separate greedy arm; '
        'use 600 seconds on both new arms, while disclosing historical comparator budgets; '
        'interpret the scale request as the existing 18-map TRAIN cohort rather than generating or selecting new maps. '
        'This is post-hoc validation and a TRAIN ladder evaluation; it supports no held-out claim.', '',
    ]
    at = lines.index('## Scale')
    extra = ['D207 pooled completion comparison (descriptive; sampling regimes differ):', '',
             '| Arm / regime | Complete / stored | Completion | Mean rewinds |',
             '|---|---:|---:|---:|',
             '| 8.2, greedy + 15 at T=.7 | 792/880 | 90.0% | 1.6 |',
             '| Uniform random | 729/880 | 82.8% | 3.9 |',
             '| Untrained, greedy + 15 at T=.7 | 668/880 | 75.9% | 4.5 |',
             '| K1c (8.2 inside the block rule), greedy + 15 at T=.7 | 560/880 | 63.6% | 6.1 |',
             f'| Frozen fitted rule, 16 at T=1 | {s.get("complete",0)}/{s.get("samples",0)} | {fmt(100*s["completion_rate"]) if s.get("completion_rate") is not None else "pending"}% | {fmt(s.get("mean_rewinds"))} |', '',
             'Rule best-of-k final results:', '',
             '| k | Maps resolved | Eligible | Passes | Mean final moves on own passes | Mean best raw moves |',
             '|---:|---:|---:|---:|---:|---:|']
    for k in (1,2,4,8,16):
        point = small.get('curves',{}).get(str(k),{})
        extra.append(f'| {k} | {point.get("maps",0)}/55 | {point.get("eligible",0)} | {point.get("passed",0)} | {fmt(point.get("mean_moves"))} | {fmt(point.get("mean_raw_best"))} |')
    extra += ['', 'Paired final moves on maps both arms pass (historical comparators):', '',
              '| k | Comparator | Rule / comparator passes on observed maps | Both pass | Mean rule / comparator moves | Difference | Fewer / equal / more |',
              '|---:|---|---:|---:|---:|---:|---:|']
    for k in (1,2,4,8,16):
        for arm, pair in small.get('comparisons',{}).get(str(k),{}).items():
            delta = 'pending' if pair['delta'] is None else f'{pair["delta"]:+.1%}'
            extra.append(f'| {k} | {arm} | {pair["mine_pass"]} / {pair["rival_pass"]} | {pair["mutual"]} | {fmt(pair["mine_mean"])} / {fmt(pair["rival_mean"])} | {delta} | {pair["fewer"]} / {pair["equal"]} / {pair["more"]} |')
    extra += ['', 'K1c masked alone and its K1b fallback portfolio are shown separately at k=16; no full K1c k-curve is stored. '
              'Raw moves are measured before finishing and do not establish a checker pass.', '']
    sampled_trials = [json.loads(p.read_text()) for p in (HERE/'small/sample-evaluations').rglob('result.json')]
    extra += [f'Sampled finisher verdicts completed: {len(sampled_trials)} distinct move signatures, '
              f'{sum(r["passed"] for r in sampled_trials)} passes, '
              f'{sum(not r["passed"] for r in sampled_trials)} failures, '
              f'{sum(r.get("timed_out") is True for r in sampled_trials)} marked finisher limits/timeouts. '
              'Cached verdicts reused across k are counted once here.', '']
    unfinished = []
    for path in sorted((HERE/'small/sample-evaluations').glob('*/*/admission.jsonl')):
        if (path.parent/'result.json').exists():
            continue
        records = path.read_text().splitlines()
        try:
            probe = json.loads(records[-1])
        except (IndexError, json.JSONDecodeError):
            continue  # A writer can be publishing its next probe.
        if not (path.parent/'result.json').exists():
            unfinished.append((path.parent.parent.name, probe))
    if unfinished:
        extra += ['Unfinished trials, latest recorded D206 probe (admitted trials may still be running):', '',
                  '| Map | Admission | Load1 | Probe UTC |', '|---|---|---:|---|']
        for name, probe in unfinished:
            stamp = datetime.fromtimestamp(probe['read_at'], timezone.utc).isoformat(timespec='seconds')
            state = 'admitted' if probe['admitted'] else 'waiting: '+', '.join(probe['errors'])
            extra.append(f'| `{name}` | {state} | {probe.get("load1")} | {stamp} |')
        extra += ['']
    if gate_rule_counts:
        extra += ['Stochastic gate failures by exact checker rule (plans can appear in multiple rows):', '',
                  '| Checker rule | Plans |', '|---|---:|']
        extra += [f'| `{name}` | {count} |' for name,count in sorted(gate_rule_counts.items())]
        extra += ['']
    lines[at:at] = extra
    greedy_rows = [json.loads(p.read_text()) for p in (HERE/'small/greedy').glob('*.json')]
    rival_rows = {p.stem:json.loads(p.read_text())['points']['1']['moves']
                  for p in (HERE.parent/'k1b/kcurve/8.2').glob('*.json')}
    pairs = [(r['evaluation'].get('moves') if r['evaluation']['passed'] else None, rival_rows[r['instance_id']])
             for r in greedy_rows if r['instance_id'] in rival_rows]
    mutual = [(a,b) for a,b in pairs if a is not None and b is not None]
    if mutual:
        ma, mb = (sum(p[i] for p in mutual)/len(mutual) for i in (0,1))
        detail = (f'Greedy paired comparison on {len(pairs)} observed maps: '
                  f'{len(mutual)} both pass, rule-only {sum(a is not None and b is None for a,b in pairs)}, '
                  f'8.2-only {sum(a is None and b is not None for a,b in pairs)}. '
                  f'Mean moves on mutual passes: rule {ma:.2f}, 8.2 {mb:.2f} ({ma/mb-1:+.1%}); '
                  f'fewer/equal/more {sum(a<b for a,b in mutual)}/{sum(a==b for a,b in mutual)}/{sum(a>b for a,b in mutual)}. '
                  f'Mean rule greedy rewinds: {sum(r["metadata"]["rewinds"] for r in greedy_rows)/len(greedy_rows):.2f}.')
        at = lines.index('D207 pooled completion comparison (descriptive; sampling regimes differ):')
        lines[at:at] = [detail,'']
    scale_manifest = read(HERE/'scale/manifest.json',{}).get('inputs',[])
    rows = scale.get('rows',[])
    coverage = []
    for arm, label in [('default','Two-step'),('rule','Block rule')]:
        values = [r.get('comparators',{}).get('sweep',{}).get('arms',{}).get(arm,{}).get('passed')
                  for r in rows if r.get('comparators',{}).get('sweep')]
        known = [v for v in values if isinstance(v,bool)]
        coverage.append(f'{label}: {sum(known)}/{len(known)} known passes, {18-len(known)} pending/unavailable')
    complete82 = sum(r.get('comparators',{}).get('model82',{}).get('complete',0)>0 for r in rows)
    extra = ['| Boxes | Maps resolved / expected | Complete | Gate clear | Pass | Mean decode s | Mean plan s, excluding admission |',
             '|---:|---:|---:|---:|---:|---:|---:|']
    for n in sorted({job['n_loads'] for job in scale_manifest}):
        group = [r for r in rows if r['n_loads']==n and r.get('evaluated') is True]
        expected = sum(j['n_loads']==n for j in scale_manifest)
        avg = lambda key: sum(r[key] for r in group)/len(group) if group else None
        extra.append(f'| {n} | {len(group)}/{expected} | {sum(r["complete"] for r in group)} | {sum(r["gate_clear"] is True for r in group)} | {sum(r["passed"] for r in group)} | {fmt(avg("decode_seconds"))} | {fmt(avg("plan_seconds"))} |')
    extra += ['', f'Historical 8.2 reached a complete sample on {complete82}/18 maps and passed 0/18. '
              + '; '.join(coverage)+'. Missing comparator rows are not counted as failures. '
              'The complete per-map table and exact timings are retained in SCALE.md.', '']
    at = lines.index('## Verification and assumptions')
    lines[at:at] = extra
    verification = read(HERE/'verification.json',{})
    lines += [f'Latest artifact audit: final={verification.get("final",False)}, passed={verification.get("passed",False)}; '
              f'{verification.get("evaluations",0)} evaluations, '
              f'{verification.get("admission_checks",0)} recorded admission checks, '
              f'{verification.get("verified_curve_points",0)} ranked curve points, '
              f'archived comparisons {verification.get("archived_comparisons",{})}; '
              f'errors: {verification.get("errors",[])}.', '']
    small_audit = read(HERE/'small/final-verification.json',{})
    if small_audit.get('verified'):
        lines += ['Independent small comparator audit passed: 55 map IDs, 275 curve points, '
                  '12 printed paired rows and 330 historical comparator source hashes. '
                  'Exact sums and ratios are retained in `small/final-verification.json`; '
                  'source hashes are in `small/comparator-provenance.json`.', '']
    lines += ['Execution records and rerun entry points:', '',
              '- `scale_runner.py`: fixed ladder, greedy decode and 600-second finishing; scale manifest, owned PID, exit receipt and artifacts under `scale/`.',
              '- `small_runner.py`: greedy queue, seeded T=1 sampling and original ranking procedure; primary source/manifest correction receipts under `small/source-epochs/` preserve prior artifact hashes.',
              '- `reverse_sampler.py`: temporary T=1-only helper after the scale worker exits; separate lock/receipt, reverse map order and exclusive publication.',
              '- `curves_sharded.py`: after all samples are ready and decoders exit, two fixed alternating map shards (28/27), unchanged finisher/ranking, two CPU workers total.',
              '- `UV_CACHE_DIR=$PWD/.uv-cache uv run --offline --no-sync python -B experiments/mvp/strategy/fitted-rule/verify.py --final`: independent full-coverage saved-result audit.', '']
    if done:
        largest = max((r['n_loads'] for r in rows if r['passed']),default=None)
        k16 = small['curves']['16']
        paired16 = small['comparisons']['16']['K1b']
        verdict = (f'The frozen greedy rule passed {g["passed"]}/55 small maps, versus historical 8.2\'s 46/55. '
                   f'Its stochastic T=1 pool completed {s["complete"]}/880 samples '
                   f'({100*s["completion_rate"]:.2f}%, versus historical 8.2\'s 90.0%; sampling regimes differ). '
                   f'Best of 16 passed {k16["passed"]}/55 at {fmt(k16["mean_moves"])} mean moves on its passes. '
                   f'Historical K1b passed {paired16["rival_pass"]}/55 at k=16; on their {paired16["mutual"]} mutual passes, '
                   f'the rule used {fmt(paired16["mine_mean"])} versus {fmt(paired16["rival_mean"])} moves '
                   f'({paired16["delta"]:+.2%}). '
                   f'On the fixed scale ladder it passed {scale["passed"]}/18, versus historical 8.2\'s 0/18; '
                   f'the largest passing map had {largest} boxes. '
                   'Read the paired move rows separately from pass coverage and completion. '
                   'These comparisons use historical model rows; a frozen held-out test remains future work.')
        lines[4:4] = [verdict,'']
    else:
        headline = (f'Small greedy: {g.get("passed",0)}/{g.get("maps",0)} observed maps pass, '
                    f'{fmt(g.get("mean_moves"))} mean moves on passes (historical 8.2: 46/55, 24.20). '
                    f'Scale: {scale.get("passed",0)}/{scale.get("evaluated",0)} resolved maps pass (historical 8.2: 0/18). '
                    f'T=1 sampling: {s.get("samples",0)}/880 stored; '
                    f'best-of-k judging: {small.get("curves",{}).get("16",{}).get("maps",0)}/55 maps resolved.')
        lines[4:4] = [headline,'']
    if errors:
        lines += ['Verification errors: '+ '; '.join(errors), '']
    (HERE/'RULE.md').write_text('\n'.join(lines))
    return dict(complete=done, small=small, scale=scale, verification_errors=errors)


if __name__ == '__main__':
    result = render()
    print(json.dumps(dict(complete=result['complete'], verification_errors=result['verification_errors'])))
