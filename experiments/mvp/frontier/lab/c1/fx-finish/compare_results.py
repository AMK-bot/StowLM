"""Compare identical reference stows under native and experimental finishers."""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

HERE = Path(__file__).parent


def rows(directory):
    result = {}
    for path in directory.glob('*.json'):
        row = json.loads(path.read_text())
        if row.get('instance_id') and row.get('complete'):
            result[row['instance_id']] = row
    return result


def compare():
    baseline = rows(HERE/'results/quick-v4')
    candidate = rows(HERE/'results/quick-fast')
    result = dict(native_maps=len(baseline), candidate_maps=len(candidate), by_size={})
    for size in ('tiny', 'small', 'medium'):
        selected = [identifier for identifier, row in baseline.items() if row.get('size') == size]
        measured = [identifier for identifier in selected if identifier in candidate]
        paired = [identifier for identifier in measured
                  if baseline[identifier]['passed'] and candidate[identifier]['passed']]
        lost = [identifier for identifier in measured
                if baseline[identifier]['passed'] and not candidate[identifier]['passed']]
        gained = [identifier for identifier in measured
                  if not baseline[identifier]['passed'] and candidate[identifier]['passed']]
        bindings = [identifier for identifier in measured if
                    baseline[identifier]['input_sha256'] != candidate[identifier]['input_sha256'] or
                    baseline[identifier]['config']['method'] != candidate[identifier]['config']['method']]
        runner_changes = [identifier for identifier in measured if
                    baseline[identifier]['config']['source_sha256'] != candidate[identifier]['config']['source_sha256']]
        actions = [identifier for identifier in paired if
                   baseline[identifier].get('actions') != candidate[identifier].get('actions')]
        result['by_size'][size] = dict(maps=len(selected), measured=len(measured),
            native_passes=sum(baseline[i]['passed'] for i in measured),
            candidate_passes=sum(candidate[i]['passed'] for i in measured),
            mutual_passes=len(paired), gained=gained, lost=lost,
            binding_mismatches=bindings, action_mismatches=actions,
            runner_aggregate_hash_changes=runner_changes,
            native_mean_lifts=mean(baseline[i]['lifts'] for i in paired) if paired else None,
            candidate_mean_lifts=mean(candidate[i]['lifts'] for i in paired) if paired else None,
            native_mean_wall_s=mean(baseline[i]['wall_s'] for i in measured) if measured else None,
            candidate_mean_wall_s=mean(candidate[i]['wall_s'] for i in measured) if measured else None,
            lift_changes={i: candidate[i]['lifts']-baseline[i]['lifts'] for i in paired})
    return result


if __name__ == '__main__':
    result = compare()
    target = HERE/'results/paired-quick.json'
    target.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
