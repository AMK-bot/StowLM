"""Immutable JSON inventory for D205 extensions; stdlib only, no physics.

All paths in job records are repository-relative. Missing raw candidates remain
explicit incomplete jobs so denominators never depend on stored successes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
K1B = HERE.parent
ROOT = HERE.parents[4]


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _relative(path):
    return str(path.relative_to(ROOT))


def _safe_input(relative):
    path = ROOT/relative
    assert '/corpus/val/' in '/'+relative, relative
    assert not any('test' in component.lower() for component in Path(relative).parts), relative
    assert path.resolve().is_relative_to(ROOT.resolve()), relative
    return path


def _maps():
    manifest = json.loads((K1B/'manifest.json').read_text())
    assert len(manifest['inputs']) == 55
    result, seen = [], set()
    for relative, expected in manifest['inputs'].items():
        raw = _safe_input(relative).read_bytes()
        assert _sha(raw) == expected, f'input changed: {relative}'
        iid = json.loads(raw)['id']
        assert iid not in seen, f'duplicate instance: {iid}'
        seen.add(iid)
        result.append(dict(instance_id=iid, input=relative, input_sha256=expected))
    return result


def _raw_job(kind, arm, folder, metadata, sample):
    path = folder/'candidates'/metadata['instance_id']/f'sample-{sample:02d}.json'
    job = dict(metadata, kind=kind, arm=arm, sample=sample,
               source=_relative(path), source_sha256=None, temperature=None,
               complete=False, plan=None, missing=not path.exists())
    if path.exists():
        raw = path.read_bytes()
        candidate = json.loads(raw)
        assert candidate['instance_id'] == metadata['instance_id'], path
        assert candidate['sample'] == sample, path
        job.update(source_sha256=_sha(raw), temperature=candidate.get('temperature'),
                   complete=candidate.get('plan') is not None, plan=candidate.get('plan'),
                   source_status=candidate.get('status'))
    return job


def jobs(kind):
    """Return fully bound jobs: rule0=110, curves=1760, describe=138.

    `describe` supplies already-selected prepared plans and verifies their hash
    against Part 1; all other kinds supply raw plans for the serial audit engine.
    """
    if kind not in ('rule0','curves','describe'):
        raise ValueError(f'unknown small job kind: {kind!r}')
    maps = _maps()
    if kind == 'rule0':
        arms = [('8.2 rule', K1B/'k1c'), ('random rule', K1B/'k1c-random')]
        return [_raw_job(kind, arm, folder, metadata, 0)
                for metadata in maps for arm,folder in arms]
    if kind == 'curves':
        arms = [('8.2',K1B), ('8.2 rule', K1B/'k1c')]
        return [_raw_job(kind, arm, folder, metadata, sample)
                for sample in range(16) for metadata in maps for arm,folder in arms]
    result = []
    for metadata in maps:
        for arm,folder in [('8.2',K1B),('null',K1B/'null'),('K1r',K1B/'k1r')]:
            job = _raw_job(kind, arm, folder, metadata, 0)
            row_path = HERE/'rows'/arm/metadata['instance_id']/'sample-00.json'
            row_raw = row_path.read_bytes()
            row = json.loads(row_raw)
            binding = row['binding']
            assert row['instance_id'] == metadata['instance_id'] and row['arm'] == arm and row['sample'] == 0
            for key in ('input','input_sha256','source','source_sha256'):
                assert binding[key] == job[key], (row_path,key)
            assert row['complete'] == job['complete'], row_path
            if not job['complete']:
                continue
            path = ROOT/row['prepared_path']
            assert path.resolve().is_relative_to((HERE/'prepared').resolve()), path
            raw = path.read_bytes()
            assert _sha(raw) == row['chosen_sha256'], f'prepared plan changed: {path}'
            plan = json.loads(raw)
            assert plan['instance_id'] == metadata['instance_id']
            job.update(plan=plan, sight_clear=row.get('sight_clear'),
                       prepared_path=_relative(path), chosen_sha256=_sha(raw),
                       audit_row=_relative(row_path), audit_row_sha256=_sha(row_raw),
                       audit_binding=binding, compacted=row.get('compacted'),
                       move_legal=row.get('move_legal'), load_complete=row.get('load_complete'))
            result.append(job)
    return result
