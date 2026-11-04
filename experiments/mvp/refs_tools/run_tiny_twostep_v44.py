#!/usr/bin/env python3
"""Pinned tiny two-step v4.4.0 references, preserving original tiny input hashes.

Example: uv run python experiments/mvp/refs_tools/run_tiny_twostep_v44.py --workers 1
APPROXIMATION: the original tiny seed policy stays fixed at zero; solve cap 60s.
D88 enables ballast v4 ONLY inside finalize; every available final plan is checked.
Historical tiny rows remain archived; this creates an independent v4.4 run folder.
"""
from __future__ import annotations
import os
for _key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','VECLIB_MAXIMUM_THREADS','NUMEXPR_NUM_THREADS','BLIS_NUM_THREADS'):
    os.environ[_key] = '1'
try:
    os.nice(10)
except OSError:
    pass  # Sandbox may forbid changing priority; outcome recorded in manifest.
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import sys
from time import perf_counter
import traceback

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location('refs_common_tiny_v44', Path(__file__).with_name('run_refs.py'))
common = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(common)
write, digest, json_hash = common.write, common.digest, common.json_hash

@contextmanager
def ballast_scope(enabled):
    previous = os.environ.pop('STOWAGE_BALLAST_V4', None)
    if enabled:
        os.environ['STOWAGE_BALLAST_V4'] = '1'
    try:
        yield
    finally:
        os.environ.pop('STOWAGE_BALLAST_V4', None)
        if previous is not None:
            os.environ['STOWAGE_BALLAST_V4'] = previous

@contextmanager
def deadline(seconds):
    previous = signal.getsignal(signal.SIGALRM)
    def expired(*_):
        raise TimeoutError(f'Stage exceeded {seconds}s')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)

def source_pin(root):
    pins = common.source_hashes(root)
    pins[str(Path(__file__).relative_to(ROOT))] = digest(__file__)
    return pins

ORIGINAL_TINY_HASHES = {'tiny/feeder_1700/random_yard/seed-008000002.json': '3c743234a18270d30f509a9552fd8ebc706801938e586e85d5104b46da2c7549', 'tiny/feeder_1700/random_yard/seed-008000008.json': '9cadaf8e2cb802df000f6a37bc0ebc5a2dd73884f1cbe6695bf3980c8b918428', 'tiny/feeder_1700/random_yard/seed-008000014.json': '8156177ba3ef17c6855a1cd8195630f718e23d92f27b4c6e7eff6c9eb59a4aff', 'tiny/feeder_1700/simulated_yard/seed-008000005.json': 'dc900b1d08ed850afffd61d0b381febbba6372111c66b151beda2ae7371d19de', 'tiny/feeder_1700/simulated_yard/seed-008000011.json': 'defd0ef58c3c098a2516f88ef899cf980899f17bc29e1c8f9e03a6f5581ca8f8', 'tiny/feeder_500/random_yard/seed-008000000.json': '3febf28980b8276f0a7abc982f93421dc1c42a4a535bb4f048dd34e366c43545', 'tiny/feeder_500/random_yard/seed-008000006.json': 'd35e398173aa2b36910edb29096dd5423dae842a8b955792de486b676fff7158', 'tiny/feeder_500/random_yard/seed-008000012.json': 'd45fbbfc2167ff23c10477abec5e740ae3b6228873dd65600d1324c51d81c2e1', 'tiny/feeder_500/simulated_yard/seed-008000003.json': '5dbac67ceac7bfbf8c27c02effd7fd3255ff13d72ce0d59bb06f9afa7efe7cc8', 'tiny/feeder_500/simulated_yard/seed-008000009.json': '45c9b464cfa37e5b5d0dd9f2ef8b275ea296adb1e15e546a76c6ba40c2ee06a9', 'tiny/feeder_800/random_yard/seed-008000004.json': 'ce75daa8d286175b798b5b44b0906c85dc88077baa019fde29e8ef7b3219e4db', 'tiny/feeder_800/random_yard/seed-008000010.json': '5899d7e2b8f7b70dfdcfb7bf3e261e4b94b4ba86dec7489fcdeb3c69d38d471c', 'tiny/feeder_800/simulated_yard/seed-008000001.json': '9d44f07febb6fe060d56ad6b67a9097b40dcaad63456b2c38702a6d4b68c5477', 'tiny/feeder_800/simulated_yard/seed-008000007.json': '87446f876f562aeaaf467f0b02875221eba9103e8be7816d24e8efddde1fb20a', 'tiny/feeder_800/simulated_yard/seed-008000013.json': '9d4d109a4525c8f77e2074e60122f5b82eefe953959d77dce0190f0ae3773b2a'}
ORIGINAL_AUDIT_HASHES = {'tiny/feeder_1700/random_yard/seed-008000002.json': '7f9e0812317411a8020ca7001bbbec98c9d8946ba8855d46a2adff2c34136623', 'tiny/feeder_1700/random_yard/seed-008000008.json': 'e5869b165fd9f258b95bef12c509e1acddcfdbfdcbcdf31fa906867502a7947d', 'tiny/feeder_1700/random_yard/seed-008000014.json': 'f0d556f855b0eb4426a37d114853ee46afa88d2e9f2822949706a0ef7fc8b895', 'tiny/feeder_1700/simulated_yard/seed-008000005.json': '6edb3bf9a8943b25d1c7d51a3e599ba8d9712c3a568b2807835c667589d0614d', 'tiny/feeder_1700/simulated_yard/seed-008000011.json': '918493ac4c9774d04b83598705afaede3558d4210d58d0ef10221e0352a30064', 'tiny/feeder_500/random_yard/seed-008000000.json': 'ce30dfb83b29705cc3b9d18fb4f412236d1d59789c0a789ab2c96df652df9f98', 'tiny/feeder_500/random_yard/seed-008000006.json': '7c31763b91109f8f60f939132bbf4a0897fb5a3a88c809f6ca3249de76ea0563', 'tiny/feeder_500/random_yard/seed-008000012.json': 'ee8910ac2ff36d489698aff2f08a0238e9e3ea52b0158ac1a7835b2ad0e85d7d', 'tiny/feeder_500/simulated_yard/seed-008000003.json': 'a7d920f369259594db41ed0e6b18d0e7cf62a1bc811c68e9c21dc57da9c3c6e6', 'tiny/feeder_500/simulated_yard/seed-008000009.json': '174a93a6b6f2c3c009528b92479d849ee8ead27b8a2cf996dfe6d0371f1e5260', 'tiny/feeder_800/random_yard/seed-008000004.json': 'fb9075bc865710e9c32a0b2185e38678becf500f3ae5cc87aae3fd3112a90178', 'tiny/feeder_800/random_yard/seed-008000010.json': '1380051f27126fc35b33243cec5c4ef490e976eb19c47a91149cf55c40472759', 'tiny/feeder_800/simulated_yard/seed-008000001.json': 'ccc3640e8fbfd4193d28e2c275c8ecfb96df394a92dc055061cca84054f78224', 'tiny/feeder_800/simulated_yard/seed-008000007.json': '178fe85dab1512bccbc3cfd5e091ace54f2a0ca54c11aba62b1ef291a9219e1c', 'tiny/feeder_800/simulated_yard/seed-008000013.json': '6110b3d54396c5093c3ed42783c636b758b5fb19f255c98c809c4ad029df35ec'}

def select(args):
    corpus = args.corpus_root.resolve()
    test = corpus / 'test'
    pins = dict(line.split(maxsplit=1)[::-1] for line in (test / 'SHA256SUMS').read_text().splitlines() if line.strip())
    if args.instance_list:
        raw = args.instance_list.read_text()
        names = json.loads(raw) if raw.lstrip().startswith('[') else [line.strip() for line in raw.splitlines() if line.strip() and not line.lstrip().startswith('#')]
        paths = []
        for name in names:
            path = Path(name)
            if not path.is_absolute():
                path = (ROOT/path) if (ROOT/path).is_file() else test/path
            paths.append(path.resolve())
    else:
        paths = [test/name for name in sorted(ORIGINAL_TINY_HASHES)]
    if args.limit:
        paths = paths[:args.limit]
    if not paths or len(set(paths)) != len(paths):
        raise ValueError('Empty/duplicate instance selection')
    selected = []
    for path in paths:
        name = path.relative_to(test).as_posix()
        if name not in ORIGINAL_TINY_HASHES:
            raise ValueError(f'Not an original frozen tiny instance: {name}')
        sha = ORIGINAL_TINY_HASHES[name]
        if digest(path) != sha or pins.get(name) != sha:
            raise ValueError(f'Frozen tiny input hash mismatch: {name}')
        audit_path = path.with_suffix('.audit.json')
        audit_sha = ORIGINAL_AUDIT_HASHES[name]
        audit = json.loads(audit_path.read_text())
        if digest(audit_path) != audit_sha or pins.get(audit_path.relative_to(test).as_posix()) != audit_sha:
            raise ValueError(f'Frozen tiny audit hash mismatch: {name}')
        if audit.get('accepted') is not True or audit.get('rule_freeze_v1_enabled') is not False or audit.get('instance_sha256') != sha:
            raise ValueError(f'Invalid original tiny corpus audit: {name}')
        selected.append(dict(path=str(path), spec_key=name.removesuffix('.json'), input_sha256=sha,
                             audit_path=str(audit_path), audit_sha256=audit_sha,
                             ship=Path(name).parts[1], generation_settings=None))
    return selected

def reconcile(output):
    """Direct checkpoints are canonical, including every failure (P2)."""
    rows = []
    seen = set()
    for path in sorted((output / 'instances').glob('*/record.json')):
        row = json.loads(path.read_text())
        key = row['spec_key']
        if key in seen:
            raise ValueError(f'Duplicate canonical identity: {key}')
        seen.add(key)
        rows.append(row)
    journal = output / 'records.jsonl'
    if journal.exists():
        prior = [json.loads(line) for line in journal.read_text().splitlines() if line.strip()]
        keys = [row['spec_key'] for row in prior]
        if len(keys) != len(set(keys)) or set(keys) - seen:
            raise ValueError('Duplicate/orphan journal identities')
    common._write_records(output, rows)
    return rows

def finalize_candidate(instance, result, finish, seconds):
    """Keep the raw candidate checkable even when ballast raises or times out."""
    try:
        candidate = finish(instance, result, time_limit_s=seconds)
        return candidate, candidate.status == 'ok' and candidate.plan is not None, None
    except Exception as exc:
        return result, False, f'{type(exc).__name__}: {exc}'


def scalable_options(config, feasibility):
    """D92/D94 gold defaults, with an explicit paired screen/order switch."""
    screen = config['securing_screen']
    return dict(feasibility_config=feasibility, constructor_v4=False,
                max_neighborhoods=config['max_neighborhoods'],
                securing_screen=screen, heavy_first_ordering=screen)


def effective_seed(config, instance):
    return instance.seed if config['seed_policy'] == 'instance' else config['seed']


def run_case(payload):
    selection, config, output = payload['selection'], payload['config'], Path(payload['output'])
    path = Path(selection['path'])
    directory = output / 'instances' / path.stem
    directory.mkdir(parents=True, exist_ok=True)
    record_path = directory / 'record.json'
    config_sha = json_hash(config)
    if record_path.exists():
        old = json.loads(record_path.read_text())
        if old.get('config_sha256') != config_sha or old.get('input_sha256') != selection['input_sha256']:
            raise ValueError('Checkpoint configuration/input changed')
        if digest(path) != selection['input_sha256'] or digest(selection['audit_path']) != selection['audit_sha256']:
            raise ValueError('Resumed input/audit changed')
        if source_pin(Path(config['source_root'])) != config['source_pin']:
            raise ValueError('Resumed source changed')
        for name, expected in old.get('artifact_sha256', {}).items():
            if digest(directory / name) != expected:
                raise ValueError(f'Resumed artifact changed: {name}')
        if old.get('completed'):
            return old
        old.update(completed=True, shippable=False, checker_passed=False, error='Interrupted prior attempt; preserved as failure')
        write(record_path, old)
        return old
    source_root = Path(config['source_root'])
    sys.path.insert(0, str(source_root / 'src'))
    row = dict(selection, instance_path=str(path.relative_to(ROOT)), instance_id=path.stem,
               split='test', rung='tiny', method=config['method'], config_sha256=config_sha,
               completed=False, pid=os.getpid(), checker_passed=False, checker_outcome='not_run', shippable=False,
               reference_eligible=True, report_binding_valid=False, first_failing_rule=None,
               count=None, lower_bound=None, stages={}, repaired=False, official=False,
               ballast_environment=dict(scope='finalize_only', other_stages=None),
               source_root=str(source_root), source_before=config['source_pin'])
    write(record_path, row)
    start = perf_counter()
    stage_name = 'input'
    result = report = None
    @contextmanager
    def stage(name, seconds, *, v4=False):
        nonlocal stage_name
        stage_name = name
        tick = perf_counter()
        details = row['stages'][name] = dict(passed=False, limit_s=seconds)
        write(record_path, row)
        try:
            with ballast_scope(v4), deadline(seconds):
                details['STOWAGE_BALLAST_V4'] = os.environ.get('STOWAGE_BALLAST_V4')
                yield details
        finally:
            details['wall_s'] = perf_counter()-tick
            write(record_path, row)
    try:
        # All imports occur after inherited opt-in is cleared.
        os.environ.pop('STOWAGE_BALLAST_V4', None)
        from stowage.io import load_instance
        from stowage.feasibility import FeasibilityConfig
        from stowage.checker import check, report_matches_inputs
        from stowage.planner import finalize
        from stowage.bounds import lower_bound
        from stowage.rules.catalogue import rule_freeze_v1_enabled
        from stowage.versions import component_versions
        from eval.harness_integrity import report_has_current_versions
        if rule_freeze_v1_enabled():
            raise RuntimeError('Rule freeze must remain OFF')
        if source_pin(source_root) != config['source_pin']:
            raise RuntimeError('Source snapshot changed before import')
        if digest(path) != selection['input_sha256'] or digest(selection['audit_path']) != selection['audit_sha256']:
            raise ValueError('Input/audit changed')
        write(directory / 'input-audit.json', json.loads(Path(selection['audit_path']).read_text()))
        instance = load_instance(path)
        row['effective_seed'] = effective_seed(config, instance)
        row['instance_id'] = instance.id
        row['component_versions'] = component_versions()
        row['instance'] = dict(vessel_id=instance.vessel.id, boxes_to_load=len(instance.load_list), family=instance.family)
        if instance.size_class != 'tiny':
            raise ValueError('Instance rung is not tiny')
        with stage('bounds', config['bounds_s']) as details:
            bound = lower_bound(instance)
            row['lower_bound'] = bound.model_dump(mode='json')
            details['passed'] = True
        if config['method'] != 'bounds':
            feasibility = FeasibilityConfig(extra={'global_screen': 0})
            if config['method'] == 'solver':
                from stowage.solver.scalable import ScalableSolver, ScalableConfig
                effective_config = ScalableConfig(**scalable_options(config, feasibility))
                planner = ScalableSolver(effective_config)
            else:
                from stowage.solver.twostep import TwoStepPlanner, VERSION
                if VERSION != '4.4.0':
                    raise RuntimeError('Tiny rerun requires two-step 4.4.0')
                row['twostep_version'] = VERSION
                row['twostep_source_sha256'] = {name: sha for name, sha in config['source_pin'].items() if Path(name).name.startswith('twostep') and name.endswith('.py')}
                planner = TwoStepPlanner(feasibility_config=feasibility)
            row['planner'] = planner.name
            if config['method'] == 'solver':
                from dataclasses import asdict
                row['scalable_config'] = asdict(effective_config)
                row['securing_screen'] = effective_config.securing_screen
            with stage('solve', config['solve_s']+5) as details:
                result = planner.plan(instance, seed=row['effective_seed'], time_limit_s=config['solve_s'])
                details.update(status=result.status, passed=result.plan is not None, metrics=result.metrics)
                write(directory / 'raw-result.json', result.model_dump(mode='json'))
                row['result'] = result.model_dump(mode='json')
                if result.plan is None:
                    raise RuntimeError(f'No solver plan: {result.status}: {result.message}')
                write(directory / 'raw-plan.json', result.plan.model_dump(mode='json'))
                row['raw_plan_sha256'] = digest(directory / 'raw-plan.json')
            with stage('ballast', config['ballast_s']+5, v4=True) as details:
                result, ballast_ok, ballast_error = finalize_candidate(instance, result, finalize, config['ballast_s'])
                details.update(status=result.status, passed=ballast_ok, metrics=result.metrics)
                if ballast_error:
                    details['error'] = ballast_error
                write(directory / 'result.json', result.model_dump(mode='json'))
                if result.plan is None:
                    raise RuntimeError(f'Ballast produced no plan: {result.status}')
            with stage('checker', config['checker_s']) as details:
                report = check(instance, result.plan)
                write(directory / 'report.json', report.model_dump(mode='json'))
                row['report'] = report.model_dump(mode='json')
                row['checker_passed'] = report.passed
                row['checker_outcome'] = 'passed' if report.passed else 'failed'
                row['report_binding_valid'] = bool(report_matches_inputs(report, instance, result.plan) and report_has_current_versions(report))
                result._record_full_check(instance, report)
                row['pipeline_shippable'] = bool(ballast_ok and result.shippable and row['report_binding_valid'])
                row['shippable'] = row['pipeline_shippable']
                row['count'] = None if report.count is None else report.count.model_dump(mode='json')
                hard = [v for v in report.violations if v.severity == 'hard']
                row['first_failing_rule'] = hard[0].rule_id if hard else None
                details['passed'] = row['shippable']
                row['result'] = result.model_dump(mode='json')
                write(directory / 'result.json', row['result'])
                write(directory / 'plan.json', result.plan.model_dump(mode='json'))
    except Exception as exc:
        row.update(error=f'{type(exc).__name__}: {exc}', failed_stage=stage_name, traceback=traceback.format_exc())
    finally:
        after = source_pin(source_root)
        changed = common.source_changes(config['source_pin'], after)
        row.update(source_after=after, source_drift=bool(changed), relevant_source_changes=changed)
        if changed or digest(path) != selection['input_sha256']:
            row.update(reference_eligible=False, shippable=False, reference_exclusion_reason='source_or_input_drift')
        row.update(completed=True, timing=dict(total_s=perf_counter()-start,
            plan_s=row['stages'].get('solve', {}).get('wall_s', 0),
            bounds_s=row['stages'].get('bounds', {}).get('wall_s', 0)))
        row['artifact_sha256'] = {name: digest(directory/name) for name in ('raw-plan.json','raw-result.json','result.json','report.json','plan.json','input-audit.json') if (directory/name).is_file()}
        write(record_path, row)
    return row

def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('method', choices=('twostep',), nargs='?', default='twostep')
    parser.add_argument('--release', type=Path, default=ROOT/'experiments/mvp/corpus/test/SHA256SUMS')
    parser.add_argument('--corpus-root', type=Path, default=ROOT/'experiments/mvp/corpus')
    parser.add_argument('--instance-list', type=Path)
    parser.add_argument('--source-root', type=Path, default=ROOT/'experiments/mvp/refs_tools/frozen-runtime-twostep-v44')
    parser.add_argument('--workers', type=int, choices=(1,2,3), default=1)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--seed-policy', choices=('fixed', 'instance'), default='fixed',
                        help='Gold uses instance; fixed preserves historical reference seed policy')
    parser.add_argument('--max-neighborhoods', type=int, default=12)
    parser.add_argument('--securing-screen', action='store_true',
                        help='D92/D94 securing screen and heavy-first ordering (official solver row)')
    for name in ('solve','ballast','checker','bounds'):
        parser.add_argument(f'--{name}-s', type=float, default=60. if name == 'solve' else 120.)
    parser.add_argument('--output-dir', type=Path)
    return parser


def main():
    parser = argument_parser()
    args = parser.parse_args()
    if args.max_neighborhoods < 0 or (args.limit is not None and args.limit < 1):
        parser.error('Invalid neighborhood/selection limit')
    if any(not 0 < getattr(args, name+'_s') < float('inf') for name in ('solve','ballast','checker','bounds')):
        parser.error('Stage limits must be finite positive seconds')
    selected = select(args)
    output = (args.output_dir or ROOT/'experiments/mvp/runs/test-twostep-tiny-v44').resolve()
    output.relative_to(ROOT/'experiments/mvp/runs')
    if not output.name.startswith('test-'):
        raise ValueError('Output outside test-* fence')
    output.mkdir(parents=True, exist_ok=True)
    config = dict(twostep_version='4.4.0', method=args.method, seed=args.seed, seed_policy=args.seed_policy,
        source_root=str(args.source_root.resolve()),
        source_pin=source_pin(args.source_root.resolve()), constructor_v4=False, max_neighborhoods=args.max_neighborhoods,
        securing_screen=args.securing_screen, heavy_first_ordering=args.securing_screen,
        c2_global_screen=0, rule_freeze_v1_enabled=False, ballast_v4_scope='finalize_only',
        solve_s=args.solve_s, ballast_s=args.ballast_s, checker_s=args.checker_s, bounds_s=args.bounds_s)
    manifest_path = output/'manifest.json'
    old = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if old and old['config_sha256'] != json_hash(config):
        raise ValueError('Run configuration/source changed; choose fresh output')
    union = {entry['spec_key']: entry for entry in old.get('selection', [])}
    union.update({entry['spec_key']: entry for entry in selected})
    manifest = dict(config=config, config_sha256=json_hash(config), selection=list(union.values()),
        release_path=str(args.release.resolve()), release_sha256=digest(args.release),
        corpus_manifest_sha256=digest(args.corpus_root/'manifest.json'), complete=False,
        native_threads=1, workers=args.workers, nice=os.getpriority(os.PRIO_PROCESS,0))
    write(manifest_path, manifest)
    # Persist exact input audit per attempt; raw plans are never overwritten on resume.
    def launch(entry):
        payload = dict(selection=entry, config=config, output=str(output))
        command = [sys.executable, str(Path(__file__).resolve()), '_child']
        try:
            done = subprocess.run(command, input=json.dumps(payload), text=True, capture_output=True,
                                  timeout=args.solve_s+args.ballast_s+args.checker_s+args.bounds_s+60)
        except subprocess.TimeoutExpired as exc:
            done = subprocess.CompletedProcess(command, -1, '', str(exc))
        checkpoint = output/'instances'/Path(entry['path']).stem/'record.json'
        if done.returncode and checkpoint.exists():
            write(checkpoint.parent / f'worker-validation-failure-{os.getpid()}.json',
                  dict(exit_code=done.returncode, stderr=done.stderr[-4000:], checkpoint_preserved=True))
            raise RuntimeError(f'Worker validation failed; checkpoint preserved: {checkpoint}: {done.stderr[-1000:]}')
        if done.returncode or not checkpoint.exists():
            row = dict(entry, instance_id=Path(entry['path']).stem, method=args.method, split='test', rung='tiny', completed=True,
                config_sha256=json_hash(config), checker_passed=False, shippable=False,
                reference_eligible=True, report_binding_valid=False, checker_outcome='not_run',
                count=None, lower_bound=None, first_failing_rule=None, stages={},
                error=f'Worker exit {done.returncode}: {done.stderr[-2000:]}', timing={'total_s':None})
            write(checkpoint,row)
        return json.loads(checkpoint.read_text())
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for row in pool.map(launch, selected):
            reconcile(output)
            print(f"done {args.method} {row['spec_key']} checker={row.get('checker_passed')} shippable={row.get('shippable')}",flush=True)
    rows = reconcile(output)
    manifest.update(complete=True, n_records=len(rows))
    write(manifest_path,manifest)

if __name__ == '__main__':
    if len(sys.argv)>1 and sys.argv[1]=='_child':
        run_case(json.load(sys.stdin))
    else:
        main()
