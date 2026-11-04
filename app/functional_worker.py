"""Dedicated subprocess stages for fresh synthetic Functional app calls."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import traceback
from uuid import uuid4


SOURCE = 'functional_app_planner_click'
PROFILES = {'live': 720, 'standard': 1800}
BOOKING_INDEX_SCHEMA = 'stowlm.functional-intake-index/1'
NO_INTAKE_HOLD = 'Raise the spend cap to continue: each Spark call holds about $1.38 until it settles.'
DOCUMENT_ROLES = frozenset({'booking', 'shipping_instructions', 'vgm', 'dg_declaration',
                            'gate_slip', 'reefer_list', 'oog_list', 'amendment',
                            'correspondence', 'unspecified'})
STAGE_FIELDS = ('stage status mode who where model adapter_sha256 started_utc wall_s cost_usd '
                'input_sha256 output_sha256 notes').split()
WHO = {'generate': 'Synthetic case generator', 'envelope': 'Design envelope gate',
       'intake': 'Muse Spark', 'answers': "Planner's answers", 'assemble': 'Voyage packet assembly',
       'reader': 'StowLM Reader', 'engine': 'Frontier Engine 0.13.0',
       'checker': 'Full checker', 'ladder': 'D241 ladder', 'package': 'Handover package'}
_OUTPUT_FILTER_ACTIVE = False


def _redact(value):
    """The worker receives its credential only through this process environment."""
    key = os.environ.get('MODEL_API_KEY')
    if not key:
        return value

    def clean(item):
        if isinstance(item, str):
            result = item.replace(key, '[REDACTED API KEY]')
            if result.startswith(('{', '[', '"')):
                try:
                    decoded = json.loads(result)
                except (ValueError, TypeError):
                    return result
                if decoded != result:
                    safe = clean(decoded)
                    if safe != decoded:
                        return json.dumps(safe, ensure_ascii=False)
            return result
        if isinstance(item, dict):
            return {clean(name): clean(content) for name, content in item.items()}
        if isinstance(item, (list, tuple)):
            return [clean(content) for content in item]
        return item

    return clean(value)


class _SecretStream:
    """Keep a tail so a credential split across separate writes is still masked."""
    def __init__(self, target, key):
        self.target, self.key, self.pending = target, key, ''
        self.lock = threading.RLock()

    def write(self, value):
        with self.lock:
            data = (self.pending + value).replace(self.key, '[REDACTED API KEY]')
            cut = max(0, len(data) - len(self.key) + 1)
            self.target.write(data[:cut])
            self.pending = data[cut:]
        return len(value)

    def flush(self):
        self.target.flush()

    def finish(self):
        with self.lock:
            self.target.write(self.pending.replace(self.key, '[REDACTED API KEY]'))
            self.pending = ''
            self.target.flush()

    def __getattr__(self, name):
        return getattr(self.target, name)


@contextmanager
def _redacted_output():
    """Mask Python output and native fd writes before they reach worker logs."""
    global _OUTPUT_FILTER_ACTIVE
    key = os.environ.get('MODEL_API_KEY')
    if not key or _OUTPUT_FILTER_ACTIVE:
        yield
        return
    _OUTPUT_FILTER_ACTIVE = True
    original_streams = sys.stdout, sys.stderr
    filtered = tuple(_SecretStream(stream, key) for stream in original_streams)
    descriptors = []

    def pump(read_fd, destination):
        pending = b''
        needle = key.encode()
        try:
            while True:
                chunk = os.read(read_fd, 65536)
                if not chunk:
                    break
                data = (pending + chunk).replace(needle, b'[REDACTED API KEY]')
                cut = max(0, len(data) - len(needle) + 1)
                ready, pending = data[:cut], data[cut:]
                while ready:
                    ready = ready[os.write(destination, ready):]
            ready = pending.replace(needle, b'[REDACTED API KEY]')
            while ready:
                ready = ready[os.write(destination, ready):]
        finally:
            os.close(read_fd)
            os.close(destination)

    try:
        for stream in original_streams:
            stream.flush()
        for descriptor in (1, 2):
            saved = os.dup(descriptor)
            read_fd, write_fd = os.pipe()
            destination = os.dup(saved)
            os.dup2(write_fd, descriptor)
            os.close(write_fd)
            thread = threading.Thread(target=pump, args=(read_fd, destination), daemon=True)
            thread.start()
            descriptors.append((descriptor, saved, thread))
        sys.stdout, sys.stderr = filtered
        yield
    finally:
        for stream in filtered:
            stream.finish()
        sys.stdout, sys.stderr = original_streams
        for descriptor, saved, thread in descriptors:
            os.dup2(saved, descriptor)
            os.close(saved)
        for _, _, thread in descriptors:
            thread.join(timeout=2.)
        _OUTPUT_FILTER_ACTIVE = False


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def _atomic(path, data):
    key = os.environ.get('MODEL_API_KEY')
    if key:
        data = data.replace(key.encode(), b'[REDACTED API KEY]')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_json(path, data):
    _atomic(path, (json.dumps(_redact(data), ensure_ascii=False, allow_nan=False, sort_keys=True) + '\n').encode())


def document_metadata(document):
    """Validate host-supplied authority metadata without inferring any role."""
    if not isinstance(document, dict):
        raise ValueError('Each cargo document must be a JSON object.')
    role = document.get('role', 'unspecified')
    if not isinstance(role, str) or role not in DOCUMENT_ROLES:
        raise ValueError('Choose a supported cargo document role.')
    booking_ref = document.get('booking_ref')
    if booking_ref is not None and not isinstance(booking_ref, str):
        raise ValueError('A document booking reference must be text or null.')
    metadata = dict(role=role, booking_ref=booking_ref)
    for key in ('complete_booking_list', 'synthetic'):
        value = document.get(key, False)
        if not isinstance(value, bool):
            raise ValueError(f'The document {key.replace("_", " ")} field must be true or false.')
        metadata[key] = value
    if 'container_ids' in document:
        roster = document['container_ids']
        if (not isinstance(roster, list) or not roster
                or any(not isinstance(cid, str) or not cid.strip() or cid != cid.strip() for cid in roster)
                or len(set(roster)) != len(roster)):
            raise ValueError('A host-confirmed booking membership must be a nonempty list of unique container ids.')
        metadata['container_ids'] = list(roster)
    return metadata


def generated_papers(call_dir):
    """Read the generated set's host manifest and measure its contained files."""
    call_dir = Path(call_dir).resolve()
    directory = (call_dir / 'papers' / 'generated').resolve()
    if not directory.is_relative_to(call_dir):
        raise ValueError('Generated cargo papers must stay inside this call folder.')
    manifest_path = directory / 'manifest.json'
    if not manifest_path.resolve().is_relative_to(directory):
        raise ValueError('The generated cargo-paper manifest must stay inside its generated directory.')
    try:
        manifest = read_json(manifest_path)
    except (OSError, ValueError):
        raise ValueError('The generated cargo-paper manifest is missing or invalid.') from None
    if not isinstance(manifest, list) or not manifest:
        raise ValueError('The generated cargo-paper manifest must contain a nonempty document list.')
    documents, seen = [], set()
    for item in manifest:
        metadata = document_metadata(item)
        value = item.get('path')
        if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
            raise ValueError('Each generated cargo paper needs a valid call-relative path.')
        relative = Path(value)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Generated cargo-paper paths must be relative and cannot traverse directories.')
        try:
            path = (call_dir / relative).resolve()
        except (OSError, RuntimeError, ValueError):
            raise ValueError('A generated cargo-paper path is invalid.') from None
        if not path.is_relative_to(directory) or path == manifest_path.resolve():
            raise ValueError('Generated cargo papers must stay inside their generated directory.')
        if path in seen:
            raise ValueError('The generated cargo-paper manifest contains a duplicate document path.')
        seen.add(path)
        name = item.get('name', relative.name)
        media = item.get('media_type')
        if not isinstance(name, str) or not name.strip() or not isinstance(media, str) or not media.strip():
            raise ValueError('Each generated cargo paper needs a name and media type.')
        try:
            if not path.is_file():
                raise OSError
            data = path.read_bytes()
        except OSError:
            raise ValueError('A generated cargo-paper file is missing or unreadable.') from None
        metadata['synthetic'] = True
        for key in ('id', 'container_ids'):
            if key in item:
                metadata[key] = item[key]
        documents.append(dict(name=name, media_type=media, path=str(path), bytes=len(data),
                              sha256=hashlib.sha256(data).hexdigest(), origin='generated',
                              label='Synthetic document', **metadata))
    return documents


def _digest(value):
    from experiments.mvp.pipeline.contract.schema import sha256_json
    return sha256_json(value)


def append_stage(call_dir, stage, status='done', **fields):
    """Atomically replace the journal under a cross-process append lock."""
    import fcntl
    path = Path(call_dir) / 'stages.jsonl'
    row = dict.fromkeys(STAGE_FIELDS)
    row.update(stage=stage, status=status, mode='live', who=WHO.get(stage, stage),
               where='software', started_utc=utc_now(), notes='')
    row.update({key: value for key, value in fields.items() if key in row})
    for key in ('instance_id', 'cold_load_s', 'provenance', 'source', 'revalidation', 'model_call', 'reading_id',
                'sentence_sha256', 'rule_sha256', 'cost_scope', 'cumulative_cost_usd',
                'incremental_cost_usd', 'elapsed_s', 'booking_wall_s', 'spend_wait_s', 'attempt'):
        if fields.get(key) is not None:
            row[key] = fields[key]
    row = _redact(row)
    Path(call_dir).mkdir(parents=True, exist_ok=True)
    with (Path(call_dir) / '.stages.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        before = path.read_bytes() if path.exists() else b''
        _atomic(path, before + (json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n').encode())
    return row


def _state(call_dir, state, message, step):
    write_json(Path(call_dir) / 'worker-state.json', dict(state=state, message=message, step=step))


def _gate_message(gate):
    return 'Outside the pipeline envelope: ' + '; '.join(row['message'] for row in gate.get('reasons', []))


class _OutsideEnvelope(ValueError):
    pass


def app_envelope(case, gate):
    """The app's claims envelope also excludes 1,700 TEU tiny/small sections."""
    ship = ((gate.get('numbers') or {}).get('ship') or case.get('ship')
            or case.get('request', {}).get('ship'))
    reasons = list(gate.get('reasons', []))
    headline = case.get('headline_envelope') or {}
    for reason in headline.get('reasons', []):
        if reason not in reasons:
            reasons.append(reason)
    if ship and ship not in ('feeder_500', 'feeder_800'):
        reasons = [row for row in reasons if row.get('test') != 'ship']
        label = 'Ship 1,700 TEU' if ship == 'feeder_1700' else str(ship)
        reasons.append(dict(test='ship', measured=ship, limit=['feeder_500', 'feeder_800'],
                            message=f'{label}; the envelope is 500–800 TEU feeders'))
    return dict(gate, in_envelope=bool(gate.get('in_envelope') and headline.get('in_envelope', True) and not reasons), reasons=reasons)


def _effective_gate(call_dir, gate, case=None):
    case = case if case is not None else read_json(Path(call_dir) / 'case.json', {})
    if not case.get('ship') and not case.get('request'):
        case = dict(case, request=read_json(Path(call_dir) / 'request.json', {}))
    return app_envelope(case, gate)


def _packet(call_dir, controls):
    base = call_dir / 'base-instance.json'
    intake = read_json(call_dir / 'intake.json') or {}
    load = None
    if not intake.get('skipped') and intake:
        if not intake.get('complete'):
            raise ValueError('Answer the cargo-paper questions before assembling this call.')
        # Per-row source labels belong to the app view, not strict Container data.
        rows = [{key: value for key, value in row.items() if key != 'origin'} for row in intake['rows']]
        load = dict(origin='intake', rows=rows, intake_receipt_sha256=intake.get('receipt_sha256'))
    packet = dict(schema_version='stowlm.voyage-packet/1', packet_id=call_dir.name,
                  created_utc=utc_now(), synthetic=True,
                  base_instance=dict(path=str(base.resolve()), sha256=hashlib.sha256(base.read_bytes()).hexdigest()),
                  load_list=load, yard_state=None, standing='', voyage_sentences=[],
                  profile=controls.get('profile', 'standard'))
    if packet['profile'] not in PROFILES:
        raise ValueError('Choose the live or standard engine profile.')
    return packet


def generate(call_dir, controls):
    from app.functional_cases import generate_case
    case = generate_case(read_json(call_dir / 'request.json'), call_dir)
    write_json(call_dir / 'case.json', case)
    raw_gate = case.get('envelope') or {}
    write_json(call_dir / 'runner-envelope.json', raw_gate)
    gate = _effective_gate(call_dir, raw_gate, case)
    write_json(call_dir / 'envelope.json', gate)
    append_stage(call_dir, 'envelope', mode='generated', output_sha256=_digest(gate),
                 notes='Design envelope screen; this is not a feasibility proof.')
    if not gate.get('in_envelope'):
        _state(call_dir, 'out_of_envelope', _gate_message(gate), 'generate')
    else:
        _state(call_dir, 'needs_papers', 'Choose cargo papers or the generated load list.', 'generate')
    return case


def intake(call_dir, controls):
    from app.functional_models import read_papers, answer_papers
    generator_choice = 'use_generator_records_for' in controls
    if not controls.get('use_generated_load_list') and not controls.get('answers') and not generator_choice:
        # A new input-validation failure must not inherit a previous cap stop.
        accounting = intake_accounting(call_dir)
        if accounting:
            accounting.update(incremental_cost_usd=0., elapsed_s=0., cap_stop=None, message=None)
            write_json(call_dir / 'intake-accounting.json', accounting)
            public = read_json(call_dir / 'intake-view.json')
            if isinstance(public, dict):
                write_json(call_dir / 'intake-view.json', dict(public, **accounting))
    if controls.get('use_generated_load_list'):
        result = dict(skipped=True, why='The planner chose the generated load list, not read by a model.')
    elif controls.get('answers'):
        accounting = intake_accounting(call_dir)
        if accounting:
            accounting['incremental_cost_usd'] = 0.
            write_json(call_dir / 'intake-accounting.json', accounting)
        prior = read_json(call_dir / 'intake-bookings.json')
        if generator_choice:
            before = generator_records_input(call_dir)
            result = _booking_stage(call_dir, None, 'answers', _answer_generator_records,
                                    before, controls['answers'], answer_papers)
        elif prior:
            result = _answer_bookings(call_dir, prior, controls['answers'], answer_papers)
        else:
            result = _booking_stage(call_dir, None, 'answers', answer_papers,
                                    read_json(call_dir / 'intake.json'), controls['answers'])
    elif generator_choice:
        before = generator_records_input(call_dir)
        chosen = controls['use_generator_records_for']
        result = _booking_stage(call_dir, None, 'answers', _use_generator_records,
                                before, call_dir / 'base-instance.json', chosen)
    else:
        documents = generated_papers(call_dir) if controls.get('use_generated_papers') else controls.get('documents', [])
        if controls.get('use_generated_papers'):
            write_json(call_dir / 'papers' / 'meta.json', dict(documents=documents))
            write_json(call_dir / 'papers.json', dict(documents=documents))
        result = _read_bookings(call_dir, documents, read_papers)
        if result.get('retry_bookings'):
            _state(call_dir, 'needs_papers', result['message'], 'intake')
            return result
    if not result.get('skipped'):
        accounting = intake_accounting(call_dir)
        if accounting:
            if generator_choice:
                accounting.update(incremental_cost_usd=0., cap_stop=None, message=None,
                                  open_questions=len(result.get('questions') or []))
                write_json(call_dir / 'intake-accounting.json', accounting)
            result.update(accounting)
    write_json(call_dir / 'intake.json', result)
    write_json(call_dir / 'intake-view.json', {key: value for key, value in result.items() if key != 'raw'})
    complete = result.get('skipped') or result.get('complete')
    _state(call_dir, 'assembling' if complete else 'needs_answers',
           'Cargo is ready to assemble.' if complete else 'Answer the cargo-paper questions before planning.', 'intake')
    return result


def generator_records_input(call_dir):
    """Read full intake evidence, rebuilding verified child receipts after a full-list skip."""
    call_dir = Path(call_dir)
    prior = read_json(call_dir / 'intake.json')
    if isinstance(prior, dict) and not prior.get('skipped'):
        return prior
    records = _load_booking_records(call_dir)
    if not records or any(not isinstance(row.get('view'), dict)
                          or row.get('status') not in ('done', 'needs_answers') for row in records):
        raise ValueError('The booking readings are incomplete; read the remaining papers before choosing generator records.')
    return _merge_bookings(records)


def _generator_record_label(result, selected):
    """Preserve the helper's full mixed view while exposing explicit generator credit."""
    choices = result.get('generator_choices')
    ids = [row.get('box') for row in choices] if isinstance(choices, list) else result.get('generator_record_ids', selected)
    if (not isinstance(ids, list) or not ids or any(not isinstance(box, str) or not box for box in ids)):
        raise ValueError('The selected generator records are missing from the intake result.')
    result['generator_record_ids'] = list(dict.fromkeys(ids))
    result['row_origins'] = {row['id']: row['origin'] for row in result.get('rows', [])
                             if isinstance(row, dict) and row.get('id') and row.get('origin') in ('spark', 'generator')}
    result['origin_label'] = (f'Muse Spark, with {len(result["generator_record_ids"])} boxes from the generator '
                              '(not read by a model)')


def _use_generator_records(before, base, selected):
    from app.functional_models import merge_generator_records
    result = dict(merge_generator_records(before, base, selected))
    _generator_record_label(result, list(before.get('generator_record_ids', [])) + list(selected))
    return result


def _answer_generator_records(before, answers, answer_papers):
    result = dict(answer_papers(before, answers))
    _generator_record_label(result, before.get('generator_record_ids', []))
    return result


def _public_error(error, step):
    names = {cls.__name__ for cls in type(error).__mro__}
    unavailable = 'Unavailable' in names and hasattr(error, 'reason')
    safe_reason = unavailable or ('ModelError' in names and hasattr(error, 'reason'))
    message = (error.reason if safe_reason else str(error) if isinstance(error, ValueError) else
               f'The {step} stage failed ({type(error).__name__}). See the worker log for details.')
    return unavailable, message


def _booking_stage(call_dir, booking_ref, operation, function, *args, **kwargs):
    instance_id = f'{operation}-booking-{uuid4().hex}'
    started, began = utc_now(), time.monotonic()
    label = str(booking_ref) if booking_ref is not None else 'no booking reference'
    who = f'Muse Spark · booking {label}' if operation == 'intake' else "Planner's answers"
    fields = dict(instance_id=instance_id, started_utc=started, who=who,
                  where='Meta Model API, cloud' if operation == 'intake' else 'this laptop',
                  model='muse-spark-1.3' if operation == 'intake' else None,
                  notes=f'Booking {label}; host-supplied document group.')
    if operation == 'answers':
        fields['cost_usd'] = 0.
    append_stage(call_dir, operation, 'running', **fields)
    try:
        result = function(*args, **kwargs)
    except Exception as error:
        elapsed = time.monotonic()-began
        error.booking_wall_s = elapsed
        unavailable, message = _public_error(error, operation)
        budget = getattr(kwargs.get('client'), 'budget', None)
        settled = sum(item.get('settled_cost_usd') or 0 for item in budget.requests) if budget else getattr(error, 'cost_usd', None)
        append_stage(call_dir, operation, 'unavailable' if unavailable else 'failed',
                     mode='unavailable' if unavailable else 'live', wall_s=elapsed,
                     **dict(fields, cost_usd=0. if operation == 'answers' else settled, notes=message))
        raise
    result = dict(result)
    if operation == 'intake':
        result.setdefault('read_wall_s', result.get('wall_s', time.monotonic()-began))
        result.setdefault('read_cost_usd', result.get('cost_usd'))
    else:
        original = args[0]
        result.setdefault('read_wall_s', original.get('read_wall_s', original.get('wall_s', 0)))
        result.setdefault('read_cost_usd', original.get('read_cost_usd', original.get('cost_usd')))
        result.setdefault('answer_wall_s', (original.get('answer_wall_s') or 0) + time.monotonic()-began)
    boxes = result.get('boxes', len(result.get('rows', [])))
    append_stage(call_dir, operation, wall_s=time.monotonic()-began,
                 output_sha256=_digest(result),
                 **dict(fields, who=f'{who} · {boxes} boxes' if operation == 'intake' else who,
                        cost_usd=result.get('cost_usd') if operation == 'intake' else 0.,
                        notes=result.get('origin_label') or f'Booking {label}; {boxes} boxes. Measured on this machine, this run.'))
    return result


def _merge_bookings(records):
    from app.functional_models import merge_intake_views
    # Unscoped uploaded bookings are one intake evaluation. The model merge
    # helper intentionally rejects a child containing several booking refs.
    result = dict(records[0]['view']) if len(records) == 1 and records[0].get('booking_ref') is None else merge_intake_views([record['view'] for record in records])
    result.setdefault('read_wall_s', sum(record['view'].get('read_wall_s', record['view'].get('wall_s', 0)) or 0 for record in records))
    result.setdefault('read_cost_usd', sum(record['view'].get('read_cost_usd', record['view'].get('cost_usd', 0)) or 0 for record in records))
    result.setdefault('answer_wall_s', sum(record['view'].get('answer_wall_s', 0) or 0 for record in records))
    return result


def _document_projection(document, *, captured=False):
    """Fingerprint exactly the bytes and host metadata supplied to intake."""
    path = Path(document.get('path', ''))
    if captured:
        digest = document.get('sha256')
        if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest):
            raise ValueError('A retained document receipt has no valid byte hash.')
        media = document.get('mime_type')
        text = document.get('text')
    else:
        try:
            data = path.read_bytes()
        except OSError:
            raise ValueError('A cargo document is missing or unreadable; select the papers again.') from None
        digest = hashlib.sha256(data).hexdigest()
        media = document.get('media_type')
        textual = (str(media).startswith('text/') or media in ('application/json', 'application/csv')
                   or path.suffix.lower() in ('.txt', '.csv', '.eml', '.json'))
        try:
            text = data.decode('utf-8-sig') if textual else None
        except UnicodeError:
            raise ValueError('A text cargo document is not valid UTF-8.') from None
    result = dict(name=document.get('name'), role=document.get('role', 'unspecified'), mime_type=media, sha256=digest,
                  booking_ref=document.get('booking_ref'),
                  complete_booking_list=document.get('complete_booking_list', False),
                  synthetic=document.get('synthetic', False))
    # Random upload filenames are storage details, not document authority.
    # An explicit host id remains part of the input fingerprint.
    if 'id' in document and (not captured or document['id'] != path.stem):
        result['id'] = document['id']
    if text is not None:
        if not isinstance(text, str):
            raise ValueError('A retained document receipt contains invalid text.')
        result['text_sha256'] = hashlib.sha256(text.encode()).hexdigest()
    return result


def _input_fingerprints(documents, base):
    return dict(documents_sha256=_digest([_document_projection(document) for document in documents]),
                base_sha256=_digest(read_json(base)))


def _legacy_fingerprints(view):
    """Only migrate a legacy paid read when its original receipt proves inputs."""
    raw = view.get('raw')
    if not isinstance(raw, dict) or view.get('receipt_sha256') != _digest(raw):
        return {}
    records = raw.get('records') or []
    if not records or any(row.get('mode') != 'live' for row in records):
        return {}
    for record in reversed(records):
        try:
            note = json.loads(record.get('notes', ''))
            context = note.get('intake_context') or {}
            documents, base = context['documents'], context['base_instance']
            if not isinstance(documents, list) or not isinstance(base, dict):
                continue
            before = dict(documents=documents, base_instance=base)
            if 'state' in context:
                before['state'] = context['state']
            else:
                before['answers'] = note.get('answers') or {}
            if record.get('input_sha256') != _digest(before):
                continue
            return dict(documents_sha256=_digest([_document_projection(document, captured=True) for document in documents]),
                        base_sha256=_digest(base))
        except (KeyError, ValueError, TypeError):
            continue
    return {}


def _view_file(call_dir, record):
    relative = record.get('file')
    if not isinstance(relative, str) or Path(relative).is_absolute():
        return None
    try:
        path = (call_dir / relative).resolve()
        if not path.is_relative_to((call_dir / 'intake' / 'bookings').resolve()) or not path.is_file():
            return None
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != record.get('sha256'):
            return None
        value = json.loads(data)
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, RuntimeError):
        return None


def _persist_view(call_dir, record):
    view = record.get('view')
    if not isinstance(view, dict):
        return
    safe = re.sub(r'[^A-Za-z0-9._-]', '-', _redact(str(record.get('booking_ref') or 'unassigned')))[:64].strip('.') or 'booking'
    name = safe + '-' + _digest(view)[:16] + '.json'
    path = call_dir / 'intake' / 'bookings' / name
    expected = (json.dumps(_redact(view), ensure_ascii=False, allow_nan=False, sort_keys=True) + '\n').encode()
    if path.exists() and path.read_bytes() != expected:
        path = path.with_name(path.stem + '-' + uuid4().hex[:8] + '.json')
    if not path.exists():
        write_json(path, view)
    record.update(file=str(path.relative_to(call_dir)), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                  status='done' if view.get('complete') else 'needs_answers', boxes=view.get('boxes', len(view.get('rows', []))),
                  wall_s=view.get('wall_s'), cost_usd=view.get('cost_usd'), open_questions=len(view.get('questions') or []))


def _compact_record(record):
    keys = ('booking_ref', 'status', 'boxes', 'wall_s', 'cost_usd', 'file', 'sha256', 'documents_sha256', 'base_sha256',
            'spend_wait_s', 'request_evidence', 'open_questions', 'reason')
    value = dict({key: record.get(key) for key in keys}, kept=bool(record.get('_kept')))
    if isinstance(record.get('view'), dict):
        value['open_questions'] = len(record['view'].get('questions') or [])
    return value


def _write_booking_index(call_dir, records):
    write_json(call_dir / 'intake-bookings.json', dict(schema=BOOKING_INDEX_SCHEMA,
                                                    bookings=[_compact_record(record) for record in records]))


def _load_booking_records(call_dir, prior=None):
    prior = prior if prior is not None else read_json(call_dir / 'intake-bookings.json', {})
    records = []
    legacy = prior.get('schema') != BOOKING_INDEX_SCHEMA
    for item in prior.get('bookings', []):
        record = dict(item)
        if isinstance(item.get('view'), dict):
            record.update(_legacy_fingerprints(item['view']))
            _persist_view(call_dir, record)
        else:
            record['view'] = _view_file(call_dir, record)
            record.setdefault('status', 'pending')
        records.append(record)
    if legacy and records:
        archive = call_dir / 'intake' / ('legacy-index-' + _digest(prior)[:16] + '.json')
        if not archive.exists():
            write_json(archive, dict(schema=BOOKING_INDEX_SCHEMA, bookings=[_compact_record(row) for row in records]))
    return records


def _booking_grant_api():
    import importlib
    models = importlib.import_module('app.functional_models')
    grant, release = getattr(models, 'grant_call', None), getattr(models, 'release', None)
    if not callable(grant) or not callable(release):
        reason = 'Muse Spark booking-budget grants are unavailable in this launch; no request was sent.'
        unavailable = getattr(models, 'Unavailable', None)
        if unavailable is not None:
            raise unavailable('spark', reason)
        raise ValueError(reason)
    return grant, release


def _grant_request(grant_call, call_dir, booking_ref):
    """Ask the atomic broker for refusal facts when its API supports them."""
    import inspect
    facts = {}
    parameters = inspect.signature(grant_call).parameters
    kwargs = dict(call_id=call_dir.name, booking_ref=booking_ref)
    if 'refusal' in parameters or any(value.kind == inspect.Parameter.VAR_KEYWORD for value in parameters.values()):
        kwargs['refusal'] = facts
    return grant_call(**kwargs), facts


def _cap_stop(booking_ref, facts, *, repair=False):
    facts = dict(facts, booking_ref=booking_ref, sent=False, charged_usd=0., request_kind='schema_repair' if repair else 'initial')
    label = str(booking_ref or '(unassigned)')
    if all(facts.get(key) is not None for key in ('spent_usd', 'hold_usd', 'cap_usd')):
        amount = f"${float(facts['spent_usd']):.2f} spent"
        for key, word in (('holds_usd', 'held'), ('grants_usd', 'reserved for other requests')):
            if float(facts.get(key) or 0):
                amount += f" plus ${float(facts[key]):.6f} {word}"
        subject = f"The schema-repair request for booking {label}" if repair else f"Booking {label}"
        message = (f"{subject} wasn't sent: {amount} plus this request's worst-case hold of "
                   f"${float(facts['hold_usd']):.6f} would pass your ${float(facts['cap_usd']):.2f} cap. "
                   'Nothing was charged for that unsent request.')
    else:
        message = NO_INTAKE_HOLD
    error = ValueError(message)
    error.cap_stop = facts
    return error


class _GrantCoordinator:
    """Only the short broker operations share a lock; HTTP never does."""
    WAIT_S = 180.
    def __init__(self, call_dir, grant_call, release):
        self.call_dir, self.grant_call, self.release = call_dir, grant_call, release
        self.condition, self.active = threading.Condition(), []

    def acquire(self, booking_ref):
        with self.condition:
            grant, facts = _grant_request(self.grant_call, self.call_dir, booking_ref)
            if grant is not None:
                self.active.append(grant)
            return grant, facts

    def close(self, grant):
        try:
            self.release(grant)
        finally:
            # A failed release remains held in the model registry, but it can
            # no longer promise to free room for another local request.
            with self.condition:
                self.active = [item for item in self.active if item is not grant]
                self.condition.notify_all()

    def repair(self, booking_ref):
        deadline = time.monotonic() + self.WAIT_S
        with self.condition:
            while True:
                grant, facts = self.acquire(booking_ref)
                if grant is not None:
                    return grant
                remaining = deadline - time.monotonic()
                if not self.active or remaining <= 0:
                    raise _cap_stop(booking_ref, facts, repair=True)
                # A sibling still owns allowance. Its completed request will
                # persist evidence and notify us when that allowance settles.
                self.condition.wait(timeout=remaining)


class _BookingBudget:
    """Own grants, including a separate allowance for the schema-only retry."""
    def __init__(self, call_dir, booking_ref, grant, coordinator, attempt_id):
        self.call_dir, self.booking_ref = call_dir, booking_ref
        self.coordinator = coordinator
        self.grants, self.released = [grant], []
        self.attempt_id = attempt_id
        self.requests = []
        self.spend_wait_s = 0.

    def close(self):
        errors = []
        for grant in self.grants:
            if not any(item is grant for item in self.released):
                self.released.append(grant)
                try:
                    self.coordinator.close(grant)
                except Exception as error:
                    errors.append(error)
        if errors:
            raise errors[0]

    def next(self):
        # The previous request has settled and its evidence is durable before
        # spare allowance returns to the launch pool for a schema-only retry.
        self.close()
        began = time.monotonic()
        try:
            grant = self.coordinator.repair(self.booking_ref)
        finally:
            self.spend_wait_s += time.monotonic() - began
        self.grants.append(grant)
        return grant

    def evidence(self, grant, response, error, began, ledger):
        cost = getattr(response, 'cost_usd', None)
        held = None
        if ledger is not None:
            # This is the completed request's own ledger, never another
            # booking's HTTP lock or the shared launch registry.
            actual, pending = ledger._totals(ledger._records())
            cost, held = float(actual), float(pending)
        data = dict(booking_ref=self.booking_ref, request=len(self.requests)+1,
                    settled_cost_usd=float(cost) if cost is not None else None,
                    held_usd=held, elapsed_s=time.monotonic()-began,
                    request_id=getattr(response, 'request_id', None),
                    raw_text=getattr(response, 'text', None),
                    error=_public_error(error, 'intake')[1] if error else None)
        path = self.call_dir / 'intake' / 'requests' / self.attempt_id / (uuid4().hex + '.json')
        write_json(path, data)
        self.requests.append(dict(file=str(path.relative_to(self.call_dir)),
                                  **{key: data[key] for key in ('settled_cost_usd', 'held_usd', 'elapsed_s')}))


class _RequestGrantClient:
    def __init__(self, budget):
        self.budget = budget

    def complete_documents(self, content, **kwargs):
        from app import functional_models as models
        from experiments.mvp.pipeline.intake.interpretation import InterpretationClient
        if len(self.budget.requests) >= 2:
            raise ValueError('Intake permits only the initial request and one schema-only repair.')
        models._require('spark')
        grant = self.budget.grants[0] if not self.budget.requests else self.budget.next()
        ledger = models.functional_ledger(grant=grant)
        client = InterpretationClient(api_key=os.environ.get('MODEL_API_KEY'),
                                      credit_ready=(os.environ.get('SPARK_CREDIT_READY') == '1'
                                                    or os.environ.get('STOWLM_ACCEPT_PAID_CALLS') == '1'),
                                      ledger_path=ledger.path, compute_path=ledger.compute_path,
                                      spend_cap_usd=ledger.cap_usd, max_retries=0)
        client.ledger, client.task_cap_usd = ledger, ledger.cap_usd
        began, response, error = time.monotonic(), None, None
        try:
            response = client.complete_documents(content, **kwargs)
            return response
        except Exception as caught:
            error = caught
            raise
        finally:
            # A provider response rejected by the schema is still paid
            # evidence; save it before releasing that request's allowance.
            self.budget.evidence(grant, response, error, began, ledger)


def _booking_read(call_dir, booking_ref, read_papers, documents, base, budget):
    import inspect
    from app import functional_models as models
    parameters = inspect.signature(read_papers).parameters
    kwargs = dict(grant=budget.grants[0])
    if 'client' in parameters and callable(getattr(models, 'functional_ledger', None)):
        kwargs['client'] = _RequestGrantClient(budget)
    return _booking_stage(call_dir, booking_ref, 'intake', read_papers, documents, base, **kwargs)


def intake_accounting(call_dir):
    """Safe cumulative/current-attempt facts for aggregate success or failure."""
    return read_json(Path(call_dir) / 'intake-accounting.json', {}) or {}


def _save_intake_accounting(call_dir, records, metrics):
    views = [record['view'] for record in records if isinstance(record.get('view'), dict)]
    incremental = sum(record.get('_attempt_cost_usd') or 0 for record in records)
    result = dict(settled_cost_usd=metrics['prior_settled_cost_usd'] + incremental,
                  incremental_cost_usd=incremental, elapsed_s=time.monotonic()-metrics['began'],
                  booking_wall_s=metrics['prior_booking_wall_s'] + sum(record.get('_attempt_wall_s') or 0 for record in records),
                  spend_wait_s=metrics['prior_spend_wait_s'] + sum(record.get('spend_wait_s') or 0 for record in records if not record.get('_kept')),
                  completed_bookings=len(views), kept_bookings=sum(bool(record.get('_kept')) for record in records),
                  unsent_bookings=sum(not record.get('_kept') and not record.get('_submitted') for record in records),
                  open_questions=sum(len(view.get('questions') or []) for view in views),
                  cap_stop=metrics.get('cap_stop'), message=None)
    if result['cap_stop'] is not None:
        result['cap_stop'] = dict(result['cap_stop'], **{key: result[key] for key in (
            'completed_bookings', 'kept_bookings', 'unsent_bookings', 'open_questions')})
        result['message'] = (metrics['cap_message'] + f" {len(views)} booking readings are kept for a retry; "
                             f"{result['open_questions']} questions remain open. Nothing has been planned from a partial reading. "
                             'Raise the cap in the API key panel to read the rest.')
    write_json(call_dir / 'intake-accounting.json', result)
    return result


def _booking_refs(records):
    labels = []
    for record in records:
        value = str(record.get('booking_ref') or 'unassigned')
        match = re.search(r'-B(\d+)$', value)
        labels.append('B' + match[1] if match else value)
    if labels and all(re.fullmatch(r'B\d+', label) for label in labels):
        groups, first, last = [], int(labels[0][1:]), int(labels[0][1:])
        for label in labels[1:]:
            number = int(label[1:])
            if number == last + 1:
                last = number
                continue
            groups.append(f'B{first}-B{last}' if first != last else f'B{first}')
            first = last = number
        groups.append(f'B{first}-B{last}' if first != last else f'B{first}')
        return ', '.join(groups)
    return ', '.join(labels)


def _booking_progress(call_dir, records):
    views = [record['view'] for record in records if isinstance(record.get('view'), dict)]
    if not views:
        prior = read_json(call_dir / 'intake-view.json', {}) or {}
        accounting = intake_accounting(call_dir)
        if accounting:
            write_json(call_dir / 'intake-view.json', dict(prior, **accounting))
        return  # Keep any previous public summary until there is new evidence.
    rows = [row for view in views for row in view.get('rows', [])]
    summary = dict(mode='live', complete=False, model='muse-spark-1.3', rows=rows[:200], boxes=len(rows),
                   questions=[], conflicts=[], receipt_sha256=None,
                   cost_usd=sum(view.get('cost_usd') or 0 for view in views),
                   wall_s=sum(view.get('wall_s') or 0 for view in views),
                   read_wall_s=sum(view.get('read_wall_s', view.get('wall_s')) or 0 for view in views),
                   read_cost_usd=sum(view.get('read_cost_usd', view.get('cost_usd')) or 0 for view in views),
                   progress=dict(completed_bookings=len(views), total_bookings=len(records),
                                 pending_bookings=len(records)-len(views)))
    summary.update(intake_accounting(call_dir))
    write_json(call_dir / 'intake-view.json', summary)


def _read_bookings(call_dir, documents, read_papers):
    if not isinstance(documents, list) or not documents:
        raise ValueError('Supply at least one cargo document.')
    base_roster = None
    for document in documents:
        metadata = document_metadata(document)
        if 'container_ids' in metadata:
            if base_roster is None:
                base_roster = set((read_json(call_dir / 'base-instance.json') or {}).get('load_list', []))
            if not set(metadata['container_ids']) <= base_roster:
                raise ValueError('Host-confirmed booking membership names containers outside this call load list.')
    has_booking_sets = any(document.get('booking_ref') is not None for document in documents)
    if has_booking_sets:
        documents = [document for document in documents if not (
            document.get('origin') == 'generated' and document.get('booking_ref') is None and document.get('role', 'unspecified') == 'unspecified'
            and (document.get('name') == 'load-list.csv' or Path(document.get('path', '')).name == 'load-list.csv'))]
    groups = {}
    for document in documents:
        groups.setdefault(document.get('booking_ref'), []).append(document)
    # An uploaded booking reference is not a membership declaration. Without
    # host scopes, evaluate the papers together once against the call roster.
    # Otherwise each separate read would ask about every box in that roster.
    unscoped = any(not any('container_ids' in doc for doc in group)
                   and not all(doc.get('origin') == 'generated' for doc in group)
                   for ref, group in groups.items())
    if unscoped:
        groups = {None: documents}
    previous = _load_booking_records(call_dir)
    prior_accounting = intake_accounting(call_dir)
    metrics = dict(began=time.monotonic(), attempt_id=uuid4().hex,
                   prior_settled_cost_usd=max(prior_accounting.get('settled_cost_usd') or 0,
                                             sum(record.get('cost_usd') or 0 for record in previous)),
                   prior_booking_wall_s=max(prior_accounting.get('booking_wall_s') or 0,
                                            sum((record.get('view') or {}).get('read_wall_s', record.get('wall_s')) or 0 for record in previous)),
                   prior_spend_wait_s=prior_accounting.get('spend_wait_s') or 0)
    records, bases, kept = [], [], []
    for index, (booking_ref, group) in enumerate(groups.items()):
        base = call_dir / 'base-instance.json' if unscoped else _booking_base(call_dir, index, booking_ref, group)
        fingerprint = _input_fingerprints(group, base)
        record = dict(booking_ref=booking_ref, status='pending', view=None, **fingerprint)
        for candidate in previous:
            if (candidate.get('booking_ref') == booking_ref and isinstance(candidate.get('view'), dict)
                    and candidate.get('status') in ('done', 'needs_answers')
                    and all(candidate.get(key) == value for key, value in fingerprint.items())):
                record = dict(candidate)
                record['_kept'] = True
                kept.append(record)
                break
        records.append(record)
        bases.append(base)
    kept_files = {record.get('file') for record in kept}
    unmatched = [record for record in previous if isinstance(record.get('view'), dict) and record.get('file') not in kept_files]
    if unmatched:
        archived = dict(schema=BOOKING_INDEX_SCHEMA, bookings=[_compact_record(record) for record in previous])
        destination = call_dir / 'intake' / ('history-index-' + _digest(archived)[:16] + '.json')
        if not destination.exists():
            write_json(destination, archived)
    _write_booking_index(call_dir, records)
    _save_intake_accounting(call_dir, records, metrics)
    _booking_progress(call_dir, records)
    pending = [index for index, record in enumerate(records) if record.get('view') is None]
    if kept:
        note = _booking_refs(kept) + ' kept from the earlier read; '
        note += 'reading ' + _booking_refs([records[index] for index in pending]) if pending else 'no additional Spark read is needed.'
        append_stage(call_dir, 'intake', who='Earlier booking reads of this call', where='software',
                     model=None, cost_usd=0., notes=note)
    if not pending:
        return _merge_bookings(records)
    grant_call, release = _booking_grant_api()
    coordinator = _GrantCoordinator(call_dir, grant_call, release)
    errors, retry = [], []
    with ThreadPoolExecutor(max_workers=6, thread_name_prefix='functional-intake') as executor:
        active = {}
        announced = False
        waiting = {}
        last_refusal = {}

        def submit():
            nonlocal announced, last_refusal
            while pending and len(active) < 6 and not errors:
                index = pending[0]
                record = records[index]
                try:
                    before = time.monotonic()
                    grant, refusal = coordinator.acquire(record['booking_ref'])
                except Exception as error:
                    denied = any(cls.__name__ == 'SpendCapExceeded' for cls in type(error).__mro__)
                    if not denied and str(error) != NO_INTAKE_HOLD and getattr(error, 'reason', None) != NO_INTAKE_HOLD:
                        errors.append(error)
                    break
                if grant is None:
                    waiting.setdefault(index, before)
                    last_refusal = refusal
                    break
                record['spend_wait_s'] = time.monotonic()-waiting.pop(index, before)
                budget = _BookingBudget(call_dir, record['booking_ref'], grant, coordinator, metrics['attempt_id'])
                pending.pop(0)
                try:
                    future = executor.submit(_booking_read, call_dir, record['booking_ref'], read_papers,
                                             groups[record['booking_ref']], bases[index], budget)
                    record['_submitted'] = True
                except Exception as error:
                    errors.append(error)
                    try:
                        budget.close()
                    except Exception as release_error:
                        errors.append(release_error)
                    break
                active[future] = (index, budget)
            if not announced and active:
                announced = True
                append_stage(call_dir, 'intake', who='Intake booking schedule', where='software', cost_usd=0.,
                             notes=(f'{len(active)} booking' + ('' if len(active) == 1 else 's')
                                    + ' at a time: each read has its own ledger and an allocated launch-budget grant; maximum 6.'))
            if pending and not active and not errors:
                error = _cap_stop(records[pending[0]]['booking_ref'], last_refusal)
                metrics.update(cap_stop=error.cap_stop, cap_message=str(error))
                errors.append(error)

        def finish(future, allocation):
            index, budget = allocation
            records[index]['spend_wait_s'] = (records[index].get('spend_wait_s') or 0) + budget.spend_wait_s
            try:
                if not future.cancelled():
                    records[index]['view'] = future.result()
                    records[index]['_attempt_cost_usd'] = records[index]['view'].get('read_cost_usd', records[index]['view'].get('cost_usd')) or 0
                    records[index]['_attempt_wall_s'] = records[index]['view'].get('read_wall_s', records[index]['view'].get('wall_s')) or 0
                    _persist_view(call_dir, records[index])
                else:
                    records[index]['_submitted'] = False
                # Make successful paid results durable before returning any
                # unused allowance or scheduling a replacement booking.
                records[index]['_attempt_cost_usd'] = max(records[index].get('_attempt_cost_usd') or 0,
                                                         sum(item.get('settled_cost_usd') or 0 for item in budget.requests))
                records[index]['request_evidence'] = budget.requests
                _write_booking_index(call_dir, records)
                _save_intake_accounting(call_dir, records, metrics)
                _booking_progress(call_dir, records)
            except Exception as error:
                records[index]['status'] = 'failed'
                records[index]['_attempt_cost_usd'] = max(getattr(error, 'cost_usd', 0) or 0,
                                                         sum(item.get('settled_cost_usd') or 0 for item in budget.requests))
                records[index]['_attempt_wall_s'] = max(getattr(error, 'booking_wall_s', 0) or 0,
                                                        sum(item.get('elapsed_s') or 0 for item in budget.requests))
                records[index]['request_evidence'] = budget.requests
                if _booking_only_failure(error):
                    # Only this booking's reading is lost: keep the others running and their readings for a retry.
                    records[index].update(reason=error.reason, cost_usd=records[index]['_attempt_cost_usd'],
                                          wall_s=records[index]['_attempt_wall_s'])
                    retry.append(index)
                else:
                    if hasattr(error, 'cap_stop'):
                        metrics.update(cap_stop=error.cap_stop, cap_message=str(error))
                    errors.append(error)
                    for other in active:
                        other.cancel()
                try:
                    _write_booking_index(call_dir, records)
                    _save_intake_accounting(call_dir, records, metrics)
                    _booking_progress(call_dir, records)
                except Exception as persistence_error:
                    errors.append(persistence_error)
            finally:
                try:
                    budget.close()
                except Exception as release_error:
                    errors.append(release_error)
                    for other in active:
                        other.cancel()

        try:
            submit()
            while active:
                done, _ = wait(active, return_when=FIRST_COMPLETED)
                for future in done:
                    finish(future, active.pop(future))
                if not errors:
                    submit()
        finally:
            # Even a coordinator/persistence failure must not orphan grants.
            # The model's release() decides how pending billing remains held.
            for future in active:
                future.cancel()
            for future, allocation in list(active.items()):
                finish(future, allocation)
            active.clear()
    if errors:
        for index, began_wait in waiting.items():
            records[index]['spend_wait_s'] = time.monotonic()-began_wait
        _write_booking_index(call_dir, records)
        accounting = _save_intake_accounting(call_dir, records, metrics)
        _booking_progress(call_dir, records)
        if accounting.get('message') and (hasattr(errors[0], 'cap_stop') or str(errors[0]).startswith(NO_INTAKE_HOLD)):
            stopped = ValueError(accounting['message'])
            stopped.cap_stop = accounting['cap_stop']
            raise stopped from errors[0]
        raise errors[0]
    _save_intake_accounting(call_dir, records, metrics)
    if retry:
        _write_booking_index(call_dir, records)
        _booking_progress(call_dir, records)
        failed = [records[index] for index in retry]
        others = sum(1 for record in records if isinstance(record.get('view'), dict))
        message = (f"{'Booking ' + _booking_refs(failed) if len(failed) == 1 else 'Bookings ' + _booking_refs(failed)}: the app couldn't "
                   "turn Muse Spark's reading into load-list rows, so nothing from "
                   + ('it' if len(failed) == 1 else 'them') + ' was used. Retry the failed '
                   + ('booking' if len(failed) == 1 else 'bookings') + f'; the other {others} booking '
                   + ('reading is' if others == 1 else 'readings are') + ' kept. Nothing has been planned from a partial reading.')
        return dict(retry_bookings=[record.get('booking_ref') for record in failed], message=message,
                    cost_usd=sum(record.get('_attempt_cost_usd') or 0 for record in records))
    return _merge_bookings(records)


def _booking_only_failure(error):
    """A failure confined to one booking's reading (its answer couldn't become rows): retry that booking alone."""
    return bool(getattr(error, 'retry_booking', False)) and isinstance(getattr(error, 'reason', None), str)


def _booking_base(call_dir, index, booking_ref, documents):
    """Use host-generated booking membership, never the model's proposed rows."""
    base_path = call_dir / 'base-instance.json'
    explicit = [document['container_ids'] for document in documents if 'container_ids' in document]
    if booking_ref is None and not explicit:
        return base_path
    generated = all(document.get('origin') == 'generated' for document in documents)
    if not explicit and (not generated or booking_ref is None):
        return base_path
    if explicit:
        roster = explicit[0]
        if any(value != roster for value in explicit[1:]):
            raise ValueError('Booking papers disagree on their host-confirmed container roster.')
    else:
        booking = [document for document in documents if document.get('role') == 'booking']
        if len(booking) != 1:
            raise ValueError('Each generated booking needs exactly one host-generated booking confirmation.')
        path = Path(booking[0]['path']).resolve()
        if not path.is_relative_to((call_dir / 'papers' / 'generated').resolve()):
            raise ValueError('Generated booking confirmations must stay inside their generated directory.')
        try:
            text = path.read_text(encoding='utf-8')
        except (OSError, UnicodeError):
            raise ValueError('The generated booking confirmation is missing or unreadable.') from None
        count = re.findall(r'^Complete container list for booking ' + re.escape(str(booking_ref)) + r'; (\d+) units\.$', text, re.MULTILINE)
        roster = [line.split('; ISO ', 1)[0] for line in text.splitlines() if '; ISO ' in line]
        if len(count) != 1 or int(count[0]) != len(roster):
            raise ValueError('The generated booking confirmation roster does not match its declared container count.')
    if (not isinstance(roster, list) or not roster or any(not isinstance(cid, str) or not cid for cid in roster)
            or len(set(roster)) != len(roster) or (generated and len(roster) > 8)):
        raise ValueError('Each booking needs a unique host roster; generated bookings contain at most eight containers.')
    base = read_json(base_path) or {}
    if not set(roster) <= set(base.get('load_list', [])):
        raise ValueError('A booking confirmation names containers outside this generated call load list.')
    scoped = dict(base, load_list=roster)
    destination = call_dir / 'intake-bases' / f'b{index+1:04d}.json'
    write_json(destination, scoped)
    return destination


def _answer_bookings(call_dir, prior, answers, answer_papers):
    records = _load_booking_records(call_dir, prior)
    if not isinstance(records, list) or not records or any(not isinstance(row.get('view'), dict) for row in records):
        raise ValueError('The per-booking intake receipts are incomplete; read the cargo papers again.')
    # Rebuild the route from the original child receipts, rather than trusting
    # editable projections or persisted question-route metadata.
    merged = _merge_bookings(records)
    questions = {row['id']: row for row in merged.get('questions', [])}
    raw = merged.get('raw') or {}
    if len(records) == 1 and not raw.get('bookings'):
        if set(answers) - questions.keys():
            raise ValueError('An answer does not match a current cargo-paper question.')
        records[0]['view'] = _booking_stage(call_dir, records[0]['booking_ref'], 'answers', answer_papers,
                                            records[0]['view'], answers)
        _persist_view(call_dir, records[0])
        _write_booking_index(call_dir, records)
        accounting = intake_accounting(call_dir)
        if accounting:
            accounting['open_questions'] = len(records[0]['view'].get('questions') or [])
            write_json(call_dir / 'intake-accounting.json', accounting)
        return _merge_bookings(records)
    routes = raw.get('question_routes') or {}
    children = raw.get('bookings') or []
    indices = {row['id']: index for index, row in enumerate(children)}
    routing = {}
    for question_id, value in answers.items():
        question = questions.get(question_id)
        if question is None:
            raise ValueError('An answer does not match a current cargo-paper question.')
        route = routes.get(question_id)
        if not route:
            raise ValueError('Duplicate container ids require corrected booking documents and another reading.')
        index = indices.get(route.get('booking'))
        if index is None or index >= len(records):
            raise ValueError('The cargo-paper question is missing its booking receipt reference.')
        routing.setdefault(index, {})[route['question']] = value
    for index, record in enumerate(records):
        if index not in routing:
            continue
        answered = _booking_stage(call_dir, record['booking_ref'], 'answers', answer_papers,
                                  record['view'], routing.pop(index))
        record['view'] = answered
        _persist_view(call_dir, record)
        _write_booking_index(call_dir, records)
    if routing:
        raise ValueError('An answer does not match a current booking receipt.')
    accounting = intake_accounting(call_dir)
    if accounting:
        accounting['open_questions'] = sum(len(record['view'].get('questions') or []) for record in records)
        write_json(call_dir / 'intake-accounting.json', accounting)
    return _merge_bookings(records)


def assemble(call_dir, controls):
    from experiments.mvp.pipeline.runner import assemble as assemble_packet
    from experiments.mvp.pipeline.runner.envelope import check
    from experiments.mvp.reader.schema import context_from_instance
    packet = _packet(call_dir, controls)
    with _runtime_scope(call_dir):
        instance = assemble_packet(packet)
    data = instance.model_dump(mode='json')
    raw_gate = check(instance)
    write_json(call_dir / 'runner-envelope.json', raw_gate)
    gate = _effective_gate(call_dir, raw_gate)
    write_json(call_dir / 'packet.json', packet)
    write_json(call_dir / 'assembled-instance.json', data)
    write_json(call_dir / 'context.json', context_from_instance(instance))
    write_json(call_dir / 'envelope.json', gate)
    append_stage(call_dir, 'envelope', output_sha256=_digest(gate), notes='Design envelope screen; this is not a feasibility proof.')
    _state(call_dir, 'ready' if gate['in_envelope'] else 'out_of_envelope',
           'Ready for an instruction or a plan with no instruction.' if gate['in_envelope'] else _gate_message(gate), 'assemble')
    return data


class _EmptyBriefParser:
    """There is no model reading when the planner explicitly supplies no instruction."""
    def parse(self, sentence, context):
        from experiments.mvp.reader.schema import SCHEMA_VERSION, validate_answer
        refusal = dict(schema_version=SCHEMA_VERSION,
                       refusal=dict(refused=True, code='not_an_instruction', reason='No instruction was supplied.'))
        return validate_answer(json.dumps(refusal), context), dict(parser='empty_brief', model=None, adapter_sha256=None)


@contextmanager
def _admission_scope(call_dir):
    """Use the binding waiver and also cover the runner's separate admission seam."""
    from unittest.mock import patch
    from app.v6 import _waive_admission_without_a_fleet
    _waive_admission_without_a_fleet()
    from experiments.mvp.pipeline.runner import admission
    original_wait, original_call = admission.wait_for_capacity, admission.call_admitted

    def wait(**kwargs):
        state = Path(kwargs.get('state_path') or admission.STATE)
        try:
            fresh = 0 <= time.time() - state.stat().st_mtime < 120
        except OSError:
            fresh = False
        reading = original_wait(**kwargs) if fresh else dict(waived='no fleet telemetry', load1=os.getloadavg()[0])
        write_json(call_dir / 'admission.json', reading)
        return reading

    def admitted(function, *args, admission, **kwargs):
        if not admission.get('waived'):
            return original_call(function, *args, admission=admission, **kwargs)
        import importlib
        with ExitStack() as stack:
            from experiments.mvp.pipeline.runner.admission import _binding_runtime_scope
            stack.enter_context(_binding_runtime_scope())
            for name in ('adapter', 'allocation'):
                module = importlib.import_module('experiments.mvp.pipeline.binding.' + name)
                stack.enter_context(patch.object(module, 'wait_for_capacity', lambda: dict(admission)))
            return function(*args, **kwargs)

    with patch.object(admission, 'wait_for_capacity', wait), patch.object(admission, 'call_admitted', admitted):
        yield


@contextmanager
def _runtime_scope(call_dir):
    """Keep generated caches and signing keys in the Functional runtime directory."""
    from unittest.mock import patch
    from experiments.mvp.pipeline.runner import core, runtime, admission
    root = call_dir.parent.parent
    cache = root / 'cache'
    original_writer = core._package_writer

    def package_writer():
        writer = original_writer()
        if writer is None:
            return None

        def write(*args, **kwargs):
            from experiments.mvp.pipeline.package import receipt
            # The runner binds KEY_DIR around this call. Override only that
            # writable path, leaving its immutable fixtures/source root intact.
            with patch.object(receipt, 'KEY_DIR', cache / 'runner' / '.keys'):
                return writer(*args, **kwargs)

        return write

    with ExitStack() as stack:
        stack.enter_context(patch.object(runtime, 'HERE', root))
        stack.enter_context(patch.object(core, '_package_writer', package_writer))
        stack.enter_context(patch.object(admission, 'CACHE_ROOT', cache / 'binding'))
        old = os.environ.get('STOWAGE_VESSEL_CACHE_DIR')
        os.environ['STOWAGE_VESSEL_CACHE_DIR'] = str(cache / 'vessels')
        try:
            runtime.configure_caches()
            yield
        finally:
            if old is None:
                os.environ.pop('STOWAGE_VESSEL_CACHE_DIR', None)
            else:
                os.environ['STOWAGE_VESSEL_CACHE_DIR'] = old


def _engine_stage_instance(call_dir, *, create=True, attempt=None):
    """Reuse the server's launch row, or publish one for a direct worker run."""
    path = Path(call_dir) / 'stages.jsonl'
    latest = {}
    if path.exists():
        for index, line in enumerate(path.read_text().splitlines()):
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            if row.get('stage') != 'engine':
                continue
            if attempt is not None and row.get('attempt', 1) != attempt:
                continue
            identity = row.get('instance_id') or row.get('started_utc') or index
            latest[identity] = (index, row)
    running = [item for item in latest.values() if item[1].get('status') == 'running']
    if running:
        row = max(running, key=lambda item: item[0])[1]
        return dict({key: row.get(key) for key in ('instance_id', 'started_utc')}, **({'attempt': attempt} if attempt is not None else {}))
    if not create:
        return None
    row = append_stage(call_dir, 'engine', 'running', instance_id='engine-plan-' + uuid4().hex,
                       started_utc=utc_now(), cost_usd=0., attempt=attempt,
                       notes='Searching for a checked plan. Runner preparation and admission precede engine search.')
    return {key: row.get(key) for key in ('instance_id', 'started_utc', 'attempt') if row.get(key) is not None}


def _runner_stages(call_dir, outcome, runner_started_utc=None, engine_stage=None, *, attempt=None, packet=None, confirmation=None):
    receipt = outcome.get('receipt') or {}
    evidence = outcome.get('evidence') or {}
    admission = read_json(call_dir / 'admission.json') or evidence.get('admission')
    attempt = attempt if attempt is not None else (read_json(call_dir / 'worker-control.json', {}) or {}).get('attempt')
    packet = outcome.get('packet') or packet or read_json(call_dir / 'run' / 'packet.json') or read_json(call_dir / 'packet.json') or {}
    events = outcome.get('events', [])

    def started(stage):
        relative = next((row.get('t_s') for row in events
                         if row.get('stage') == stage and row.get('kind') == 'stage_start'), None)
        if runner_started_utc is None or relative is None:
            return None
        return (runner_started_utc + timedelta(seconds=relative)).isoformat().replace('+00:00', 'Z')

    for row in receipt.get('steps', []):
        stage = row.get('stage')
        if stage not in ('reader', 'engine', 'checker', 'ladder'):
            continue
        notes = row.get('notes') or ''
        if stage == 'reader':
            notes += ' Re-validates the reading confirmed above; no second model call.' if packet.get('voyage_sentences') else ' No instruction was supplied; the reader had nothing to do.'
        if stage == 'engine':
            notes += ' Admission: ' + json.dumps(admission) if admission else ' Admission was not reached.'
            if evidence.get('engine_error'):
                notes += ' ' + json.dumps(evidence['engine_error'], sort_keys=True)
            if engine_stage:
                notes += " Wall time is the runner's measured engine stage; the stage start also includes runner preparation."
        if stage == 'checker':
            notes += ' ' + json.dumps(evidence.get('checker') or receipt.get('checker') or {}, sort_keys=True)
        if stage == 'ladder':
            notes += ' ' + str(outcome.get('message', ''))
        fields = {key: row.get(key) for key in ('mode', 'model', 'adapter_sha256', 'wall_s', 'cost_usd', 'input_sha256', 'output_sha256')}
        if stage == 'reader':
            confirmed = confirmation if confirmation is not None else read_json(Path(call_dir) / 'confirmation.json', {})
            fields.update(provenance='runner_revalidation' if packet.get('voyage_sentences') else 'runner_no_instruction',
                          source='runner_receipt', revalidation=bool(packet.get('voyage_sentences')),
                          model_call=False, where='software',
                          reading_id=confirmed.get('reading') if packet.get('voyage_sentences') else None,
                          sentence_sha256=confirmed.get('sentence_sha256') if packet.get('voyage_sentences') else None,
                          rule_sha256=confirmed.get('rule_sha256') if packet.get('voyage_sentences') else None)
        failed = (any(event.get('stage') == stage and event.get('kind') == 'error' for event in events)
                  or (stage == 'engine' and bool(evidence.get('engine_error'))))
        identity = dict(engine_stage) if stage == 'engine' and engine_stage else dict(started_utc=started(stage))
        identity['attempt'] = attempt
        if attempt is not None:
            identity.setdefault('instance_id', f'runner-{stage}-attempt-{attempt}')
        append_stage(call_dir, stage, 'failed' if failed else 'done', notes=notes.strip(), **identity, **fields)
    recorded = {row.get('stage') for row in receipt.get('steps', [])}
    for stage in ('engine', 'checker', 'ladder'):
        if stage in recorded:
            continue
        stage_events = [row for row in events if row.get('stage') == stage]
        if not stage_events:
            continue
        first = next((row.get('t_s') for row in stage_events if row.get('kind') == 'stage_start'), None)
        final = next((row.get('t_s') for row in reversed(stage_events)
                      if row.get('kind') in ('stage_end', 'error')), None)
        failed = any(row.get('kind') == 'error' for row in stage_events)
        identity = dict(engine_stage) if stage == 'engine' and engine_stage else dict(started_utc=started(stage))
        identity['attempt'] = attempt
        if attempt is not None:
            identity.setdefault('instance_id', f'runner-{stage}-attempt-{attempt}')
        append_stage(call_dir, stage, 'failed' if failed else 'done', **identity,
                     wall_s=final-first if first is not None and final is not None else None,
                     notes=json.dumps([row.get('data', {}) for row in stage_events], sort_keys=True))
    if engine_stage and 'engine' not in recorded and not any(row.get('stage') == 'engine' for row in events):
        failed = outcome.get('status') not in ('checked_plan', 'relaxed_plan', 'no_checked_plan')
        append_stage(call_dir, 'engine', 'failed' if failed else 'skipped',
                     mode='live' if failed else 'skipped', **dict(engine_stage, attempt=attempt),
                     notes='No engine-stage receipt was produced. ' + str(outcome.get('message') or 'The runner has finished.'))
    events = [row for row in outcome.get('events', []) if row.get('stage') == 'package']
    if events:
        start = next((row['t_s'] for row in events if row['kind'] == 'stage_start'), None)
        end = next((row['t_s'] for row in reversed(events) if row['kind'] == 'stage_end'), None)
        failed = any(row['kind'] == 'error' for row in events)
        append_stage(call_dir, 'package', status='failed' if failed else 'done' if outcome.get('package') else 'skipped',
                     started_utc=started('package'), attempt=attempt,
                     instance_id=f'runner-package-attempt-{attempt}' if attempt is not None else None,
                     wall_s=end-start if start is not None and end is not None else None,
                     output_sha256=_digest(outcome['package']) if outcome.get('package') else None,
                     notes=json.dumps([row.get('data', {}) for row in events], sort_keys=True))


def display_message(message, profile='standard'):
    """Keep app advice actionable while retaining the runner's raw evidence."""
    if not isinstance(message, str):
        return message
    advice = ('The 1800 s standard deadline is the longest in this app. '
              'Try a fresh call with a lower fill or fewer boxes.' if profile == 'standard' else
              'Start a fresh call with the 1800 s standard profile, or a lower fill or fewer boxes.')
    message = re.sub(r'Retry with a longer deadline or reduce the load\.?', advice, message, flags=re.I)
    message = re.sub(r'[^.!?]*(?:use|choose|supply|provide) (?:a |the )?(?:matching recording|recorded note|recorded answer|recording)[^.!?]*[.!?]?',
                     'Retry, or start a fresh call. The Functional app never uses recordings.', message, flags=re.I)
    return message


def _instruction_infeasibility(outcome, packet):
    """Project a scoped engine observation, never a global impossibility claim."""
    evidence = outcome.get('evidence') or {}
    confirmed = evidence.get('confirmation_source') == SOURCE or (evidence.get('confirmation') or {}).get('source') == SOURCE
    if (not packet.get('voyage_sentences') or not confirmed or outcome.get('passed')
            or outcome.get('status') in ('checked_plan', 'relaxed_plan')):
        return None
    stages = evidence.get('stages') or {}
    certified = stages.get('certified_bound') or {}
    reason = (certified.get('proof_work') or {}).get('stop_reason')
    target = (certified.get('proof_details') or {}).get('tested_integer_improvement_target')
    cost_cap = certified.get('cost_cap')
    bound_infeasible = certified.get('status') == 'infeasible' and target is None and cost_cap is None
    raw_masters = ((stages.get('starts') or {}).get('frontier') or {}).get('masters') or []
    masters = [row for row in raw_masters if isinstance(row, dict)] if isinstance(raw_masters, list) else []
    if not certified and not masters:
        return None
    return dict(source='runner_evidence', certified_cargo_infeasible=bound_infeasible, certified_bound=dict(
        status=certified.get('status'), stop_reason=reason,
        scope=str(certified.get('proof_scope'))[:1000] if certified.get('proof_scope') is not None else None,
        cost_cap=cost_cap, improvement_target=target) if certified else None,
        masters=[dict(deck_tiers=row.get('deck_tiers'), status=row.get('status')) for row in masters[:32]])


def _outcome_message(outcome, packet):
    profile = packet.get('profile', 'standard')
    message = display_message(outcome.get('message'), profile) or ''
    measured = _instruction_infeasibility(outcome, packet)
    if measured and measured['certified_cargo_infeasible']:
        explanation = "The engine's relaxation found no room for the cargo under this instruction"
        if explanation not in message:
            message = (message + ' ' + explanation + '.').strip()
    elif measured and measured['masters'] and all(row['status'] == 'INFEASIBLE' for row in measured['masters']):
        explanation = 'The engine reported INFEASIBLE for its tested master relaxations under this instruction.'
        if explanation not in message:
            message = (message + ' ' + explanation).strip()
    if profile == 'standard' and outcome.get('status') == 'no_checked_plan':
        advice = ('This run already used the standard profile; with a confirmed instruction, '
                  'try a looser one or plan with no instruction.')
        if advice not in message:
            message = (message + ' ' + advice).strip()
    return message


def _runner_refusal_kind(evidence):
    """Reuse the frozen pure metadata classifier without importing its engine."""
    classifier = getattr(_runner_refusal_kind, '_classifier', None)
    if classifier is None:
        module = sys.modules.get('experiments.mvp.pipeline.binding.review3.api')
        classifier = getattr(module, 'refusal_kind', None)
        if not callable(classifier):
            # Importing binding eagerly imports engine modules and alters
            # runtime cache settings. Compile only this unchanged pure helper.
            import ast
            source = Path(__file__).resolve().parents[1] / 'experiments/mvp/pipeline/binding/review3/api.py'
            try:
                tree = ast.parse(source.read_text())
                function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'refusal_kind')
                namespace = {}
                exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
                classifier = namespace['refusal_kind']
            except (OSError, SyntaxError, StopIteration, ValueError, TypeError):
                return None  # Missing policy evidence keeps the uncertainty label.
        _runner_refusal_kind._classifier = classifier
    return classifier(evidence)


def _plan_attempt(controls):
    attempt = controls.get('attempt', 1)
    if type(attempt) is not int or attempt < 1:
        raise ValueError('A plan attempt must be a positive integer.')
    return attempt


def _plan_directory(call_dir, attempt):
    """The server owns rotation; the runner always gets its explicit attempt."""
    call_dir = Path(call_dir).resolve()
    runs = call_dir / 'runs'
    directory = runs / str(attempt)
    if not runs.resolve().is_relative_to(call_dir) or not directory.resolve().is_relative_to(runs.resolve()):
        raise ValueError('The plan attempt directory must stay inside this call folder.')
    if any((directory / name).exists() for name in ('outcome.json', 'package', '.runner.lock')):
        raise ValueError('This plan attempt has already been started. Start a new plan attempt.')
    latest = call_dir / 'run'
    if latest.is_symlink():
        if latest.resolve() != directory.resolve():
            raise ValueError('The latest run link does not point at this plan attempt.')
    elif latest.exists():
        raise ValueError('The server must preserve the previous run before starting this plan attempt.')
    directory.mkdir(parents=True, exist_ok=True)
    if not latest.is_symlink():
        latest.symlink_to(Path('runs') / str(attempt), target_is_directory=True)
    return directory


def _plan_state(call_dir, state, message, attempt):
    write_json(Path(call_dir) / 'worker-state.json', dict(state=state, message=message, step='plan', attempt=attempt))


def outcome_view(call_dir, *, attempt=None):
    """Expose measured runner fields and package receipt coverage without rerunning checks."""
    call_dir = Path(call_dir)
    selected_attempt = attempt
    run_dir = call_dir / 'run' if attempt is None else call_dir / 'runs' / str(_plan_attempt({'attempt': attempt}))
    outcome = read_json(run_dir / 'outcome.json')
    if not outcome:
        return None
    state = read_json(call_dir / 'worker-state.json', {})
    metadata = read_json(run_dir / 'attempt.json', {}) or {}
    stopped_attempt = state.get('attempt')
    if metadata.get('status') == 'stopped' or (state.get('stopped') and state.get('step') in (None, 'plan') and (
            attempt is None or stopped_attempt == attempt or
            (stopped_attempt is None and (call_dir / 'run').resolve() == run_dir.resolve()))):
        return None  # A late runner result was not released after the planner's stop.
    receipt = outcome.get('receipt') or {}
    package = read_json(run_dir / 'package' / 'receipt.json') or {}
    coverage = package.get('checker') or receipt.get('checker') or {}
    evidence = outcome.get('evidence') or {}
    timing = outcome.get('timing') or {}
    fields = ('status', 'passed', 'level', 'label', 'message', 'broken_rules', 'boxes_ashore', 'lifts')
    result = {field: outcome.get(field) for field in fields}
    packet = outcome.get('packet') or read_json(run_dir / 'packet.json') or (
        read_json(call_dir / 'packet.json', {}) if attempt is None else dict(profile=metadata.get('profile', 'standard')))
    instruction_evidence = _instruction_infeasibility(outcome, packet)
    result['message'] = _outcome_message(outcome, packet)
    attempt = selected_attempt if selected_attempt is not None else (read_json(call_dir / 'worker-control.json', {}) or {}).get('attempt')
    if selected_attempt is None and (call_dir / 'run').is_symlink():
        name = (call_dir / 'run').resolve().name
        if name.isdigit():
            attempt = int(name)
    result.update(attempt=attempt, engine_instruction_infeasible=bool(instruction_evidence and instruction_evidence['certified_cargo_infeasible']),
                  engine_instruction_evidence=instruction_evidence)
    result.update(first_checked_wall_s=timing.get('first_checked_wall_s', outcome.get('first_checked_wall_s')),
                  wall_s=timing.get('wall_s', outcome.get('wall_s')),
                  active_rules=coverage.get('active_rules'), inactive_rules=coverage.get('inactive_rules'),
                  receipt_sha256=_digest(receipt) if receipt else None,
                  package_files=sorted(p.name for p in (run_dir / 'package').iterdir() if p.is_file())
                  if (run_dir / 'package').is_dir() else [])
    for field in ('level', 'broken_rules', 'boxes_ashore', 'lifts'):
        if result[field] is None:
            result[field] = evidence.get(field)
    if result['passed'] and result['status'] == 'checked_plan':
        result['label'] = 'Passes the full checker'
    elif result['status'] == 'relaxed_plan':
        result['label'] = {
            1: 'Relaxed plan: gives up preferences',
            2: 'Relaxed plan: gives up stowage practice',
            3: 'Relaxed plan: gives up bookings or leaves boxes ashore',
            4: "Breaks a class or statutory limit: needs a person's sign-off",
            5: 'Not safe to sail: shown only to say what would have to change',
        }.get(result['level'], 'Relaxed plan: see the broken rules')
        if result['boxes_ashore']:
            instance = outcome.get('instance') or {}
            total = len(instance.get('load_list') or [])
            partial = f'; partial, {100 * (total - len(result["boxes_ashore"])) / total:.1f} % aboard' if total else '; partial'
            result['label'] += partial
    suffix = "No checked plan found. That isn't a proof that none exists."
    result['message'] = (result['message'] or '').replace(suffix, '').strip()
    # A necessary full-load screen is a proof about loading every box under
    # this instruction. Preserve its scope rather than contradicting it with
    # the uncertainty label used for unfinished searches and timeout refusals.
    full_load_refusal = result['status'] == 'constraint_unavailable' and (
        evidence.get('refusal_kind') == 'proved_full_load' or
        (evidence.get('status') == 'constraint_unavailable' and _runner_refusal_kind(evidence) == 'proved_full_load'))
    if result['status'] == 'no_checked_plan' or (result['status'] == 'constraint_unavailable' and not full_load_refusal):
        result['message'] = (result['message'] + ' ' + suffix).strip()
    return result


def plan(call_dir, controls, *, engine_stage=None):
    from app.brief import _FixedAnswer, safe_evidence
    from app.v6 import _ConfirmedReader, _rule_digest, _sha256
    from experiments.mvp.pipeline.runner import run_packet
    case = read_json(call_dir / 'case.json', {})
    gate = _effective_gate(call_dir, read_json(call_dir / 'envelope.json') or
                           read_json(call_dir / 'runner-envelope.json') or case.get('envelope') or {})
    if not gate.get('in_envelope'):
        write_json(call_dir / 'envelope.json', gate)
        raise _OutsideEnvelope('This call is outside the app envelope; no engine run was started. '
                               + '; '.join(row['message'] for row in gate.get('reasons', [])))
    packet = _packet(call_dir, controls)
    parser = _EmptyBriefParser()
    confirmation = None
    if controls.get('instruction') == 'confirmed':
        confirmation = read_json(call_dir / 'confirmation.json') or {}
        readings = read_json(call_dir / 'readings.json', [])
        clean = next((row for row in readings if row.get('id') == confirmation.get('reading')), None)
        if (not clean or clean.get('status') != 'constraint_ready' or confirmation.get('source') != SOURCE
                or _sha256(clean['sentence']) != confirmation.get('sentence_sha256')
                or _rule_digest(clean['answer']) != confirmation.get('rule_sha256')):
            raise ValueError('The planner confirmation does not match this exact reading.')
        packet['voyage_sentences'] = [dict(text=clean['sentence'], origin=clean.get('origin', 'typed'))]
        context = read_json(call_dir / 'context.json')
        parser = _ConfirmedReader(_FixedAnswer(clean['sentence'], clean['answer'], safe_evidence(clean.get('evidence')), context.get('id')))
    elif controls.get('instruction') != 'none':
        raise ValueError('Choose a confirmed instruction or no instruction.')
    attempt = _plan_attempt(controls)
    directory = _plan_directory(call_dir, attempt)

    def confirm(row):
        return bool(confirmation and _sha256(row['sentence']) == confirmation['sentence_sha256']
                    and _rule_digest(row['parsed']) == confirmation['rule_sha256'])

    write_json(call_dir / 'packet.json', packet)
    write_json(directory / 'packet.json', packet)
    with _runtime_scope(call_dir), _admission_scope(call_dir):
        runner_started_utc = datetime.now(timezone.utc)
        outcome = run_packet(packet, parser=parser, out_dir=directory, confirm=confirm,
                             confirmation_source=SOURCE, engine_timeout_s=PROFILES[packet['profile']])
    # The runner normally writes this; preserve its returned error even if publication failed.
    if not (directory / 'outcome.json').exists():
        write_json(directory / 'outcome.json', outcome)
    _runner_stages(call_dir, outcome, runner_started_utc, engine_stage=engine_stage,
                   attempt=attempt, packet=packet, confirmation=confirmation)
    state = 'done' if outcome.get('status') in ('checked_plan', 'relaxed_plan', 'no_checked_plan') else 'failed'
    _plan_state(call_dir, state, _outcome_message(outcome, packet), attempt)
    return outcome


def _run_step(step, call_dir):
    call_dir = Path(call_dir).resolve()
    controls = read_json(call_dir / 'worker-control.json', {})
    stage = 'engine' if step == 'plan' else 'answers' if step == 'intake' and (controls.get('answers') or 'use_generator_records_for' in controls) else step
    began, started = time.monotonic(), utc_now()
    instance_id = f'{stage}-{uuid4().hex}'
    engine_stage = None
    if step == 'plan':
        engine_stage = _engine_stage_instance(call_dir, attempt=controls.get('attempt', 1))
        instance_id, started = engine_stage['instance_id'], engine_stage['started_utc']
    else:
        append_stage(call_dir, stage, 'running', mode='generated' if step == 'generate' else 'live',
                     instance_id=instance_id, started_utc=started,
                     where='this laptop' if stage == 'answers' else 'Meta Model API, cloud' if stage == 'intake' else 'software',
                     cost_usd=0. if stage == 'answers' else None)
    try:
        result = (plan(call_dir, controls, engine_stage=engine_stage) if step == 'plan' else
                  {'generate': generate, 'intake': intake, 'assemble': assemble}[step](call_dir, controls))
        if step != 'plan':
            skipped = isinstance(result, dict) and result.get('skipped')
            cost = 0. if stage == 'answers' else result.get('cost_usd') if isinstance(result, dict) else None
            notes = (result.get('origin_label') or result.get('why', '')) if isinstance(result, dict) else ''
            retrying = step == 'intake' and isinstance(result, dict) and bool(result.get('retry_bookings'))
            if step == 'intake' and stage != 'answers':
                cost = 0.
                if not skipped:
                    notes = (f'Aggregate intake; cumulative cost ${float(result.get("cost_usd", 0) or 0):.8f} '
                             'is itemized in the booking stages. This aggregate adds no charge.')
                if retrying:
                    notes = result['message'] + ' ' + notes
            append_stage(call_dir, stage, 'skipped' if skipped else 'incomplete' if retrying else 'done',
                         instance_id=instance_id,
                         mode='skipped' if skipped else 'generated' if step == 'generate' else 'live',
                         who='Generated load list' if skipped else 'Muse Spark' if stage == 'intake' else WHO.get(stage, stage),
                         where='this laptop' if stage == 'answers' else 'Meta Model API, cloud' if stage == 'intake' and not skipped else 'software',
                         model=None if skipped or stage == 'answers' else result.get('model') if isinstance(result, dict) else None,
                         wall_s=time.monotonic()-began, started_utc=started,
                         cost_usd=cost, output_sha256=_digest(result), notes=notes)
        return 0
    except Exception as error:
        traceback.print_exc()
        # Exception bodies can contain provider details; only availability reasons are public.
        unavailable, message = _public_error(error, step)
        message = _redact(message)
        accounting = intake_accounting(call_dir) if step == 'intake' and stage != 'answers' else {}
        if accounting.get('message') and (hasattr(error, 'cap_stop') or str(error).startswith(NO_INTAKE_HOLD)):
            message = accounting['message']
        cost_fields = {}
        if stage == 'intake':
            cost_fields = dict(cost_scope='cumulative_booking_total',
                               cumulative_cost_usd=accounting.get('settled_cost_usd', 0.),
                               incremental_cost_usd=accounting.get('incremental_cost_usd', 0.),
                               elapsed_s=accounting.get('elapsed_s'), booking_wall_s=accounting.get('booking_wall_s'),
                               spend_wait_s=accounting.get('spend_wait_s'))
        append_stage(call_dir, 'engine' if step == 'plan' else stage,
                     'unavailable' if unavailable else 'failed', mode='unavailable' if unavailable else 'live',
                     instance_id=instance_id,
                     attempt=controls.get('attempt', 1) if step == 'plan' else None,
                     cost_usd=0. if stage == 'answers' else accounting.get('settled_cost_usd', 0.) if stage == 'intake' else None,
                     who=WHO.get(stage, stage), where='Meta Model API, cloud' if stage == 'intake' else 'this laptop' if stage == 'answers' else 'software',
                     wall_s=time.monotonic()-began, started_utc=started,
                     notes=message + (' This is the cumulative settled booking spend, already itemized in the booking stages; this row adds no charge.' if stage == 'intake' else ''),
                     **cost_fields)
        if step == 'plan':
            _plan_state(call_dir, 'out_of_envelope' if isinstance(error, _OutsideEnvelope) else 'failed', message, controls.get('attempt', 1))
        else:
            _state(call_dir, 'out_of_envelope' if isinstance(error, _OutsideEnvelope) else 'failed', message, step)
        return 1


def _scrub_call_artifacts(call_dir):
    """Catch dependency-written output too; compromised receipts are not released."""
    key = os.environ.get('MODEL_API_KEY')
    if not key:
        return False
    root = Path(call_dir).resolve()
    changed = False
    for path in root.rglob('*'):
        if (not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root)
                or (path.name.startswith('worker-') and path.suffix == '.log')):
            continue
        data = path.read_bytes()
        safe = data.replace(key.encode(), b'[REDACTED API KEY]')
        if path.suffix in ('.json', '.jsonl'):
            try:
                if path.suffix == '.json':
                    value = json.loads(data)
                    cleaned = _redact(value)
                    if cleaned != value:
                        safe = (json.dumps(cleaned, ensure_ascii=False, allow_nan=False) + '\n').encode()
                else:
                    rows = [json.loads(line) for line in data.splitlines() if line.strip()]
                    cleaned = _redact(rows)
                    if cleaned != rows:
                        safe = ''.join(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n' for row in cleaned).encode()
            except (ValueError, TypeError, UnicodeError):
                pass
        if safe != data:
            _atomic(path, safe)
            changed = True
    return changed


def run_step(step, call_dir):
    with _redacted_output():
        result = _run_step(step, call_dir)
        if _scrub_call_artifacts(call_dir):
            message = 'A dependency returned credential text; the call stopped and sensitive output was removed.'
            outcome_path = Path(call_dir) / 'run' / 'outcome.json'
            outcome = read_json(outcome_path)
            if isinstance(outcome, dict):
                outcome.update(status='error', passed=False, plan=None, receipt=None, package=None, message=message)
                write_json(outcome_path, outcome)
            controls = read_json(Path(call_dir) / 'worker-control.json', {}) if step == 'plan' else {}
            attempt = controls.get('attempt', 1) if step == 'plan' else None
            append_stage(call_dir, 'engine' if step == 'plan' else step, 'failed', notes=message, attempt=attempt)
            if step == 'plan':
                _plan_state(call_dir, 'failed', message, attempt)
            else:
                _state(call_dir, 'failed', message, step)
            return 1
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('step', choices=['generate', 'intake', 'assemble', 'plan'])
    parser.add_argument('call_dir', type=Path)
    args = parser.parse_args(argv)
    return run_step(args.step, args.call_dir)


if __name__ == '__main__':
    with _redacted_output():
        raise SystemExit(main())
