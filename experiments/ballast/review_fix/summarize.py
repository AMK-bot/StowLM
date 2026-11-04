"""Summarize checkpointed numerical evidence without rerunning any physics."""
import collections
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def failed(part):
    return not part['ran'] or any(v['severity']=='hard' for v in part['violations'])


if __name__=='__main__':
    before=read(HERE/'before/results.json')
    after_label=next(label for label in ('after-final-v2','after-context','after-final')
                     if (HERE/label/'summary.json').exists())
    after=read(HERE/after_label/'results.json')
    diagnostics={row['case']:row for row in read(HERE/'diagnostics.json')}
    counts=collections.Counter()
    lines=['# Ballast review qualification, 2026-10-28', '',
        'Six frozen feeder_500 G1 small stows (20 export boxes each), with both 5-second process-cold and second-call measurements. '
        'The before and after calls receive byte-identical saved instances and plans. '
        'The six historical G1 candidate move sequences and bookings are preserved; '
        'their obsolete vessel snapshots are rebuilt using the current public small preset and '
        '`recondition_slice` (D40/D42/D48), with declared D50 consumable groups and voyage identities. '
        'Every source input is train/validation, never a held-out test input. '
        'These adapted snapshots are not represented as unchanged historical G1 inputs.', '',
        '| Frozen case | Before cold / second | After cold / second | Initial exact hard-failing parts |',
        '|---|---|---|---|']
    for old,new in zip(before,after,strict=True):
        assert old['case']==new['case']
        assert old['instance_sha256']==new['instance_sha256']
        assert old['plan_sha256']==new['plan_sha256']
        row=diagnostics[old['case']]
        departure_parts={name:part for name,part in row['departure']['parts'].items()
                         if not name.startswith('condition:')}
        for name in ('stability','strength'):
            departure_parts[name]=row['departure']['parts'].get(
                f'condition:{old["case"]}:departure:{name}',departure_parts[name])
        failures=[name for name,part in departure_parts.items() if failed(part)]
        failures += ['arrival/'+name for name,part in row['arrival'].items() if failed(part)]
        counts.update(failures)
        show=lambda calls:' / '.join(f'{c["status"]} {c["wall_s"]:.3f}s' for c in calls)
        lines.append(f'| {old["case"]} | {show(old["calls"])} | {show(new["calls"])} | '+', '.join(failures)+' |')
    lines += ['', '**Result: before 12/12 `not_found` (100%); final after 12/12 `not_found` (100%).** '
        'No unsafe plan or infeasibility proof was returned. Same bounded six cases, no expanded campaign. '
        'Final after source fingerprints are unchanged across each captured checker, linear, model, vessel, '
        f'and ballast source file (`{after_label}/identity-start.json`, `identity-end.json`). '
        'The initial before campaign predates source-fingerprint instrumentation; the complete original ballast '
        'module family is frozen under `before_source/`, and all input hashes are recorded. '
        'Historical G1 failure rates are not pooled with this adapted-input campaign.', '',
        'Exact initial diagnostic failure counts: '+', '.join(f'{k} {v}/6' for k,v in sorted(counts.items()))+'. '
        'All six complete exact checks; no endpoint diagnostic timed out or failed for missing data. '
        'None is independently known feasible, so 100% inconclusive is not proof of a 100% missed-feasible rate.', '',
        'On case `small-100100015-2a1913beea`, the unchanged initial condition has departure '
        'containership total GZ-area clearance −0.01456 m·rad, blind distance 485.17 m against 232.8 m, '
        'and fitting/racking/guide ratios 1.709/1.294/1.101 (all require ≤1). '
        'The 30-second search repairs the departure GZ-area clearance to +0.01453 m·rad, but its '
        'arrival GZ-area clearance remains −0.01418 m·rad, visibility clearance −253.61 m, '
        'and fitting ratio 1.716. Those are actual checked candidate failures, not missing-data artefacts. '
        'No claim that all possible ballast fills fail these constraints is made.', '',
        'The intermediate 30-second profile (`profile-small-100100015-2a1913beea.json`, before '
        'context deferral and the shorter LP sub-budget) completes 11 candidates. '
        'LP construction stops at its local deadline after 3.40 seconds; `solve_ballast_lp` is not reached. '
        'Thus this profile provides no evidence of conservative-margin LP infeasibility. '
        'Context construction takes 1.34 s; stability 15 calls/4.84 s, strength 10/6.71 s, '
        'visibility 11/9.13 s, lashing 10/1.16 s. Pressed branching takes 11.87 s and successive '
        'repair 5.91 s, including their checker calls (these nested totals must not be added). '
        'Large exact-check costs plus multiple coupled hard failures limit coverage. '
        'No tank branch, regulatory threshold, conservative LP reserve, or acceptance check is weakened.', '',
        '## Independently feasible synthetic witness', '',
        '`synthetic-witness/` freezes one complete ordinary dry 20-foot, 12-tonne export with legal '
        'hatch-off/load/hatch-on moves. Its declared zero-ballast witness passes the unchanged full '
        'checker and independently pure-derived arrival stability/strength checks '
        '(`witness-diagnostics.json`). This uses the integration test’s labelled synthetic submerged '
        'non-cargo balancing mass and broad synthetic strength ratings; it is a search regression, '
        'not a production small-slice throughput observation. Consumables are empty in this witness, '
        'so separate D50 regressions cover nonzero consumption.', '']
    witness_labels=['witness-before','witness-after']
    if (HERE/'witness-after-context/results.json').exists():
        witness_labels.append('witness-after-context')
    if (HERE/'witness-final/results.json').exists():
        witness_labels.append('witness-final')
    for label in witness_labels:
        row=read(HERE/label/'results.json')[0]
        lines.append(f'- {label}: '+', '.join(f'{"cold" if n==0 else "second"} {c["status"]} {c["wall_s"]:.3f}s'
            for n,c in enumerate(row['calls']))+'.')
    lines += ['', 'The original cold/second discrepancy reproduces the review’s suspected wall-clock '
        'outcome nondeterminism on an independently feasible case. Deterministic search order does not '
        'guarantee identical outcomes under cache-sensitive wall-clock termination. '
        'The final source finds the same independently feasible witness cold and warm: '
        '**synthetic witness `not_found` rate improves from 1/2 (50%) to 0/2 (0%)**; '
        'both returned plans independently pass the full checker. Final witness source hashes are '
        'unchanged (`witness-final/identity-start.json`, `identity-end.json`). This improvement is '
        'reported separately from the unchanged six-production-slice result.', '',
        '## Limits and required follow-up', '',
        '- The universal “find exact ballast or prove none exists for any stow” criterion remains unproved. '
        'A complete certified nonlinear search is a separate algorithmic project; alternatively, the leader '
        'must obtain approval for an explicitly bounded/inconclusive completion criterion.',
        '- Production slice effectiveness remains unqualified on independently verified feasible inputs. '
        'The vessel/generator owners need a current, independently joint-checker-passing D42/D48/D50 '
        'small cohort (including background securing and visibility). Reusing infeasible or obsolete '
        'witnesses cannot establish missed-feasible coverage.',
        '- Arrival uses the declared next-call LOCODE and density. Current-port berth draft/rules are '
        'cleared for the D50 seagoing endpoint; vessel loading limits remain hard. No next-berth '
        'acceptance is implied. The campaign declares the same synthetic seawater density at both endpoints. '
        'The model’s pure arrival function sets the 10% remaining consumables; ballast stays fixed.', '',
        '## Reproduce', '', '```sh',
        'OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/ballast/review_fix/campaign.py prepare --count 6',
        'BALLAST_REVIEW_BEFORE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 10 uv run python experiments/ballast/review_fix/campaign.py run --label before-replay --count 6 --limit 5 --repeat 2 --check-limit 8',
        'OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 nice -n 10 uv run python experiments/ballast/review_fix/campaign.py run --label after-replay --count 6 --limit 5 --repeat 2 --check-limit 8',
        'OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/ballast/review_fix/diagnose.py',
        'OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 uv run python experiments/ballast/review_fix/summarize.py',
        '```', '', 'Resource estimates and actuals: `compute.md`. No cache warm, GPU, cloud, external network or login. '
        '`nice` priority adjustment and `ps` inventory are sandbox-denied; owned workers alone are terminated.']
    (HERE/'README.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(dict(initial_failure_counts=dict(counts),cases=len(before),readme=str(HERE/'README.md'))))
