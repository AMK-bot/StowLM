"""Pipeline/two-step adapters with a fresh native checker and intended-rule audit."""
from __future__ import annotations

import gzip
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import time

from .common import atomic_json, canonical, object_hash, resolve
from .instructions import RULE_TYPES, FixtureParser, instruction_targets, public_facts, select_rule, sentence_for

TWOSTEP_LABEL = "a simple two-step planner, modelled from one field expert's description"
AUDIT_KEYS = ('passed', 'full_checker_pass', 'complete', 'mask_ok', 'replay_legal',
              'all_loaded', 'instruction_recheck', 'instruction_violations', 'level',
              'counterfactual_instruction_recheck', 'counterfactual_instruction_violations',
              'counterfactual_rule', 'counterfactual_rule_sha256',
              'lifts_current_port', 'lifts_projected_later_port', 'lifts_total', 'weighted_lifts',
              'final_cells', 'calibration_rechecks', 'calibration_rules_sha256')

# Only an authorized, hash-checked plan-phase load may enter the runner seam.
# Strong references prevent id reuse. A worker spends each marker once.
_PIPELINE_LOADS = {}


def _loaded_for_pipeline(instance, job):
    marker = _PIPELINE_LOADS.get(id(instance))
    return bool(marker and marker[0] is instance and marker[1] == object_hash(job))


def _log(job, event):
    if job.get('open_log'):
        path = Path(job['open_log'])
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, canonical(dict(event, map_id=job['map_id'], arm=job['arm']))+b'\n')
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _load(job, *, phase='plan'):
    path = resolve(job['path'])
    try:
        _authorize_open(job, path, phase=phase)
    except Exception as error:
        from . import driver
        from .authorization import AuthorizationError
        if driver.is_sealed(path) or job.get('mode') == 'sealed':
            raise AuthorizationError(str(error)) from error
        raise
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    _log(job, dict(event='map_opened', path=str(path), sha256=actual, bytes=len(data)))
    if actual != job['sha256']:
        raise ValueError('map bytes differ from the sealed manifest')
    from stowage.io import _check_version
    from stowage.model import Instance
    payload = json.loads(gzip.decompress(data) if path.suffix == '.gz' else data)
    _check_version(payload.get('schema_version'))
    instance = Instance.model_validate(payload)
    if job.get('mode') == 'sealed' and phase == 'plan':
        _PIPELINE_LOADS[id(instance)] = (instance, object_hash(job))
    return instance


def _authorize_open(job, path, *, phase='plan'):
    """A standalone worker must not accidentally spend the unopened cohort."""
    from . import driver
    if not driver.is_sealed(path) and job.get('mode') != 'sealed':
        return
    if not job.get('open_log'):
        raise ValueError('sealed map opening requires the driver-owned open log and active claim')
    log = resolve(job['open_log'])
    run_dir = resolve(job.get('run_dir', log.parent))
    if log != run_dir / 'opened-maps.jsonl':
        raise ValueError('sealed open log must belong to the claimed run directory')
    claim = json.loads(driver.GLOBAL_CLAIM.read_text())
    started = json.loads((run_dir / 'STARTED.json').read_text())
    from .authorization import TERMINAL, consume
    if (resolve(claim['out_dir']) != run_dir or any((run_dir/name).exists() for name in TERMINAL) or
            claim.get('run_id') != started.get('run_id')):
        raise ValueError('sealed map opening requires the active matching one-run claim')
    identity = driver.verify_freeze(run_dir)
    config = json.loads((run_dir / 'config.json').read_text())
    if (identity.get('run_id') != claim['run_id'] or identity.get('mode') != 'sealed' or
            config.get('mode') != 'sealed' or job.get('mode', 'sealed') != 'sealed'):
        raise ValueError('sealed map opening requires the frozen sealed identity')
    members = [item for item in config['maps'] if
        resolve(item['path']) == path and item['sha256'] == job['sha256'] and
        item['map_id'] == job['map_id'] and item['cohort'] == job['cohort']]
    if len(members) != 1:
        raise ValueError('sealed map is not an exact member of the frozen run')
    consume(dict(job, run_dir=str(run_dir)), phase=phase)


def _gate(instance):
    from experiments.mvp.pipeline.runner.envelope import check
    return check(instance)


def prepare_instructions(maps, out_dir, open_log, *, reader_factory=None,
                         parser_options=None, fixture_surrogate=False, mode='dry-run'):
    """Open and fix the entire instruction draw before any planning begins.

    Caller owns the one-run claim. Input maps must be the envelope cohort only.
    Returns copies in input order, augmented with factual rank and exact rule.
    """
    if (Path(out_dir)/'instruction-draw.json').exists():
        raise ValueError('Instruction draw already exists; refusing overwrite')
    loaded = []
    for item in maps:
        job = dict(item, arm='instruction', open_log=str(open_log))
        instance = facts = None
        error = None
        try:
            instance = _load(job, phase='prepass')
            facts = canonical(public_facts(instance))
        except Exception as exc:
            from .authorization import AuthorizationError
            if isinstance(exc, AuthorizationError):
                raise
            error = f'{type(exc).__name__}: {exc}'
        loaded.append((facts, item['map_id'], item, instance, error))
    ranked = sorted(loaded, key=lambda entry: (entry[0] is None, entry[0] or b'', entry[1]))
    selected = {}
    instances = {}
    records = []
    for rank, (facts, map_id, item, instance, error) in enumerate(ranked):
        rule = None
        targets = dict(instruction_target_count=None, no_matching_cargo=None)
        if error is None:
            try:
                rule = select_rule(instance, rank)
                targets = instruction_targets(instance, rule)
            except Exception as exc:
                rule = None
                error = f'{type(exc).__name__}: {exc}'
        condition = dict(cohort_index=rank, expected_rule=rule,
                         instruction_type=RULE_TYPES[rank % 4], instruction_unavailable=error, **targets)
        prepared = dict(item, **condition)
        selected[map_id] = prepared
        instances[map_id] = instance
        record = dict(map_id=map_id, map_sha256=item['sha256'], **condition,
                      public_facts_sha256=hashlib.sha256(facts).hexdigest() if facts is not None else None)
        _log(dict(item, arm='instruction', open_log=str(open_log)),
             dict(event='instruction_selected', **record))
        records.append(record)
    allocation_hash = object_hash(records)
    reader = None
    reader_error = None
    try:
        if reader_factory:
            module, name = reader_factory.split(':', 1)
            reader = getattr(importlib.import_module(module), name)(**(parser_options or {}))
        elif fixture_surrogate and mode == 'dry-run':
            reader = FixtureParser()
        else:
            raise ValueError('a frozen reader parser is required in the serial prepass')
        if getattr(reader, 'fixture_surrogate', False) and not (fixture_surrogate and mode == 'dry-run'):
            raise ValueError('sealed runs refuse fixture reader surrogates')
        if mode == 'sealed' and getattr(reader, 'development_only', False):
            raise ValueError('sealed prepass requires the adopted reader, not development recordings')
    except Exception as exc:
        reader_error = f'{type(exc).__name__}: {exc}'
    # One reader, one serial parse per available allocation. No reader is ever
    # constructed by a planning worker. Failures remain in the frozen draw.
    from experiments.mvp.reader.schema import context_from_instance, answer_envelope
    from experiments.mvp.reader.public_brief import _read_sentence
    from llm.instructions.schema import Refusal
    for record in records:
        prepared = selected[record['map_id']]
        rule = prepared['expected_rule']
        reading = dict(map_id=record['map_id'], map_sha256=record['map_sha256'],
            allocation_sha256=allocation_hash, cohort_index=record['cohort_index'], expected_rule=rule,
            sentence=sentence_for(rule) if rule is not None else None,
            context_sha256=None, parsed=None, parser=None, status='unavailable',
            expected_match=False, wall_s=0.)
        if rule is not None:
            start = time.perf_counter()
            try:
                context = context_from_instance(instances[record['map_id']])
                reading['context_sha256'] = object_hash(context)
                if reader_error:
                    raise ValueError(reader_error)
                if isinstance(reader, FixtureParser):
                    reader.expected_rule = rule
                answer, provider = _read_sentence(reader, reading['sentence'], context)
                parsed = answer_envelope(answer)
                reading.update(parsed=parsed, parser=provider,
                    status='refused' if isinstance(answer, Refusal) else 'accepted',
                    expected_match=parsed == rule)
            except Exception as exc:
                reading.update(status='error', error_type=type(exc).__name__, error=str(exc))
            reading['wall_s'] = time.perf_counter()-start
        prepared.update(recorded_reading=reading, reading_sha256=object_hash(reading),
                        reading_allocation_sha256=allocation_hash)
        record.update(recorded_reading=reading, reading_sha256=prepared['reading_sha256'],
                      reading_allocation_sha256=allocation_hash)
    atomic_json(Path(out_dir) / 'instruction-draw.json', records)
    return [selected[item['map_id']] for item in maps]


class RecordedParser:
    """Validate recorded output; an empty standing sentence never calls a model."""
    def __init__(self, job, expected):
        self.job, self.expected = job, expected

    def parse(self, sentence, context):
        from experiments.mvp.reader.schema import validate_answer
        if not sentence.strip():
            empty = dict(schema_version='stowlm.reader/2', refusal=dict(
                refused=True, code='not_an_instruction', reason='Empty standing text.'))
            return validate_answer(json.dumps(empty), context), dict(
                parser='SealedEmptyStanding', adapter_sha256=None, recorded=True)
        reading = _recorded_reading(self.job, self.expected)
        if sentence != reading['sentence'] or object_hash(context) != reading['context_sha256']:
            raise ValueError('recorded reading does not match the exact sentence/context')
        if reading['status'] != 'accepted' or not reading['expected_match']:
            raise ValueError('recorded reading was not an exact confirmed instruction')
        provider = dict(reading['parser'], recorded=True,
                        reading_sha256=self.job['reading_sha256'],
                        allocation_sha256=self.job['reading_allocation_sha256'])
        return validate_answer(json.dumps(reading['parsed']), context), provider


def _recorded_reading(job, expected):
    reading = job.get('recorded_reading')
    if not isinstance(reading, dict) or object_hash(reading) != job.get('reading_sha256'):
        raise ValueError('a frozen reader recording is required; hash mismatch or missing reading')
    if (reading.get('map_id') != job['map_id'] or reading.get('map_sha256') != job['sha256']
            or reading.get('cohort_index') != job.get('cohort_index')
            or reading.get('expected_rule') != expected or reading.get('sentence') != sentence_for(expected)
            or not job.get('reading_allocation_sha256')
            or reading.get('allocation_sha256') != job['reading_allocation_sha256']):
        raise ValueError('recorded reading differs from the fixed allocation')
    if reading.get('expected_match') != (reading.get('parsed') == expected):
        raise ValueError('recorded confirmation flag differs from the exact expected match')
    if job.get('mode') == 'sealed' or job.get('instruction_allocation_sha256'):
        allocation_path = resolve(job['run_dir'])/'instruction-allocation.json'
        allocation_bytes = allocation_path.read_bytes()
        if hashlib.sha256(allocation_bytes).hexdigest() != job.get('instruction_allocation_sha256'):
            raise ValueError('committed instruction allocation hash differs')
        matches = [item for item in json.loads(allocation_bytes) if item['map_id'] == job['map_id']]
        keys = ('map_id', 'path', 'sha256', 'cohort', 'cohort_index', 'expected_rule',
                'instruction_type', 'instruction_unavailable', 'recorded_reading',
                'reading_sha256', 'reading_allocation_sha256')
        if len(matches) != 1 or any(matches[0].get(key) != job.get(key) for key in keys):
            raise ValueError('recorded reading differs from committed instruction allocation')
    return reading


def _parser(job, expected, parser=None):
    if getattr(parser, 'fixture_surrogate', False) and not (
            job.get('fixture_surrogate') and job.get('mode') == 'dry-run'):
        raise ValueError('sealed runs refuse fixture reader surrogates')
    if expected is not None:
        _recorded_reading(job, expected)
    # Ignore factory strings and injected parser implementations: reading is a
    # serial prepass operation, while planning only replays committed output.
    return RecordedParser(job, expected)


def _pipeline(instance, job, directory, parser, expected):
    from experiments.mvp.pipeline.contract.schema import VoyagePacket
    from experiments.mvp.pipeline.runner import run_packet
    packet = VoyagePacket(packet_id='sealed-'+job['map_id']+'-'+job['arm'],
        created_utc=job.get('created_utc', '2026-11-01T00:00:00Z'), synthetic=True,
        base_instance=dict(path=str(resolve(job['path'])), sha256=job['sha256']),
        profile='standard', standing='',
        voyage_sentences=[dict(text=sentence_for(expected), origin='typed')] if expected else [])
    def confirm(row):
        # This block measures confirmed instructions, not accidental wrong readings.
        # A different reading remains unresolved and is retained as a failed map.
        return row.get('parsed') == expected
    if job.get('mode') == 'sealed':
        from .runner_seam import authorized_instance
        try:
            with authorized_instance(instance, job, packet.model_dump(mode='json'),
                                     require_loaded=_loaded_for_pipeline):
                return run_packet(packet.model_dump(mode='json'), parser=parser,
                    out_dir=str(directory / 'pipeline'), confirm=confirm,
                    confirmation_source='sealed_expected_match')
        finally:
            _PIPELINE_LOADS.pop(id(instance), None)
    return run_packet(packet.model_dump(mode='json'), parser=parser,
        out_dir=str(directory / 'pipeline'), confirm=confirm,
        confirmation_source='sealed_expected_match')


def _twostep(instance, job):
    from stowage.solver.twostep import TwoStepPlanner
    from stowage.planner import finalize
    result = TwoStepPlanner().plan(instance, seed=int(job.get('seed', 0)),
                                  time_limit_s=float(job.get('twostep_s', 60.)))
    constructor_status = result.status
    if result.plan is not None:
        finalizer_s = 300. if job['arm'] == 'twostep_300' else float(job.get('twostep_finalize_s', 60.))
        result = finalize(instance, result, time_limit_s=finalizer_s)
    return dict(plan=result.plan.model_dump(mode='json') if result.plan is not None else None,
                status='no_checked_plan', evidence=dict(constructor_status=constructor_status,
                finalizer_status=result.status, label=TWOSTEP_LABEL, repaired=False,
                finalizer_budget_s=300. if job['arm'] == 'twostep_300' else float(job.get('twostep_finalize_s', 60.))))


def _audit(instance, raw_plan, expected):
    from stowage.model import MovePlan
    from stowage.checker import check
    from stowage.moves import apply_plan
    from stowage.actions import get_action_space
    from stowage.feasibility import FeasibilityConfig, make_feasibility_engine
    from engine.peel import inspect
    plan = MovePlan.model_validate(raw_plan)
    if plan.instance_id != instance.id or plan.call_index != instance.call_index:
        raise ValueError('plan belongs to another map or call')
    report = check(instance, plan)
    replay = apply_plan(instance, plan)
    mask_ok = True
    mask_complete = False
    mask_error = None
    try:
        space = get_action_space('boxslot')
        engine = make_feasibility_engine(instance, space,
            config=FeasibilityConfig(extra={'global_screen': 0}))
        for action in space.from_plan(instance, plan):
            if not engine.is_legal(action):
                mask_ok = False
                break
            engine.push(action)
        mask_complete = bool(engine.is_complete())
    except (ValueError, TypeError, KeyError) as error:
        mask_ok = False
        mask_error = str(error)
    loaded = {placement.container_id for placement in replay.final_state.ship.placements}
    all_loaded = set(instance.load_list) <= loaded
    # Construction masks are diagnostics, not the registered pass definition.
    # Their vectorized E9 path can reject a placement the shared checker accepts;
    # physical replay already checks required loads, discharges and quay returns.
    complete = bool(replay.legal and all_loaded)
    classification = inspect(instance, plan, report) if report.count is not None else {}
    violations = None
    if expected is not None:
        from experiments.mvp.reader.schema import context_from_instance, validate_answer
        from experiments.mvp.reader.pipeline import intrinsic_violations
        answer = validate_answer(json.dumps(expected), context_from_instance(instance))
        violations = list(intrinsic_violations(instance, answer, plan))
    count = report.count
    current = count.relocations + count.restow_lifts + count.hatch_cover_lifts if count else None
    later = sum(f.restow_lifts+f.hatch_cover_lifts for f in count.future) if count else None
    level = classification.get('level')
    from stowage.conventions import cells_of_slot
    final_cells = {str(box_id): [] for box_id in instance.load_list}
    for placement in replay.final_state.ship.placements:
        if placement.container_id in final_cells:
            final_cells[placement.container_id] = sorted(cells_of_slot(placement.slot))
    return dict(full_checker_pass=bool(report.passed), complete=complete,
        mask_ok=mask_ok, mask_complete=mask_complete, mask_error=mask_error,
        mask_diagnostic_only=True, replay_legal=bool(replay.legal), all_loaded=all_loaded,
        instruction_recheck=not violations if violations is not None else None,
        instruction_violations=violations, level=level,
        passed=bool(report.passed and complete and level == 0 and violations in (None, [])),
        lifts_current_port=current, lifts_projected_later_port=later,
        lifts_total=current+later if count else None,
        weighted_lifts=float(count.weighted_nonproductive) if count else None,
        checker=report.model_dump(mode='json'), classification=classification, final_cells=final_cells)


def _counterfactual(instance, raw_plan, rule):
    """Audit the paired intended rule without changing the baseline's pass gate."""
    from stowage.model import MovePlan
    from experiments.mvp.reader.schema import context_from_instance, validate_answer
    from experiments.mvp.reader.pipeline import intrinsic_violations
    answer = validate_answer(json.dumps(rule), context_from_instance(instance))
    violations = list(intrinsic_violations(instance, answer, MovePlan.model_validate(raw_plan)))
    return dict(counterfactual_instruction_recheck=not violations,
                counterfactual_instruction_violations=violations,
                counterfactual_rule=rule, counterfactual_rule_sha256=object_hash(rule))


def _calibration_rules(job, instance):
    """Development-only rechecks reuse one baseline; they never launch search."""
    if 'calibration_rules' not in job:
        return None
    if job.get('mode') != 'dry-run' or job['arm'] != 'pipeline':
        raise ValueError('Calibration rechecks are development pipeline only')
    rules = job['calibration_rules']
    kinds = [rule.get('constraint', {}).get('type') if isinstance(rule, dict)
             and isinstance(rule.get('constraint'), dict) else None
             for rule in rules] if isinstance(rules, list) else []
    if (len(kinds) != 4 or any(not isinstance(kind, str) for kind in kinds)
            or set(kinds) != set(RULE_TYPES)):
        raise ValueError('Calibration needs exactly four distinct registered rule types')
    from experiments.mvp.reader.schema import answer_envelope, context_from_instance, validate_answer
    context = context_from_instance(instance)
    for rule in rules:
        if answer_envelope(validate_answer(json.dumps(rule), context)) != rule:
            raise ValueError('Calibration rule differs from its strict reader schema')
    return rules


def _calibration_rechecks(instance, raw_plan, rules):
    return {rule['constraint']['type']: _counterfactual(instance, raw_plan, rule) for rule in rules}


def run_job(job: dict, out_dir: str, *, parser=None) -> dict:
    """One manifest entry/arm. Every exclusion and failure gets a JSON-able row."""
    started = time.perf_counter()
    directory = Path(out_dir)
    if (directory/'row.json').exists():
        raise ValueError('Worker result already exists; refusing overwrite')
    directory.mkdir(parents=True, exist_ok=True)
    row = dict(map_id=job['map_id'], cohort=job['cohort'], map_sha256=job['sha256'],
        arm=job['arm'], status='error', passed=False, full_checker_pass=False,
        instruction_recheck=None, level=None, relaxed_level=None, plan=None,
        lifts_current_port=None, lifts_projected_later_port=None, lifts_total=None,
        weighted_lifts=None, fixture_surrogate=bool(job.get('fixture_surrogate', False)),
        instruction_target_count=None, no_matching_cargo=None, planning_pid=os.getpid(),
        final_cells=None, ship_id=job.get('ship_id'), reader_failure=False,
        engine_failure=False, reading_status=None)
    engine_attempted = False
    try:
        if job['arm'] not in {'pipeline', 'twostep', 'twostep_300', 'instruction'}:
            raise ValueError('arm is not pre-registered')
        if job['arm'] == 'twostep_300' and job['cohort'] != 'envelope24':
            raise ValueError('the 300-second finisher sensitivity is envelope-only')
        if job['arm'] == 'instruction' and job.get('instruction_unavailable'):
            _authorize_open(job, resolve(job['path']))
            row.update(status='constraint_unavailable', expected_rule=None,
                       instruction_type=job['instruction_type'],
                       instruction_unavailable=job['instruction_unavailable'],
                       reason=job['instruction_unavailable'], wall_s=time.perf_counter()-started)
            atomic_json(directory/'row.json', row)
            return row
        if job['arm'] == 'instruction' and job.get('expected_rule') is not None:
            declared = job['expected_rule']
            row.update(expected_rule=declared, instruction_type=declared['constraint']['type'],
                       instruction_sentence=sentence_for(declared))
        instance = _load(job)
        calibration_rules = _calibration_rules(job, instance)
        row['instance_id'] = instance.id
        if hasattr(instance, 'vessel'):
            row['vessel_id'] = instance.vessel.id
            if row['ship_id'] is None:
                row['ship_id'] = instance.vessel.id
        expected = None
        if job['arm'] == 'instruction':
            expected = select_rule(instance, job['cohort_index'])
            if job.get('expected_rule') is not None and expected != job['expected_rule']:
                raise ValueError('expected rule differs from the pre-registered deterministic draw')
            row.update(expected_rule=expected, instruction_type=expected['constraint']['type'],
                       instruction_sentence=sentence_for(expected))
            row.update(instruction_targets(instance, expected))
            _log(job, dict(event='instruction_selected', expected_rule=expected,
                           cohort_index=job['cohort_index'],
                           instruction_target_count=row['instruction_target_count'],
                           no_matching_cargo=row['no_matching_cargo']))
            atomic_json(directory / 'intended-rule.json', expected)
        elif job.get('expected_rule') is not None:
            row.update(instruction_targets(instance, job['expected_rule']))
            if job['arm'] == 'pipeline':
                row['counterfactual_rule'] = job['expected_rule']
        gate = _gate(instance)
        row['envelope'] = gate
        if not gate['in_envelope']:
            row['status'] = 'out_of_envelope'
        else:
            if expected is not None:
                reading = _recorded_reading(job, expected)
                row.update(reading_sha256=job['reading_sha256'],
                    reading_allocation_sha256=job['reading_allocation_sha256'],
                    reading_status=('mismatch' if reading['status'] == 'accepted'
                                    and not reading['expected_match'] else reading['status']),
                    reader_failure=reading['status'] != 'accepted' or not reading['expected_match'],
                    confirmation_source='sealed_expected_match', reading_wall_s=reading['wall_s'])
                if row['reader_failure']:
                    row.update(status='needs_confirmation',
                               reason='Serial prepass reading did not match the intended instruction.')
                    row['wall_s'] = time.perf_counter()-started
                    atomic_json(directory/'row.json', row)
                    return row
            engine_attempted = True
            if job['arm'] in {'twostep', 'twostep_300'}:
                outcome = _twostep(instance, job)
            else:
                outcome = _pipeline(instance, job, directory, _parser(job, expected, parser), expected)
            row.update(status=outcome['status'], evidence=outcome.get('evidence', {}),
                       profile=outcome.get('profile'), plan=outcome.get('plan'))
            if row['plan'] is not None:
                row.update(_audit(instance, row['plan'], expected))
                if job['arm'] == 'pipeline' and job.get('expected_rule') is not None:
                    row['counterfactual_rule'] = job['expected_rule']
                    row.update(_counterfactual(instance, row['plan'], job['expected_rule']))
                if calibration_rules is not None:
                    row.update(calibration_rechecks=_calibration_rechecks(instance, row['plan'], calibration_rules),
                               calibration_rules_sha256=object_hash(calibration_rules))
                row['plan_sha256'] = hashlib.sha256(canonical(row['plan'])).hexdigest()
                if row['passed']:
                    row['status'] = 'checked_plan'
                elif row['level'] in range(1, 6) and row['instruction_recheck'] is not False:
                    row.update(status='relaxed_plan', relaxed_level=row['level'])
                else:
                    row['status'] = 'no_checked_plan'
    except Exception as error:
        row.update(status='error', passed=False, error_type=type(error).__name__, error=str(error))
    if job['arm'] == 'instruction' and engine_attempted:
        row['engine_failure'] = not row['passed']
    row['wall_s'] = time.perf_counter()-started
    atomic_json(directory / 'row.json', row)
    return row


def _recover_deadline(job, row, instance, *, target_sha256):
    """Fresh audit of an existing publication, never another planner call."""
    if (row.get('status') != 'deadline_exceeded' or row.get('deadline_exceeded') is not True
            or row.get('child_timed_out') is not True or row.get('child_cleanup_failed')):
        raise ValueError('Deadline recovery requires a clean planning deadline')
    gate = _gate(instance)
    expected = None
    recovered = dict(row, envelope=gate, instance_id=instance.id, harness_error=False,
                     audit_verified=True, audit_fresh_process=True, audit_pid=os.getpid(),
                     audited_target_sha256=target_sha256, proved_infeasible=False)
    if job['arm'] == 'instruction':
        expected = select_rule(instance, job['cohort_index'])
        if expected != job.get('expected_rule'):
            raise ValueError('Deadline intended instruction differs from fixed draw')
        reading = _recorded_reading(job, expected)
        from experiments.mvp.reader.schema import context_from_instance
        if (reading['context_sha256'] != object_hash(context_from_instance(instance))
                or reading['status'] != 'accepted' or reading['expected_match'] is not True):
            raise ValueError('Deadline publication has no exact confirmed reading')
        recovered.update(expected_rule=expected, instruction_type=expected['constraint']['type'],
            instruction_sentence=sentence_for(expected), instruction_recheck=None,
            reading_sha256=job['reading_sha256'], reading_allocation_sha256=job['reading_allocation_sha256'],
            reading_status='accepted', reader_failure=False, engine_failure=False,
            reading_wall_s=reading['wall_s'], confirmation_source='sealed_expected_match')
    if job.get('expected_rule') is not None:
        recovered.update(instruction_targets(instance, job['expected_rule']))
    if row.get('plan') is None:
        if row.get('deadline_with_incumbent') or row.get('passed'):
            raise ValueError('A deadline without a published plan cannot pass')
        recovered.update(passed=False, full_checker_pass=False, level=None,
            deadline_with_incumbent=False, deadline_incumbent_verified=False,
            audit_scope='deadline_no_plan',
            message='no plan within the time budget', reason='no plan within the time budget')
        if not gate['in_envelope']:
            recovered.update(status='out_of_envelope',reason='Map rejected by the envelope gate.')
        return recovered
    if not gate['in_envelope']:
        raise ValueError('An out-of-envelope publication cannot pass deadline recovery')
    publication = row.get('deadline_publication') or {}
    published, deadline = publication.get('published_wall_s'), row.get('hard_wall_s')
    if (type(published) not in (int,float) or type(deadline) not in (int,float)
            or not math.isfinite(published) or not math.isfinite(deadline)
            or not 0 <= published <= deadline or deadline <= 0
            or row.get('deadline_with_incumbent') is not True
            or row.get('child_timeout_s') != deadline
            or publication.get('plan') != row['plan']
            or publication.get('validation_source') not in {
                'runner_checked_incumbent','worker_original_instance_full_audit'}):
        raise ValueError('Deadline publication is not bound inside the parent wall')
    source = Path(job['target']).parent
    if publication['validation_source'] == 'runner_checked_incumbent':
        matched = None
        for line in (source/'pipeline/events.jsonl').read_text().splitlines():
            try:
                candidate = json.loads(line)
                if object_hash(candidate) == publication.get('publication_sha256'):
                    matched = candidate
                    break
            except (ValueError, TypeError):
                continue
        data = (matched or {}).get('data',{})
        if (not matched or matched.get('stage') != 'engine' or matched.get('kind') != 'incumbent'
                or data.get('checked') is not True or data.get('plan') != row['plan']
                or data.get('plan_sha256') != object_hash(row['plan'])):
            raise ValueError('Deadline checker publication hash or plan differs')
        if job['arm'] == 'instruction' and (data.get('confirmation_source') != 'sealed_expected_match'
                or (data.get('confirmation') or {}).get('source') != 'sealed_expected_match'):
            raise ValueError('Deadline checker publication lacks exact confirmation')
    else:
        original = None
        for name in ('worker-row.original.json','row.json'):
            try:
                candidate = json.loads((source/name).read_bytes())
            except (OSError,ValueError):
                continue
            if object_hash(candidate) == publication.get('publication_sha256'):
                original = candidate
                break
        if (not isinstance(original,dict)
                or original.get('plan') != row['plan'] or original.get('passed') is not True
                or original.get('full_checker_pass') is not True or original.get('level') not in (0,'L0')):
            raise ValueError('Deadline original worker publication differs')
    measured = _audit(instance, row['plan'], expected)
    if measured.get('passed') is not True or measured.get('level') not in (0,'L0'):
        raise ValueError('Published incumbent failed its fresh L0 audit')
    if job['arm'] == 'pipeline' and job.get('expected_rule') is not None:
        measured.update(_counterfactual(instance, row['plan'], job['expected_rule']))
    rules = _calibration_rules(job, instance)
    if rules is not None:
        measured.update(calibration_rechecks=_calibration_rechecks(instance, row['plan'], rules),
                        calibration_rules_sha256=object_hash(rules))
    recovered.update(measured, plan_sha256=object_hash(row['plan']),
        deadline_with_incumbent=True, deadline_incumbent_verified=True,
        audit_scope='published pre-deadline incumbent; fresh full checker and rule audit')
    recovered.pop('error',None)
    return recovered


def audit_job(job: dict) -> dict:
    """Independent process attestation of committed bytes, never another search."""
    target = Path(job['target'])
    data = target.read_bytes()
    if job.get('target_sha256') is not None and hashlib.sha256(data).hexdigest() != job['target_sha256']:
        raise ValueError('audit target bytes changed after authorization')
    row = json.loads(data)
    if (row['map_id'] != job['map_id'] or row['arm'] != job['arm'] or
            row['map_sha256'] != job['sha256']):
        raise ValueError('audit target identity differs from the manifest')
    if row.get('planning_pid') == os.getpid() or type(row.get('planning_pid')) is not int:
        raise ValueError('audit requires a different process from planning')
    instance = _load(job, phase='audit')
    if job.get('deadline_recovery'):
        return _recover_deadline(job, row, instance, target_sha256=hashlib.sha256(data).hexdigest())
    calibration_rules = _calibration_rules(job, instance)
    if job['arm'] == 'instruction' and job.get('instruction_unavailable'):
        try:
            candidate = select_rule(instance, job['cohort_index'])
            instruction_targets(instance, candidate)
        except Exception as error:
            if f'{type(error).__name__}: {error}' != job['instruction_unavailable']:
                raise ValueError('unavailable instruction reason differs') from error
        else:
            raise ValueError('unavailable instruction now has a rule')
        if (row.get('status') != 'constraint_unavailable' or row.get('plan') is not None
                or row.get('passed') or row.get('full_checker_pass')
                or row.get('instruction_type') != RULE_TYPES[job['cohort_index'] % 4]
                or row.get('instruction_unavailable') != job['instruction_unavailable']):
            raise ValueError('unavailable instruction result differs')
        return dict(row, audit_verified=True, audit_fresh_process=True, audit_pid=os.getpid(),
                    audit_scope='unavailable instruction; no plan',
                    audited_target_sha256=hashlib.sha256(data).hexdigest())
    expected = None
    gate = _gate(instance)
    if job['arm'] == 'instruction':
        expected = select_rule(instance, job['cohort_index'])
        if row.get('expected_rule') != expected or job.get('expected_rule') != expected:
            raise ValueError('audit intended instruction differs from the fixed draw')
        if gate['in_envelope']:
            reading = _recorded_reading(job, expected)
            reading_status = ('mismatch' if reading['status'] == 'accepted'
                              and not reading['expected_match'] else reading['status'])
            reader_failure = reading['status'] != 'accepted' or not reading['expected_match']
            from experiments.mvp.reader.schema import context_from_instance
            if (reading['context_sha256'] != object_hash(context_from_instance(instance))
                    or row.get('reading_sha256') != job['reading_sha256']
                    or row.get('reading_allocation_sha256') != job['reading_allocation_sha256']
                    or row.get('reading_status') != reading_status
                    or row.get('reading_wall_s') != reading['wall_s']
                    or row.get('reader_failure') != reader_failure
                    or row.get('confirmation_source') != 'sealed_expected_match'
                    or row.get('engine_failure') != (not reader_failure and not row.get('passed'))):
                raise ValueError('audit recorded reading or failure classification differs')
            if reader_failure and (row.get('plan') is not None or row.get('status') != 'needs_confirmation'):
                raise ValueError('a failed reading cannot execute the engine')
    if job.get('expected_rule') is not None:
        targets = instruction_targets(instance, job['expected_rule'])
        if any(row.get(key) != value for key, value in targets.items()):
            raise ValueError('audit instruction target count differs')
    if row.get('envelope') != gate:
        raise ValueError('audit envelope verdict differs')
    if row.get('plan') is not None:
        measured = _audit(instance, row['plan'], expected)
        if job['arm'] == 'pipeline' and job.get('expected_rule') is not None:
            if row.get('counterfactual_rule') != job['expected_rule']:
                raise ValueError('baseline counterfactual rule differs from the fixed draw')
            measured.update(_counterfactual(instance, row['plan'], job['expected_rule']))
        if calibration_rules is not None:
            measured.update(calibration_rechecks=_calibration_rechecks(instance, row['plan'], calibration_rules),
                            calibration_rules_sha256=object_hash(calibration_rules))
        if any(row.get(key) != measured.get(key) for key in AUDIT_KEYS):
            raise ValueError('fresh checker, instruction or lift audit differs')
        if row.get('plan_sha256') != hashlib.sha256(canonical(row['plan'])).hexdigest():
            raise ValueError('audit plan hash differs')
    elif row.get('passed') or row.get('full_checker_pass') or row.get('status') in {'checked_plan', 'relaxed_plan'}:
        raise ValueError('a missing plan cannot be a pass')
    if not gate['in_envelope'] and row.get('status') != 'out_of_envelope':
        raise ValueError('rejected map must occupy the out_of_envelope column')
    return dict(row, audit_verified=True, audit_fresh_process=True, audit_pid=os.getpid(),
                audited_target_sha256=hashlib.sha256(data).hexdigest())


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--audit', action='store_true')
    args = parser.parse_args()
    from .native_runtime import configure
    configure()
    if Path(args.output).exists():
        raise ValueError('Worker/audit output already exists; refusing overwrite')
    job = json.loads(Path(args.request).read_text())
    if args.audit:
        try:
            row = audit_job(job)
        except Exception as error:
            row = dict(map_id=job['map_id'], cohort=job['cohort'], arm=job['arm'],
                       map_sha256=job['sha256'], status='error', passed=False,
                       audit_verified=False, error_type=type(error).__name__, error=str(error))
    else:
        row = run_job(job, str(Path(args.output).parent / (Path(args.output).stem+'-artifacts')))
    atomic_json(args.output, row)


if __name__ == '__main__':
    main()
