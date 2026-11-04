"""Current-screen audit with explicitly attributed recorded full-check reuse."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import time

from experiments.mvp.pipeline.contract.schema import sha256_file, sha256_json
from .benchmark import HERE, ROOT, read_fixture
from .clear_benchmark import candidates, placements_hash, record, summary, _write, _admit_measurement


def unchanged_full_path(current_source, original_source):
    """Ignore only the preliminary screen; every proposal/full-path AST stays."""
    def retained(source):
        module = ast.parse(source)
        module.body = [node for node in module.body if not (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == '_screen')]
        return ast.dump(module, include_attributes=False)
    try:
        return retained(current_source) == retained(original_source)
    except SyntaxError:
        return False


def reusable(row, original, source):
    """Require pinned checker identity; never choose based on the full outcome."""
    if original['source_sha256'] != sha256_file(source):
        return False
    session = original.get('measurement_sessions', {}).get(row.get('measurement_session'))
    if not session:
        return False
    from stowage.versions import component_versions
    if session['component_versions'] != component_versions():
        return False
    if row['code_sha256']['moves.py'] != sha256_file(HERE/'moves.py'):
        archives = [p for p in HERE.glob('fixtures/screen*/moves.py.txt')
                    if sha256_file(p) == row['code_sha256']['moves.py']]
        if not any(unchanged_full_path((HERE/'moves.py').read_text(), archived.read_text())
                   for archived in archives):
            return False
    for name, digest in session['source_sha256'].items():
        if name.startswith('src/stowage/checker/') or name == 'src/stowage/versions.py':
            if sha256_file(ROOT/name) != digest:
                return False
    return True


def discover(original_path, source, output, count=40, minimum_each=10):
    from stowage.model import Instance, MovePlan
    from .moves import prepare_move, _screen
    original = read_fixture(original_path)
    if not original['complete']:
        raise ValueError('Original audit must finish before the current-screen audit')
    fixture = read_fixture(source)
    instance, plan = Instance.model_validate(fixture['instance']), MovePlan.model_validate(fixture['plan'])
    pinned = {p.name: sha256_file(p) for p in HERE.glob('*.py') if not p.name.startswith('test_')}
    selected, reused_rows, screened, counts, empty_counts, seen, states = [], [], [], Counter(), Counter(), set(), set()
    prior_fast_rejected = {(r['box_id'], r['target_slot'])
                           for r in original.get('rescreened_original', []) if not r['current_fast']['legal']}
    data = {'synthetic': True, 'mode': 'recorded', 'label': 'Recorded development current-screen audit',
            'created_utc': datetime.now(timezone.utc).isoformat(), 'n_boxes': len(instance.load_list),
            'source_fixture': source.name, 'source_sha256': sha256_file(source),
            'source_result': fixture['source_result'], 'original_audit': original_path.name,
            'original_audit_sha256': sha256_file(original_path), 'fast_source_sha256': pinned,
            'method': 'Current fast screens choose the cohort. Original full outcomes never select it. '
                      'Recorded full verdicts are reused only with the identical source fixture, unchanged proposal/full-path AST, '
                      'checker files and component versions, and reproduced requested/reached placement identities. '
                      'Rows without pre-check source pins are not reusable. Additional clear requests '
                      'receive fresh full checks. Reused full latency remains its historical API latency.',
            'reuse_identity_note': 'The archived move module must match each original row hash; the current module AST '
                                  'must match after removing only _screen. Candidate plans are reconstructed from the '
                                  'same pinned base and unchanged proposal/full path; original rows stored placement '
                                  'identities, not candidate-plan hashes.',
            'selection_version': 1, 'empty_drags_per_box_limit': 2,
            'prior_fast_negative_prefilter': [list(r) for r in sorted(prior_fast_rejected)],
            'measurement_sessions': original.get('measurement_sessions', {}),
            'selected': selected, 'moves': reused_rows, 'rescreened_original': screened,
            'discovery_complete': False, 'complete': False}

    def select(kind, request, prepared, fast, load, old=None):
        if counts[kind] >= count-minimum_each:
            return
        if kind == 'empty' and empty_counts[request[0]] >= 2:
            return
        requested, reached = placements_hash(prepared.ship), placements_hash(prepared.replay.final_state.ship)
        if requested != reached or requested in states:
            return
        item = {'kind': kind, 'box_id': request[0], 'target_slot': request[1],
                'candidate_plan_sha256': sha256_json(prepared.plan.model_dump(mode='json')),
                'instance_sha256': sha256_json(instance.model_dump(mode='json')),
                'requested_state_sha256': requested, 'reached_state_sha256': reached,
                'screen_s': fast.wall_s, 'selection_admission': load}
        selected.append(item)
        counts[kind] += 1
        if kind == 'empty':
            empty_counts[request[0]] += 1
        seen.add(request)
        states.add(requested)
        if old is not None:
            if old['requested_state_sha256'] != requested or old['reached_state_sha256'] != reached:
                raise ValueError('Recorded full placement identities do not reproduce')
            if old.get('candidate_plan_sha256', item['candidate_plan_sha256']) != item['candidate_plan_sha256']:
                raise ValueError('Recorded candidate plan hash does not reproduce')
            copied = dict(old, **item, fast=fast.model_dump(mode='json'), fast_s=fast.wall_s,
                          fast_source_sha256=pinned,
                          full_origin='recorded_full_reuse', full_measured_now=False,
                          original_full_s=old['full_s'], original_fast_s=old['fast_s'],
                          original_full_audit=original_path.name)
            reused_rows.append(copied)
        _write(output, data)
        print({'selected': len(selected), 'kinds': dict(counts), 'recorded_full_reused': len(reused_rows)}, flush=True)

    # Replay-screen every original request, including original full failures.
    for old in original['moves']:
        request = old['box_id'], old['target_slot']
        load = _admit_measurement()
        started = time.perf_counter()
        prepared = prepare_move(instance, plan, *request)
        fast = _screen(prepared, started)
        eligible = reusable(old, original, source)
        screened.append({'box_id': request[0], 'target_slot': request[1], 'current_fast': fast.model_dump(mode='json'),
                         'recorded_full_eligible': eligible})
        if fast.legal and prepared.replay.legal and eligible:
            select(old['kind'], request, prepared, fast, load, old)
        if not fast.legal:
            prior_fast_rejected.add(request)
        _write(output, data)

    for kind, request in candidates(instance, plan):
        if len(selected) >= count and min(counts.values()) >= minimum_each:
            break
        if request in seen or request in prior_fast_rejected or counts[kind] >= count-minimum_each:
            continue
        if kind == 'empty' and empty_counts[request[0]] >= 2:
            continue
        load = _admit_measurement()
        started = time.perf_counter()
        prepared = prepare_move(instance, plan, *request)
        if not prepared.replay.legal:
            continue
        fast = _screen(prepared, started)
        if fast.legal:
            select(kind, request, prepared, fast, load)
    if len(selected) < count or any(counts[k] < minimum_each for k in ('swap', 'empty')):
        raise ValueError(f'Not enough current clears: {dict(counts)}')
    if any(sha256_file(HERE/name) != digest for name, digest in pinned.items()):
        raise RuntimeError('Fast-screen source changed during discovery; rerun this audit')
    data.update(discovery_complete=True, reused_full_count=len(reused_rows),
                fresh_full_needed=len(selected)-len(reused_rows))
    if reused_rows:
        data['summary'] = summary(reused_rows)
    _write(output, data)
    return data


def finish(source, output, workers=4):
    data = read_fixture(output)
    if not data['discovery_complete']:
        raise ValueError('Finish discovery before measuring new requests')
    if any(sha256_file(HERE/name) != digest for name, digest in data['fast_source_sha256'].items()):
        raise ValueError('Current fast-screen source changed after discovery')
    data = record(source, output, workers)
    for row in data['moves']:
        row.setdefault('fast_source_sha256', data['fast_source_sha256'])
        row.setdefault('full_origin', 'fresh_full_check')
        row.setdefault('full_measured_now', True)
    data['fresh_full_count'] = sum(r['full_measured_now'] for r in data['moves'])
    _write(output, data)
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('discover', 'record'))
    parser.add_argument('--source', type=Path, default=HERE/'fixtures/envelope190.json')
    parser.add_argument('--original', type=Path, default=HERE/'fixtures/clear190.json')
    parser.add_argument('--output', type=Path, default=HERE/'fixtures/clear190-current.json')
    parser.add_argument('--workers', type=int, choices=(1, 2, 3, 4), default=4)
    args = parser.parse_args()
    if args.phase == 'discover':
        discover(args.original, args.source, args.output)
    else:
        finish(args.source, args.output, args.workers)


if __name__ == '__main__':
    main()
