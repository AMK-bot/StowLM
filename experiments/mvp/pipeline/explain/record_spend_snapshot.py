"""Record portable, exact receipts for this feature's measured Spark allocation."""
from __future__ import annotations

import argparse
from copy import deepcopy
from decimal import Decimal
import fcntl
import json
from pathlib import Path

from experiments.mvp.pipeline.contract.schema import sha256_json
from llm.spark.spend import Usage, calculate_cost
from .cache import _write

SCHEMA = 'stowlm.explain-spend-snapshot/1'
HERE = Path(__file__).resolve().parent
SNAPSHOT = HERE / 'fixtures/spend-snapshot.json'
STATE = HERE / 'budget/.spend.json'
LEDGER = HERE.parents[3] / 'experiments/spark-spend/calls.jsonl'
_FIELDS = ('purpose', 'status', 'cost_usd', 'request_id', 'usage')
_USAGE = ('input_tokens', 'output_tokens', 'cached_tokens')


def _amount(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError('Invalid measured spending amount')
    return amount


def validate_state(state, settlements):
    """Validate unique measured calls against receipts, then strip raw prose."""
    receipts = {}
    for row in settlements:
        rid = row.get('request_id')
        if not rid or rid in receipts:
            raise ValueError('Spend receipts require unique request ids')
        receipts[rid] = row
    calls, seen, total = [], set(), Decimal(0)
    for row in state['calls']:
        if row.get('status') not in {'measured', 'measured_incomplete'}:
            raise ValueError('Unknown billing or an active hold cannot be snapshotted')
        if any(key not in row for key in _FIELDS):
            raise ValueError('Measured call lacks receipt metadata')
        rid = row['request_id']
        if not isinstance(rid, str) or not rid or rid in seen or rid not in receipts:
            raise ValueError('Measured calls require unique matched request ids')
        seen.add(rid)
        receipt = receipts[rid]
        usage = row['usage']
        if not isinstance(usage, dict) or set(usage) != set(_USAGE):
            raise ValueError('Measured call usage is incomplete')
        cost = _amount(row['cost_usd'])
        if (receipt.get('phase') != 'settlement' or receipt.get('cost_kind') != 'measured'
                or receipt.get('purpose') != row['purpose']
                or _amount(receipt['cost_usd']) != cost
                or any(receipt.get(key) != usage[key] for key in _USAGE)
                or calculate_cost(Usage(**usage)) != cost):
            raise ValueError('Measured call differs from shared settlement cost or usage')
        total += cost
        calls.append({key: deepcopy(row[key]) for key in _FIELDS})
    if seen != set(receipts):
        raise ValueError('Snapshot contains unrelated settlement receipts')
    if total != _amount(state['used_usd']) or total > Decimal(5):
        raise ValueError('Measured total differs from state or exceeds the $5 allocation')
    return dict(used_usd=state['used_usd'], calls=calls)


def build_snapshot(state, ledger_rows):
    ids = {call.get('request_id') for call in state['calls']} - {None}
    settlements = [deepcopy(row) for row in ledger_rows
                   if row.get('phase') == 'settlement' and row.get('request_id') in ids]
    stripped = validate_state(state, settlements)
    result = dict(schema=SCHEMA, source_state_sha256=sha256_json(state),
                  ledger_snapshot_sha256=sha256_json(settlements), state=stripped,
                  settlements=settlements, task_cap_usd=5)
    result['sha256'] = sha256_json(result)
    return result


def validate_snapshot(value):
    value = deepcopy(value)
    digest = value.pop('sha256', None)
    if digest != sha256_json(value) or value.get('schema') != SCHEMA or value.get('task_cap_usd') != 5:
        raise ValueError('Spend snapshot integrity/schema mismatch')
    source = value.get('source_state_sha256')
    if not isinstance(source, str) or len(source) != 64 or any(c not in '0123456789abcdef' for c in source):
        raise ValueError('Spend snapshot source-state hash is malformed')
    if value.get('ledger_snapshot_sha256') != sha256_json(value['settlements']):
        raise ValueError('Spend snapshot settlement digest mismatch')
    stripped = validate_state(value['state'], value['settlements'])
    if stripped != value['state']:
        raise ValueError('Spend snapshot contains unstripped source state')
    return stripped, value['settlements']


def record(out_path=SNAPSHOT, *, state_path=STATE, ledger_path=LEDGER):
    """Call after paid work ends; refuse an active allocation lock or hold."""
    state_path, out_path = Path(state_path), Path(out_path)
    with state_path.with_suffix('.lock').open('a') as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Paid work is active; no snapshot recorded') from error
        try:
            state = json.loads(state_path.read_text())
            rows = [json.loads(line) for line in Path(ledger_path).read_text().splitlines()]
            value = build_snapshot(state, rows)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            _write(out_path, value)
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    return value


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=SNAPSHOT)
    parser.add_argument('--state', type=Path, default=STATE)
    parser.add_argument('--ledger', type=Path, default=LEDGER)
    args = parser.parse_args()
    value = record(args.out, state_path=args.state, ledger_path=args.ledger)
    print(json.dumps(dict(schema=value['schema'], measured_cost_usd=value['state']['used_usd'],
                          paid_calls=len(value['state']['calls']), sha256=value['sha256']), sort_keys=True))
