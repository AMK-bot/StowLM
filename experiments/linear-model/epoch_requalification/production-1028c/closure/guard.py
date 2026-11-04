"""Immutable raw-byte epoch guard. Watch mode uses stdlib/read-bytes only.

No signal is sent: an ABORT sentinel and stop request ask admitted controllers
to stop launching work. Every controller must check this guard before launch.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import runpy
import time
import tempfile
import tomllib

HERE = Path(__file__).resolve().parent
RUN = HERE.parent
OWNER = RUN.parent
ROOT = next(path for path in HERE.parents if (path/'pyproject.toml').exists())
BASELINE = HERE/'start.json'
REVIEW = ROOT/'experiments/checker/followup-1028/review1803/after-production-identities.json'


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def pin(path):
    path = path.resolve()
    return str(path.relative_to(ROOT)), sha(path)


def write(path, value):
    # Watcher and admitted workers check concurrently. Each owns its temporary
    # file; no process can replace/remove another check's still-open temp.
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent,
            prefix=path.name+'.', suffix='.tmp', delete=False) as stream:
        stream.write(json.dumps(value, indent=2)+'\n')
        temporary = stream.name
    os.replace(temporary, path)


def constants(path):
    result = {}
    for node in ast.parse(path.read_bytes()).body:
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        else:
            continue
        for name in names:
            if 'VERSION' in name:
                try:
                    result[name] = ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    result[name] = ast.unparse(node.value)
    return result


def gm_rule_input():
    record = tomllib.loads((ROOT/'src/stowage/rules/catalogue.toml').read_text())
    matches = [rule for rule in record['rules'] if rule.get('id') == 'iscode.a2_2.initial_gm']
    if len(matches) != 1:
        raise ValueError('consumed initial GM rule missing or duplicated')
    minimum = matches[0]['parameters']['minimum']
    value = float(minimum['value'])
    if minimum.get('unit') != 'm' or not math.isfinite(value) or value <= 0:
        raise ValueError('invalid consumed initial GM minimum')
    return {'iscode.a2_2.initial_gm.minimum': value}


def capture():
    if BASELINE.exists():
        raise ValueError('baseline exists: never recapture it')
    deps = runpy.run_path(str(ROOT/'src/stowage/linear/dependencies.py'))
    profiles = {name: dict(fingerprint=deps['dependency_fingerprint'](name),
        contents=deps['dependency_contents'](name), versions=deps['builder_versions'](name),
        selectors=profile) for name, profile in deps['_PROFILES'].items()}
    closure = sorted({'src/stowage/'+name for profile in profiles.values()
                      for name in profile['contents']})
    if len(closure) != 33:
        raise ValueError('expected exact reviewed33fileclosure')
    paths = {ROOT/name for name in closure}
    paths.update(ROOT/'src/stowage/linear'/name for name in
                 ('dependencies.py', 'cache.py', 'margin_policy.py', 'boundary_calibration.py', 'qualification.py'))
    paths.add(Path(__file__).resolve())
    paths.update(OWNER.glob('*.py'))
    paths.update(OWNER.parent.glob('*.py'))
    for name in ('claude-edge-cause.py', 'claude-face-pilot.py', 'claude-face-budget-pilot.py',
                 'claude-freeze-review-v2.py', 'd49_holdout_audit.py', 'claude_edge_audit.py',
                 'claude-prior-holdout-gate.py', 'claude_audit_inputs.py'):
        paths.add(OWNER/'production-1028b'/name)
    params = sorted((ROOT/'params/fleet').glob('*.toml'))
    paths.update(params)
    def offsets(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'offsets_path':
                    name = item.get('value') if isinstance(item, dict) else item
                    yield ROOT/name
                yield from offsets(item)
        elif isinstance(value, list):
            for item in value:
                yield from offsets(item)
    for path in params:
        paths.update(offsets(tomllib.loads(path.read_text())))
    strict = dict(pin(path) for path in sorted(paths))
    if any(value is None for value in strict.values()):
        raise ValueError('required strict source/input missing')
    review = json.loads(REVIEW.read_text())
    differences = {name: dict(expected=profile['fingerprint'], actual=profiles[name]['fingerprint'])
                   for name, profile in review['profiles'].items()
                   if profile['fingerprint'] != profiles[name]['fingerprint']}
    source_differences = {name: dict(expected=review['source_manifest'].get(name), actual=strict[name])
                          for name in closure+['src/stowage/linear/dependencies.py']
                          if review['source_manifest'].get(name) != strict[name]}
    if differences or source_differences:
        write(HERE/'review1803-mismatch.json', dict(profiles=differences, sources=source_differences))
        raise ValueError('current closure differs from reviewed1803finalsource; stop')
    record = dict(captured_utc=now(), hashing='exact complete source bytes; selectors validate only',
        reviewed_identity=dict(pin=[*pin(REVIEW)], captured_utc=review['captured_utc'], matches=True),
        closure_files=closure, profiles=profiles, strict_sources=strict,
        constants={name: constants(ROOT/name) for name in closure+
                   ['src/stowage/linear/dependencies.py', 'src/stowage/linear/margin_policy.py',
                    'src/stowage/linear/boundary_calibration.py']},
        global_versions_audit_only=constants(ROOT/'src/stowage/versions.py'),
        consumed_rule_inputs=gm_rule_input(),
        broad_source_hashes={name: sha(ROOT/name) for name in review['source_manifest']
                             if name != 'src/stowage/linear/margin_policy_data.json'},
        bank_sha256_at_start=sha(ROOT/'src/stowage/linear/margin_policy_data.json'))
    with BASELINE.open('x') as stream:
        stream.write(json.dumps(record, indent=2)+'\n')
    print(json.dumps(dict(captured=record['captured_utc'], closure_files=33,
                          strict_sources=len(strict), review1803_matches=True)))


def check(*, abort=True):
    baseline = json.loads(BASELINE.read_text())
    expected = dict(baseline['strict_sources'])
    rule_inputs = baseline.get('consumed_rule_inputs')
    amendment_path = HERE/'catalogue-guard-amendment.json'
    if amendment_path.exists():
        amendment = json.loads(amendment_path.read_text())
        if amendment['baseline_sha256'] != sha(BASELINE):
            raise ValueError('guard correction does not bind the immutable original baseline')
        if amendment['excluded_whole_file'] != 'src/stowage/rules/catalogue.toml':
            raise ValueError('unauthorized strict source exclusion')
        expected.pop(amendment['excluded_whole_file'])
        expected[str(Path(__file__).resolve().relative_to(ROOT))] = amendment['corrected_guard_sha256']
        rule_inputs = amendment['consumed_rule_inputs']
    write_amendment_path = HERE/'guard-write-amendment.json'
    if write_amendment_path.exists():
        write_amendment = json.loads(write_amendment_path.read_text())
        if (write_amendment['baseline_sha256'] != sha(BASELINE)
                or write_amendment['prior_guard_sha256'] != amendment['corrected_guard_sha256']):
            raise ValueError('guard concurrent-write correction has an invalid immutable baseline/source chain')
        expected[str(Path(__file__).resolve().relative_to(ROOT))] = write_amendment['corrected_guard_sha256']
    runtime = {}
    runtime_amendment_path = HERE/'guard-runtime-amendment.json'
    if runtime_amendment_path.exists():
        runtime_amendment = json.loads(runtime_amendment_path.read_text())
        runtime_path = HERE/'catalogue-runtime-baseline.json'
        if (runtime_amendment['baseline_sha256'] != sha(BASELINE)
                or runtime_amendment['prior_guard_sha256'] != write_amendment['corrected_guard_sha256']
                or runtime_amendment['runtime_baseline_sha256'] != sha(runtime_path)):
            raise ValueError('runtime-consistency correction has an invalid immutable evidence chain')
        expected[str(Path(__file__).resolve().relative_to(ROOT))] = runtime_amendment['corrected_guard_sha256']
        runtime = json.loads(runtime_path.read_text())['sources']
        if set(runtime) != {'src/stowage/rules/catalogue.py', 'src/stowage/rules/catalogue.toml'}:
            raise ValueError('unexpected catalogue runtime source scope')
        expected.update(runtime)
    producer_manifest = HERE/'run-producers.json'
    if producer_manifest.exists():
        producers = json.loads(producer_manifest.read_text())
        expected.update(producers['sources'])
    changed = {name: dict(before=value, after=sha(ROOT/name)) for name, value in expected.items()
               if sha(ROOT/name) != value}
    try:
        current_rule_inputs = gm_rule_input()
    except Exception as error:
        current_rule_inputs = {'invalid': repr(error)}
    if current_rule_inputs != rule_inputs:
        changed['@consumed_rule_inputs'] = dict(before=rule_inputs, after=current_rule_inputs)
    audit = {name: dict(before=value, after=sha(ROOT/name))
             for name, value in baseline['broad_source_hashes'].items()
             if name not in expected and sha(ROOT/name) != value}
    result = dict(checked_utc=now(), baseline_utc=baseline['captured_utc'], passed=not changed,
                  strict_changes=changed, audit_only_changes=audit,
                  consumed_rule_inputs=current_rule_inputs,
                  runtime_consistency_changes={name: value for name, value in changed.items() if name in runtime})
    write(HERE/'latest-check.json', result)
    if changed and abort:
        # Never signal unrelated or discovered processes. Registry content is
        # copied only as evidence for the owning controller/root to act on.
        registries = {str(path.relative_to(RUN)): json.loads(path.read_text())
                      for path in (RUN/'compute-active.json', RUN/'active-jobs.json') if path.exists()}
        reason = ('catalogue runtime consistency drift' if set(changed) <= set(runtime)
                  else 'strict raw source/input drift')
        stop = dict(reason=reason, **result, owned_registries=registries,
                    action='Controllers must stop launching; root stops only recorded owned children. No signals sent.')
        write(RUN/'ABORT', stop)
        write(HERE/'stop-request.json', stop)
    return result


def assert_current():
    result = check()
    if (RUN/'ABORT').exists() or not result['passed']:
        raise RuntimeError('production1028c aborted or strict epoch drifted; no launch allowed')
    return result


def freeze_producers(paths):
    manifest = HERE/'run-producers.json'
    if manifest.exists():
        raise ValueError('run producer manifest exists; never recapture')
    assert_current()
    record = dict(captured_utc=now(), sources=dict(pin(Path(path)) for path in paths))
    if not record['sources'] or any(value is None for value in record['sources'].values()):
        raise ValueError('run producers must exist')
    with manifest.open('x') as stream:
        stream.write(json.dumps(record, indent=2)+'\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--capture', action='store_true')
    group.add_argument('--check', action='store_true')
    group.add_argument('--watch', action='store_true')
    group.add_argument('--freeze-run-producers', nargs='+')
    args = parser.parse_args()
    if args.capture:
        capture()
    elif args.freeze_run_producers:
        freeze_producers(args.freeze_run_producers)
    elif args.check:
        result = check()
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result['passed'] and not (RUN/'ABORT').exists() else 1)
    else:
        write(HERE/'watcher.json', dict(started_utc=now(), interval_s=5,
              policy='stdlib byte reads only; ABORT+stop-request, no signals'))
        while not (HERE/'STOP_WATCHER').exists():
            result = check()
            if not result['passed'] or (RUN/'ABORT').exists():
                print(json.dumps(result), flush=True)
                raise SystemExit(1)
            time.sleep(5)


if __name__ == '__main__':
    main()
