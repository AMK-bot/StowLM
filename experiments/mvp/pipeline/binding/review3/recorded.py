"""Exact archived Spark readings -> authenticated approval -> the current engine.

These public historical validation/development inputs are not the fresh32 maps.
Reader outputs, original latencies and request IDs are recorded; only replay,
confirmation and the new engine execution are timed now. No oracle or live model
is used. The hatch case explicitly reuses a recorded standing interpretation as
the identical sentence in a newly host-confirmed voyage instruction.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict, is_dataclass
import hashlib
import json
import os
from pathlib import Path
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
FIXTURE = HERE / 'fixtures/recorded-reader-cases.json'
KINDS = ('close_bay', 'max_tiers', 'dg_keep_away', 'one_port_per_hatch')
CONFIRMATION_SOURCE = 'review3_recorded_reader_explicit_batch'


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _source(relative, digest):
    path = (ROOT / relative).resolve()
    if not path.is_relative_to(ROOT) or any('sealed' in part.lower() or part == 'testv3'
                                           for part in path.relative_to(ROOT).parts):
        raise ValueError('recorded reader execution accepts public nonsealed source paths only')
    if _hash(path) != digest:
        raise ValueError(f'recorded source hash changed: {relative}')
    return path


def cases():
    """Frozen public four-case queue; no planner outcome influences its order."""
    data = json.loads(FIXTURE.read_text())
    rows = data['cases']
    if tuple(row['case_id'] for row in rows) != KINDS:
        raise ValueError('recorded reader queue must contain exactly the four fixed rule kinds')
    return deepcopy(rows)


def next_case(completed=()):
    """Return the first not-completed queue entry, or None after all four."""
    completed = set(completed)
    return next((row for row in cases() if row['case_id'] not in completed), None)


def load_case(case_id):
    """Verify raw provider output and exact original input/context provenance."""
    from experiments.mvp.reader.schema import answer_envelope, context_from_instance, validate_answer
    from stowage.io import load_instance
    case = next((row for row in cases() if row['case_id'] == case_id), None)
    if case is None:
        raise ValueError('unknown recorded reader case')
    input_path = _source(case['input'], case['input_sha256'])
    recording_path = _source(case['recording'], case['recording_sha256'])
    cohort_path = _source(case['cohort'], case['cohort_sha256'])
    recording = json.loads(recording_path.read_text())
    cohort = json.loads(cohort_path.read_text())
    source_case = next(row for row in cohort['cases'] if row['case_id'] == case['cohort_case_id'])
    instance = load_instance(input_path)
    context = context_from_instance(instance)
    if source_case['input'] != case['input'] or source_case['context'] != case['context'] or context != case['context']:
        raise ValueError('recorded reader input/context differs from the original request')
    for row in case['readings']:
        original = recording['brief']['sentences'][row['source_row_index']]
        if (original['sentence'] != row['sentence'] or original['role'] != row['source_role']
                or original['parser'] != row['provider'] or original['parser']['raw'] != row['raw']
                or original['parsed'] != row['parsed']):
            raise ValueError('recorded response differs from the pinned provider output')
        if not row['provider'].get('request_id') or row['provider'].get('parser') != 'SparkParser':
            raise ValueError('genuine recorded Spark request provenance is required')
        if answer_envelope(validate_answer(row['raw'], context)) != row['parsed']:
            raise ValueError('recorded raw output does not validate to the recorded reading')
    return instance, case


class RecordedParser:
    """Replay only exact source sentences on their original physical context."""
    def __init__(self, case):
        self.case = deepcopy(case)
        self.calls = []

    def parse(self, sentence, context):
        from experiments.mvp.reader.schema import validate_answer
        if context != self.case['context']:
            raise ValueError('recorded parser context mismatch; no transfer to another map')
        row = next((row for row in self.case['readings'] if row['sentence'] == sentence), None)
        if row is None:
            raise ValueError('sentence has no exact archived reader response')
        self.calls.append(dict(sentence=sentence, source_row_index=row['source_row_index'],
                               request_id=row['provider']['request_id']))
        provider = dict(row['provider'], recorded=True, replay_parser=type(self).__name__,
            source=self.case['recording'], source_sha256=self.case['recording_sha256'],
            context_bound=True, original_reader_wall_s=row['recorded_reader_wall_s'],
            mode='recorded_reader', historical_public_development=True)
        return validate_answer(row['raw'], context), provider


def execute_case(case_id, *, executor=None, on_incumbent=None, **planning_kwargs):
    """One current execute_brief invocation; no stored plan is supplied to search."""
    from experiments.mvp.reader import public_brief
    from experiments.mvp.pipeline.binding import confirm_brief
    if executor is None:
        from .api import execute_brief
        executor = execute_brief
    began = time.perf_counter()
    instance, case = load_case(case_id)
    parser = RecordedParser(case)
    standing = next(row['sentence'] for row in case['readings'] if row['role'] == 'standing')
    voyage = tuple(row['sentence'] for row in case['readings'] if row['role'] == 'voyage')
    read_began = time.perf_counter()
    receipt = public_brief.read_brief(instance, public_brief.VoyageBrief(standing, voyage), parser=parser)
    replay_wall_s = time.perf_counter()-read_began
    confirmed = confirm_brief(receipt, CONFIRMATION_SOURCE)
    first_checked = None
    checked_events = []
    def incumbent(event):
        nonlocal first_checked
        if not isinstance(event, dict):
            return
        if event.get('passed') is True and first_checked is None:
            first_checked = time.perf_counter()-began
        checked_events.append(deepcopy(event))
        if on_incumbent is not None:
            on_incumbent(event)
    engine_began = time.perf_counter()
    result, evidence = executor(instance, confirmed, on_incumbent=incumbent, **planning_kwargs)
    engine_wall_s = time.perf_counter()-engine_began
    evidence = dict(evidence)
    evidence['recorded_reader_execution'] = dict(case_id=case_id, rule_type=case['rule_type'],
        mode='recorded_reader_live_engine', scope=case['scope'], selection=case['selection'],
        input=case['input'], input_sha256=case['input_sha256'],
        fixture_sha256=_hash(FIXTURE), recording=case['recording'],
        recording_sha256=case['recording_sha256'], cohort=case['cohort'],
        cohort_sha256=case['cohort_sha256'], context_bound=True,
        source_role_transfer=case['role_transfer'],
        role_transfer_note=('Identical recorded standing sentence/output reused as a newly confirmed voyage rule; '
                            'the model prompt did not encode the role.' if case['role_transfer'] else None),
        confirmation_source=CONFIRMATION_SOURCE, reading=receipt.evidence(),
        parser_calls=parser.calls, replay_wall_s=replay_wall_s,
        original_reader_wall_s=[row['recorded_reader_wall_s'] for row in case['readings']],
        unique_original_request_ids=sorted({row['provider']['request_id'] for row in case['readings']}),
        original_response_reused=case['role_transfer'],
        original_reader_latency_is_recorded=True, engine_wall_s=engine_wall_s,
        requested_options={key: asdict(value) if is_dataclass(value) else value
                           for key, value in planning_kwargs.items()},
        first_checked_wall_s=first_checked, checked_incumbents=len(checked_events),
        wall_s=time.perf_counter()-began, fresh_model_call=False, reader_accuracy_measurement=False)
    return result, evidence


def run_case(case_id, output, *, executor=None, **planning_kwargs):
    """Persist one queued execution after the live engine finishes its audits."""
    output = Path(output).resolve()
    if not output.is_relative_to(HERE):
        raise ValueError('recorded reader output must stay inside binding/review3/')
    if output.exists():
        raise FileExistsError('recorded reader execution already recorded; use a fresh output path')
    result, evidence = execute_case(case_id, executor=executor, **planning_kwargs)
    row = dict(schema_version='stowlm.recorded-reader-result/1', case_id=case_id,
        mode='recorded_reader_live_engine', finished_at=time.time(), worker_pid=os.getpid(),
        status=evidence.get('status'), passed=evidence.get('passed') is True,
        evidence=evidence, plan=(result.plan.model_dump(mode='json')
             if result is not None and getattr(result, 'plan', None) is not None else None))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f'.{output.name}.{os.getpid()}.tmp')
    try:
        temporary.write_text(json.dumps(row, sort_keys=True, indent=2)+'\n')
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', required=True, choices=KINDS)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--budget-s', type=float, default=90.)
    parser.add_argument('--finalize-s', type=float, default=30.)
    args = parser.parse_args()
    row = run_case(args.case, args.output, budget_s=args.budget_s, finalize_s=args.finalize_s)
    print(json.dumps({key: row[key] for key in ('case_id', 'status', 'passed')}))


if __name__ == '__main__':
    main()
