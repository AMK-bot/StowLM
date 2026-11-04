"""Synthetic planner handover with an offline, independently checkable receipt."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import sys
from time import perf_counter

_root = str(Path(__file__).resolve().parents[4])
if _root not in sys.path:
    sys.path.insert(0, _root)

from experiments.mvp.pipeline.contract.schema import (
    PACKAGE_SCHEMA, Receipt, StageRecord, canonical_json, chain, sha256_file, sha256_json,
)
from stowage.model import Instance, MovePlan

from .receipt import sign_receipt, verify_receipt
from .rule_coverage import rule_coverage as _rule_coverage

HERE = Path(__file__).resolve().parent
REQUIRED_FILES = {
    'instance.json', 'plan.json', 'checker-report.json', 'bay-plan.html',
    'crane-sequence.csv', 'stability.json', 'note-to-master.md', 'receipt.json',
    'packet.json', 'engine-manifest.json', 'rule-coverage.json',
    'handover-scope.json', 'manifest.csv', 'weight-by-port.csv', 'tank-plan.csv',
    'restows.csv', 'yard-sequence.csv', 'ballast-plan.csv', 'cargo-work.json', 'not-included.html', 'stability-summary.md',
}


def _dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + '\n', encoding='utf-8')


def _source_hash():
    sources = {p.name: sha256_file(p) for p in sorted(HERE.glob('*.py')) if not p.name.startswith('test_')}
    return sha256_json(sources)


def _load(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError(f'unsafe symlink: {path.name}')
    return json.loads(path.read_text(encoding='utf-8'))


def _rule_checks(instance, plan, rules, *, packet=None):
    """Recheck confirmed readings, without invoking a parser, allocator or solver."""
    if packet is not None:
        try:
            expected = [row['text'] for row in packet.get('voyage_sentences', [])]
            actual = [row.get('sentence') for row in rules]
            if actual != expected:
                return {'passed': False, 'rules': [], 'errors': [{
                    'error': 'packet sentences differ from receipt rules (order and duplicate count must match)',
                    'expected_sentences': expected, 'receipt_sentences': actual,
                }]}
        except (AttributeError, TypeError, KeyError) as exc:
            return {'passed': False, 'rules': [], 'errors': [{'error': 'invalid packet sentences: ' + str(exc)}]}
    if not rules:
        return {'passed': True, 'rules': [], 'errors': []}
    from experiments.mvp.reader.pipeline import intrinsic_violations
    from experiments.mvp.reader.schema import context_from_instance, validate_answer
    rows, errors = [], []
    for index, row in enumerate(rules):
        problems = []
        try:
            if row.get('confirmed') is not True:
                problems.append(f"Instruction {row.get('sentence')!r} has no recorded confirmation; "
                                'a planner must confirm its reading before a package is written')
            reading = row.get('reading', row.get('canonical_envelope'))
            if reading is None:
                raise ValueError('confirmed instruction has no canonical reading')
            answer = validate_answer(canonical_json(reading), context_from_instance(instance))
            problems.extend(intrinsic_violations(instance, answer, plan))
        except Exception as exc:
            problems.append(f'instruction recheck: {type(exc).__name__}: {exc}')
        rows.append({'index': index, 'passed': not problems, 'violations': problems})
        errors.extend({'rule': index, 'error': problem} for problem in problems)
    return {'passed': not errors, 'rules': rows, 'errors': errors}


def _normalise_approval(rules, confirmation, steps, *, reading=None, runner_receipt=None, packet=None):
    """Recover missing runner flags, retaining any explicitly invalid approval."""
    from .provenance import confirmed_instruction_rows
    if not isinstance(rules, list) or any(not isinstance(row, dict) for row in rules):
        raise ValueError('instruction rules must be a list of sentence mappings')
    recovered = confirmed_instruction_rows(rules, confirmation, steps, reading=reading,
                                           runner_receipt=runner_receipt, packet=packet)
    if len(recovered) != len(rules):
        normalised = [dict(row) for row in rules]
    else:
        normalised = [dict(original) if 'confirmed' in original and original['confirmed'] is not True else approved
                      for original, approved in zip(rules, recovered)]
    for row in normalised:
        if 'canonical_envelope' in row:
            if 'reading' in row and row['reading'] != row['canonical_envelope']:
                raise ValueError(f"Instruction {row.get('sentence')!r} has conflicting canonical readings")
            row['reading'] = row.pop('canonical_envelope')
    return normalised


def _select_rules(outcome, evidence, inherited):
    def reading(row):
        for key in ('reading', 'canonical_envelope', 'envelope'):
            if key in row:
                return row[key]
        return row if row.get('schema_version') == 'stowlm.reader/2' else None

    sources = []
    for source, key, name in ((inherited, 'rules', 'receipt.rules'),
                              (evidence, 'runner_rules', 'evidence.runner_rules'),
                              (outcome, 'rules', 'outcome.rules'),
                              (evidence, 'rules', 'evidence.rules')):
        if key in source:
            sources.append((name, source[key]))
    selected = sources[0][1] if sources else []
    for name, rows in sources:
        if not isinstance(rows, list) or len(rows) != len(selected):
            raise ValueError('Conflicting instruction rule sources: ' + name)
        for first, other in zip(selected, rows):
            a, b = reading(first), reading(other)
            if (a != b or ('sentence' in first and 'sentence' in other and first['sentence'] != other['sentence'])
                    or (first.get('adapter_sha256') is not None and other.get('adapter_sha256') is not None
                        and first['adapter_sha256'] != other['adapter_sha256'])):
                raise ValueError('Conflicting instruction rule sources: ' + name)
            if name != 'evidence.rules' and any(first.get(key) != other.get(key)
                    for key in ('sentence', 'adapter_sha256', 'parser', 'confirmed')):
                raise ValueError('Conflicting instruction provenance: ' + name)
    return selected


def _plan_facts(instance, plan, report):
    from engine.peel import inspect
    row = inspect(instance, plan, report)
    return dict(lifts=row['lifts'], level=row['level'], broken_rules=row['broken_rules'],
                boxes_ashore=row['boxes_left_ashore'], completed=row['completed'])


def _check_claims(outcome, evidence, inherited, facts):
    for source in (outcome, evidence, inherited.get('checker', {})):
        for key, actual in facts.items():
            if key in source and source[key] is not None and source[key] != actual:
                raise ValueError(key + ' disagrees with fresh checker classification')
    if inherited.get('lifts') is not None and inherited['lifts'] != facts['lifts']:
        raise ValueError('lifts disagrees with fresh checker measurement')


def _engine_manifest(outcome, evidence, inherited):
    expected = outcome.get('engine_manifest_sha256', evidence.get('engine_manifest_sha256',
                           inherited.get('engine_manifest_sha256')))
    supplied = outcome.get('engine_manifest')
    if supplied is not None:
        content = (json.dumps(supplied, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
    else:
        from experiments.mvp.pipeline.binding.adapter import MANIFEST
        content = MANIFEST.read_bytes()
    from hashlib import sha256
    if not expected or sha256(content).hexdigest() != expected:
        raise ValueError('engine manifest is missing or differs from engine_manifest_sha256')
    manifest = json.loads(content)
    if not isinstance(manifest, dict):
        raise ValueError('engine manifest must be a JSON object')
    declared_version = outcome.get('engine_version', evidence.get('engine_version', inherited.get('engine_version')))
    if (not isinstance(declared_version, str) or not declared_version
            or declared_version != manifest.get('engine_version', manifest.get('version'))):
        raise ValueError('engine_version disagrees with engine manifest')
    return content, expected


def _checker(instance, plan):
    from stowage.checker import check
    return check(instance, plan)


def _banner(status, evidence, report):
    if status == 'relaxed_plan':
        broken = sorted(set(evidence.get('broken_rules') or []) | {v.rule_id for v in report.violations})
        level = evidence.get('level', 'unspecified')
        ashore = evidence.get('boxes_ashore', report.shut_out)
        warning = (" Breaks a class or statutory limit; needs a person's sign-off." if level == 4 else
                   ' Not safe to sail, shown only to say what would have to change.' if level == 5 else '')
        return (f'RELAXED PLAN — level {level}; broken rules: {canonical_json(broken)}; '
                f'boxes ashore: {len(ashore) if isinstance(ashore, list) else ashore}. '
                'Synthetic ships and cargo, simulated port. Not a full checker pass.' + warning)
    return 'Synthetic ships and cargo, simulated port. Full checker passed; local demo receipt.'


def _crane_csv(plan, banner, relaxed, *, covers_off=None):
    stream = io.StringIO(newline='')
    stream.write('# ' + banner.replace('\n', ' ') + '\n')
    writer = csv.writer(stream)
    writer.writerow(['lift_order', 'box', 'from', 'to', 'kind'])
    parked_covers = dict(covers_off or {})
    for order, move in enumerate(plan.moves, 1):
        box = getattr(move, 'container_id', getattr(move, 'cover_id', ''))
        source = getattr(move, 'src', parked_covers.get(box, 'quay') if move.kind == 'hatch_cover_on' else
                         'quay' if move.kind == 'restow_on' else
                         box if move.kind == 'hatch_cover_off' else 'gate' if move.kind == 'gate_in' else '')
        target = getattr(move, 'dst', 'quay' if move.kind == 'restow_off' else
                         box if move.kind == 'hatch_cover_on' else 'gate' if move.kind == 'gate_out' else '')
        writer.writerow([order, box, source, target, move.kind])
        if move.kind == 'hatch_cover_off':
            parked_covers[box] = target
        elif move.kind == 'hatch_cover_on':
            parked_covers.pop(box, None)
    return stream.getvalue()


def write_package(outcome, out_dir):
    """Write a handover folder and return its JSON index. See README for input mapping.

    A fresh full checker gates the labels; the caller cannot turn a failing plan
    into a pass by supplying a report. No model or network is called here.
    """
    package_started = perf_counter()
    from .note import render_note
    from .render import render_bay_plan, stability_summary
    from .scope import scope_summary
    from .manifest import render_manifest_csv, render_weight_by_port_csv, render_tank_csv, render_restow_csv
    from .crane import cargo_work
    from .exclusions import render_not_included
    from .provenance import require_hero_provenance

    if hasattr(outcome, 'model_dump'):
        outcome = outcome.model_dump(mode='json')
    if not isinstance(outcome, dict):
        raise ValueError('outcome must be a JSON mapping')
    status = outcome.get('status')
    if status not in {'checked_plan', 'relaxed_plan'}:
        raise ValueError('Only checked_plan or explicitly relaxed_plan outcomes have a plan package')
    instance = Instance.model_validate(outcome['instance'])
    plan = MovePlan.model_validate(outcome['plan'])
    if plan.instance_id != instance.id or plan.call_index != instance.call_index:
        raise ValueError('plan does not identify this instance and call')
    evidence = outcome.get('evidence') or {}
    inherited = outcome.get('receipt') or {}
    presentation = outcome.get('presentation', 'development_fixture' if outcome.get('fixture_provenance') else 'handover')
    if presentation not in {'hero', 'development_fixture', 'handover'}:
        raise ValueError('presentation must be hero, development_fixture or handover')
    hero = require_hero_provenance(outcome) if presentation == 'hero' else None
    packet = outcome.get('packet')
    if not isinstance(packet, dict):
        raise ValueError('packet artifact is required to audit every sentence')
    confirmation = outcome.get('confirmation', evidence.get('confirmation'))
    approval = dict(rules=_select_rules(outcome, evidence, inherited),
                    confirmation=confirmation, steps=inherited.get('steps', []),
                    reading=outcome.get('reading'), runner_receipt=inherited or None)
    rules = _normalise_approval(**approval, packet=packet)
    instruction_check = _rule_checks(instance, plan, rules, packet=packet)
    if not instruction_check['passed']:
        raise ValueError('Instructions failed confirmation or deterministic recheck: ' + canonical_json(instruction_check['errors']))
    manifest_content, engine_manifest_hash = _engine_manifest(outcome, evidence, inherited)
    started = perf_counter()
    report = _checker(instance, plan)
    checker_wall = perf_counter() - started
    facts = _plan_facts(instance, plan, report)
    _check_claims(outcome, evidence, inherited, facts)
    evidence = {**evidence, **facts}
    if status == 'checked_plan' and not report.passed:
        raise ValueError('checked_plan failed the full checker: ' + ', '.join(sorted({v.rule_id for v in report.violations})))
    if status == 'relaxed_plan' and report.passed:
        raise ValueError('relaxed_plan requires a failing checker report; use checked_plan for a full pass')
    if status == 'relaxed_plan' and facts['level'] not in range(1, 6):
        raise ValueError('relaxed_plan has an unpeelable checker failure')
    if status == 'checked_plan' and (facts['level'] != 0 or not facts['completed']):
        raise ValueError('checked_plan disagrees with fresh checker classification')
    packet_hash = sha256_json(packet)
    pipeline_hash = outcome.get('pipeline_sha256', inherited.get('pipeline_sha256'))
    if pipeline_hash in ('', 'not frozen yet'):
        pipeline_hash = None
    if pipeline_hash is not None and (not isinstance(pipeline_hash, str) or len(pipeline_hash) != 64
                                     or any(c not in '0123456789abcdef' for c in pipeline_hash)):
        raise ValueError('pipeline_sha256 must be a SHA256 or not frozen yet')
    pipeline_freeze = 'frozen' if pipeline_hash else 'not frozen yet'
    hero_handover = outcome.get('hero_handover')
    if hero_handover is not None:
        from .provenance import handover_errors
        failures = handover_errors(hero_handover, packet, rules, pipeline_hash,
                                   outcome.get('fixture_provenance'))
        if failures:
            raise ValueError('hero handover: ' + '; '.join(failures))
    label = outcome.get('label', inherited.get('label', 'stored'))
    if label not in {'live', 'stored', 'recorded', 'generated'}:
        raise ValueError('invalid plan mode label')
    scope = scope_summary(instance, plan, report)
    banner = scope['banner'] + ' ' + _banner(status, evidence, report)
    if label == 'stored':
        date = outcome.get('computed_utc', evidence.get('computed_utc'))
        banner += (' Stored plan, computed on ' + date + ', rechecked now.' if date else
                   ' Stored plan; original computation date unrecorded; rechecked now.')
    if outcome.get('fixture_provenance', {}).get('label'):
        banner += ' Stored synthetic development fixture; source provenance is recorded separately.'
    if hero_handover:
        reader = hero_handover['reader']
        banner += (' Recorded reading: ' if reader.get('mode') == 'recorded' else ' Live reading: ')
        banner += 'StowLM Reader ' + reader['name'] + '.'
    banner += ' Pipeline: ' + pipeline_freeze + '.'
    coverage = _rule_coverage()
    banner += f" Checker coverage: {coverage['active_rule_count']} active, {coverage['inactive_rule_count']} inactive."
    directory = Path(out_dir)
    if directory.resolve() == (HERE / '.keys').resolve():
        raise ValueError('out_dir cannot be the application key directory')
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        raise ValueError('out_dir must be empty; use a new folder to preserve existing receipts')
    report_data = report.model_dump(mode='json')
    instance_data, plan_data = instance.model_dump(mode='json'), plan.model_dump(mode='json')
    _dump(directory / 'instance.json', instance_data)
    _dump(directory / 'plan.json', plan_data)
    _dump(directory / 'checker-report.json', report_data)
    _dump(directory / 'packet.json', packet)
    (directory / 'engine-manifest.json').write_bytes(manifest_content)
    _dump(directory / 'rule-coverage.json', coverage)
    _dump(directory / 'handover-scope.json', {**scope, 'banner': banner})
    work = cargo_work(instance, plan, report, banner=banner)
    _dump(directory / 'cargo-work.json', {'banner': banner, **work['evidence']})
    for name, text in work['files'].items():
        (directory / name).write_text(text, encoding='utf-8')
    for name, text in {
        'manifest.csv': render_manifest_csv(instance, plan, report, banner=banner),
        'weight-by-port.csv': render_weight_by_port_csv(scope, banner=banner),
        'tank-plan.csv': render_tank_csv(scope, banner=banner),
        'restows.csv': render_restow_csv(instance, plan, banner=banner),
        'not-included.html': render_not_included(banner=banner, report=report, scope=scope, cargo_work=work['evidence']),
    }.items():
        (directory / name).write_text(text, encoding='utf-8')
    stability = stability_summary(instance, plan, report)
    stability.update(status=status, label=label, numbers_label='measured', banner=banner,
                     weight_table=scope['weights'], weight_closes=scope['weight_closes'],
                     ballast_line=scope['ballast_line'], cargo_work_stability_checked=False)
    _dump(directory / 'stability.json', stability)
    (directory / 'bay-plan.html').write_text(render_bay_plan(instance, plan, report, stability,
        banner=banner, scope=scope, rules=rules, confirmation=confirmation), encoding='utf-8')
    note_started = perf_counter()
    note = render_note(instance, plan, report, relaxed=status == 'relaxed_plan',
                       broken_rules=evidence.get('broken_rules'), recorded_selection=outcome.get('note_selection'),
                       rules=rules, scope=scope, confirmation=confirmation, cargo_work=work['evidence'])
    note_wall = perf_counter() - note_started
    note_text = banner + '\n\n' + note['text']
    (directory / 'note-to-master.md').write_text(note_text, encoding='utf-8')
    _dump(directory / 'note-evidence.json', note)
    summary_lines = [banner, '', '# Stability, strength and weight account', '',
        'Departure scalars and sampled-condition margins come from checker-report.json. '
        'Stability during loading is not checked. List estimates are labelled projections in cargo-work.json.', '',
        '| Component | Mass (t) |', '| --- | ---: |']
    summary_lines.extend(f"| {row['component']} | {row['mass_t']:.6f} |" for row in scope['weights'])
    summary_lines.extend([f"| **Total / checker displacement** | **{scope['weight_total_kg'] / 1000:.6f}** |", '',
        f"Closure residual: {scope['weight_residual_kg']} kg. {scope['ballast_line']}", '',
        '| POD | Boxes | Gross mass (t) |', '| --- | ---: | ---: |'])
    summary_lines.extend(f"| {row['pod']} | {row['boxes']} | {row['mass_t']:.6f} |" for row in scope['weight_by_port'])
    summary_lines.extend([f"| Fixed background: POD not declared | {scope['background_boxes']} | {scope['background_mass_t']:.6f} |", '',
        f"Cargo-work operational status: {work['evidence']['operational_status']}", '',
        "Lashing is our approximation, with synthetic gear, because class rules aren't public.", '',
        '## Checker statements', '', *('- ' + row['text'] for row in note['statements']
            if row['id'].startswith(('departure_', 'tight_limit:'))), ''])
    (directory / 'stability-summary.md').write_text('\n'.join(summary_lines), encoding='utf-8')
    if outcome.get('fixture_provenance'):
        _dump(directory / 'provenance.json', outcome['fixture_provenance'])
    if hero is not None:
        _dump(directory / 'runner-receipt.json', inherited)
        _dump(directory / 'hero-provenance.json', hero)
    files = {p.name: sha256_file(p) for p in sorted(directory.iterdir())}
    metadata = dict(schema_version=PACKAGE_SCHEMA, packet_sha256=packet_hash, status=status,
                    label=label, synthetic=True, pipeline_sha256=pipeline_hash or 'not frozen yet',
                    presentation=presentation, banner=banner,
                    exchange_format={'status': 'cut', 'reason':
                    'BAPLIE omitted: synthetic identifiers and incomplete exchange metadata; see package/BAPLIE.md.'})
    records = [StageRecord.model_validate(r) for r in outcome.get('upstream_records', [])]
    records.extend(StageRecord.model_validate(r) for r in outcome.get('records', outcome.get('steps', inherited.get('steps', []))))
    from stowage.checker import full
    note_record = (outcome.get('note_evidence') or {}) if note['mode'] == 'recorded' else {}
    records.extend([
        StageRecord(stage='package.checker', mode='live', input_sha256=sha256_json({'instance': instance_data, 'plan': plan_data}),
                    output_sha256=sha256_json(report_data), source_sha256=sha256_file(full.__file__), wall_s=checker_wall,
                    notes='Full checker rerun and confirmed instruction semantics; no model or network.'),
        StageRecord(stage='package.note', mode=note['mode'], input_sha256=sha256_json(report_data),
                    output_sha256=sha256_json(note), model=note.get('model'), prompt_sha256=note.get('prompt_sha256'),
                    source_sha256=note.get('source_sha256'),
                    wall_s=float(note_record.get('wall_s', note_record.get('wall_s_measured', note_wall))),
                    cost_usd=float(note_record.get('cost_usd', note.get('cost_usd', 0))),
                    notes='Allow-listed checker statements only; ' + note['mode']),
        StageRecord(stage='package.write', mode='live', input_sha256=sha256_json(report_data),
                    output_sha256=sha256_json(files), source_sha256=_source_hash(),
                    wall_s=perf_counter() - package_started,
                    notes='Artifact file manifest; local demo key signs the receipt. No carrier approval.'),
    ])
    receipt = Receipt(
        pipeline_sha256=pipeline_hash,
        engine_version=outcome.get('engine_version', evidence.get('engine_version', inherited.get('engine_version', 'unknown'))),
        engine_manifest_sha256=engine_manifest_hash,
        packet_sha256=packet_hash, instance_sha256=sha256_json(instance_data), plan_sha256=sha256_json(plan_data),
        steps=chain(records), rules=rules, checker=dict(
            passed=report.passed and instruction_check['passed'], report_sha256=sha256_json(report_data),
            level=facts['level'], broken_rules=facts['broken_rules'], boxes_ashore=facts['boxes_ashore'],
            completed=facts['completed'], instruction_recheck=instruction_check,
            instruction_approval=approval,
            scope_banner=scope['banner'], cargo_work_stability_checked=False,
            hero_provenance=hero,
            hero_handover=hero_handover,
            pipeline_freeze=pipeline_freeze, **coverage, package_files=files, package_index=metadata),
        lifts=facts['lifts'], label=label,
    )
    # This service's application key must not follow a runner's temporary key override.
    signed = sign_receipt(receipt.model_dump(mode='json'), key_dir=HERE / '.keys')
    _dump(directory / 'receipt.json', signed)
    index = {**metadata, 'files': {**files, 'receipt.json': sha256_file(directory / 'receipt.json')}}
    _dump(directory / 'package.json', index)
    return index


def verify_package(path, *, expected_public_key=None):
    """Check file hashes, signed chain, signature, then rerun the full checker.

    Returns independent checks even after integrity failures. Never parses words,
    loads a model, calls a solver, searches for a plan, or uses the network.
    """
    started = perf_counter()
    directory = Path(path)
    if directory.is_file():
        directory = directory.parent
    result = {name: {'passed': False, 'errors': []} for name in ('files', 'chain', 'signature', 'checker', 'rules', 'coverage', 'handover')}
    errors = result['files']['errors']
    receipt = {}
    try:
        receipt = _load(directory / 'receipt.json')
        if not isinstance(receipt, dict):
            raise ValueError('receipt must be a JSON object')
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        errors.append({'file': 'receipt.json', 'error': str(exc)})
        receipt = {}
    try:
        index = _load(directory / 'package.json')
        if not isinstance(index, dict):
            raise ValueError('index must be a JSON object')
        files = index.get('files')
        if not isinstance(files, dict):
            raise ValueError('package.json files must be an object')
        for required in sorted(REQUIRED_FILES - files.keys()):
            errors.append({'file': required, 'error': 'required file missing from index'})
        for name, expected in files.items():
            candidate = directory / name
            if not isinstance(name, str) or Path(name).name != name or candidate.is_symlink():
                errors.append({'file': str(name), 'error': 'unsafe file path'})
                continue
            try:
                actual = sha256_file(candidate)
                if actual != expected:
                    errors.append({'file': name, 'error': 'sha256 mismatch', 'expected': expected, 'actual': actual})
            except OSError:
                errors.append({'file': name, 'error': 'missing or unreadable file'})
        extras = {p.name for p in directory.iterdir()} - set(files) - {'package.json'}
        errors.extend({'file': name, 'error': 'unlisted package file'} for name in sorted(extras))
        signed = receipt.get('checker', {})
        if not isinstance(signed, dict):
            raise ValueError('receipt checker must be an object')
        if signed.get('package_files') != {k: v for k, v in files.items() if k != 'receipt.json'}:
            errors.append({'file': 'package.json', 'error': 'file manifest differs from signed receipt'})
        if signed.get('package_index') != {k: v for k, v in index.items() if k != 'files'}:
            errors.append({'file': 'package.json', 'error': 'index metadata differs from signed receipt'})
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        errors.append({'file': 'package.json', 'error': str(exc)})
    result.update(verify_receipt(receipt, expected_public_key=expected_public_key))
    result['files']['passed'] = not errors
    for name in ('chain', 'signature'):
        if not result[name]['passed'] and not result[name]['errors']:
            result[name]['errors'].append({'error': 'receipt could not be read or validated'})
    checker_started = perf_counter()
    try:
        instance_data, plan_data = _load(directory / 'instance.json'), _load(directory / 'plan.json')
        instance, plan = Instance.model_validate(instance_data), MovePlan.model_validate(plan_data)
        identities = {'instance_sha256': sha256_json(instance.model_dump(mode='json')),
                      'plan_sha256': sha256_json(plan.model_dump(mode='json'))}
        for key, value in identities.items():
            if receipt.get(key) != value:
                result['checker']['errors'].append({'error': key + ' differs from signed receipt'})
        report = _checker(instance, plan)
        report_data = report.model_dump(mode='json')
        result['checker'].update(full_checker_passed=report.passed, report=report_data,
                                 broken_rules=sorted({v.rule_id for v in report.violations}),
                                 boxes_ashore=report.shut_out)
        facts = _plan_facts(instance, plan, report)
        result['checker']['classification'] = facts
        for key, actual in facts.items():
            signed_claim = receipt.get(key) if key == 'lifts' else receipt.get('checker', {}).get(key)
            if signed_claim != actual:
                result['checker']['errors'].append({'error': key + ' differs from fresh checker classification',
                                                    'signed': signed_claim, 'actual': actual})
        legacy_approval = None
        try:
            from .scope import scope_summary
            from .crane import cargo_work
            from .provenance import require_hero_provenance
            scope = scope_summary(instance, plan, report)
            stored_scope = _load(directory / 'handover-scope.json')
            expected_scope = {**scope, 'banner': receipt.get('checker', {}).get('package_index', {}).get('banner')}
            if stored_scope != expected_scope:
                result['handover']['errors'].append({'file': 'handover-scope.json', 'error': 'scope or weight accounting differs from native departure'})
            # Estimates cite the signed source report's GM. Independently qualified
            # cache/direct methods may change fresh scalar bits without changing
            # the rerun verdict; never silently rewrite the historical estimate.
            from stowage.model import CheckReport
            estimate_report = CheckReport.model_validate(_load(directory / 'checker-report.json'))
            work = cargo_work(instance, plan, estimate_report, banner=expected_scope['banner'])
            if _load(directory / 'cargo-work.json') != {'banner': expected_scope['banner'], **work['evidence']}:
                result['handover']['errors'].append({'file': 'cargo-work.json', 'error': 'cargo-work estimates differ from native replay'})
            if receipt.get('checker', {}).get('scope_banner') != scope['banner']:
                result['handover']['errors'].append({'error': 'scope banner differs from native departure'})
            if receipt.get('checker', {}).get('cargo_work_stability_checked') is not False:
                result['handover']['errors'].append({'error': 'cargo-work stability must remain explicitly unchecked'})
            presentation = receipt.get('checker', {}).get('package_index', {}).get('presentation')
            if presentation == 'hero':
                proof = _load(directory / 'hero-provenance.json')
                runner_receipt = _load(directory / 'runner-receipt.json')
                audited = require_hero_provenance(dict(packet=_load(directory / 'packet.json'),
                    instance=instance_data, plan=plan_data, receipt=runner_receipt,
                    evidence={'runner_rules': runner_receipt.get('rules'), 'confirmation': proof.get('confirmation')}))
                if proof != audited or proof != receipt.get('checker', {}).get('hero_provenance'):
                    result['handover']['errors'].append({'error': 'hero provenance differs from runner evidence'})
                elif receipt.get('rules', []) == runner_receipt.get('rules', []):
                    # Older hero artifacts retain their original runner rows.
                    # Recover only after the independent hero provenance audit.
                    legacy_approval = dict(rules=runner_receipt['rules'],
                        confirmation=proof.get('confirmation'), steps=runner_receipt.get('steps', []),
                        runner_receipt=runner_receipt)
                handover = receipt.get('checker', {}).get('hero_handover')
                if handover is not None:
                    from .provenance import handover_errors
                    failures = handover_errors(handover, _load(directory / 'packet.json'),
                                              receipt.get('rules', []), receipt.get('pipeline_sha256'),
                                              _load(directory / 'provenance.json'))
                    result['handover']['errors'].extend({'error': error} for error in failures)
            elif presentation not in {'handover', 'development_fixture'}:
                result['handover']['errors'].append({'error': 'unknown handover presentation'})
            result['handover']['passed'] = not result['handover']['errors']
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result['handover']['errors'].append({'error': 'handover evidence: ' + str(exc)})
        frozen = 'frozen' if receipt.get('pipeline_sha256') else 'not frozen yet'
        if receipt.get('checker', {}).get('pipeline_freeze') != frozen:
            result['checker']['errors'].append({'error': 'pipeline_freeze differs from pipeline_sha256'})
        if receipt.get('checker', {}).get('package_index', {}).get('pipeline_sha256') != (receipt.get('pipeline_sha256') or 'not frozen yet'):
            result['checker']['errors'].append({'error': 'pipeline_sha256 display differs from receipt'})
        try:
            packet = _load(directory / 'packet.json')
            if not isinstance(packet, dict):
                raise ValueError('packet must be a JSON object')
            rules = receipt.get('rules', [])
            approval = receipt.get('checker', {}).get('instruction_approval')
            if approval is not None:
                if not isinstance(approval, dict) or set(approval) != {'rules', 'confirmation', 'steps', 'reading', 'runner_receipt'}:
                    raise ValueError('invalid instruction approval evidence')
                normalised = _normalise_approval(**approval, packet=packet)
                if normalised != rules:
                    raise ValueError('receipt instruction sentences or approvals differ from recorded approval evidence: '
                                     + repr([row.get('text') for row in packet.get('voyage_sentences', [])]))
                rules = normalised
            elif legacy_approval is not None:
                rules = _normalise_approval(**legacy_approval, packet=packet)
            result['rules'] = _rule_checks(instance, plan, rules, packet=packet)
        except (OSError, ValueError, TypeError) as exc:
            result['rules'] = {'passed': False, 'rules': [], 'errors': [{'error': 'packet sentences unavailable: ' + str(exc)}]}
        try:
            coverage = _rule_coverage()
            for key, actual in coverage.items():
                if receipt.get('checker', {}).get(key) != actual:
                    result['coverage']['errors'].append({'error': key + ' differs from current checker coverage'})
            if _load(directory / 'rule-coverage.json') != coverage:
                result['coverage']['errors'].append({'error': 'rule-coverage.json differs from current checker coverage'})
            result['coverage'].update(passed=not result['coverage']['errors'],
                                      active_rule_count=coverage['active_rule_count'],
                                      inactive_rule_count=coverage['inactive_rule_count'],
                                      catalogue_sha256=coverage['catalogue_sha256'])
        except (OSError, ValueError, TypeError) as exc:
            result['coverage']['errors'].append({'error': 'rule coverage unavailable: ' + str(exc)})
        for filename, key in (('packet.json', 'packet_sha256'), ('engine-manifest.json', 'engine_manifest_sha256')):
            if (directory / filename).exists():
                try:
                    digest = (sha256_json(_load(directory / filename)) if filename == 'packet.json'
                              else sha256_file(directory / filename))
                    if digest != receipt.get(key):
                        result['checker']['errors'].append({'error': key + ' differs from signed receipt'})
                    if filename == 'engine-manifest.json':
                        manifest = _load(directory / filename)
                        if receipt.get('engine_version') != manifest.get('engine_version', manifest.get('version')):
                            result['checker']['errors'].append({'error': 'engine_version differs from engine manifest'})
                except (OSError, ValueError) as exc:
                    result['checker']['errors'].append({'error': filename + ': ' + str(exc)})
            else:
                result['checker']['errors'].append({'error': filename + ' is required'})
        if not report.passed:
            result['checker']['errors'].append({'error': 'full checker rejected plan',
                                               'rules': result['checker']['broken_rules']})
        recorded_report = _load(directory / 'checker-report.json')
        if sha256_json(recorded_report) != receipt.get('checker', {}).get('report_sha256'):
            result['checker']['errors'].append({'error': 'checker-report.json differs from signed report hash'})
        # Part values can contain timing metrics; verdict/rules and identities must
        # agree, while a hash of an old report is never accepted as a fresh check.
        if receipt.get('checker', {}).get('passed') != report.passed:
            result['checker']['errors'].append({'error': 'fresh checker verdict differs from receipt'})
        result['checker']['passed'] = not result['checker']['errors']
    except Exception as exc:
        result['checker']['errors'].append({'error': f'checker rerun: {type(exc).__name__}: {exc}'})
    result['checker']['wall_s'] = perf_counter() - checker_started
    result['passed'] = all(result[name]['passed'] for name in ('files', 'chain', 'signature', 'checker', 'rules', 'coverage', 'handover'))
    result['wall_s'] = perf_counter() - started
    return result


__all__ = ['write_package', 'verify_package']
