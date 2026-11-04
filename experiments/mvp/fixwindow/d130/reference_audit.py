"""D130 showcase-reference inventory and sequential, fresh full-checker audit.

Never reads test bodies, changes an existing result, or uses the app verdict cache.
Only exact full plan JSON is deduplicated; provenance and plan IDs are retained.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
FROZEN = {
    'random.small.29000201.0.7': 'feeder_1700',
    'random.small.29000022.0.7': 'feeder_500',
    'random.small.29000102.0.7': 'feeder_800',
}
CACHE = ROOT / 'experiments/mvp/fixwindow/d126-20261030/cache'


def utc():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.partial')
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n')
    temp.replace(path)


def source_epoch():
    paths = sorted(p for top in ('src', 'params') for p in (ROOT / top).rglob('*')
                   if p.is_file() and p.suffix in ('.py', '.toml', '.json'))
    paths.append(ROOT / 'data/raw/kcs/kcs.fix')
    mapping = {str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in paths}
    return {'sha256': digest(canonical(mapping)), 'files': mapping}


def json_bytes(path):
    data = path.read_bytes()
    return gzip.decompress(data) if path.suffix == '.gz' else data


def plans(value, pointer=()):
    if isinstance(value, dict):
        if value.get('instance_id') in FROZEN and isinstance(value.get('moves'), list):
            yield pointer, value
            return
        for key, item in value.items():
            yield from plans(item, (*pointer, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from plans(item, (*pointer, index))


def extract(path, pointer, line=None):
    data = json_bytes(path)
    value = json.loads(data if line is None else data.splitlines()[line])
    for key in pointer:
        value = value[key]
    return value


def inventory():
    report_path = ROOT / 'experiments/mvp/corpus_tools/EVAL-V2-REPORT.json'
    release = json.loads(report_path.read_text())
    frozen = {}
    for iid, ship in FROZEN.items():
        relative = f'showcase/{ship}-small.json'
        expected = release.get('hashes', {}).get(relative) or release.get('added_hashes', {}).get(relative)
        path = ROOT / 'experiments/mvp/corpus' / relative
        if not expected or digest(path.read_bytes()) != expected:
            raise ValueError(f'Frozen showcase hash mismatch: {relative}')
        data = json.loads(path.read_text())
        assert data['id'] == iid
        frozen[iid] = {'path': str(path.relative_to(ROOT)), 'sha256': expected,
                       'semantic_sha256': digest(canonical(data))}
    references, errors, scanned, pruned = [], [], [], []
    for run in sorted((ROOT / 'experiments/mvp/runs').glob('showcase-*')):
        if not run.is_dir():
            continue
        if any(x in run.name.lower() for x in ('cache', 'timeline')):
            pruned.append(str(run.relative_to(ROOT)))
            continue
        for directory, dirs, files in os.walk(run):
            dirs.sort()
            for name in list(dirs):
                if any(x in name.lower() for x in ('cache', 'timeline', 'frames')):
                    dirs.remove(name)
                    pruned.append(str((Path(directory) / name).relative_to(ROOT)))
            for name in sorted(files):
                if not name.endswith(('.json', '.json.gz', '.jsonl', '.jsonl.gz')):
                    continue
                if any(x in name.lower() for x in ('cache', 'timeline', 'frames', '.d130-')):
                    continue
                path = Path(directory) / name
                scanned.append(str(path.relative_to(ROOT)))
                try:
                    data = json_bytes(path)
                    objects = ([(line, json.loads(raw)) for line, raw in enumerate(data.splitlines()) if raw.strip()]
                               if '.jsonl' in name else [(None, json.loads(data))])
                    for line, value in objects:
                        for pointer, plan in plans(value):
                            iid = plan['instance_id']
                            instance_path = ROOT / frozen[iid]['path']
                            # A same-ID stored instance is checked for exact frozen equivalence.
                            local_path = None
                            for parent in [path.parent, *path.parent.parents]:
                                candidate = parent / 'instance.json'
                                if candidate.is_file():
                                    local_path = candidate
                                    break
                                if parent == run:
                                    break
                            local = json.loads(local_path.read_text()) if local_path else None
                            local_matches = (local is None or
                                digest(canonical(local)) == frozen[iid]['semantic_sha256'])
                            direct = digest(canonical(plan))
                            ref = {'path': str(path.relative_to(ROOT)), 'pointer': list(pointer), 'line': line,
                                   'file_sha256': digest(path.read_bytes()), 'instance_id': iid,
                                   'ship': FROZEN[iid], 'input': str(instance_path.relative_to(ROOT)),
                                   'input_sha256': frozen[iid]['sha256'], 'plan_sha256': direct,
                                   'moves': len(plan['moves']), 'ballast_present': plan.get('ballast') is not None,
                                   'local_instance': str(local_path.relative_to(ROOT)) if local_path else None,
                                   'local_instance_matches_frozen': local_matches}
                            ref['ref_id'] = digest(canonical([ref['path'], ref['pointer'], line]))[:16]
                            ref['group'] = digest(canonical([ref['input_sha256'], direct]))
                            references.append(ref)
                except Exception as error:
                    errors.append({'path': str(path.relative_to(ROOT)), 'error': f'{type(error).__name__}: {error}'})
    groups = {}
    for ref in references:
        groups.setdefault(ref['group'], []).append(ref['ref_id'])
    record = {'created_utc': utc(), 'frozen': frozen, 'references': references,
              'groups': groups, 'scanned': scanned, 'pruned': pruned, 'errors': errors,
              'dedupe': 'exact canonical full plan JSON plus exact frozen input; IDs/provenance retained',
              'advertised': {'source': 'app/stage_extras.py VIDEO_SHOWCASE; experiments/mvp/ui/RECORDING.md',
                  'primary': 'showcase-v2-gold', 'rival': 'showcase-v2-twostep',
                  'calls': ['random.small.29000201.0.7', 'random.small.29000022.0.7'],
                  'feeder_800_registered': False}}
    save(OUT / 'inventory.json', record)
    print(json.dumps({'references': len(references), 'groups': len(groups), 'errors': errors,
                      'local_mismatches': [r['path'] for r in references if not r['local_instance_matches_frozen']]}))


def configure_caches():
    """Reuse qualified bytes; new cache writes stay in this lane's own directory."""
    own = OUT / 'cache'
    stability = own / 'stability'
    stability.mkdir(parents=True, exist_ok=True)
    for source in (CACHE / 'stability').iterdir():
        target = stability / source.name
        if source.is_file() and not target.exists() and not target.is_symlink():
            target.symlink_to(source.resolve())
    # Full-vessel files are few, exact content-addressed public-builder outputs.
    vessel = own / 'vessels'
    vessel.mkdir(exist_ok=True)
    original = Path(os.environ.get('STOWAGE_VESSEL_CACHE_DIR', ROOT / 'experiments/cache/vessels'))
    for source in original.glob('*.json'):
        target = vessel / source.name
        if source.is_file() and not target.exists() and not target.is_symlink():
            target.symlink_to(source.resolve())
    os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(vessel)
    import stowage.linear.cache as linear
    import stowage.checker.stability_tables as tables
    import stowage.checker.stability_liquid_tables as liquid
    import stowage.checker.stability_liquid as liquid_direct
    import stowage.checker.stability_sounding as sounding
    linear._DEFAULT_ROOT = CACHE / 'linear'  # read-only; never used as verdict evidence
    for module in (liquid, liquid_direct, sounding):
        module.CACHE_DIRECTORY = stability
    tables.CACHE_DIR = stability
    for name in ('load_table_geometry', 'cached_geometry', 'migrate_legacy'):
        function = getattr(tables, name)
        if function.__kwdefaults__ and 'cache_dir' in function.__kwdefaults__:
            function.__kwdefaults__['cache_dir'] = stability


def write_fence():
    """Keep provider cache misses private; inherited caches are read-only."""
    def allowed(path, follow=False):
        if isinstance(path, int) or path is None:
            return
        value = Path(os.fsdecode(path))
        value = value.resolve() if follow else Path(os.path.abspath(value))
        if value.is_relative_to(OUT):
            return
        if (value.parent.is_relative_to(ROOT / 'experiments/mvp/runs')
                and '.d130-' in value.name
                and value.relative_to(ROOT / 'experiments/mvp/runs').parts[0].startswith('showcase-')):
            return
        raise PermissionError(f'D130 read-only dependency/cache: {value}')
    def audit(event, args):
        if event == 'open':
            mode, flags = args[1], args[2]
            if (isinstance(mode, str) and any(c in mode for c in 'wax+')) or (
                    isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
                allowed(args[0], follow=True)
        elif event in ('os.rename', 'os.replace'):
            allowed(args[0]); allowed(args[1])
        elif event in ('os.mkdir', 'os.remove', 'os.rmdir', 'os.unlink', 'os.chmod'):
            allowed(args[0])
        elif event == 'os.symlink':
            allowed(args[1])
    sys.addaudithook(audit)


def worker(group, attempt):
    started, cpu = time.perf_counter(), time.process_time()
    before = source_epoch()
    inv = json.loads((OUT / 'inventory.json').read_text())
    refs = [r for r in inv['references'] if r['group'] == group]
    record = {'group': group, 'attempt': attempt, 'started_utc': utc(), 'pid': os.getpid(),
              'source_before': before, 'references': [r['ref_id'] for r in refs],
              'completed': False, 'current_tree_evidence_valid': False}
    directory = OUT / 'checks' / group / str(attempt)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        write_fence()
        if not refs or not all(r['local_instance_matches_frozen'] for r in refs):
            raise ValueError('Missing reference or same-ID local input differs from frozen input')
        configure_caches()
        from stowage.model import Instance, MovePlan
        from stowage.checker import check
        from stowage.checker.full import report_matches_inputs
        from stowage.rules.catalogue import rule_freeze_v1_enabled
        assert rule_freeze_v1_enabled() is False
        reference = refs[0]
        instance_path = ROOT / reference['input']
        if digest(instance_path.read_bytes()) != reference['input_sha256']:
            raise ValueError('Frozen input changed')
        instance = Instance.model_validate_json(instance_path.read_text())
        plan_data = extract(ROOT / reference['path'], reference['pointer'], reference['line'])
        if digest(canonical(plan_data)) != reference['plan_sha256']:
            raise ValueError('Stored plan changed')
        plan = MovePlan.model_validate(plan_data)
        phase = time.perf_counter()
        report = check(instance, plan)  # fresh, unchanged checker; never app check cache
        record['checker_s'] = time.perf_counter() - phase
        assert report_matches_inputs(report, instance, plan), 'Checker omitted exact input bindings'
        raw_report = report.model_dump(mode='json')
        save(directory / 'report.json', raw_report)
        record.update(completed=True, passed=report.passed, report_sha256=digest(canonical(raw_report)),
                      plan_sha256=reference['plan_sha256'],
                      validated_plan_sha256=digest(canonical(plan.model_dump(mode='json'))),
                      input_sha256=reference['input_sha256'], input_bound=True,
                      hard_violations=[v.model_dump(mode='json') for v in report.violations if v.severity == 'hard'],
                      parts=[{'name': p.name, 'ran': p.ran, 'error': p.error, 'metrics': p.metrics}
                             for p in report.parts.values()], count=report.count.model_dump(mode='json') if report.count else None)
        # Verify every representation before copying an actual report beside it.
        for ref in refs:
            path = ROOT / ref['path']
            if digest(path.read_bytes()) != ref['file_sha256']:
                raise ValueError(f'Reference file changed: {path}')
            value = extract(path, ref['pointer'], ref['line'])
            if digest(canonical(value)) != ref['plan_sha256']:
                raise ValueError(f'Reference plan changed: {path}')
            assert report_matches_inputs(report, instance, MovePlan.model_validate(value))
    except BaseException as error:
        record.update(error=f'{type(error).__name__}: {error}', traceback=traceback.format_exc())
    record.update(source_after=source_epoch(), wall_s=time.perf_counter()-started,
                  cpu_s=time.process_time()-cpu, ended_utc=utc(),
                  peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024))
    record['current_tree_evidence_valid'] = (record['completed'] and 'error' not in record
        and record['source_before'] == record['source_after'])
    if record['current_tree_evidence_valid']:
        # Sidecars are always new and preserve every old artifact unchanged.
        for ref in refs:
            path = ROOT / ref['path']
            stem = f'{path.name}.d130-{before["sha256"][:12]}-{ref["ref_id"]}-{attempt}'
            report_path = path.with_name(stem + '.report.json')
            binding_path = path.with_name(stem + '.binding.json')
            if report_path.exists() or binding_path.exists():
                raise FileExistsError(f'New sidecar required: {report_path}')
            save(report_path, raw_report)
            save(binding_path, {**record, 'reference': ref, 'report': str(report_path.relative_to(ROOT)),
                                'check_record': str((directory / 'check.json').relative_to(ROOT)),
                                'copied_actual_fresh_report': True, 'deduped_exact_group': group})
        record['sidecars_written'] = len(refs)
    save(directory / 'check.json', record)
    print(json.dumps({k: record.get(k) for k in ('group', 'passed', 'completed', 'error', 'wall_s',
        'checker_s', 'cpu_s', 'peak_rss_bytes', 'current_tree_evidence_valid', 'sidecars_written')}), flush=True)
    return 0 if record['current_tree_evidence_valid'] else 2


def run(limit, maximum):
    inv = json.loads((OUT / 'inventory.json').read_text())
    # Advertised finalized candidates first, then every old/raw variant.
    refs = {r['ref_id']: r for r in inv['references']}
    groups = sorted(inv['groups'], key=lambda g: (
        not any('/showcase-v2-gold/instances/' in refs[r]['path'] for r in inv['groups'][g]),
        not all(refs[r]['ballast_present'] for r in inv['groups'][g]), g))
    completed = 0
    for group in groups:
        prior = sorted((OUT / 'checks' / group).glob('*/check.json'))
        if any(json.loads(p.read_text()).get('current_tree_evidence_valid') for p in prior):
            continue
        if completed >= maximum:
            break
        attempt = len(list((OUT / 'checks' / group).glob('*'))) + 1
        directory = OUT / 'checks' / group / str(attempt)
        directory.mkdir(parents=True, exist_ok=False)
        process_record = {'group': group, 'attempt': attempt, 'started_utc': utc(), 'reaped': False}
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
        started = time.perf_counter()
        env = os.environ.copy()
        env.update({key: '1' for key in ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS',
                    'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS', 'RAYON_NUM_THREADS')})
        env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONHASHSEED='0', HF_HUB_OFFLINE='1',
                   TRANSFORMERS_OFFLINE='1', UV_OFFLINE='1')
        with (directory / 'stdout.txt').open('w') as log:
            process = subprocess.Popen(['nice', '-n', '10', sys.executable, str(Path(__file__).resolve()),
                'worker', '--group', group, '--attempt', str(attempt)], cwd=ROOT, env=env,
                stdout=log, stderr=subprocess.STDOUT)
            process_record['owned_pid'] = process.pid
            save(directory / 'process.json', process_record)
            try:
                code = process.wait(timeout=limit)
            except subprocess.TimeoutExpired:
                process_record['timeout'] = True
                process.terminate()
                try:
                    code = process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    code = process.wait()
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        process_record.update(reaped=True, exit_code=code, wall_s=time.perf_counter()-started,
            cpu_s=after.ru_utime+after.ru_stime-before.ru_utime-before.ru_stime,
            peak_waited_child_rss_bytes=after.ru_maxrss*(1 if sys.platform == 'darwin' else 1024),
            finished_utc=utc())
        save(directory / 'process.json', process_record)
        print(json.dumps(process_record), flush=True)
        completed += 1
        if (directory / 'check.json').exists():
            result = json.loads((directory / 'check.json').read_text())
            if result.get('peak_rss_bytes', 0) > 4 * 1024**3:
                raise RuntimeError('Measured peak exceeded lane4GB; stop before next plan')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('inventory', 'worker', 'run'))
    parser.add_argument('--group')
    parser.add_argument('--attempt', type=int, default=1)
    parser.add_argument('--limit', type=float, default=240)
    parser.add_argument('--maximum', type=int, default=999)
    args = parser.parse_args()
    if args.mode == 'inventory':
        inventory()
    elif args.mode == 'worker':
        return worker(args.group, args.attempt)
    else:
        run(args.limit, args.maximum)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
