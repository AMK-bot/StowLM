"""Read-only source audit: time canonical-policy cold construction outside a check.

Each worker has a separate empty disposable vessel-cache directory, preserving
the real public construction path while exposing a true disk/process miss.
No checker budget, validation, criterion or numerical provider is modified.
"""
import os
for variable in ('OPENBLAS_NUM_THREADS','OMP_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS'):
    os.environ[variable] = '1'

import argparse
from functools import wraps
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
INPUT = ROOT/'experiments/solver-diag-1028/inputs/09-random_yard-tiny-feeder_800.json'


def worker(mode):
    from stowage.io import load_instance
    from stowage.checker import stability
    from stowage.vessel import fleet, fleet_cache, hull, tanks, fleet_lashing_design
    from stowage.vessel.hydrostatics import clear_hydrostatic_caches
    import tomllib

    instance = load_instance(INPUT)
    name = instance.vessel.slice_of or instance.vessel.id
    from stowage.conventions import canonical_vessel_id
    name = canonical_vessel_id(name)
    payload = fleet._parameter_bytes(name)
    construction = fleet_cache._cache_key(name, 'full', True, payload)
    default_dir = fleet_cache._cache_dir()
    target = default_dir/f'{name}.full.hull.{construction}.json'
    cache_dir = HERE/f'canonical-policy-empty-cache-{mode}'
    cache_dir.mkdir(exist_ok=True)
    # A uniquely named empty child also keeps repeated invocations cold.
    import tempfile
    cache_dir = Path(tempfile.mkdtemp(prefix='run-', dir=cache_dir))
    os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(cache_dir)
    fleet_cache.clear_vessel_memo()
    clear_hydrostatic_caches()
    stability._canonical_policy_baseline.cache_clear()
    stability._parsed_parameter_payload.cache_clear()
    events, stack = [], []
    def install(module, attribute, label=None):
        original = getattr(module, attribute)
        @wraps(original)
        def invoke(*args, **kwargs):
            record = dict(phase=label or attribute, depth=len(stack), start_s=perf_counter()-started)
            if attribute == 'evaluate_manual_condition':
                record.update(condition=args[2].get('condition'), voyage_phase=args[3])
            events.append(record)
            stack.append(record)
            mark = perf_counter()
            try:
                result = original(*args, **kwargs)
                record['success'] = True
                return result
            except Exception as exc:
                record['error'] = repr(exc)
                raise
            finally:
                record['seconds'] = perf_counter()-mark
                stack.pop()
                (HERE/f'canonical-policy-trace-{mode}-progress.json').write_text(json.dumps(events, indent=2)+'\n')
        setattr(module, attribute, invoke)
    install(fleet, 'load_vessel')
    install(fleet, '_build_vessel')
    install(fleet_cache, 'cached_vessel')
    install(hull, 'build_hull_model')
    install(tanks, 'build_tanks')
    for attribute in ('populate_lashing_design','standard_manual_conditions',
                      'evaluate_manual_condition','_corrected_condition'):
        install(fleet_lashing_design, attribute)

    def hashes():
        return {str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest()
                for path in [*fleet_cache._source_files(), ROOT/'src/stowage/checker/stability.py',
                             ROOT/f'params/fleet/{name}.toml']}
    before = hashes()
    started = perf_counter()
    if mode == 'public':
        stability._canonical_fleet_policy(instance.vessel)
        values = stability._canonical_policy_baseline(name, hashlib.sha256(payload).hexdigest(), construction)
    elif mode == 'hull':
        built = hull.build_hull_model(tomllib.loads(payload.decode('utf-8')))
        values = tuple(stability._hull_policy_number(built, field) for field in stability._CANONICAL_POLICY_FIELDS)
    else:
        # EXPERIMENT ONLY: execute the authoritative builder's unchanged
        # validation/physical-displacement prefix. Stop before its unused
        # lightship-draft, hydrostatic-table, KN and Bonjean construction.
        # This is diagnostic instrumentation, not a production AST adapter.
        import ast
        import inspect
        import textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(inspect.unwrap(hull.build_hull_model))))
        function = tree.body[0]
        cutoff = next(index for index,node in enumerate(function.body)
            if isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='light_t'
                                                   for target in node.targets))
        function.body = function.body[:cutoff]+ast.parse(
            "return tuple(map(float, (supplied['min_trim_m'], supplied['max_trim_m'], "
            "light_mass, maximum_mass, supplied['bilge_keel_area_m2'])))").body
        namespace = dict(hull.__dict__)
        exec(compile(ast.fix_missing_locations(tree), '<experiment-authoritative-hull-policy-prefix>', 'exec'), namespace)
        values = namespace['build_hull_model'](tomllib.loads(payload.decode('utf-8')))
    elapsed = perf_counter()-started
    after = hashes()
    record = dict(mode=mode, input=str(INPUT.relative_to(ROOT)), ship=name,
        default_cache_target=str(target), default_cache_target_existed=target.is_file(),
        isolated_cache=str(cache_dir), construction_identity=construction,
        parameter_sha256=hashlib.sha256(payload).hexdigest(),
        policy=dict(zip(stability._CANONICAL_POLICY_FIELDS, values, strict=True)),
        evaluated_policy={field:stability._hull_policy_number(instance.vessel.hull,field)
                          for field in stability._CANONICAL_POLICY_FIELDS},
        elapsed_seconds=elapsed, events=events, source_sha256_before=before,
        source_sha256_after=after, sources_stable=before==after,
        budget_context_used=False)
    (HERE/f'canonical-policy-trace-{mode}.json').write_text(json.dumps(record, indent=2)+'\n')
    print(json.dumps({key:value for key,value in record.items() if key not in ('source_sha256_before','source_sha256_after')}), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', choices=('public','hull','prefix'))
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
        return
    for mode in ('public','hull'):
        subprocess.run([sys.executable,__file__,'--worker',mode],cwd=ROOT,check=True)
    public = json.loads((HERE/'canonical-policy-trace-public.json').read_text())
    hull = json.loads((HERE/'canonical-policy-trace-hull.json').read_text())
    assert public['policy'] == hull['policy']
    summary = dict(public_seconds=public['elapsed_seconds'],hull_seconds=hull['elapsed_seconds'],
                   five_policy_values_bitwise_equal=public['policy']==hull['policy'],
                   sources_stable=public['sources_stable'] and hull['sources_stable'],
                   policy=public['policy'])
    (HERE/'canonical-policy-trace-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary),flush=True)


if __name__=='__main__':
    main()
