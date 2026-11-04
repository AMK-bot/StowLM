"""Gate-owned execution of paired migration witnesses and acceptance boundaries.

This is not an evidence campaign. A catalogue entry, schema rejection, generic
C2 dead end or asserted ``passed`` flag cannot prove checker/mask integration.
Admission verifies signed gate execution receipts; raw reports never prove execution.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from eval.snapshot_io import read_snapshot
from stowage.versions import component_versions

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_REPORT = HERE/'migration-canaries.json'
_case_spec = importlib.util.spec_from_file_location('migration_case_registry', HERE/'qualified_canary_cases.py')
_case_registry = importlib.util.module_from_spec(_case_spec)
sys.modules[_case_spec.name] = _case_registry
_case_spec.loader.exec_module(_case_registry)
CANARIES = _case_registry.CANARIES

_ARTIFACTS = ('input', 'plan', 'checker', 'mask')
_REPORT_CONTEXT = None


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def source_identity(root):
    root = Path(root).resolve()
    files = {}
    for name in ('src', 'params', 'data/fixtures', 'pyproject.toml', 'uv.lock',
                 'experiments/campaign-g1/cache_adapter.py', 'experiments/campaign-g1/pin_transport.py',
                 'experiments/campaign-g1/qualified_canary_cases.py',
                 'experiments/campaign-g1/trusted_receipts.py'):
        path = root/name
        if not path.exists():
            continue
        for file in sorted(path.rglob('*')) if path.is_dir() else [path]:
            if file.is_file():
                files[file.relative_to(root).as_posix()] = read_snapshot(file).sha256
    return dict(files=files, sha256=_digest(files))


def driver_identity():
    names = ('migration_canaries.py', 'qualified_canary_cases.py',
             'INPORT_CANARY_CONTRACT.md', 'INPORT_CANARY_REQUEST.json')
    return _digest({name:read_snapshot(HERE/name).sha256 for name in names})


_LOADED_DRIVER_IDENTITY = driver_identity()


def _load(name):
    spec = importlib.util.spec_from_file_location('migration_'+name, HERE/(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+'.partial')
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+'\n')
    temporary.replace(path)


def _evidence_lineage(value):
    if isinstance(value, dict):
        if (any(value.get(k) is True for k in ('diagnostic', 'tiny_run', 'allow_drift'))
                or value.get('evidence_eligible') is False or value.get('official') is False):
            raise ValueError('diagnostic or ineligible canary proof')
        for child in value.values():
            _evidence_lineage(child)
    elif isinstance(value, list):
        for child in value:
            _evidence_lineage(child)


def _validate_case(name, entry, versions):
    """Only a fresh controlled execution can establish a behavioural verdict.

    Serialized reports remain an audit inventory, never execution authority.
    Admission obtains the result from its own child process, not result.json.
    """
    expected = CANARIES[name]
    if not isinstance(entry, dict):
        return dict(expected_rule=expected, eligible=False, missing=['missing_proof'])
    if entry.get('status') in {'not_implemented', 'setup_rejected', 'not_run', 'missing'}:
        return dict(expected_rule=expected, eligible=False, missing=['qualified_pair_missing', *entry.get('missing', [])],
                    status=entry['status'], reason=entry.get('reason', ''))
    return _fresh_case_result(name, entry, versions)


def _receipt_context(root, cache_sha256, *, diagnostic=False):
    return dict(source=source_identity(root), driver_sha256=driver_identity(),
                versions=component_versions(), numerical_cache_sha256=cache_sha256,
                diagnostic=diagnostic, evidence_eligible=not diagnostic)


def _parent_execution_guard(context, *, diagnostic=False):
    """Seal source before imports; loaded old code cannot sign a new disk epoch."""
    expected_source = os.environ.get('CAMPAIGN_CANARY_SOURCE_SHA256')
    expected_driver = os.environ.get('CAMPAIGN_CANARY_DRIVER_SHA256')
    matched = (expected_source == context.get('source', {}).get('sha256')
               and expected_driver == context.get('driver_sha256')
               and isinstance(expected_source, str) and len(expected_source) == 64
               and isinstance(expected_driver, str) and len(expected_driver) == 64)
    if not matched and not diagnostic:
        raise ValueError('controlled parent source/implementation pin is missing or differs')
    return matched


def _fresh_case_result(name, entry, versions):
    """Authenticate a gate-owned execution; raw checker/mask JSON is powerless."""
    expected = CANARIES[name]
    receipt = entry.get('execution_receipt')
    if not isinstance(receipt, dict) or not {'path', 'sha256'} <= set(receipt):
        return dict(expected_rule=expected, eligible=False, missing=['gate_execution_receipt_missing'])
    try:
        context = _REPORT_CONTEXT
        if context is None:
            pin = _load('freeze').snapshot_numerical_caches(ROOT)
            context = _receipt_context(ROOT, pin['sha256'])
        payload = _load('trusted_receipts').verify_execution(receipt,
            kind='migration-canary-v2', context=context)
        if payload.get('name') != name or payload.get('expected_rule') != expected:
            raise ValueError('signed execution names another canary')
        if payload.get('diagnostic') is not False:
            raise ValueError('diagnostic canary execution cannot qualify production')
        # The signed payload contains only observations made by our worker.
        # Recompute the aggregate; imported entry eligible/passed flags are ignored.
        variants = payload.get('variants')
        if not isinstance(variants, dict) or set(variants) != {'clean', 'defect'}:
            raise ValueError('signed execution has no complete clean/defect pair')
        missing = [key+':'+issue for key, value in variants.items() for issue in value.get('missing', [])]
        if any(value.get('eligible') is not True and not value.get('missing') for value in variants.values()):
            missing.append('signed_execution_incomplete')
        return {**payload, 'eligible':not missing, 'evidence_eligible':not missing,
                'missing':missing, 'execution_receipt':receipt}
    except Exception as exc:
        return dict(expected_rule=expected, eligible=False, missing=['gate_execution_receipt_invalid'],
                    reason=f'{type(exc).__name__}: {exc}')


def canary_report(root, report_path=None, execute=False):
    """Inspect the current source's complete proof journal; missing means refuse.

    ``execute=True`` explicitly invokes bounded one-core workers, using scoped
    numerical cache copies. It is never implicit in source-freeze admission.
    """
    root = Path(root).resolve()
    report_path = Path(report_path) if report_path is not None else DEFAULT_REPORT
    if execute:
        _execute(root, report_path)
    current, versions = source_identity(root), component_versions()
    identity_error, snapshot, journal = None, None, None
    cache_snapshot, numerical_cache_sha256 = None, None
    try:
        snapshot = read_snapshot(report_path)
        journal = snapshot.json()
        _evidence_lineage({key:value for key,value in journal.items() if key != 'cases'})
        if (type(journal.get('schema_version')) is not int or journal.get('schema_version') != 1
                or not isinstance(journal.get('cases'), dict) or set(journal['cases'])-set(CANARIES)
                or journal.get('evidence_eligible') is not True or journal.get('source') != current
                or journal.get('driver_sha256') != driver_identity() or journal.get('versions') != versions
                or journal.get('diagnostic') is not False):
            raise ValueError('proof journal source/component/driver identity differs')
        complete_claim = any(isinstance(case, dict) and (isinstance(case.get('execution_receipt'), dict)
            or isinstance(case.get('artifacts'), dict)) for case in journal['cases'].values())
        if complete_claim:
            cache_pin = journal.get('numerical_cache_pin')
            if not isinstance(cache_pin, dict) or set(cache_pin) != {'path', 'sha256'}:
                raise ValueError('numerical cache pin missing from canary journal')
            try:
                cache_snapshot = read_snapshot(cache_pin['path'], expected_sha256=cache_pin['sha256'])
                pinned = cache_snapshot.json()
                _load('freeze').verify_numerical_caches(pinned)
                numerical_cache_sha256 = pinned['sha256']
            except Exception as exc:
                raise ValueError(f'numerical cache proof differs: {exc}') from exc
    except FileNotFoundError:
        pass
    except Exception as exc:
        identity_error = f'{type(exc).__name__}: {exc}'
    global _REPORT_CONTEXT
    _REPORT_CONTEXT = _receipt_context(root, numerical_cache_sha256)
    cases = {}
    for name, rule in CANARIES.items():
        if identity_error:
            cases[name] = dict(expected_rule=rule, eligible=False, missing=['journal_identity'], reason=identity_error)
        elif journal is None:
            cases[name] = dict(expected_rule=rule, eligible=False, missing=['missing_proof'])
        else:
            cases[name] = _validate_case(name, journal.get('cases', {}).get(name), versions)
    _REPORT_CONTEXT = None
    try:
        if cache_snapshot is not None:
            _load('freeze').verify_numerical_caches(cache_snapshot.json())
        if snapshot is not None:
            snapshot.require_fresh()
        if cache_snapshot is not None:
            cache_snapshot.require_fresh()
        if source_identity(root) != current or component_versions() != versions:
            raise ValueError('source/component epoch changed during canary admission')
    except Exception as exc:
        identity_error = f'{type(exc).__name__}: {exc}'
        cases = {name:dict(expected_rule=rule, eligible=False, missing=['journal_identity'], reason=identity_error)
                 for name, rule in CANARIES.items()}
    missing = [name for name in CANARIES if not cases[name]['eligible']]
    return dict(schema_version=1, evidence_eligible=not missing, eligible=not missing,
        issues=[dict(case=name, missing=cases[name]['missing'], reason=cases[name].get('reason', '')) for name in missing],
        missing_cases=missing, cases=cases, source_sha256=current['sha256'], source=current,
        driver_sha256=driver_identity(), versions=versions,
        report_path=str(report_path.resolve()), report_sha256=snapshot.sha256 if snapshot else None,
        numerical_cache_pin=journal.get('numerical_cache_pin') if isinstance(journal, dict) else None,
        numerical_cache_sha256=numerical_cache_sha256)


def _cases_module():
    return _load('qualified_canary_cases')


def build_case(name):
    return _cases_module().build_case(name)


def _checker_observation(checker, expected, expectation, *, expected_rules=None,
                         expected_secondary_rules=None):
    """Require the primary rule(s), allowing only declared refusal fallout.

    Refusing a lift can also leave cargo unloaded. That optional secondary is
    not itself a witness for the rule that refused the lift. Clean controls and
    acceptance exercises still require no violation of any severity.
    """
    required = {'moves', 'rules', 'stability', 'strength', 'visibility', 'lashing', 'constraints', 'voyage'}
    complete = required.issubset(checker.parts) and all(p.ran and not p.error for p in checker.parts.values())
    hard = sorted({v.rule_id for v in checker.violations if v.severity == 'hard'})
    expected_hard = sorted(expected_rules or [expected]) if expectation == 'deny' else []
    secondary = sorted(set(expected_secondary_rules or []) - set(expected_hard)) if expectation == 'deny' else []
    return dict(complete=complete, passed=checker.passed, hard_rule_ids=hard,
                expected_hard_rule_ids=expected_hard,
                expected_secondary_rule_ids=secondary,
                eligible=complete and set(expected_hard) <= set(hard)
                and set(hard) <= set(expected_hard) | set(secondary)
                and all(v.severity == 'hard' for v in checker.violations)
                and checker.passed == (expectation != 'deny'))


def _mask_observation(instance, built):
    from stowage.actions import get_action_space
    from llm.stowlm.format import ActionCodec, load_tokenizer
    from llm.stowlm.mask import MaskedDecoder, MaskConfig, MaskError, MASK_VERSION
    from stowage.feasibility import make_feasibility_engine, FeasibilityConfig
    space = get_action_space()
    engine = make_feasibility_engine(instance, space)
    screen = getattr(engine, 'diagnostics', {}).get('global_screen')
    result = dict(status='unavailable', rule_ids=[], mask_version=MASK_VERSION,
        feasibility_config=asdict(FeasibilityConfig()), mask_config=asdict(MaskConfig()),
        global_screen=screen, production_c2_active=screen == 'on', versions=component_versions())
    if screen != 'on':
        result['reason'] = f'production C2 unavailable: {screen}'
        return result
    codec = ActionCodec(load_tokenizer(), [box.id for box in instance.containers])
    decoder = MaskedDecoder(engine, codec, MaskConfig())
    actions = [*built.get('prefix_actions', []), built['action']]
    for index, encoded in enumerate(actions):
        action = space.decode(encoded)
        tokens = list(codec.encode_action(action))
        prefix = []
        for token in tokens:
            allowed = list(decoder.allowed_token_ids())
            try:
                decoder.advance(token)
            except MaskError:
                dead = decoder.dead_end
                result.update(status='rejected', action_index=index, action_tokens=tokens,
                    prefix_tokens=prefix, denied_token=token, allowed_tokens=allowed,
                    rule_ids=sorted(set(dead.rule_ids)) if dead is not None else [],
                    reason=dead.reason if dead is not None and hasattr(dead, 'reason') else str(dead))
                return result
            prefix.append(token)
    result.update(status='admitted', rule_ids=[], actions=actions)
    # The expander is authoritative for counted yard rehandles. Missing
    # instrumentation stays unknown; it must never be inferred from admission.
    result['expanded_plan'] = None
    try:
        expanded = space.expand(instance, [space.decode(a) for a in actions])
        result['expanded_plan'] = expanded.model_dump(mode='json')
    except Exception as exc:
        result['expansion_error'] = f'{type(exc).__name__}: {exc}'
    return result


def _mask_matches(mask, expected, expectation, *, expected_mask_reasons=None, expected_rules=None):
    """Mask attribution is independent of checker-only secondary violations.

    ``expected_rules`` remains accepted for old callers, but cannot grant a
    denial reason. The exercise declares its own canonical mask reasons.
    """
    if expectation != 'deny':
        return mask.get('status') == 'admitted' and mask.get('production_c2_active') is True
    raw_reasons = mask.get('rule_ids', [])
    if not isinstance(raw_reasons, list) or any(not isinstance(reason, str) for reason in raw_reasons):
        return False
    reasons = set(raw_reasons)
    allowed = set([expected] if expected_mask_reasons is None else expected_mask_reasons)
    return (mask.get('status') == 'rejected' and mask.get('production_c2_active') is True
            and bool(reasons) and reasons <= allowed)


def _variant(output, built, expected, expectation):
    from stowage.model import Instance, MovePlan
    from stowage.checker import check, report_matches_inputs
    output.mkdir(parents=True, exist_ok=True)
    _atomic(output/'input.json', built['input'])
    _atomic(output/'plan.json', built['plan'])
    input_snapshot, plan_snapshot = read_snapshot(output/'input.json'), read_snapshot(output/'plan.json')
    instance, plan = input_snapshot.model(Instance), plan_snapshot.model(MovePlan)
    checker = check(instance, plan)
    expected_rules = built.get('expected_rules', [expected]) if expectation == 'deny' else []
    expected_secondary_rules = built.get('expected_secondary_rules', []) if expectation == 'deny' else []
    expected_mask_reasons = built.get('expected_mask_reasons', [expected]) if expectation == 'deny' else []
    observation = _checker_observation(checker, expected, expectation, expected_rules=expected_rules,
        expected_secondary_rules=expected_secondary_rules)
    observation['input_bound'] = report_matches_inputs(checker, instance, plan)
    observation['component_epoch_bound'] = checker.provenance.versions == component_versions() and not checker.provenance.input_versions
    _atomic(output/'checker.json', checker.model_dump(mode='json'))
    checker_snapshot = read_snapshot(output/'checker.json')
    mask = _mask_observation(instance, built)
    _atomic(output/'mask.json', mask)
    mask_snapshot = read_snapshot(output/'mask.json')
    missing = []
    if not observation['complete']:
        missing.append('full_checker_complete')
    if not observation['component_epoch_bound']:
        missing.append('checker_component_epoch')
    if not observation['eligible'] or not observation['input_bound']:
        missing.append('checker_exact_rule' if expectation == 'deny' else 'checker_clean_acceptance')
    if not _mask_matches(mask, expected, expectation, expected_mask_reasons=expected_mask_reasons):
        missing.append('production_mask_exact_rule_denial' if expectation == 'deny' else 'production_mask_acceptance')
    metrics = {key:value for part in checker.parts.values() for key,value in part.metrics.items()}
    if checker.count is not None:
        metrics.update(checker.count.model_dump(mode='json'))
    for key, minimum in built.get('required_metrics', {}).items():
        value = metrics.get(key)
        if not isinstance(value, (int, float)) or value < minimum:
            missing.append('metric_'+key)
    if built.get('requires_counted_rehandle'):
        expanded = mask.get('expanded_plan')
        moves = expanded.get('moves', []) if isinstance(expanded, dict) else []
        if not any(move.get('kind') == 'relocate' for move in moves):
            missing.append('counted_yard_rehandle')
    snapshots = dict(input=input_snapshot, plan=plan_snapshot, checker=checker_snapshot, mask=mask_snapshot)
    for snapshot in snapshots.values():
        snapshot.require_fresh()
    return dict(eligible=not missing, missing=missing,
        expected_rules=expected_rules, expected_secondary_rules=expected_secondary_rules,
        expected_mask_reasons=expected_mask_reasons, checker_observation=observation,
        mask_observation=mask, artifacts={kind:dict(path=str(snapshot.path.resolve()),
        sha256=snapshot.sha256) for kind,snapshot in snapshots.items()})


def _prepare_worker_cache(output, *, diagnostic=False):
    """A diagnostic can copy one already-pinned owned base, never an attempt."""
    adapter = _load('cache_adapter')
    if diagnostic and os.environ.get('CAMPAIGN_DIAGNOSTIC_OWNED_BASE') == '1':
        from uuid import uuid4
        pin = _load('pin_transport').read_pin()
        frozen = _load('freeze')
        frozen.verify_numerical_caches(pin)
        for source in pin['sources'].values():
            path = Path(source).resolve()
            if not path.is_relative_to(HERE) or 'cache-attempts' in path.parts:
                raise ValueError('diagnostic base must be an owned immutable copy, never a mutable attempt')
        output = Path(output).resolve()
        work = output/'cache-attempts'/uuid4().hex
        work.mkdir(parents=True, exist_ok=False)
        for name, source in pin['sources'].items():
            adapter._clone(Path(source), work/name)
        frozen.verify_numerical_caches(pin, sources={name:str(work/name) for name in pin['sources']})
        original = adapter.prepare_cache
        adapter.prepare_cache = lambda *args, **kwargs:work
        try:
            adapter.redirect_cache(output, pin)
        finally:
            adapter.prepare_cache = original
    else:
        adapter.redirect_cache(output)
    adapter.enforce_scope()


def _worker(name, output, *, diagnostic=False, prepared=False):
    """The only process that invokes physics; result returned on controlled IPC."""
    output = Path(output).resolve()
    if not output.is_relative_to(HERE):
        raise ValueError('canary output must stay inside campaign-g1')
    output.mkdir(parents=True, exist_ok=True)
    _parent_execution_guard(_receipt_context(ROOT, None, diagnostic=diagnostic), diagnostic=diagnostic)
    if not prepared:
        _prepare_worker_cache(output, diagnostic=diagnostic)
    if driver_identity() != _LOADED_DRIVER_IDENTITY:
        raise ValueError('canary implementation changed since worker module was loaded')
    started = time.monotonic()
    pair = _cases_module().build_pair(name)
    row = dict(name=name, status=pair.get('status', 'missing'), expected_rule=CANARIES[name],
        diagnostic=diagnostic, evidence_eligible=False, eligible=False,
        missing=pair.get('missing', []), reason=pair.get('reason', ''), variants={},
        qualification=pair.get('qualification'), expected_rules=pair.get('expected_rules'),
        expected_secondary_rules=pair.get('expected_secondary_rules'),
        expected_mask_reasons=pair.get('expected_mask_reasons'))
    if pair.get('status') != 'ready':
        return row
    frozen = _load('freeze')
    pin = _load('pin_transport').read_pin()
    frozen.verify_numerical_caches(pin)  # before each actual numerical reuse
    context = _receipt_context(ROOT, pin['sha256'], diagnostic=diagnostic)
    birth_matched = _parent_execution_guard(context, diagnostic=diagnostic)
    row['source_drift'] = not birth_matched
    for key in ('clean', 'defect'):
        built = dict(pair['baseline' if key == 'clean' else 'defect'])
        if key == 'defect':
            for field in ('expected_rules', 'expected_secondary_rules', 'expected_mask_reasons'):
                if field in pair:
                    built.setdefault(field, pair[field])
        expectation = built.get('expectation', 'accept' if key == 'clean' else 'deny')
        try:
            row['variants'][key] = _variant(output/key, built, CANARIES[name], expectation)
        except Exception as exc:
            row['variants'][key] = dict(eligible=False, missing=['execution_failed'],
                reason=f'{type(exc).__name__}: {exc}')
    row.update(status='observed', missing=[key+':'+issue for key, variant in row['variants'].items()
        for issue in variant['missing']], elapsed_s=time.monotonic()-started)
    row['eligible'] = not row['missing'] and not diagnostic
    row['behaviour_passed'] = not row['missing']
    row['evidence_eligible'] = row['eligible']
    if not prepared:
        frozen.verify_numerical_caches(pin)
    after_context = _receipt_context(ROOT, pin['sha256'], diagnostic=diagnostic)
    _parent_execution_guard(after_context, diagnostic=diagnostic)
    if after_context != context:
        if not diagnostic:
            raise ValueError('source/component changed during canary execution')
        row['source_drift'] = True
        row['missing'].append('source_changed_during_diagnostic')
        row['behaviour_passed'] = False
    artifacts = {spec['path']:spec['sha256'] for variant in row['variants'].values()
        for spec in variant.get('artifacts', {}).values()}
    receipt = _load('trusted_receipts').sign_execution(kind='migration-canary-v2', payload=row,
        artifact_pins=artifacts, output=output/'execution-receipt.json', context=context)
    row['execution_receipt'] = receipt
    return row


def _controlled_pair(name, *, diagnostic=False, output=None, pin=None, timeout_s=90):
    import uuid
    if output is None:
        output = HERE/'.canary-admission'/uuid.uuid4().hex/name
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    before = source_identity(ROOT)
    frozen = _load('freeze')
    pin = pin if pin is not None else frozen.snapshot_numerical_caches(ROOT)
    frozen.verify_numerical_caches(pin)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        env[key] = '1'
    env = _load('pin_transport').pin_environment(pin, output, env)
    env['CAMPAIGN_CACHE_ROOT'] = str(output)
    env['CAMPAIGN_CANARY_SOURCE_SHA256'] = before['sha256']
    env['CAMPAIGN_CANARY_DRIVER_SHA256'] = driver_identity()
    command = [sys.executable, str(Path(__file__).resolve()), '--worker', name, '--artifact-dir', str(output)]
    if diagnostic:
        command.append('--diagnostic')
    with (output/'stdout.log').open('w') as log, (output/'stderr.log').open('w') as error:
        child = subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=error, text=True)
        _atomic(output/'pid.json', dict(pid=child.pid, owner_pid=os.getpid()))
        try:
            stdout, _ = child.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            child.terminate()
            try:
                stdout, _ = child.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                stdout, _ = child.communicate()
        log.write(stdout)
    if child.returncode != 0:
        raise ValueError(f'owned canary child exit {child.returncode}')
    lines = [line.removeprefix('GATE_CANARY_RESULT=') for line in stdout.splitlines()
             if line.startswith('GATE_CANARY_RESULT=')]
    if len(lines) != 1:
        raise ValueError('controlled worker did not return one execution result')
    row = json.loads(lines[0])
    if row.get('expected_rule') != CANARIES[name] or row.get('diagnostic') != diagnostic:
        raise ValueError('controlled worker returned another case/mode')
    frozen.verify_numerical_caches(pin)
    if source_identity(ROOT) != before:
        raise ValueError('source changed during controlled canary execution')
    _atomic(output/'case.json', row)
    return row


def _ensure_current_baseline(*, generate_missing=False):
    """Refresh pre-flip inputs rather than perpetually rejecting their old epoch."""
    cases = _cases_module()
    def current():
        instance, qualification = cases.acquire_baseline()
        provenance = instance.provenance
        mismatches = sorted(key for key, value in component_versions().items()
                            if provenance.versions.get(key) != value)
        if provenance.stubs or provenance.input_versions or mismatches:
            raise ValueError('canary baseline is tainted or stale: '+','.join(mismatches))
        return instance, qualification
    try:
        return current()
    except (FileNotFoundError, ValueError):
        if not generate_missing:
            raise
    explicit = os.environ.get('STOWAGE_CANARY_BASELINE')
    if explicit:
        destination = Path(explicit).resolve()
        if not destination.is_relative_to(HERE):
            raise ValueError('stale external baseline is read-only; choose a destination inside campaign-g1')
        cases.generate_baseline(destination)
    else:
        cases.generate_baseline()
    return current()


def _worker_suite(output, *, diagnostic=False, baseline_if_missing=False):
    """One isolated cache attempt serves all cases; no mutable cross-run reuse."""
    output = Path(output).resolve()
    _parent_execution_guard(_receipt_context(ROOT, None, diagnostic=diagnostic), diagnostic=diagnostic)
    _prepare_worker_cache(output, diagnostic=diagnostic)
    rows = {}
    baseline_issue = None
    try:
        _ensure_current_baseline(generate_missing=baseline_if_missing)
    except Exception as exc:
        baseline_issue = f'{type(exc).__name__}: {exc}'
    for name in CANARIES:
        try:
            if baseline_issue and name != 'inport_list_failure':
                rows[name] = dict(status='missing', eligible=False, diagnostic=diagnostic,
                    evidence_eligible=False, expected_rule=CANARIES[name],
                    missing=['qualified_baseline_unavailable'], reason=baseline_issue, variants={})
            else:
                rows[name] = _worker(name, output/name, diagnostic=diagnostic, prepared=True)
        except Exception as exc:
            rows[name] = dict(status='not_run', eligible=False, diagnostic=diagnostic,
                missing=['execution_failed'], reason=f'{type(exc).__name__}: {exc}')
        _atomic(output/'progress.json', dict(diagnostic=diagnostic,
            evidence_eligible=False, baseline_issue=baseline_issue, cases=rows))
    pin = _load('pin_transport').read_pin()
    _load('freeze').verify_numerical_caches(pin)
    return dict(cases=rows, baseline_issue=baseline_issue)


def _execute(root, report_path, *, cases=None, timeout_s=900, diagnostic=False, diagnostic_cache_root=None):
    if cases is not None:
        raise ValueError('execute the whole paired suite; partial evidence is not admitted')
    if Path(root).resolve() != ROOT:
        raise ValueError('execution requires the actual repository root')
    report_path = Path(report_path).resolve()
    if not report_path.is_relative_to(HERE):
        raise ValueError('proof journal must stay inside campaign-g1')
    if report_path.exists():
        raise ValueError('choose a new proof journal path; never replace prior artifacts')
    _load('trusted_receipts').initialize_signer()
    source = source_identity(root)
    run = report_path.with_suffix('')
    run.mkdir(parents=True, exist_ok=False)
    frozen = _load('freeze')
    if diagnostic_cache_root is not None:
        if not diagnostic:
            raise ValueError('an alternate numerical cache root is diagnostic only')
        cache_root = Path(diagnostic_cache_root).resolve()
        if not cache_root.is_relative_to(HERE):
            raise ValueError('diagnostic numerical snapshot must stay inside campaign-g1')
        sources = {name:str(cache_root/name) for name in ('stability','vessels','linear')}
        files = frozen.numerical_cache_inventory(sources)
        pin = dict(schema_version=1,sources=sources,files=files,
            sha256=frozen.numerical_cache_digest(sources,files),generated_additions='Diagnostic-owned immutable snapshot; never production')
    else:
        pin = frozen.snapshot_numerical_caches(root)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
        env[key] = '1'
    env = _load('pin_transport').pin_environment(pin, run, env)
    if diagnostic_cache_root is not None:
        env['CAMPAIGN_DIAGNOSTIC_OWNED_BASE'] = '1'
    else:
        env.pop('CAMPAIGN_DIAGNOSTIC_OWNED_BASE', None)
    env['CAMPAIGN_CACHE_ROOT'] = str(run/'worker')
    env['CAMPAIGN_CANARY_SOURCE_SHA256'] = source['sha256']
    env['CAMPAIGN_CANARY_DRIVER_SHA256'] = driver_identity()
    journal = dict(schema_version=1, diagnostic=diagnostic, evidence_eligible=not diagnostic,
        source=source, driver_sha256=driver_identity(), versions=component_versions(), cases={},
        numerical_cache_pin=dict(path=env['CAMPAIGN_NUMERICAL_CACHE_PIN_PATH'],
            sha256=env['CAMPAIGN_NUMERICAL_CACHE_PIN_SHA256']))
    command = [sys.executable, str(Path(__file__).resolve()), '--suite-worker',
        '--artifact-dir', str(run/'worker')]
    command.append('--baseline-if-missing')
    if diagnostic:
        command.append('--diagnostic')
    started = time.monotonic()
    import resource
    with (run/'stdout.log').open('w') as log, (run/'stderr.log').open('w') as error:
        child = subprocess.Popen(command, cwd=root, env=env, stdout=subprocess.PIPE, stderr=error, text=True)
        _atomic(run/'pid.json', dict(pid=child.pid, owner_pid=os.getpid()))
        timed_out = False
        try:
            stdout, _ = child.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            child.terminate()
            try:
                stdout, _ = child.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                stdout, _ = child.communicate()
        log.write(stdout)
    lines = [line.removeprefix('GATE_CANARY_SUITE=') for line in stdout.splitlines()
             if line.startswith('GATE_CANARY_SUITE=')]
    if child.returncode == 0 and len(lines) == 1:
        observed = json.loads(lines[0])
        journal['cases'] = observed['cases']
        journal['baseline_issue'] = observed.get('baseline_issue')
    elif diagnostic and (run/'worker/progress.json').exists():
        # An interrupted diagnostic may disclose partial observations; it can
        # never become evidence and its raw progress file is never admission.
        journal['cases'] = read_snapshot(run/'worker/progress.json').json().get('cases', {})
    for name in CANARIES:
        journal['cases'].setdefault(name, dict(status='not_run', eligible=False,
            missing=['controlled_suite_timeout' if timed_out else 'controlled_suite_failed'],
            reason=f'owned suite child exit {child.returncode}'))
    journal['execution'] = dict(wall_s=time.monotonic()-started, exit_code=child.returncode,
        timed_out=timed_out, max_child_rss_bytes=resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
    if source_identity(root) != source:
        journal['evidence_eligible'] = False
        journal['source_drift'] = True
    try:
        frozen.verify_numerical_caches(pin)
    except Exception as exc:
        if not diagnostic:
            raise
        journal['cache_drift'] = f'{type(exc).__name__}: {exc}'
        journal['evidence_eligible'] = False
    _atomic(report_path, journal)
    return journal


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--report', type=Path, default=DEFAULT_REPORT)
    parser.add_argument('--diagnostic-cache-root', type=Path, help='Diagnostic only: a fixed owned numerical snapshot')
    parser.add_argument('--diagnostic', action='store_true', help='Stamp execution diagnostic, never evidence')
    parser.add_argument('--execute', action='store_true', help='Explicit numerical work; estimate/admit first')
    parser.add_argument('--suite-worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--baseline-if-missing', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--generate-baseline', type=Path, help='Explicit diagnostic production-slice generator worker')
    parser.add_argument('--worker', choices=CANARIES, help=argparse.SUPPRESS)
    parser.add_argument('--artifact-dir', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.diagnostic_cache_root is not None and not (args.diagnostic and args.execute):
        parser.error('--diagnostic-cache-root requires --diagnostic --execute')
    if args.suite_worker:
        result = _worker_suite(args.artifact_dir, diagnostic=args.diagnostic, baseline_if_missing=args.baseline_if_missing)
        print('GATE_CANARY_SUITE='+json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    if args.generate_baseline:
        if not args.diagnostic:
            parser.error('--generate-baseline is an explicit diagnostic worker; production --execute refreshes its baseline automatically')
        if args.artifact_dir is None:
            parser.error('--generate-baseline requires --artifact-dir for scoped numerical caches')
        output = args.artifact_dir.resolve()
        if not output.is_relative_to(HERE):
            parser.error('baseline numerical work must stay inside campaign-g1')
        _prepare_worker_cache(output, diagnostic=args.diagnostic)
        instance = _cases_module().generate_baseline(args.generate_baseline)
        print(json.dumps(dict(diagnostic=True, evidence_eligible=False, instance_id=instance.id)))
        return 0
    if args.worker:
        result = _worker(args.worker, args.artifact_dir, diagnostic=args.diagnostic)
        print('GATE_CANARY_RESULT='+json.dumps(result, sort_keys=True, allow_nan=False))
        return 0
    if args.execute and args.diagnostic:
        journal = _execute(args.root, args.report, diagnostic=True, diagnostic_cache_root=args.diagnostic_cache_root)
        result = dict(diagnostic=True, evidence_eligible=False, eligible=False,
            missing_cases=[name for name, row in journal['cases'].items() if not row.get('behaviour_passed')],
            cases=journal['cases'], report_path=str(args.report))
    else:
        result = canary_report(args.root, args.report, execute=args.execute)
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result['evidence_eligible'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
