"""Pre-final D49 gates for both exact density profiles; central CPU admission.

Original numerical gate functions execute unchanged. Storage/import locations
are redirected explicitly. Only the checked freshwater historical-training
exclusion can remove its out-of-domain reviewer requirement.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types

HERE = Path(__file__).resolve().parent
OWNER = HERE.parent
ROOT = OWNER.parents[2]
ORIGINAL = OWNER/'production-1028b'
SHIPS = ('feeder_500', 'feeder_1000', 'feeder_1700')
LEVELS = ('light', 'medium', 'full')
FILES = {'ordinary': 'd49_holdout_audit.py', 'rows': 'claude_edge_audit.py',
         'residual': 'claude-prior-holdout-gate.py', 'manifest': 'claude_audit_inputs.py'}


def pin(path):
    path = Path(path).resolve()
    return dict(path=str(path.relative_to(ROOT)), sha256=hashlib.sha256(path.read_bytes()).hexdigest())


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False)+'\n')
    temporary.replace(path)


def profile_adapter(profile):
    if profile != 'freshwater':
        return None
    sys.path.insert(0, str(HERE/'freshwater'))
    import profile_run
    profile_run.install()
    return profile_run


def driver(profile):
    adapter = profile_adapter(profile)
    if adapter:
        return adapter.load_driver()
    spec = importlib.util.spec_from_file_location('pre_final_seawater_driver', OWNER/'driver.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def exclusion(profile, family, ship):
    if profile != 'freshwater' or ship != 'feeder_500':
        return False
    profile_adapter(profile)
    import necessary_adapter
    promoter = load_source(OWNER/'promote.py', profile, redirect=False)
    raw = json.loads((HERE/profile/'ordinary'/ship/'raw.json').read_text())
    return necessary_adapter.validate_exclusion(
        promoter, HERE/profile/'edges-v3'/ship, raw, family.template)


def load_source(path, profile, *, redirect=True):
    """Keep original numerical text; redirect explicit artifact paths only."""
    profile_adapter(profile)
    path = Path(path).resolve()
    source = path.read_text()
    storage = HERE/profile
    if redirect:
        for marker in ('HERE = Path(__file__).resolve().parent', 'HERE=Path(__file__).resolve().parent'):
            source = source.replace(marker, 'HERE = Path('+repr(str(storage))+')')
        for marker in ('ROOT = HERE.parents[3]', 'ROOT=HERE.parents[3]'):
            source = source.replace(marker, 'ROOT = Path('+repr(str(ROOT))+')')
        for name in ('driver.py', 'necessary.py', 'promote.py', 'edge_grid.py'):
            for quote in ("'", '"'):
                source = source.replace('HERE.parent/'+quote+name+quote,
                                        'Path('+repr(str(OWNER/name))+')')
    if profile == 'freshwater' and path.parent == ORIGINAL and path.name == FILES['rows']:
        before = "if ship == 'feeder_500':\n            sources.append"
        after = "if ship == 'feeder_500' and not _checked_review_exclusion(family, ship):\n            sources.append"
        if source.count(before) != 1:
            raise ValueError('original individual-row reviewer guard changed')
        source = source.replace(before, after)
    name = 'pre_final_'+profile+'_'+path.stem.replace('-', '_')
    result = types.ModuleType(name)
    result.__file__ = str(path)
    result._checked_review_exclusion = lambda family, ship: exclusion(profile, family, ship)
    sys.modules[name] = result
    exec(compile(source, str(path), 'exec'), result.__dict__)
    if path.name == 'necessary.py' and profile == 'freshwater':
        import necessary_adapter
        necessary_adapter.install(result)
    if path.name == FILES['ordinary']:
        result.driver = lambda: driver(profile)
    if 'module' in result.__dict__ and callable(result.module):
        def nested_module(unused_name, requested):
            requested = Path(requested)
            if requested.name in FILES.values():
                return load_source(ORIGINAL/requested.name, profile)
            if requested.name in ('necessary.py', 'promote.py', 'driver.py'):
                return load_source(OWNER/requested.name, profile, redirect=False)
            return load_source(requested, profile, redirect=False)
        result.module = nested_module
    if 'pin' in result.__dict__:
        original_pin = result.pin
        def source_aware_pin(requested):
            requested = Path(requested)
            if (not requested.exists() and requested.parent == storage
                    and requested.name in ('claude-face-pilot.py', 'claude-face-budget-pilot.py')):
                requested = ORIGINAL/requested.name
            return original_pin(requested)
        result.pin = source_aware_pin
    return result


def load_gate(profile='seawater', kind='residual'):
    """Read-only adapter also used by evidence-path regression tests."""
    return load_source(ORIGINAL/FILES[kind], profile)


def worker(kind, profile, bank=None, ship=None, level=None):
    sys.path.insert(0, str(HERE/'closure'))
    from guard import assert_current
    assert_current()
    storage = HERE/profile
    if profile == 'freshwater' and kind in ('residual', 'rows'):
        # The density-specific validators retain original N1 arithmetic and
        # explicitly validate the historical out-of-domain training exclusion.
        # Every newly measured prescribed diagnostic is mandatory in both gates.
        profile_adapter(profile)
        name = 'freshwater_residual_gate.py' if kind == 'residual' else 'claude_edge_audit.py'
        gate = load_source(storage/name, profile, redirect=False)
        diagnostics = [storage/'diagnostic-edges'/(name+'.json') for name in
                       ('claude-edge-cause', 'claude-face-pilot', 'claude-face-budget-pilot')]
        if not all(path.exists() for path in diagnostics):
            raise ValueError('all fresh prescribed diagnostics must finish before candidate gates')
        output = 'candidate-residual-gate.json' if kind == 'residual' else 'candidate-row-audit.json'
        sys.argv = [gate.__file__, '--bank', str(bank), '--output', str(storage/output)]
        for path in diagnostics:
            sys.argv += ['--additional-fresh-population', str(path)]
        status = gate.main()
        if status:
            raise RuntimeError('freshwater '+kind+' gate failed; retained actual rows')
    elif kind == 'residual':
        gate = load_gate(profile, 'residual')
        sys.argv = [gate.__file__, '--bank', str(bank), '--prepared', str(storage/'ordinary'),
                    '--edge-input', *[str(storage/name) for name in ('edges-v1', 'edges-v2', 'edges-v3')],
                    '--controlled-diagnostics', str(storage/'claude-edge-cause.json'),
                    '--probe-root', str(OWNER.parent/'fix-t3-linear'),
                    '--output', str(storage/'candidate-residual-gate.json')]
        status = gate.main()
        if status:
            raise RuntimeError('seawater observed necessary reserve breach')
    elif kind == 'rows':
        gate = load_gate(profile, 'rows')
        sys.argv = [gate.__file__, '--bank', str(bank), '--output', str(storage/'candidate-row-audit.json')]
        gate.main()
    elif kind.startswith('ordinary-'):
        audit = load_gate(profile, 'ordinary')
        phase = kind.removeprefix('ordinary-')
        sys.argv = [audit.__file__, '--input-root', str(storage/'ordinary'),
                    '--output', str(storage/'holdout-audit'), '--phase', phase]
        if ship:
            sys.argv += ['--ship', ship, '--level', level]
        audit.main()
    elif kind == 'manifest':
        # This original producer executes at module load. Its numerical input
        # checks remain intact; the count annotation is refreshed from records.
        load_source(ORIGINAL/FILES['manifest'], profile)
        path = storage/'claude-current-audit-inputs.json'
        record = json.loads(path.read_text())
        record['independent_holdouts'] = dict(
            ordinary=sum(json.loads((storage/'ordinary'/s/f'holdout-{l}.json').read_text())['samples']
                         for s in SHIPS for l in LEVELS),
            fresh_edge=sum(json.loads((storage/'edges-v3'/s/'necessary-edges-holdout.json').read_text())['samples']
                           for s in SHIPS), earlier_edge_holds='explicit calibration enrichment')
        record['verification_producer'] = pin(__file__)
        record['water_density_kg_m3'] = 1025. if profile == 'seawater' else 1000.
        write(path, record)
    else:
        raise ValueError('unknown verification worker')
    assert_current()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=('candidate', 'ordinary', 'manifest'))
    parser.add_argument('--profile', choices=('seawater', 'freshwater', 'both'), default='both')
    parser.add_argument('--bank', type=Path)
    parser.add_argument('--workers', type=int, choices=range(1, 10), default=6)
    parser.add_argument('--worker', choices=('residual', 'rows', 'ordinary-replay',
                                          'ordinary-rows', 'ordinary-summary', 'manifest'))
    parser.add_argument('--ship', choices=SHIPS)
    parser.add_argument('--level', choices=LEVELS)
    args = parser.parse_args()
    if args.worker:
        if args.profile == 'both':
            parser.error('worker requires one exact profile')
        worker(args.worker, args.profile, args.bank, args.ship, args.level)
        return
    if args.phase is None:
        parser.error('--phase or --worker required')
    if args.phase == 'candidate' and args.bank is None:
        parser.error('candidate requires --bank')
    from compute import run
    profiles = ('seawater', 'freshwater') if args.profile == 'both' else (args.profile,)
    def submit(kind, profile, ship=None, level=None):
        argv = [Path(__file__), '--worker', kind, '--profile', profile]
        if args.bank:
            argv += ['--bank', args.bank]
        if ship:
            argv += ['--ship', ship, '--level', level]
        return run('verify-'+profile+'-'+kind+('-'+ship+'-'+level if ship else ''), argv)
    if args.phase == 'candidate':
        jobs = [(kind, profile, None, None) for profile in profiles for kind in ('residual', 'rows')]
    elif args.phase == 'ordinary':
        jobs = [('ordinary-replay', profile, ship, level) for profile in profiles
                for ship in SHIPS for level in LEVELS]
    else:
        jobs = [('manifest', profile, None, None) for profile in profiles]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed([pool.submit(submit, *job) for job in jobs]):
            future.result()
    if args.phase == 'ordinary':
        for profile in profiles:
            submit('ordinary-rows', profile)
            submit('ordinary-summary', profile)


if __name__ == '__main__':
    main()
