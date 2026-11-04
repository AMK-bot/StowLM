"""One serial load-free worker for medium, rule-masked, and descriptive evidence.

Reads stored TRAIN ladder / VAL small plans only. No finisher, repair, solver
deadline, GPU, or writes outside skill-check/. Source-bound rows are resumable.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
OUT = HERE/'extensions'
for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS',
            'NUMEXPR_NUM_THREADS','BLIS_NUM_THREADS','RAYON_NUM_THREADS','POLARS_MAX_THREADS'):
    os.environ[key] = '1'
os.environ.update(PYTHONDONTWRITEBYTECODE='1', UV_CACHE_DIR=str(ROOT/'.uv-cache'),
                  TMPDIR=str(HERE/'tmp'), CUDA_VISIBLE_DEVICES='', TOKENIZERS_PARALLELISM='false')
sys.dont_write_bytecode = True
sys.path.insert(0, str(HERE))

import part1 as engine
from medium_jobs import inventory
from small_jobs import jobs as small_jobs
from extension_reports import render_medium, render_rule, render_curves
from describe_plans import describe, render_description
from compose_extensions import compose
from stowage.io import load_instance
from stowage.model import MovePlan

engine.HERE = OUT/'proofs'
engine.HERE.mkdir(parents=True, exist_ok=True)
# Reuse the already independently validated exact-argument memo algorithm.
# Unlike Part 1's first-candidate validation, do not require a medium loading
# condition to exist: the unchanged gate must report that failure itself.
engine.VALIDATED_MEMO = True


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    temp.write_text(json.dumps(value, indent=1, sort_keys=True)+'\n')
    temp.replace(path)


def textfile(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    temp.write_text(text)
    temp.replace(path)


def source_inventory():
    old = json.loads((HERE/'source-hashes.json').read_text())
    inherited_changed = [rel for rel, sha in old.items() if digest(ROOT/rel) != sha]
    if inherited_changed:
        raise RuntimeError(f'inherited deterministic epoch changed: {inherited_changed[:6]}')
    old.update({str(p.relative_to(ROOT)): digest(p) for p in (
        HERE/'extended_audit.py', HERE/'medium_jobs.py', HERE/'small_jobs.py', HERE/'describe_plans.py',
        HERE/'extension_reports.py')})
    for rel, row in json.loads((HERE/'config-data-baseline.json').read_text()).get('files', {}).items():
        if digest(ROOT/rel) != row['sha256']:
            raise RuntimeError(f'inherited configuration changed: {rel}')
        old[rel] = row['sha256']
    for base in (ROOT/'src/stowage/rules', ROOT/'params'):
        for p in base.rglob('*'):
            if p.is_file() and p.suffix in ('.toml','.json','.csv','.npz','.npy','.fix'):
                old[str(p.relative_to(ROOT))] = digest(p)
    for p in (ROOT/'data/raw').rglob('*.fix'):
        old[str(p.relative_to(ROOT))] = digest(p)
    # Verify inherited outcome sources before accepting any previous row.
    changed = [rel for rel, sha in old.items() if digest(ROOT/rel) != sha]
    if changed:
        raise RuntimeError(f'upstream source changed: {changed[:6]}')
    write(OUT/'source-hashes.json', old)
    return old, hashlib.sha256(json.dumps(old,sort_keys=True).encode()).hexdigest()


def assert_sources(sources):
    changed = [rel for rel, sha in sources.items() if digest(ROOT/rel) != sha]
    if changed:
        raise RuntimeError(f'bound sources/configuration changed: {changed[:6]}')


def row_path(job):
    arm = job['arm'].replace(' ','_').replace('/','_')
    sample = 'na' if job['sample'] is None else f'{job["sample"]:02d}'
    return OUT/'rows'/job['kind']/arm/job['instance_id']/f'sample-{sample}.json'


def guard_job(job):
    rel = job['input']
    if '/test/' in rel or 'test-v3' in rel or not ('/corpus/val/' in rel or '/maps/train/' in rel):
        raise ValueError(f'input outside authorized train/validation scope: {rel}')
    if digest(ROOT/rel) != job['input_sha256'] or digest(ROOT/job['source']) != job['source_sha256']:
        raise RuntimeError(f'input or source changed: {rel} / {job["source"]}')
    if job.get('prepared_path') and digest(ROOT/job['prepared_path']) != job['chosen_sha256']:
        raise RuntimeError('prepared chosen plan changed')
    if job.get('audit_row') and digest(ROOT/job['audit_row']) != job['audit_row_sha256']:
        raise RuntimeError('original audit row changed')


def main():
    OUT.mkdir(exist_ok=True)
    lock = (OUT/'worker.lock').open('a+')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    marker = dict(pid=os.getpid(), started_at=stamp(), status='running', workers=1, native_threads=1)
    write(HERE/'extensions-active.json', marker)
    write(OUT/'launch.json', marker)
    sources, source_sha = source_inventory()
    inv = inventory()
    write(OUT/'medium-inventory.json', inv)
    medium = [dict(job, kind='medium', complete=True) for job in inv['jobs']]
    rule = small_jobs('rule0')
    descriptions = small_jobs('describe')
    curves = small_jobs('curves')
    groups = [('medium', medium), ('rule0', rule), ('describe', descriptions), ('curves', curves)]
    for kind, group in groups:
        for job in group:
            job['kind'] = kind
        write(OUT/f'{kind}-manifest.json', [dict((k,v) for k,v in job.items() if k != 'plan') for job in group])
    results = {kind: [] for kind,_ in groups}
    instances = OrderedDict()
    memo = {}

    def state(kind, done, total):
        row = dict(status='running', stage=kind, processed=done, expected=total, pid=os.getpid(), updated_at=stamp())
        write(OUT/'status.json', row)
        textfile(OUT/'status.md', '## Additional deterministic evidence\n\n'
                 f'Serial load-free stage `{kind}`: {done}/{total} stored rows processed, as of {row["updated_at"]}. '
                 'Missing plans remain visible; unavailable arms and unprocessed rows are not gate failures. '
                 'Part 2 continues unchanged. Supplemental original samples yield to this worker.\n')

    def render(kind):
        if kind == 'medium':
            textfile(OUT/'part3.md', render_medium(inv, results[kind]))
        elif kind == 'rule0':
            textfile(OUT/'rule-masked.md', render_rule(results[kind]))
        elif kind == 'describe':
            textfile(OUT/'small-description.md', render_description(results[kind]))
        else:
            textfile(OUT/'sample-curves.md', render_curves(results[kind]))
        compose()

    for kind, group in groups:
        # Sample-index ordering publishes complete per-index cohorts promptly.
        group.sort(key=lambda j:(j['sample'] if j['sample'] is not None else -1,j['input'],j['arm']))
        state(kind,0,len(group))
        for index, job in enumerate(group):
            guard_job(job)
            binding = dict(input_sha256=job['input_sha256'], source_sha256=job['source_sha256'],
                           source=job['source'], deterministic_sources_sha256=source_sha,
                           default_tanks=True, compaction='kcurve strict-cheaper', sample=job['sample'])
            if kind == 'describe':
                binding.update(chosen_sha256=job['chosen_sha256'],audit_row_sha256=job['audit_row_sha256'])
            path = row_path(job)
            previous = json.loads(path.read_text()) if path.exists() else None
            if previous and previous.get('binding') == binding and not previous.get('error'):
                results[kind].append(previous)
                continue
            row = {k:v for k,v in job.items() if k != 'plan'}
            row.update(binding=binding, error=None, complete=job.get('plan') is not None)
            reused = None
            if kind == 'curves' and job['arm'] == '8.2':
                legacy = HERE/'rows'/'8.2'/job['instance_id']/f'sample-{job["sample"]:02d}.json'
                if legacy.exists():
                    candidate = json.loads(legacy.read_text())
                    old = candidate.get('binding', {})
                    if (not candidate.get('error') and old.get('input_sha256') == job['input_sha256']
                            and old.get('source_sha256') == job['source_sha256']):
                        reused = candidate
                        row['reused_from'] = str(legacy.relative_to(ROOT))
            elif kind == 'curves' and job['arm'] == '8.2 rule' and job['sample'] == 0:
                legacy = row_path(dict(job, kind='rule0'))
                if legacy.exists():
                    candidate = json.loads(legacy.read_text())
                    if candidate.get('binding') == binding and not candidate.get('error'):
                        reused = candidate
                        row['reused_from'] = str(legacy.relative_to(ROOT))
            try:
                if reused is not None:
                    for field in ('compacted','compaction','cost','signature','chosen_sha256','move_legal','load_complete',
                                  'missing_loads','sight_clear','sight','static_ok','static_rules','static_ran','static_error',
                                  'frontier_mask','all_clear'):
                        if field in reused:
                            row[field] = reused[field]
                elif row['complete']:
                    rel = job['input']
                    if rel not in instances:
                        instance = load_instance(ROOT/rel)
                        instances[rel] = (instance, None)
                        if len(instances)>3:
                            instances.popitem(last=False)
                        memo.clear()
                    instance, problem = instances[rel]
                    plan = MovePlan.model_validate(job['plan']).model_copy(update={'ballast':None})
                    if kind == 'describe':
                        row['description'] = describe(instance, plan)
                    else:
                        compacted, info = engine.compact.compact_plan(instance, plan)
                        better = compacted is not None and info['compact_raw']['total'] < info['before']['total']
                        chosen = (compacted if better else plan).model_copy(update={'ballast':None})
                        signature = hashlib.sha256(json.dumps([m.model_dump(mode='json') for m in chosen.moves], sort_keys=True).encode()).hexdigest()
                        row.update(cost=info['compact_raw']['total'] if better else info['before']['total'],
                                   compacted=better, compaction=info, signature=signature,
                                   chosen_sha256=hashlib.sha256(chosen.model_dump_json().encode()).hexdigest())
                        key=(rel,row['chosen_sha256'])
                        if key not in memo:
                            if problem is None:
                                problem=engine.Problem(instance)
                                instances[rel]=(instance,problem)
                            memo[key]=engine.deterministic(instance,problem,chosen)
                        row.update(memo[key])
                guard_job(job)
            except Exception as error:
                row['error']=f'{type(error).__name__}: {error}'
            write(path,row)
            results[kind].append(row)
            state(kind,index+1,len(group))
            print(json.dumps(dict(stage=kind,done=index+1,total=len(group),arm=job['arm'],instance_id=job['instance_id'],sample=job['sample'],error=row['error'])),flush=True)
            if (index+1)%3==0 or index+1==len(group):
                render(kind)
        assert_sources(sources)
        write(OUT/f'{kind}-verification.json', dict(passed=True, checked_sources=len(sources),
              rows=len(results[kind]), errors=sum(bool(r.get('error')) for r in results[kind]),verified_at=stamp()))
        render(kind)
    write(OUT/'status.json',dict(status='complete',pid=os.getpid(),updated_at=stamp()))
    textfile(OUT/'status.md','## Additional deterministic evidence\n\nAll requested extension rows have been processed. See per-stage availability, error counts, source verification and sparse-cell qualifications below.\n')
    compose()
    (HERE/'extensions-active.json').unlink()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        write(OUT/'status.json',dict(status='failed',error=f'{type(error).__name__}: {error}',updated_at=stamp()))
        # Keep the priority marker after a failure: no second physics worker
        # should silently resume while a partially audited cohort is reviewed.
        raise
