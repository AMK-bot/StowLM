"""Fail-closed D172 export plus blocking 30-winner audit; no solver/dataset work.
Usage: d172_finish.py RUN_DIR [--workers 3] [--seed 172]
"""
from __future__ import annotations
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import math
import os
from pathlib import Path
import random
import shutil
import time
import d151

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
_AUDIT_EPOCH = None
_AUDIT_MANIFEST = None
AUTHORIZED_TIMEOUT_CASES = frozenset({
    'train/small/feeder_1700/simulated_yard/seed-031100375',
    'train/small/feeder_500/simulated_yard/seed-030100118',
})


def authorized_exclusions(manifest):
    """The leader authorized only these two exhausted finisher-limit rows."""
    exclusions = manifest.get('authorized_exclusions', [])
    if not exclusions:
        return {}
    cases = [item['case'] for item in exclusions]
    entries = {entry['case']: entry for entry in manifest['entries']}
    if (len(cases) != len(set(cases)) or set(cases) != AUTHORIZED_TIMEOUT_CASES or
            any(case not in entries or entries[case]['mode'] != 'full' for case in cases)):
        raise RuntimeError('authorized exclusions must be exactly the two leader-approved full-mode maps')
    if any(item.get('authority') != 'leader' or item.get('reason') != 'two_step finisher timeout after 3 launches'
           for item in exclusions):
        raise RuntimeError('missing explicit leader finisher-timeout authorization')
    return {item['case']: item for item in exclusions}


def check_coverage(manifest, old_best):
    authorized = authorized_exclusions(manifest)
    entries = manifest['entries']
    cases = [e['case'] for e in entries]
    small = {e['case'] for e in manifest['inventory_entries']}
    if len(cases) != len(set(cases)) or len(small) != len(manifest['inventory_entries']):
        raise RuntimeError('duplicate manifest/inventory cases')
    if any(not case.startswith('train/small/') for case in small):
        raise RuntimeError('inventory is not small train maps')
    old = {}
    for path in sorted(Path(old_best).glob('*/record.json')):
        case = d151.read(path)['provenance']['case']
        if case in old:
            raise RuntimeError(f'duplicate D151 winner: {case}')
        old[case] = path
    if not old:
        raise RuntimeError('no D151 winners')
    expected = small | old.keys()
    if set(cases) != expected:
        raise RuntimeError(f'manifest coverage differs: missing={len(expected-set(cases))}, extra={len(set(cases)-expected)}')
    overlap = set(cases) & set(manifest['excluded_val_cases'])
    overlap_ids = {e.get('instance_id') for e in entries} & set(manifest.get('val_instance_ids', []))
    if overlap or overlap_ids:
        raise RuntimeError(f'held-out val overlap: {overlap} {overlap_ids}')
    for entry in entries:
        case = entry['case']
        if not case.startswith(('train/small/', 'train/tiny/')):
            raise RuntimeError(f'not a permitted train case: {case}')
        if entry.get('rung') != case.split('/')[1]:
            raise RuntimeError(f'rung metadata differs from case: {case}')
        if case in old:
            if entry['mode'] != 'winner_only' or Path(entry['retained_record']).resolve() != old[case].resolve():
                raise RuntimeError(f'D151 winner not retained: {case}')
            if entry['retained_source'] != d151.read(old[case])['provenance']['source']:
                raise RuntimeError(f'D151 retained source differs: {case}')
        elif entry['mode'] != 'full':
            raise RuntimeError(f'new small map requires both teachers: {case}')
    return dict(eligible_small=len(small), d151_winners=len(old), expected_union=len(expected),
                authorized_excluded=len(authorized), expected_export=len(expected)-len(authorized))


def receipts(manifest, launch, run_dir):
    workers = launch['workers']
    if not 1 <= workers <= 8 or not launch.get('launch_id'):
        raise RuntimeError('launch needs UUID and 1..8 workers')
    assigned = []
    for shard in range(workers):
        path = Path(run_dir)/'receipts'/launch['launch_id']/f'shard-{shard}.json'
        receipt = d151.read(path) if path.exists() else {}
        required = dict(ok=True, uuid=manifest['uuid'], launch_id=launch['launch_id'], shard=shard,
                        outcome_epoch=manifest['outcome_epoch'], checker_epoch=manifest['checker_epoch'])
        if any(receipt.get(k) != v for k, v in required.items()):
            raise RuntimeError(f'no valid receipt for shard {shard}')
        expected = [e['case'] for e in manifest['entries'][shard::workers]]
        if receipt.get('assigned') != expected or receipt.get('crashed'):
            raise RuntimeError(f'shard {shard} assignment differs or has crashes')
        assigned.extend(receipt['assigned'])
    if len(assigned) != len(set(assigned)) or set(assigned) != {e['case'] for e in manifest['entries']}:
        raise RuntimeError('receipts do not provide exact coverage')


def row_problem(row, entry, manifest, run_dir):
    if row.get('crashed'):
        return 'crashed'
    if row.get('check_path') == 'reused_same_epoch':
        if (d151.sha(row['reused_d151_row']) != row['reused_d151_row_sha256'] or
                d151.sha(HERE/'admitted.json') != row['reused_d151_admission_sha256']):
            return 'original D151 verification evidence changed'
    if row.get('case') != entry['case'] or row.get('gold_plan_sha256') != entry['gold_plan_sha256']:
        return 'case or gold plan hash differs'
    if any(row.get(name) != entry[name] for name in ('mode', 'input', 'instance_id')):
        return 'row identity differs'
    if entry['mode'] == 'winner_only':
        if (row.get('retained_record_sha256') != entry['retained_record_sha256']
                or d151.sha(entry['retained_record']) != entry['retained_record_sha256']):
            return 'retained record changed'
    for name, expected in [('outcome_epoch', manifest['outcome_epoch']), ('checker_epoch', manifest['checker_epoch']),
                           ('input_sha256', entry['input_sha256']), ('budget_s', 120.)]:
        if row.get(name) != expected:
            return f'{name} differs'
    names = [('retained', 'retained')] if entry['mode'] == 'winner_only' else [('gold', 'gold'), ('two_step', 'baseline_twostep')]
    for name, source in names:
        detail = row.get(name) or {}
        if not detail.get('attempted'):
            return f'{name} incomplete'
        if detail.get('unavailable') or detail.get('finisher_timeout'):
            return f'{name} not a verdict'
        if detail.get('passed'):
            cost = detail.get('moves')
            if isinstance(cost, bool) or not isinstance(cost, (float, int)) or not math.isfinite(cost) or cost < 0:
                return f'{name} invalid cost'
            path = d151.candidate_path(run_dir, entry, source)
            if not path.exists():
                return f'{name} candidate missing'
            candidate = d151.read(path)
            if row.get('check_path') == 'reused_same_epoch':
                original = d151.read(entry['retained_record'])
                if candidate['plan'] != original['plan'] or candidate['report'] != original['report']:
                    return 'reused candidate differs from original D151 winner'
            if (candidate.get('input_sha256') != entry['input_sha256'] or candidate.get('weighted_moves') != detail.get('moves')
                or candidate.get('report', {}).get('count', {}).get('weighted_nonproductive') != detail.get('moves')
                or candidate.get('report', {}).get('passed') is not True or detail.get('mask_passed') is not True):
                return f'{name} candidate disagrees or mask did not pass'
    return None


def admit(manifest, launch, run_dir, root=ROOT):
    if manifest.get('budget_s') != 120.:
        raise RuntimeError('D172 requires the 120-second recipe')
    receipts(manifest, launch, run_dir)
    authorized = authorized_exclusions(manifest)
    admitted, excluded = [], []
    for entry in manifest['entries']:
        path = Path(run_dir)/'rows'/(d151.key(entry['case'])+'.json')
        row = d151.read(path) if path.exists() else None
        problem = 'missing row' if row is None else row_problem(row, entry, manifest, run_dir)
        raw = Path(root)/entry['input']
        train = (Path(root)/'experiments/mvp/corpus/train').resolve()
        if problem is None and (train not in raw.resolve().parents or not raw.exists() or d151.sha(raw) != entry['input_sha256']):
            problem = 'input missing, changed or outside train'
        expected_input = Path(root)/'experiments/mvp/corpus'/(entry['case']+'.json')
        if problem is None and (raw.resolve() != expected_input.resolve() or d151.read(raw).get('id') != entry['instance_id']):
            problem = 'input case or instance identity differs'
        gold = Path(root)/'experiments/mvp/gold'/entry['case']/'plan.json'
        if problem is None and (not gold.exists() or d151.sha(gold) != entry['gold_plan_sha256']):
            problem = 'stored gold plan changed'
        if problem:
            if entry['case'] in authorized:
                if (not path.is_file() or not raw.is_file() or raw.resolve() != expected_input.resolve() or train not in raw.resolve().parents
                        or d151.sha(raw) != entry['input_sha256'] or d151.read(raw).get('id') != entry['instance_id']
                        or not gold.exists() or d151.sha(gold) != entry['gold_plan_sha256']
                        or d151.sha(path) != authorized[entry['case']].get('row_sha256')):
                    problem = 'authorized exclusion input/gold/row integrity differs'
            excluded.append(dict(case=entry['case'], reason=problem, authorized=entry['case'] in authorized,
                                 finisher_timeout=bool(row and (row.get('two_step') or {}).get('finisher_timeout'))))
            continue
        if entry['case'] in authorized:
            raise RuntimeError(f'authorized exclusion lacks the recorded timeout: {entry["case"]}')
        if entry['mode'] == 'winner_only':
            source = entry['retained_source']
            costs = d151.read(entry['retained_record'])['provenance']['costs']
            if not row['retained']['passed'] or row['retained']['moves'] != costs[source]:
                excluded.append(dict(case=entry['case'], reason='retained winner failed or cost changed'))
                continue
            candidate_source = 'retained'
        else:
            source, costs = d151.teacher(row, entry)
            if source is None:
                excluded.append(dict(case=entry['case'], reason='no verified teacher'))
                continue
            candidate_source = 'gold' if source == 'gold' else 'baseline_twostep'
        admitted.append(dict(entry=entry, source=source, costs=costs, candidate_source=candidate_source))
    d151.write(Path(run_dir)/'excluded.json', excluded)
    if (set(item['case'] for item in excluded) != set(authorized) or
            any(not item.get('authorized') or item['reason'] != 'two_step not a verdict' or not item.get('finisher_timeout')
                for item in excluded)):
        raise RuntimeError(f'{len(excluded)} excluded maps; zero required unless exactly leader-authorized: {excluded[:3]}')
    if len(admitted) != len(manifest['entries'])-len(authorized):
        raise RuntimeError('admission does not match exact authorized coverage')
    return admitted


def stage(manifest, launch, admitted, run_dir, target):
    target = Path(target)
    if target.exists():
        raise RuntimeError(f'refusing to overwrite existing export: {target}')
    temporary = target.with_name(f'{target.name}.tmp-{manifest["uuid"]}')
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    for item in admitted:
        entry = item['entry']
        candidate = d151.read(d151.candidate_path(run_dir, entry, item['candidate_source']))
        if candidate['weighted_moves'] != item['costs'][item['source']]:
            raise RuntimeError(f'candidate cost changed: {entry["case"]}')
        provenance = dict(teacher='D172 best verified plan', uuid=manifest['uuid'], launch_id=launch['launch_id'],
                          source=item['source'], costs=item['costs'], case=entry['case'], mode=entry['mode'],
                          input_sha256=entry['input_sha256'], outcome_epoch=manifest['outcome_epoch'],
                          checker_epoch=manifest['checker_epoch'], budget_s=120., stored_gold_moves=entry['stored_moves'],
                          gold_solver=entry['solver'], rung=entry['rung'], ship=entry['ship'])
        if entry['mode'] == 'winner_only':
            provenance['retained_provenance'] = d151.read(entry['retained_record'])['provenance']
            row = d151.read(Path(run_dir)/'rows'/(d151.key(entry['case'])+'.json'))
            reused = row.get('check_path') == 'reused_same_epoch'
            provenance['fresh_verification'] = not reused
            provenance['verification_path'] = row.get('check_path')
            if reused:
                provenance['original_verification_row_sha256'] = row['reused_d151_row_sha256']
                provenance['original_admission_sha256'] = row['reused_d151_admission_sha256']
        d151.write(temporary/d151.key(entry['case'])/'record.json', dict(instance_path=entry['input'], plan=candidate['plan'],
                                                                      report=candidate['report'], provenance=provenance))
    return temporary


def audit_init(manifest):
    global _AUDIT_EPOCH, _AUDIT_MANIFEST
    from llm.exit import outcome_epoch
    from llm.exit.replay import checker_epoch
    _AUDIT_EPOCH = outcome_epoch.process_snapshot()
    _AUDIT_MANIFEST = manifest
    if outcome_epoch.epoch_id(_AUDIT_EPOCH) != manifest['outcome_epoch'] or checker_epoch() != manifest['checker_epoch']:
        raise RuntimeError('audit worker epoch differs')


def audit_one(path):
    from llm.exit import outcome_epoch
    from llm.exit.selection import Candidate, verify_candidate
    from stowage.io import load_instance
    from stowage.model import MovePlan
    record = d151.read(path)
    given, manifest = record['provenance'], _AUDIT_MANIFEST
    raw = ROOT/record['instance_path']
    ok = (given['uuid'] == manifest['uuid'] and given['outcome_epoch'] == manifest['outcome_epoch']
          and given['checker_epoch'] == manifest['checker_epoch'] and given['input_sha256'] == d151.sha(raw)
          and given['launch_id'] == manifest['launch_id'])
    checked = verify_candidate(load_instance(raw), Candidate(MovePlan.model_validate(record['plan']), given['source'], given['source']),
                               ballast_s=120., expected_outcome_epoch=_AUDIT_EPOCH)
    outcome_epoch.assert_current(_AUDIT_EPOCH)
    return dict(case=given['case'], source=given['source'], recorded=given['costs'][given['source']], passed=checked.passed,
                moves=checked.weighted_moves, provenance_ok=ok, mask_passed=(checked.details.get('mask') or {}).get('passed'),
                finisher_timeout=checked.details.get('finisher_timeout'), error=checked.details.get('error'))


def audit_verdict(rows, n=30):
    bad = [r for r in rows if r.get('passed') is not True or r.get('moves') != r.get('recorded')
           or r.get('provenance_ok') is not True or r.get('mask_passed') is not True or r.get('finisher_timeout')]
    if len(rows) != n or len({r['case'] for r in rows}) != n:
        bad.append(dict(case='population', error=f'{len(rows)} audited, {n} distinct required'))
    return bad


def audit(temporary, manifest, run_dir, workers=3, seed=172):
    paths = sorted(Path(temporary).glob('*/record.json'))
    if len(paths) < 30:
        raise RuntimeError('fewer than 30 winners to audit')
    with ProcessPoolExecutor(max_workers=workers, initializer=audit_init, initargs=(manifest,)) as pool:
        rows = []
        for row in pool.map(audit_one, random.Random(seed).sample(paths, 30)):
            rows.append(row)
            d151.write(Path(run_dir)/'audit-progress.json', dict(checked=len(rows), required=30, seed=seed,
                        bad=audit_verdict(rows, len(rows)), rows=rows))
            print(f'audit checked {len(rows)}/30: {row["case"]}', flush=True)
    bad = audit_verdict(rows)
    report = dict(n=30, seed=seed, uuid=manifest['uuid'], outcome_epoch=manifest['outcome_epoch'],
                  agree=30-len(bad), passed=not bad, bad=bad, rows=rows)
    d151.write(Path(run_dir)/'audit-sample.json', report)
    if bad:
        raise RuntimeError(f'blocking 30-winner audit failed: {bad[:3]}')
    return report


def summary(manifest, launch, admitted, coverage, audit_report):
    ships = {}
    for item in admitted:
        entry = item['entry']
        counts = ships.setdefault((entry['rung'], entry['ship']), Counter())
        counts['maps'] += 1
        counts[item['source']] += 1
        counts['new_comparisons' if entry['mode'] == 'full' else 'retained_d151'] += 1
    total = Counter(item['source'] for item in admitted)
    full = [i for i in admitted if i['entry']['mode'] == 'full']
    exclusions = authorized_exclusions(manifest)
    small_exported = sum(i['entry']['rung'] == 'small' for i in admitted)
    lines = ['# D172 best verified teachers', '',
             f"Eligible small train maps with verified gold (held-out IDs excluded): {coverage['eligible_small']}.",
             f"D151 verified winners retained under the identical current epoch: {coverage['d151_winners']}. Exact union exported: {len(admitted)}.",
             f"Exported small: {small_exported}; tiny: {len(admitted)-small_exported}. Leader-authorized exclusions: {len(exclusions)}.",
             f"New two-teacher comparisons: {len(full)}; two-step wins {sum(i['source'] == 'two_step' for i in full)}.",
             f"Across the union, two-step wins {total['two_step']} / {len(admitted)}; gold wins {total['gold']}. Ties keep gold.",
             'Retained D151 gold_only maps have no new two-step comparison; original source and comparison provenance are preserved.', '',
             '| Rung | Ship | Maps | Gold | Two-step | New comparisons | Retained D151 |',
             '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for (rung, ship), c in sorted(ships.items()):
        lines.append(f"| {rung} | {ship} | {c['maps']} | {c['gold']} | {c['two_step']} | {c['new_comparisons']} | {c['retained_d151']} |")
    if exclusions:
        lines += ['', 'Excluded after three launches (leader authorized; neither has a two-step verdict):']
        lines += [f'- `{case}`: {item["reason"]}.' for case, item in sorted(exclusions.items())]
    lines += ['', f"Blocking random winner audit: {audit_report['agree']}/30 passed; seed {audit_report['seed']}.",
              f"Manifest UUID: {manifest['uuid']}. Launch UUID: {launch['launch_id']}.",
              f"Outcome epoch: {manifest['outcome_epoch']}. Checker epoch: {manifest['checker_epoch']}.",
              '120-second finisher, full checker, mask replay, CPU only. Zero unapproved exclusions. No training dataset created.', '']
    return '\n'.join(lines)


def finish(run_dir, workers=3, seed=172):
    from llm.exit import outcome_epoch
    from llm.exit.replay import checker_epoch
    from llm.exit.runner import source_pins
    run_dir = Path(run_dir).resolve()
    manifest, launch = d151.read(run_dir/'manifest.json'), d151.read(run_dir/'launch.json')
    wait_for_workers(run_dir)
    epoch = outcome_epoch.process_snapshot()
    if outcome_epoch.epoch_id(epoch) != manifest['outcome_epoch'] or checker_epoch() != manifest['checker_epoch']:
        raise RuntimeError('finish epoch differs from manifest')
    outcome_epoch.assert_current(manifest['expected_outcome_snapshot'])
    proof = d151.change_proof(manifest, run_dir/'CODE.READY.json', source_pins(), outcome_epoch.epoch_id(epoch))
    d151.write(run_dir/'change-proof.json', proof)
    if not proof['passed']:
        raise RuntimeError(f'change proof failed: {proof}')
    coverage = check_coverage(manifest, Path(manifest.get('d151_best', HERE/'best-d151')))
    admitted = admit(manifest, launch, run_dir)
    target = run_dir.parent/'best-d172'
    temporary = stage(manifest, launch, admitted, run_dir, target)
    report = audit(temporary, dict(manifest, launch_id=launch['launch_id']), run_dir, workers, seed)
    outcome_epoch.assert_current(epoch)
    proof = d151.change_proof(manifest, run_dir/'CODE.READY.json', source_pins(), outcome_epoch.epoch_id(epoch))
    d151.write(run_dir/'change-proof.json', proof)
    if not proof['passed']:
        raise RuntimeError(f'post-audit change proof failed: {proof}')
    text = summary(manifest, launch, admitted, coverage, report)
    (temporary/'summary.md').write_text(text)
    completion = dict(coverage, complete=True, exported=len(admitted), excluded=len(manifest.get('authorized_exclusions', [])),
                      excluded_cases=sorted(authorized_exclusions(manifest)),
                      exported_small=sum(item['entry']['rung']=='small' for item in admitted),
                      exported_tiny=sum(item['entry']['rung']=='tiny' for item in admitted), uuid=manifest['uuid'],
                      launch_id=launch['launch_id'], audit_passed=True, best_directory=str(target))
    d151.write(temporary/'completion.json', completion)
    if target.exists():
        raise RuntimeError(f'refusing to overwrite existing export: {target}')
    temporary.rename(target)
    (run_dir/'summary.md').write_text(text)
    d151.write(run_dir/'completion.json', completion)
    d151.write(run_dir/'COMPLETE.json', completion)
    (run_dir/'leader-report.md').write_text(
        f'D172 complete: {len(admitted)} exported maps, including {completion["exported_small"]} small maps; '
        f'{completion["excluded"]} leader-authorized exclusions; blocking audit 30/30.\n\n'
        'Output: ../best-d172/. Per-ship counts and two-step wins: summary.md. '
        'CPU only; no dataset construction or training. Assumption: retain D151 gold_only winners.\n')
    (run_dir/'FAILED.json').unlink(missing_ok=True)
    print(completion, flush=True)


def wait_for_workers(run_dir):
    """Receipts precede interpreter teardown; audit starts only after recorded workers exit."""
    path = Path(run_dir)/'pids.txt'
    pids = [int(value) for value in path.read_text().split()] if path.exists() else []
    while pids:
        remaining = []
        for pid in pids:
            if pid <= 0:
                raise RuntimeError('invalid recorded worker PID')
            try:
                os.kill(pid, 0)  # Existence check only. Never stop a worker or another session.
                remaining.append(pid)
            except ProcessLookupError:
                pass
        pids = remaining
        if pids:
            time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run_dir', type=Path)
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--seed', type=int, default=172)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8:
        parser.error('--workers must be 1..8')
    os.environ.update({name: '1' for name in d151.THREADS})
    os.environ.pop('STOWAGE_BALLAST_V4', None)
    try:
        finish(args.run_dir, args.workers, args.seed)
    except Exception as error:
        d151.write(args.run_dir/'FAILED.json', dict(complete=False, error=f'{type(error).__name__}: {error}'))
        raise


if __name__ == '__main__':
    main()
