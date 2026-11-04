"""The release boundary: read, confirm, plan, independently check, package."""
from __future__ import annotations

import importlib
import base64
import json
import os
from pathlib import Path
import shutil
import tempfile
from time import perf_counter
from typing import Any
from pydantic import ValidationError

HERE = Path(__file__).resolve().parent
os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(HERE / 'cache' / 'vessels')

from experiments.mvp.pipeline.contract.schema import (
    Event, Mode, Receipt, StageRecord, Status, VoyagePacket, canonical_json,
    chain, sha256_file, sha256_json,
)
from stowage.model import MovePlan

from . import envelope
from .assembly import AssemblyError, assemble, assembly_metadata
from .profiles import get_profile
from .runtime import configure_caches
from .evidence import json_evidence, best_safe_partial, normalize_ladder_work

PipelineOutcome = dict[str, Any]
write_package = None  # Optional package task; resolved again on each run.


class _LazyModule:
    def __init__(self, name):
        self.name = name

    def __getattr__(self, name):
        return getattr(importlib.import_module(self.name), name)


# Keep the envelope path independent of engine/search and reader imports.
binding = _LazyModule('experiments.mvp.pipeline.binding')
public_brief = _LazyModule('experiments.mvp.reader.public_brief')


def intrinsic_violations(*args):
    from experiments.mvp.reader.pipeline import intrinsic_violations as function
    return function(*args)


def context_from_instance(*args):
    from experiments.mvp.reader.schema import context_from_instance as function
    return function(*args)


def validate_answer(*args):
    from experiments.mvp.reader.schema import validate_answer as function
    return function(*args)


def check(*args):
    from stowage.checker import check as function
    return function(*args)


def inspect(*args):
    from engine.peel import inspect as function
    return function(*args)


def active_stubs():
    from stowage.frozen import active_stubs as function
    return function()


def _engine_identity():
    module = importlib.import_module('experiments.mvp.pipeline.binding.adapter')
    verify = getattr(binding, 'verify_manifest', module.verify_manifest)
    identity = verify()
    return dict(engine_version=module.VERSION, engine_manifest_sha256=identity['manifest_sha256'],
                engine_manifest=identity)


def engine_call(instance, receipt, profile, on_incumbent, *, timeout_s):
    """Isolate every engine call, including injected engines and workers."""
    from .limits import call_with_limit
    from .admission import wait_for_capacity, call_admitted
    admission = wait_for_capacity()
    function = binding.execute_brief
    kwargs = {k: profile[k] for k in ('budget_s', 'finalize_s', 'seed', 'config')}
    return call_with_limit(call_admitted, function, instance, receipt, timeout_s=timeout_s,
        stage='engine', on_event=on_incumbent, event_kw='on_incumbent', admission=admission, **kwargs)


def _write_json(path, value):
    """Replace complete JSON; a killed writer never truncates a prior outcome."""
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+'\n')
            stream.flush()
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _package_writer():
    if write_package is not None:
        return write_package
    try:
        module = importlib.import_module('experiments.mvp.pipeline.package')
    except ModuleNotFoundError as error:
        if error.name != 'experiments.mvp.pipeline.package':
            raise
        return None
    return getattr(module, 'write_package', None)


def _dump(value):
    return value.model_dump(mode='json') if hasattr(value, 'model_dump') else value


def _sign(receipt):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    key = Ed25519PrivateKey.generate()
    receipt['public_key'] = base64.b64encode(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode('ascii')
    unsigned = {k: v for k, v in receipt.items() if k != 'signature'}
    receipt['signature'] = base64.b64encode(key.sign(canonical_json(unsigned).encode())).decode('ascii')
    return receipt


def _audit(instance, plan, readings):
    """Recompute every instruction from the authenticated reader output."""
    context = context_from_instance(instance)
    rules = []
    for row in readings:
        answer = validate_answer(canonical_json(row['parsed']), context)
        violations = list(intrinsic_violations(instance, answer, plan))
        rules.append(dict(sentence=row['sentence'], reading=row['parsed'],
                          parser=row['parser'], adapter_sha256=row['parser'].get('adapter_sha256'),
                          recheck=dict(passed=not violations, violations=violations)))
    return rules


def _earlier_plan_recheck(instance, instance_hash, earlier, readings):
    """Check a supplied earlier no-rule outcome once, without running search."""
    result = dict(status='unknown', violations_count=None, rules=[], searched=False,
                  message='No checked earlier no-rule plan was supplied.')
    if not readings:
        return dict(result, status='not_applicable', message='No new confirmed limit to recheck.')
    if earlier is None:
        return result
    try:
        if not isinstance(earlier, dict):
            raise ValueError('Supply an earlier checked no-rule runner outcome.')
        if earlier.get('status') != 'checked_plan' or earlier.get('passed') is not True:
            raise ValueError('The earlier result was not a full checker pass.')
        if earlier.get('instance_sha256') != instance_hash:
            raise ValueError('The earlier plan belongs to a different assembled instance.')
        prior_reading = earlier.get('reading') or {}
        prior_rows = prior_reading.get('sentences')
        if (earlier.get('rules') != [] or not isinstance(prior_rows, list) or not prior_rows
                or any(r.get('role') == 'voyage' or r.get('binds') is True for r in prior_rows)):
            raise ValueError('The earlier result was not a no-rule plan.')
        plan = MovePlan.model_validate(earlier['plan'])
        if plan.instance_id != instance.id or plan.call_index != instance.call_index:
            raise ValueError('The earlier plan belongs to a different voyage or call.')
        report = check(instance, plan)
        if active_stubs() or not report.passed or report.count is None:
            raise ValueError('The earlier plan did not pass a fresh full check.')
        rules = _audit(instance, plan, readings)
        for rule in rules:
            count = len(rule['recheck']['violations'])
            rule.update(violations_count=count,
                message=(f'The earlier plan broke this limit in {count} places.' if count else
                         'The earlier plan already obeyed it.'))
        count = sum(rule['violations_count'] for rule in rules)
        message = (rules[0]['message'] if len(rules) == 1 else
                   f'The earlier plan broke these limits in {count} places.' if count else
                   'The earlier plan already obeyed these limits.')
        return dict(result, status='checked', violations_count=count, rules=rules, message=message,
                    plan_sha256=sha256_json(plan.model_dump(mode='json')), instance_sha256=instance_hash,
                    checker=report.model_dump(mode='json'))
    except Exception as error:
        message = str(error) if isinstance(error, ValueError) else 'The earlier plan could not be rechecked.'
        return dict(result, status='unavailable', message=message)


def _message(status, evidence):
    if status == 'checked_plan':
        return 'Plan passed the full checker and every confirmed instruction recheck.'
    if status == 'constraint_unavailable' and evidence.get('refusal_kind'):
        return binding.planner_message(evidence)
    if status == 'relaxed_plan':
        level = evidence.get('level')
        warning = (' Breaks a class or statutory limit; needs a person’s sign-off.' if level == 4 else
                   ' Not safe to sail; shown only to say what would have to change.' if level == 5 else '')
        completion = evidence.get('completion') or {}
        percent = completion.get('boxes_aboard_percent')
        if percent is None:
            total = evidence.get('boxes_total')
            percent = 100. * (total-len(evidence.get('boxes_ashore', []))) / total if total else None
        partial = bool(evidence.get('boxes_ashore')) or completion.get('completed') is False
        label = (f'Partial relaxed plan at L{level}; {percent:.1f}% aboard' if partial and percent is not None else
                 f'Partial relaxed plan at L{level}; aboard percentage unavailable' if partial else
                 f'Relaxed plan at L{level}')
        return (label + "; full checker pass: false. Broken rules: "
                + ', '.join(evidence.get('broken_rules', []))
                + f". Boxes ashore: {len(evidence.get('boxes_ashore', []))}." + warning)
    return {
        'needs_confirmation': 'Confirm each reading, or correct or remove unresolved voyage sentences, then rerun.',
        'constraint_unavailable': (evidence.get('reason') or evidence.get('error') or
                                   'A confirmed instruction could not be enforced.') +
                                  ' Correct the instruction or reduce cargo, then confirm the reading again.',
        'no_checked_plan': 'No checked plan was found within this run. Try the standard profile or reduce cargo; this does not prove no plan exists.',
    }.get(status, evidence.get('reason') or 'The packet could not be completed.')


def run_packet(packet, *, parser, out_dir, on_event=None, stored=None, confirm=None,
               reader_timeout_s=30., engine_timeout_s=None, stored_sha256=None,
               confirmation_source=None, earlier=None):
    """JSON-able outcome. ``confirm(row) is True`` authorizes that exact reading.

    Standing text is a search preference, never a confirmed hard instruction.
    A stored dictionary must match the exact instance and canonical_rules list;
    it is always checked again. No reader or GPU is loaded by this function.
    ``earlier`` is a checked no-rule runner outcome for a single fresh recheck
    against the newly confirmed limits; it never starts another search.
    """
    started = perf_counter()
    events, records, warnings = [], [], []
    outcome = dict(status='error', message='The packet could not be completed.', passed=False,
                   plan=None, receipt=None, evidence={}, reading=None, events=events, warnings=warnings,
                   label='live', synthetic=True, first_checked_wall_s=None)
    directory = lock_path = None
    lock_owned = False
    package_temp = None
    package_published = False
    current_stage = 'assemble'
    instance = None
    packet_hash = instance_hash = '0' * 64
    readings, confirmed = [], []
    source = confirmation_source if confirmation_source is not None else ('callback' if confirm is not None else 'none')
    confirmation_evidence = {}
    runner_deadline = None
    earlier_recheck = dict(status='unknown', violations_count=None, rules=[], searched=False,
        message='No checked earlier no-rule plan was supplied.' if earlier is None else
                'Confirm the new limits before rechecking the earlier plan.')
    outcome['earlier_plan_recheck'] = earlier_recheck
    first_checked_wall_s = None
    engine_started = None
    engine_recorded = False
    if getattr(parser, 'recorded_offline', False):
        outcome.update(execution_mode='recorded_offline', offline_message=
                       'Recorded answers and stored plan; no network, GPU or live search.')

    def discard_package():
        nonlocal package_temp, package_published
        for target in (package_temp, directory / 'package' if package_published else None):
            if target is not None:
                # Remove the verification index first, even if later cleanup fails.
                try:
                    (target / 'package.json').unlink(missing_ok=True)
                    shutil.rmtree(target)
                except OSError:
                    pass
        package_temp = None
        package_published = False
        outcome.update(package=None, plan=None, receipt=None)

    def emit(stage, kind, data=None):
        row = Event(seq=len(events), t_s=perf_counter()-started, stage=stage,
                    kind=kind, data=data or {}).model_dump(mode='json')
        events.append(row)
        with (directory / 'events.jsonl').open('a') as stream:
            stream.write(canonical_json(row)+'\n')
        if on_event is not None:
            try:
                on_event(json.loads(canonical_json(row)))
            except Exception as error:
                warnings.append('Event callback failed: '+type(error).__name__)

    def record(stage, before, after, began, *, mode='live', provider=None, notes=''):
        provider = provider or {}
        source = provider.get('source_sha256') or sha256_file(__file__)
        records.append(StageRecord(stage=stage, mode=Mode(mode), input_sha256=sha256_json(before),
            output_sha256=sha256_json(after), model=provider.get('model'),
            adapter_sha256=provider.get('adapter_sha256'), prompt_sha256=provider.get('prompt_sha256'),
            source_sha256=source, wall_s=perf_counter()-began,
            cost_usd=0. if mode == 'recorded' else float(provider.get('cost_usd', 0) or 0), notes=notes))

    def stage(name, data=None):
        nonlocal current_stage
        current_stage = name
        emit(name, 'stage_start', data)
        return perf_counter()

    def finish(status, message, evidence=None):
        nonlocal package_temp, package_published
        evidence = evidence or outcome['evidence']
        evidence = normalize_ladder_work(evidence, profile=outcome.get('packet', {}).get('profile'))
        evidence.update(confirmation_evidence)
        if runner_deadline is not None:
            evidence['runner_deadline'] = dict(runner_deadline)
            if status == 'checked_plan' and runner_deadline['expired']:
                message += ' Runner engine deadline reached; returning the best checked incumbent after rechecking it.'
        evidence.update(earlier_plan_recheck=earlier_recheck, first_checked_wall_s=first_checked_wall_s)
        outcome.update(earlier_plan_recheck=earlier_recheck, first_checked_wall_s=first_checked_wall_s)
        outcome.update({key: evidence[key] for key in
            ('ladder_work', 'ladder_message', 'searched_levels', 'unsearched_levels') if key in evidence})
        if earlier_recheck['status'] == 'checked':
            message += ' ' + earlier_recheck['message']
        ladder_message = evidence.get('ladder_message')
        if ladder_message and ladder_message not in message:
            message += ' ' + ladder_message
        evidence.setdefault('runner_rules', [dict(sentence=r['sentence'], reading=r.get('parsed'),
            parser=r.get('parser', {}), adapter_sha256=r.get('parser', {}).get('adapter_sha256'),
            confirmed=r['status'] == 'accepted' and r in confirmed,
            recheck=dict(passed=None, violations=[], reason='No returned plan to recheck.')) for r in readings])
        outcome['rules'] = evidence['runner_rules']
        outcome.update(checker=evidence.get('checker'), lifts=evidence.get('lifts'),
                       level=evidence.get('level'), broken_rules=evidence.get('broken_rules', []),
                       boxes_ashore=evidence.get('boxes_ashore', []), best_safe_partial=best_safe_partial(evidence))
        outcome.update(status=status, message=message, passed=status == 'checked_plan', evidence=evidence)
        if instance is not None:
            began = stage('receipt')
            plan = outcome['plan']
            checker = evidence.get('checker') or {}
            checker_summary = dict(passed=status == 'checked_plan', report_sha256=sha256_json(checker),
                level=evidence.get('level'), broken_rules=evidence.get('broken_rules', []),
                boxes_ashore=evidence.get('boxes_ashore', []))
            if outcome['best_safe_partial'] is not None:
                checker_summary['best_safe_partial'] = outcome['best_safe_partial']
            record('receipt', dict(status=status, evidence=evidence), checker_summary, began)
            receipt = Receipt(engine_version=evidence.get('engine_version') or 'not_run',
                engine_manifest_sha256=evidence.get('engine_manifest_sha256') or '0'*64,
                packet_sha256=packet_hash, instance_sha256=instance_hash,
                plan_sha256=sha256_json(plan) if plan is not None else None,
                steps=chain(records), rules=evidence.get('runner_rules', []), checker=checker_summary,
                lifts=evidence.get('lifts'), label=Mode(outcome['label']))
            outcome['receipt'] = _sign(receipt.model_dump(mode='json'))
            emit('receipt', 'stage_end', {'receipt_sha256': sha256_json(outcome['receipt'])})
        began = stage('package')
        writer = _package_writer() if status in {'checked_plan', 'relaxed_plan'} else None
        outcome['package'] = None
        if writer is not None and status in {'checked_plan', 'relaxed_plan'}:
            try:
                # The package writer requires an empty folder. Keep mutable
                # runner events/outcome outside its signed file manifest.
                package_dir = directory / 'package'
                package_temp = Path(tempfile.mkdtemp(prefix='.package-', dir=directory))
                outcome['package_dir'] = str(package_dir)
                from experiments.mvp.pipeline.package import receipt as package_receipt
                from unittest.mock import patch
                with patch.object(package_receipt, 'KEY_DIR', HERE / '.keys'):
                    outcome['package'] = _dump(writer(outcome, package_temp))
            except Exception as error:
                status = 'error'
                message = ('Package service is unavailable. Restore the network or use a recorded note and retry.'
                           if isinstance(error, ConnectionError) else
                           'Package could not be written ('+type(error).__name__+'). Free disk space or choose a writable output directory and retry.')
                discard_package()
                outcome.update(status=status, message=message, passed=False)
                emit('package', 'error', {'message': message})
        elif writer is None and status in {'checked_plan', 'relaxed_plan'}:
            warnings.append('Package writer unavailable; outcome.json is the integration seam.')
        emit('package', 'stage_end', {'writer': 'package.write_package' if outcome['package'] is not None else 'outcome.json'})
        outcome['wall_s'] = perf_counter()-started
        evidence['runner_wall_s'] = outcome['wall_s']
        outcome['timing'] = dict(wall_s=outcome['wall_s'], first_checked_wall_s=first_checked_wall_s,
                                scope='runner elapsed time, including admission')
        timing_message = ('First legal plan: '+(f'{first_checked_wall_s:.1f} s' if first_checked_wall_s is not None
                          else 'not found')+f"; total wall time: {outcome['wall_s']:.1f} s.")
        outcome['message'] = message + ' ' + timing_message
        emit('runner', 'done', dict(status=status, message=outcome['message'], passed=outcome['passed'],
            wall_s=outcome['wall_s'], first_checked_wall_s=first_checked_wall_s))
        _write_json(directory / 'outcome.json', outcome)
        if package_temp is not None:
            # No package exists at its public location until every write succeeded.
            package_temp.rename(directory / 'package')
            package_temp = None
            package_published = True
        return outcome

    try:
        directory = Path(out_dir)
        lock_path = directory / '.runner.lock'
        directory.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            outcome['message'] = 'This output directory is already in use. Choose a fresh output directory.'
            try:
                pid = int(lock_path.read_text().strip())
                if pid > 0:
                    try:
                        os.kill(pid, 0)  # Existence probe only; never stops another run.
                    except ProcessLookupError:
                        outcome['message'] = 'A stopped runner left a stale lock. Choose a fresh output directory and rerun.'
            except (OSError, ValueError):
                pass
            return outcome
        lock_owned = True
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(str(os.getpid())+'\n')
        if (directory / 'package').exists():
            outcome['message'] = 'This output directory already contains a package. Choose a fresh output directory.'
            return outcome
        (directory / 'events.jsonl').write_text('')
        began = stage('assemble')
        if outcome.get('execution_mode') == 'recorded_offline' and not isinstance(stored, dict):
            raise AssemblyError('Recorded offline mode needs a stored-plan fixture. Supply the fixture and retry.')
        if isinstance(packet, str):
            try:
                packet = json.loads(packet)
            except ValueError:
                raise AssemblyError('Voyage packet is not valid JSON. Correct the JSON file and retry.') from None
        parsed = VoyagePacket.model_validate(packet)
        packet_data = parsed.model_dump(mode='json')
        packet_hash = sha256_json(packet_data)
        outcome.update(packet=packet_data, packet_sha256=packet_hash)
        instance = assemble(parsed)
        instance_data = instance.model_dump(mode='json')
        instance_hash = sha256_json(instance_data)
        outcome.update(instance=instance_data, instance_sha256=instance_hash)
        metadata = assembly_metadata(parsed, instance)
        outcome['assembly'] = metadata
        record('assemble', packet_data, instance_data, began, notes=canonical_json(metadata))
        emit('assemble', 'stage_end', metadata)

        began = stage('envelope')
        gate = envelope.check(instance)
        outcome['envelope'] = gate
        record('envelope', instance_data, gate, began)
        emit('envelope', 'stage_end', gate)
        if not gate['in_envelope']:
            message = 'Outside the pipeline envelope: '+'; '.join(r['message'] for r in gate['reasons'])
            emit('envelope', 'refusal', {'message': message})
            return finish('out_of_envelope', message)

        began = stage('reader')
        brief = public_brief.VoyageBrief(standing=parsed.standing,
                                       voyage=tuple(s.text for s in parsed.voyage_sentences))
        from .limits import run_in_process_with_limit
        class TimedParser:
            def parse(self, sentence, context):
                return run_in_process_with_limit(parser.parse, sentence, context,
                    timeout_s=reader_timeout_s, stage='reader')
        receipt = public_brief.read_brief(instance, brief, parser=TimedParser())
        reading = receipt.evidence()
        outcome['reading'] = reading
        standing_pending = any(row['role'] == 'standing' and row['sentence'].strip()
            and row['status'] != 'preference_enabled' for row in reading['sentences'])
        # Each sentence retains its own model, adapter, timing and replay label.
        for row in reading['sentences']:
            provider = row.get('parser') or {}
            mode = 'recorded' if provider.get('recorded') else 'live'
            record('reader', {'sentence': row['sentence'], 'role': row['role']}, row,
                   perf_counter()-row['wall_s'], mode=mode, provider=provider,
                   notes='Recorded answer' if mode == 'recorded' else '')
        emit('reader', 'stage_end', {'reading': reading, 'empty_voyage': not parsed.voyage_sentences,
              'message': 'Standing text needs correction or movement to voyage_sentences.' if standing_pending else
              'No voyage instructions; planning normally.' if not parsed.voyage_sentences else 'Readings await planner confirmation.'})
        readings = [r for r in reading['sentences'] if r['role'] == 'voyage']
        began = stage('confirmation')
        standing_questions = []
        for index, row in enumerate(reading['sentences']):
            if row['role'] == 'standing' and row['sentence'].strip() and row['status'] != 'preference_enabled':
                question = dict(id=f'standing.{index}', row=None, field='standing',
                    prompt='Move this instruction to voyage_sentences and confirm its reading, or correct or remove the standing text.',
                    reason='Standing text cannot silently become a hard instruction.', candidates=[])
                standing_questions.append(question)
                emit('confirmation', 'question', question)
        if standing_questions:
            outcome['questions'] = standing_questions
        unresolved = [r for r in readings if r['status'] != 'accepted']
        confirmed = []
        for row in readings:
            if row['status'] != 'accepted':
                emit('reader', 'refusal', {'reading': row, 'message': 'This sentence needs correction or removal.'})
            if row['status'] == 'accepted' and confirm is not None and confirm(json.loads(canonical_json(row))) is True:
                confirmed.append(row)
            else:
                emit('confirmation', 'question', {'reading': row, 'message': 'Confirm this reading, or correct or remove the sentence.'})
        outcome['confirmation_source'] = source
        ready = not standing_questions and not unresolved and len(confirmed) == len(readings)
        if ready:
            # Bind the host approval to this exact authenticated reader output.
            receipt = binding.confirm_brief(receipt, source)
            from experiments.mvp.pipeline.binding.confirmation import verify_confirmation
            confirmation = verify_confirmation(receipt)
            confirmation_evidence.update(confirmation=confirmation,
                confirmation_source=confirmation['source'],
                confirmation_sha256=confirmation['confirmation_sha256'])
            outcome['confirmation'] = confirmation
        confirmation_step = dict(confirmed=confirmed, confirmation_source=source,
            standing_questions=standing_questions, **({'confirmation': confirmation} if ready else {}))
        record('confirmation', reading['sentences'], confirmation_step, began,
            notes=canonical_json(dict(confirmation_source=source, confirmed_count=len(confirmed),
                standing_question_count=len(standing_questions),
                **({'confirmation': confirmation} if ready else {}))))
        emit('confirmation', 'stage_end', dict(confirmed=len(confirmed), required=len(readings),
            confirmation_source=source, **({'confirmation': confirmation} if ready else {})))
        if not ready:
            return finish('needs_confirmation', _message('needs_confirmation', {}))

        best_lifts = float('inf')
        best_plan = None
        best_event = {}
        def incumbent(event, *, checked_report=None, checked_rules=None):
            nonlocal best_lifts, best_plan, best_event, first_checked_wall_s
            try:
                plan = MovePlan.model_validate(event['plan'])
                report = checked_report if checked_report is not None else check(instance, plan)
                rules = checked_rules if checked_rules is not None else _audit(instance, plan, readings)
                if active_stubs() or not report.passed or report.count is None or any(not r['recheck']['passed'] for r in rules):
                    return
                if first_checked_wall_s is None:
                    first_checked_wall_s = perf_counter()-started
                lifts = float(report.count.weighted_nonproductive)
                if lifts >= best_lifts:
                    return
                best_lifts = lifts
                data = plan.model_dump(mode='json')
                best_plan, best_event = data, dict(event)
                emit('engine', 'incumbent', dict(lifts=lifts, plan_sha256=sha256_json(data),
                     lane=event.get('lane') or event.get('stage_attribution'), checked=True, plan=data,
                     label=outcome['label'], first_checked_wall_s=first_checked_wall_s,
                     message='First legal plan available. Continuing to improve.' if
                         not any(row['kind'] == 'incumbent' for row in events) else
                         'Better legal plan available. Continuing to improve.',
                     **confirmation_evidence))
            except Exception as error:
                warnings.append('Candidate withheld: '+type(error).__name__)

        anytime = json.loads((HERE / 'fixtures' / 'anytime-handover.json').read_text())
        guidance = dict(anytime['anytime_policy'], historical_first_legal=anytime['lp_first_full_checker_pass'])
        outcome['anytime'] = guidance
        began = stage('engine', {'anytime': guidance})
        engine_started = began
        identity = _engine_identity()
        evidence = dict(identity, **confirmation_evidence, status='no_checked_plan', passed=False,
                        level=None, boxes_total=len(instance.load_list))
        outcome['evidence'] = evidence
        configure_caches()
        profile = get_profile(parsed.profile)
        outcome['profile'] = {k: _dump(v) for k, v in profile.items() if k != 'config'}
        canonical_rules = [r['parsed'] for r in readings]
        if not identity['engine_manifest']['passed']:
            raise AssemblyError('Pinned engine sources do not match. Restore the pinned engine before planning or replay.', status='constraint_unavailable')
        if active_stubs():
            raise AssemblyError('Active checker stubs prevent plan release. Remove the stubs and rerun with the full checker.', status='constraint_unavailable')
        earlier_began = perf_counter()
        earlier_recheck = _earlier_plan_recheck(instance, instance_hash, earlier, readings)
        outcome['earlier_plan_recheck'] = earlier_recheck
        evidence['earlier_plan_recheck'] = earlier_recheck
        if earlier is not None and readings:
            record('earlier_plan_recheck', {'instance_sha256': instance_hash,
                'plan': earlier.get('plan') if isinstance(earlier, dict) else None,
                'readings': readings}, earlier_recheck, earlier_began)
            emit('earlier_plan_recheck', 'stage_end', earlier_recheck)
        if stored is not None:
            candidate = stored(instance_hash, canonical_rules) if callable(stored) else stored
        else:
            candidate = None
        if candidate is not None:
            from .stored import authenticate_candidate
            try:
                candidate = json.loads(canonical_json(candidate))
                authenticated_hash = authenticate_candidate(candidate, expected_sha256=stored_sha256)
            except ValueError as error:
                raise AssemblyError(str(error)) from None
            if (candidate.get('instance_sha256') != instance_hash or
                    candidate.get('canonical_rules') != canonical_rules or not candidate.get('computed_utc') or
                    candidate.get('engine_version') != identity['engine_version'] or
                    candidate.get('engine_manifest_sha256') != identity['engine_manifest_sha256']):
                raise AssemblyError('Stored plan identity, rules, date or pinned engine provenance do not match. Select a matching authenticated stored plan and retry.')
            plan = MovePlan.model_validate(candidate['plan'])
            stored_status = candidate.get('status', 'checked_plan')
            if stored_status not in {'checked_plan', 'relaxed_plan'}:
                raise AssemblyError('Stored plan status is invalid. Select an authenticated checked or relaxed plan.')
            evidence = dict(candidate, **identity, **confirmation_evidence, status=stored_status, passed=False,
                            stored_authenticated_sha256=authenticated_hash, boxes_total=len(instance.load_list))
            outcome.update(label='stored', stored_message='Stored plan, computed on '+candidate['computed_utc']+', rechecked now')
        else:
            runner_deadline = dict(
                timeout_s=engine_timeout_s if engine_timeout_s is not None else profile['engine_timeout_s'],
                scope='owned engine call after admission', budget_s_is_wall_limit=False, expired=False)
            evidence['runner_deadline'] = dict(runner_deadline)
            try:
                result, returned = engine_call(instance, receipt, profile, incumbent,
                    timeout_s=runner_deadline['timeout_s'])
                evidence.update(returned)
                # The local manifest is authoritative, including timeout receipts.
                evidence.update(identity)
                evidence.update(confirmation_evidence)
                evidence['runner_deadline'] = dict(runner_deadline)
            except Exception as error:
                from .limits import StageCallError, StageTimeout
                timed_out = isinstance(error, TimeoutError) or (
                    isinstance(error, StageCallError) and error.error_type in {'TimeoutError', 'StageTimeout'})
                if timed_out:
                    runner_deadline['expired'] = isinstance(error, StageTimeout) and error.stage == 'engine'
                    evidence.update(timed_out=True, runner_deadline=dict(runner_deadline))
                if not timed_out or best_plan is None:
                    raise
                result = None
                evidence.update(status='checked_plan', timed_out=True, lane='checked_incumbent_after_timeout',
                                reason='Engine stopped at timeout; the best checked incumbent is rechecked before release.')
            plan = getattr(result, 'plan', None) if result is not None else None
            if best_plan is not None and evidence.get('status') in {'checked_plan', 'no_checked_plan', 'relaxed_plan'}:
                # A later optional lane cannot erase a published legal incumbent.
                if plan is None:
                    plan = MovePlan.model_validate(best_plan)
                    evidence.update(status='checked_plan', lane=best_event.get('lane') or 'checked_incumbent')
                else:
                    final_report = check(instance, MovePlan.model_validate(_dump(plan)))
                    final_rules = _audit(instance, MovePlan.model_validate(_dump(plan)), readings)
                    if (not final_report.passed or final_report.count is None or
                            any(not r['recheck']['passed'] for r in final_rules) or
                            final_report.count.weighted_nonproductive > best_lifts):
                        plan = MovePlan.model_validate(best_plan)
                        evidence.update(status='checked_plan', lane=best_event.get('lane') or 'checked_incumbent')
        evidence = normalize_ladder_work(json_evidence(evidence), profile=parsed.profile)
        evidence.update(earlier_plan_recheck=earlier_recheck, first_checked_wall_s=first_checked_wall_s)
        record('engine', {'instance_sha256': instance_hash, 'reading': reading}, evidence, began,
               mode=outcome['label'], notes=canonical_json(identity))
        engine_recorded = True
        emit('engine', 'stage_end', dict(status=evidence.get('status'), lane=evidence.get('lane'),
             first_checked_wall_s=first_checked_wall_s, ladder_work=evidence.get('ladder_work')))
        outcome['evidence'] = evidence
        if plan is None:
            status = evidence.get('status', 'no_checked_plan')
            if status not in {'needs_confirmation', 'constraint_unavailable', 'no_checked_plan', 'error'}:
                status = 'no_checked_plan'
            return finish(status, _message(status, evidence), evidence)

        plan = MovePlan.model_validate(_dump(plan))
        began = stage('checker')
        report = check(instance, plan)
        if active_stubs():
            raise AssemblyError('Active checker stubs prevent plan release. Remove the stubs and rerun with the full checker.', status='constraint_unavailable')
        rules = _audit(instance, plan, readings)
        evidence.update(checker=report.model_dump(mode='json'), runner_rules=rules,
                        instruction_recheck_passed=all(r['recheck']['passed'] for r in rules))
        record('checker', plan.model_dump(mode='json'), {'checker': evidence['checker'], 'rules': rules}, began)
        emit('checker', 'stage_end', {'passed': report.passed, 'instructions_passed': evidence['instruction_recheck_passed']})
        began = stage('ladder')
        classification = json_evidence(inspect(instance, plan, report))
        evidence.update(level=classification['level'], broken_rules=classification['broken_rules'],
                        boxes_ashore=classification['boxes_left_ashore'], completion=classification,
                        lifts=classification['lifts'])
        evidence.setdefault('boxes_total', len(instance.load_list))
        legal = report.passed and classification['level'] == 0 and classification['completed']
        status = ('checked_plan' if legal else 'relaxed_plan' if
                  evidence.get('status') == 'relaxed_plan' and classification['level'] in range(1, 6)
                  else 'no_checked_plan')
        if not evidence['instruction_recheck_passed']:
            status = 'no_checked_plan'
        evidence.update(status=status, passed=status == 'checked_plan')
        ladder_result = dict(classification, **{key: evidence[key] for key in
            ('ladder_work', 'searched_levels', 'unsearched_levels', 'ladder_trials', 'ladder_message')
            if key in evidence})
        record('ladder', evidence['checker'], ladder_result, began)
        emit('ladder', 'stage_end', ladder_result)
        if status in {'checked_plan', 'relaxed_plan'}:
            outcome['plan'] = plan.model_dump(mode='json')
            if status == 'checked_plan':
                incumbent(dict(plan=outcome['plan'], lane=evidence.get('lane')),
                          checked_report=report, checked_rules=rules)
            else:
                emit('ladder', 'relaxed', {'level': classification['level'],
                     'broken_rules': classification['broken_rules'], 'boxes_ashore': classification['boxes_left_ashore'],
                     **{key: evidence[key] for key in ('ladder_work', 'ladder_message') if key in evidence}})
        return finish(status, _message(status, evidence), evidence)
    except Exception as error:
        from .limits import StageTimeout, StageProcessError, StageCallError
        from .admission import AdmissionUnavailable
        if isinstance(error, AssemblyError):
            status, message = error.status, str(error)
            outcome['questions'] = [_dump(q) for q in error.questions]
        elif isinstance(error, AdmissionUnavailable):
            status, message = 'error', str(error)
        elif isinstance(error, ValidationError):
            status = 'error'
            detail = error.errors()[0]
            field = '.'.join(str(value) for value in detail['loc']) or 'packet'
            message = (f'Voyage packet field {field} is invalid. Correct the packet schema and retry.'
                       if current_stage == 'assemble' else
                       f'{current_stage.capitalize()} returned invalid data in {field}. Retry the service or use a matching recording.')
        elif isinstance(error, (TimeoutError, StageTimeout)) or (
                isinstance(error, StageCallError) and error.error_type in {'TimeoutError', 'StageTimeout'}):
            status = 'no_checked_plan' if current_stage == 'engine' else 'error'
            message = (('Runner engine deadline reached; no checked plan was found. Retry with a longer deadline or reduce the load.'
                        if isinstance(error, StageTimeout) and error.stage == 'engine' else
                        'Engine timed out before a checked plan was found. Retry or reduce the load.')
                       if current_stage == 'engine' else
                       'Reader timed out. Retry the reader or use recorded answers for this packet.')
        elif isinstance(error, (ChildProcessError, StageProcessError, BrokenPipeError)) or (
                isinstance(error, StageCallError) and error.error_type in {'ChildProcessError', 'BrokenPipeError'}):
            status = 'error'
            message = f'{current_stage.capitalize()} worker stopped before returning a result. Restart the worker and rerun in a fresh output directory.'
        elif isinstance(error, StageCallError) and error.error_type == 'MainThreadRequired':
            status = 'error'
            message = 'Reader deadlines need the main thread. Run this packet in the main thread of a dedicated runner process.'
        elif current_stage == 'reader' and isinstance(error, (ImportError, AttributeError)):
            status = 'error'
            message = 'Reader is unavailable or returned malformed output. Restore the reader or use recorded answers and retry.'
        elif isinstance(error, OSError):
            status = 'error'
            message = ('Reader service is unavailable. Restore the service or use recorded answers and retry.'
                       if current_stage == 'reader' else
                       'Output could not be written. Free disk space or choose a writable output directory and rerun.'
                       if current_stage in {'assemble', 'receipt', 'package'} else
                       f'{current_stage.capitalize()} service is unavailable. Restore the connection or use recorded answers and retry.')
        else:
            status = 'error'
            message = f'{current_stage.capitalize()} could not complete ({type(error).__name__}). Check the input or attached service and retry.'
        evidence = json_evidence(outcome.get('evidence') or {})
        evidence.update(status=status, passed=False, plan_released=False, level=None,
                        instruction_recheck_passed=False)
        if current_stage in {'checker', 'ladder'}:
            evidence.update(checker=dict(passed=False, available=False, reason=message), completion=None)
        outcome['evidence'] = evidence
        if engine_started is not None and not engine_recorded:
            if not evidence.get('engine_version'):
                evidence.update(engine_version='0.13.0', engine_manifest_sha256='0'*64)
            evidence['engine_error'] = dict(type=type(error).__name__, message=message)
            record('engine', {'instance_sha256': instance_hash, 'reading': outcome.get('reading')},
                evidence, engine_started, mode=outcome['label'], notes=canonical_json({
                    'engine_version': evidence['engine_version'],
                    'engine_manifest_sha256': evidence.get('engine_manifest_sha256')}))
            engine_recorded = True
        discard_package()
        outcome.update(status=status, message=message, passed=False, plan=None)
        try:
            emit(current_stage, 'question' if status == 'needs_answers' else 'error', {'message': message})
            return finish(status, message)
        except Exception as final_error:
            outcome.update(status='error', passed=False, plan=None,
                           message=message+' Output could not be completed ('+type(final_error).__name__+').')
            try:
                _write_json(directory / 'outcome.json', outcome)
            except Exception:
                pass
            return outcome
    finally:
        if package_temp is not None:
            discard_package()
        if lock_owned:
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass
