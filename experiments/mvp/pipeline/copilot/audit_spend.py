"""Copy this task's authoritative shared-ledger records, including failed calls."""
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path

from .live import PURPOSE, CAP


def audit():
    from llm.spark.spend import SpendLedger
    ledger = SpendLedger()
    with ledger.locked():
        records = [row for row in ledger._records() if str(row.get('purpose', '')).startswith(PURPOSE)]
        actual, held = ledger._totals(records)
    phases = defaultdict(list)
    for row in records:
        suffix = row['purpose'][len(PURPOSE) + 1:]
        phase = suffix.split('/')[0] if suffix.startswith(('tuned-v', 'fresh-v', 'agency-v', 'native-recovery', 'hero-final/', 'shot10/')) else 'original-work'
        phases[phase].append(row)
    value = dict(purpose=PURPOSE, measured_cost_usd=str(actual), held_usd=str(held), cap_usd=str(CAP),
                 completed_calls=sum(r.get('status') == 'completed' for r in records),
                 accounting='Canonical managed shared ledger; all task-prefixed original, tuning and fresh attempts, including incomplete responses and holds.',
                 created_utc=datetime.now(timezone.utc).isoformat(),
                 by_phase={name: dict(measured_cost_usd=str(ledger._totals(rows)[0]),
                                      held_usd=str(ledger._totals(rows)[1])) for name, rows in phases.items()},
                 records=records)
    (Path(__file__).parent / 'spend.json').write_text(json.dumps(value, indent=2) + '\n')
    return {key: val for key, val in value.items() if key != 'records'}


if __name__ == '__main__':
    print(json.dumps(audit(), indent=2))
