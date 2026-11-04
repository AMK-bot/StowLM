"""Recover missing incomplete-call metadata from unique ordered ledger matches.

Run only after QA stops. Defaults to a read-only dry run; --apply preserves exact
original bytes under their content hash before updating either spending state.
No shared ledger is modified, and no model or network call is made.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from decimal import Decimal
import fcntl
from functools import lru_cache
import hashlib
import json
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import sha256_json
from llm.spark.spend import Usage, calculate_cost
from .cache import _write

HERE = Path(__file__).resolve().parent
DEFAULTS = (HERE / 'budget/.spend.json', HERE / 'fixtures/development/current-cache/.spend.json')
LEDGER = HERE.parents[3] / 'experiments/spark-spend/calls.jsonl'
SEED = HERE / 'fixtures/development/retired-cache/.spend.json'
_USAGE_KEYS = ('input_tokens', 'output_tokens', 'cached_tokens')


def _amount(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError('Invalid measured amount')
    return amount


def _matches(call, settlement):
    return (settlement.get('cost_kind') == 'measured'
            and call['purpose'] == settlement.get('purpose')
            and _amount(call['cost_usd']) == _amount(settlement['cost_usd']))


def ordered_matches(calls, settlements, *, seed_line, used_ids=()):
    """Return a unique increasing assignment; ambiguity fails closed.

    Settlements carry their zero-based JSONL line in ``_line``. Known request
    ids are anchors. Missing ids cannot consume an id already present in either
    state, and every candidate must occur after the final seeded old call.
    """
    by_id = {}
    for row in settlements:
        rid = row.get('request_id')
        if not rid:
            continue
        if rid in by_id:
            raise ValueError('Repeated settlement request id')
        by_id[rid] = row
    sequence = []
    for index, call in enumerate(calls):
        if call['status'] not in {'measured', 'measured_incomplete'}:
            raise ValueError('QA must be finished; held or unknown billing cannot be reconciled')
        rid = call.get('request_id')
        if rid:
            if rid not in by_id or not _matches(call, by_id[rid]):
                raise ValueError('Known request id does not match its measured settlement')
            if by_id[rid]['_line'] <= seed_line:
                continue
        elif call['status'] != 'measured_incomplete':
            raise ValueError('Only incomplete measured calls may recover a missing id')
        sequence.append((index, call))
    used_ids = set(used_ids)
    options = []
    for _, call in sequence:
        rid = call.get('request_id')
        candidates = ([by_id[rid]] if rid else
                      [row for row in settlements if row.get('request_id') and row['_line'] > seed_line
                       and row['request_id'] not in used_ids and _matches(call, row)])
        options.append(sorted(candidates, key=lambda row: row['_line']))

    @lru_cache(None)
    def solve(index, previous_line):
        if index == len(options):
            return ((),)
        found = []
        for row in options[index]:
            if row['_line'] <= previous_line:
                continue
            for suffix in solve(index + 1, row['_line']):
                found.append((row['request_id'], *suffix))
                if len(found) == 2:
                    return tuple(found)
        return tuple(found)

    solutions = solve(0, seed_line)
    if len(solutions) != 1:
        raise ValueError('Incomplete-call settlement assignment is absent or chronologically ambiguous')
    return {index: by_id[rid] for (index, _), rid in zip(sequence, solutions[0])}


@contextmanager
def _idle_lock(path):
    with path.with_suffix('.lock').open('a') as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('QA is active; reconciliation refuses to wait or mutate') from error
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def reconcile(paths=None, *, ledger_path=LEDGER, seed_path=SEED, apply=False):
    paths = tuple(Path(path).resolve() for path in (paths or DEFAULTS))
    if len(paths) != len(set(paths)) or not paths:
        raise ValueError('Provide distinct spending state paths')
    if any(not path.is_relative_to(HERE) for path in paths):
        raise ValueError('Reconciliation writes stay inside explain')
    with ExitStack() as locks:
        for path in sorted(paths):
            locks.enter_context(_idle_lock(path))
        originals = {path: path.read_bytes() for path in paths}
        states = {path: json.loads(data) for path, data in originals.items()}
        settlements = [dict(row, _line=line) for line, text in enumerate(Path(ledger_path).read_text().splitlines())
                       if (row := json.loads(text)).get('phase') == 'settlement']
        by_id = {row['request_id']: row for row in settlements if row.get('request_id')}
        seed = json.loads(Path(seed_path).read_text())
        seed_ids = [call['request_id'] for call in seed['calls']]
        if len(seed_ids) != len(set(seed_ids)) or any(rid not in by_id for rid in seed_ids):
            raise ValueError('Seeded old accounting is not bound to unique shared settlements')
        seed_line = max(by_id[rid]['_line'] for rid in seed_ids)
        used_ids = {call['request_id'] for state in states.values() for call in state['calls']
                    if call.get('request_id')}
        plans, common = {}, {}
        for path, state in states.items():
            if any(call['status'] == 'held' for call in state['calls']):
                raise ValueError('QA is active or billing is unknown; no state changed')
            total = sum((_amount(call['cost_usd']) for call in state['calls']), Decimal(0))
            if total != _amount(state['used_usd']) or total > 5:
                raise ValueError('Measured state total is inconsistent or exceeds $5')
            assignments = ordered_matches(state['calls'], settlements, seed_line=seed_line, used_ids=used_ids)
            updated, changed = deepcopy(state), []
            for index, ledger_row in assignments.items():
                original = state['calls'][index]
                usage = {key: ledger_row[key] for key in _USAGE_KEYS}
                if calculate_cost(Usage(**usage)) != _amount(ledger_row['cost_usd']):
                    raise ValueError('Shared settlement usage does not reproduce measured cost')
                if original.get('request_id') and original['request_id'] != ledger_row['request_id']:
                    raise ValueError('Reconciliation cannot replace an existing request id')
                if original.get('usage') and original['usage'] != usage:
                    raise ValueError('Reconciliation cannot replace existing usage')
                signature = (original.get('prompt_sha256'), original['purpose'], original['cost_usd'])
                # Shared global/local rows for the same prompt must resolve identically.
                if signature in common and common[signature] != ledger_row['request_id']:
                    raise ValueError('Global/local mappings disagree or repeated prompts are ambiguous')
                common[signature] = ledger_row['request_id']
                if original.get('request_id') and original.get('usage'):
                    continue
                row = updated['calls'][index]
                row.update(request_id=ledger_row['request_id'], usage=usage,
                    ledger_reconciliation=dict(basis='unique chronological purpose + exact Decimal cost; known-id anchors',
                        ledger_line=ledger_row['_line'] + 1, ledger_request_id=ledger_row['request_id'],
                        seed_last_request_id=by_id[max(seed_ids, key=lambda rid: by_id[rid]['_line'])]['request_id'],
                        original_row_sha256=sha256_json(original)))
                changed.append(index)
            before_hash = hashlib.sha256(originals[path]).hexdigest()
            backup = path.with_name(path.stem + '.before-reconcile-' + before_hash + '.json')
            if changed:
                updated.setdefault('reconciliations', []).append(dict(original_bytes_sha256=before_hash,
                    backup=backup.name, changed_call_indices=changed, ledger_path=str(Path(ledger_path)),
                    basis='unique ordered shared-ledger settlement mapping; no amount or status changed'))
            plans[path] = (updated, backup, changed)
        # Validate the whole batch before preserving originals or changing states.
        if apply:
            for path, (updated, backup, changed) in plans.items():
                if not changed:
                    continue
                if backup.exists() and backup.read_bytes() != originals[path]:
                    raise ValueError('Existing backup differs from its named original bytes')
                if not backup.exists():
                    backup.write_bytes(originals[path])
                _write(path, updated)
        return dict(applied=apply, paid_calls=0, changed_calls={str(path.relative_to(HERE)): len(plan[2])
                    for path, plan in plans.items()}, totals_usd={str(path.relative_to(HERE)): state['used_usd']
                    for path, state in states.items()}, basis='unique chronological ledger assignment; no unknown holds')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Write only after QA has stopped and no holds remain')
    args = parser.parse_args()
    print(json.dumps(reconcile(apply=args.apply), sort_keys=True))
