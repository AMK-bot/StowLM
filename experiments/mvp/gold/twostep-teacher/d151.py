"""D151/D152 gates for the 8.3 teacher: run manifest, row admission, change proof, fresh export. No solver work here.

The manifest names exactly 8.2's 1,253 train maps: tiny and non-warmstart small maps get both teachers ('full'), small
gold_warmstart maps only re-verify gold ('gold_only', D151: their gold is never worse). 8.2's val rows can't enter it.
Admission trusts no file because it exists: a row must match the manifest's epoch, input hash and gold plan hash, every
shard of the current launch must have a receipt, and each winner's stored report must equal the recorded cost.
Usage: d151.py manifest | d151.py admit
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
V2 = ROOT/'experiments/mvp/sft/stowlm-v2'
TRAIN_RUNG = {'tiny': 713, 'small': 540}
VAL_RUNG = {'tiny': 40, 'small': 40}
MAX_EXCLUDED_FRACTION = .03
OUTCOME_FILES = ('pyproject.toml', 'uv.lock', 'experiments/mvp/gold_tools/driver.py',
                 *(f'models/glimmer-30b-4bit/{name}' for name in ('config.json', 'generation_config.json', 'tokenizer.json',
                                                                  'tokenizer_config.json', 'model.safetensors.index.json')))
OUTCOME_TREES = ('src', 'params', 'data')
THREADS = ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True, indent=1)+'\n')
    temporary.replace(path)


def key(case):
    return case.replace('/', '__')


def v2_rows(split):
    with open(V2/f'data/{split}.jsonl') as stream:
        return [json.loads(line) for line in stream]


def v2_case(row):
    return str(Path(row['meta']['gold']['source'].split('experiments/mvp/gold/', 1)[1]).parent)


def make_manifest(rows, epoch, checker, run_start, val_cases, gold_root=ROOT/'experiments/mvp/gold', root=ROOT):
    entries = []
    for row in rows:
        case = v2_case(row)
        result = read(gold_root/case/'result.json')
        if result.get('passed') is not True:
            raise RuntimeError(f'8.2 row whose gold no longer passes: {case}')
        warm = result['rung'] == 'small' and result['solver'] == 'solver.gold_warmstart'
        entries.append(dict(case=case, instance_id=row['instance_id'], rung=result['rung'], ship=result['ship'],
                            family=result['family'], solver=result['solver'], policy=result.get('policy'),
                            input=result['input_path'], input_sha256=sha(root/result['input_path']),
                            gold_plan_sha256=sha(gold_root/case/'plan.json'), stored_moves=result['objective'],
                            mode='gold_only' if warm else 'full'))
    overlap = {e['case'] for e in entries} & set(val_cases)
    if overlap:
        raise RuntimeError(f'8.2 val maps in the 8.3 train list: {sorted(overlap)[:3]}')
    order = {'tiny': 0, 'full': 1, 'gold_only': 2}
    entries.sort(key=lambda e: (order['tiny' if e['rung'] == 'tiny' else e['mode']], hashlib.sha256(e['case'].encode()).hexdigest()))
    return dict(schema='d151-teacher-manifest-v1', uuid=str(uuid.uuid4()), outcome_epoch=epoch, checker_epoch=checker,
                budget_s=120., run_start=run_start, entries=entries)


def candidate_path(here, entry, source):
    return Path(here)/'candidates'/key(entry['case'])/f'{source}.json'


def row_problem(row, entry, manifest, here=HERE):
    """Why a stored row can't be used for this manifest entry; None if it can. A failed check is a result; an
    unavailable checker, a finisher time limit or a crash is not, so those are retried."""
    if row.get('crashed'):
        return 'crashed'
    for name, expected in (('outcome_epoch', manifest['outcome_epoch']), ('checker_epoch', manifest['checker_epoch']),
                           ('input_sha256', entry['input_sha256']), ('budget_s', manifest['budget_s'])):
        if row.get(name) != expected:
            return f'{name} differs'
    teachers = (('gold', 'gold'),) if entry['mode'] == 'gold_only' else (('gold', 'gold'), ('two_step', 'baseline_twostep'))
    for name, source in teachers:
        detail = row.get(name)
        if not detail or not detail.get('attempted'):
            return f'{name} incomplete'
        if detail.get('unavailable') or detail.get('finisher_timeout'):
            return f'{name} not a verdict'
        if detail.get('passed'):
            path = candidate_path(here, entry, source)
            if not path.exists():
                return f'{name} candidate missing'
            candidate = read(path)
            if (candidate['input_sha256'] != entry['input_sha256'] or candidate['weighted_moves'] != detail['moves']
                    or candidate['report']['count']['weighted_nonproductive'] != detail['moves']):
                return f'{name} candidate disagrees'
    return None


def teacher(row, entry):
    """(source, costs). gold_only maps keep gold; otherwise fewer verified moves, gold on a tie."""
    costs = {name: (row.get(name) or {}).get('moves') if (row.get(name) or {}).get('passed') else None
             for name in ('gold', 'two_step')}
    if entry['mode'] == 'gold_only':
        return ('gold' if costs['gold'] is not None else None), costs
    live = {k: v for k, v in costs.items() if v is not None}
    if not live:
        return None, costs
    return ('two_step' if 'gold' not in live or ('two_step' in live and live['two_step'] < live['gold']) else 'gold'), costs


def change_proof(manifest, code_ready, current_pins, current_epoch, root=ROOT, trees=OUTCOME_TREES, files=OUTCOME_FILES):
    """D151 (b): closes the in-process checker's change-and-revert window with mtimes plus hashes from before the run."""
    newest, path = 0., None
    paths = [p for tree in trees for p in (Path(root)/tree).rglob('*')
             if p.is_file() and '__pycache__' not in p.parts] + [Path(root)/f for f in files]
    for item in paths:
        stamp = item.stat().st_mtime
        if stamp > newest:
            newest, path = stamp, item
    ready = read(code_ready)
    proof = dict(files_scanned=len(paths), newest_mtime=newest, newest_path=str(path), run_start=manifest['run_start'],
                 unchanged_since_run_start=newest < manifest['run_start'],
                 code_ready=str(code_ready), code_ready_utc=ready['utc'], source_pins_equal_code_ready=ready['source_pins'] == current_pins,
                 outcome_epoch_current=current_epoch == manifest['outcome_epoch'])
    proof['passed'] = (proof['unchanged_since_run_start'] and proof['source_pins_equal_code_ready']
                       and proof['outcome_epoch_current'])
    return proof


def admit(manifest, launch, here=HERE, gold_root=ROOT/'experiments/mvp/gold', gold_cutoff=None):
    """One decision per expected map: admitted winners, and every other map listed with its reason."""
    here = Path(here)
    assigned = set()
    for shard in range(launch['workers']):
        path = here/'receipts'/launch['launch_id']/f'shard-{shard}.json'
        receipt = read(path) if path.exists() else None
        if not receipt or receipt.get('ok') is not True or receipt.get('uuid') != manifest['uuid'] \
                or receipt.get('outcome_epoch') != manifest['outcome_epoch'] or receipt.get('shard') != shard:
            raise RuntimeError(f'no valid receipt for shard {shard} of launch {launch["launch_id"]}')
        assigned |= set(receipt['assigned'])
    expected = {e['case'] for e in manifest['entries']}
    if assigned != expected:
        raise RuntimeError(f'receipts cover {len(assigned)} maps, manifest has {len(expected)}')
    admitted, excluded = [], []
    for entry in manifest['entries']:
        path = here/'rows'/(key(entry['case'])+'.json')
        row = read(path) if path.exists() else None
        problem = 'missing' if row is None else row_problem(row, entry, manifest, here)
        plan = gold_root/entry['case']/'plan.json'
        if problem is None and (not plan.exists() or sha(plan) != entry['gold_plan_sha256']):
            problem = 'stored gold plan changed'
        source, costs = (None, {}) if problem else teacher(row, entry)
        if problem or source is None:
            excluded.append(dict(case=entry['case'], rung=entry['rung'], mode=entry['mode'], reason=problem or 'no verified teacher'))
        else:
            admitted.append(dict(entry=entry, source=source, costs=costs))
    return admitted, excluded


def export(manifest, admitted, target, here=HERE):
    """Into a fresh directory, published by one rename. Anything already at target is kept aside, never merged."""
    target, here = Path(target), Path(here)
    temporary = target.with_name(f'{target.name}.tmp-{manifest["uuid"]}')
    if temporary.exists():
        shutil.rmtree(temporary)
    for item in admitted:
        entry = item['entry']
        candidate = read(candidate_path(here, entry, 'gold' if item['source'] == 'gold' else 'baseline_twostep'))
        cost = item['costs'][item['source']]
        if candidate['weighted_moves'] != cost or candidate['input_sha256'] != entry['input_sha256']:
            raise RuntimeError(f"candidate disagrees: {entry['case']}")
        path = temporary/key(entry['case'])/'record.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(dict(
            instance_path=entry['input'], plan=candidate['plan'], report=candidate['report'],
            provenance=dict(teacher='D151 best verified plan', uuid=manifest['uuid'], source=item['source'], costs=item['costs'],
                            stored_gold_moves=entry['stored_moves'], gold_solver=entry['solver'], case=entry['case'],
                            input_sha256=entry['input_sha256'], outcome_epoch=manifest['outcome_epoch'],
                            checker_epoch=manifest['checker_epoch'], budget_s=manifest['budget_s'], mode=entry['mode'])),
            sort_keys=True)+'\n')
    if target.exists():
        target.rename(target.with_name(f'{target.name}.superseded-{int(time.time())}'))
    temporary.rename(target)


def cmd_manifest():
    from llm.exit import outcome_epoch
    from llm.exit.replay import checker_epoch
    epoch = outcome_epoch.epoch_id(outcome_epoch.process_snapshot())
    first = min(p.stat().st_mtime for p in (HERE/'rows').glob('*.json'))-600
    manifest = make_manifest(v2_rows('train'), epoch, checker_epoch(), first, {v2_case(r) for r in v2_rows('val')})
    if {r: sum(e['rung'] == r for e in manifest['entries']) for r in TRAIN_RUNG} != TRAIN_RUNG:
        raise SystemExit('manifest is not 713 tiny + 540 small')
    write(HERE/'d151-manifest.json', manifest)
    print(manifest['uuid'], epoch, {m: sum(e['mode'] == m for e in manifest['entries']) for m in ('full', 'gold_only')})


def cmd_admit():
    from llm.exit import outcome_epoch
    from llm.exit.runner import source_pins
    manifest, launch = read(HERE/'d151-manifest.json'), read(HERE/'launch.json')
    proof = change_proof(manifest, ROOT/'experiments/mvp/exit/deployment-d137/D137-CODE.READY', source_pins(),
                         outcome_epoch.epoch_id(outcome_epoch.snapshot()))
    if not proof['passed']:
        write(HERE/'change-proof.json', proof)
        raise SystemExit(f'change proof failed: {proof}')
    admitted, excluded = admit(manifest, launch)
    fraction = len(excluded)/len(manifest['entries'])
    summary = dict(uuid=manifest['uuid'], launch_id=launch['launch_id'], admitted=len(admitted), excluded=excluded,
                   excluded_fraction=fraction, change_proof=proof)
    write(HERE/'admitted.json', dict(summary, rows=[dict(case=a['entry']['case'], source=a['source'], costs=a['costs'],
                                                         rung=a['entry']['rung'], ship=a['entry']['ship'],
                                                         solver=a['entry']['solver'], mode=a['entry']['mode']) for a in admitted]))
    if fraction > MAX_EXCLUDED_FRACTION:
        raise SystemExit(f'{len(excluded)} maps excluded ({fraction:.1%} > 3 %): tell the leader before training')
    export(manifest, admitted, HERE/'best-d151')
    print(json.dumps(dict(admitted=len(admitted), excluded=len(excluded))))


if __name__ == '__main__':
    os.environ.update({name: '1' for name in THREADS})  # part of the outcome epoch: the same as the workers'
    {'manifest': cmd_manifest, 'admit': cmd_admit}[sys.argv[1]]()
