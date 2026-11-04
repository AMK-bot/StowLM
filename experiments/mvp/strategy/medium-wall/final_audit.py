"""Check completed medium-wall evidence without re-solving or held-out data."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    checked = {}
    for folder, marker, key in (
        ('capacity', 'completion.json', 'artifact_hashes'),
        ('search', 'final.json', 'artifacts'),
    ):
        base = HERE / folder
        completion = read(base / marker)
        for relative, expected in completion[key].items():
            path = base / relative
            assert sha(path) == expected, str(path)
            checked[str(path.relative_to(HERE))] = expected
    capacity = read(HERE / 'capacity/verification.json')
    assert capacity['status'] == 'pass'
    assert capacity['input_maps'] == capacity['integer_geometry_witnesses'] == 12
    assert capacity['fractional_lp_witnesses'] == 42
    assert capacity['strict_mask_certificates'] == 4
    assert capacity['cp_pending'] == capacity['cp_witnesses'] == 0
    search = read(HERE / 'search/summary.json')
    assert search['completed'] and len(search['rows']) == 24
    assert Counter(row['status'] for row in search['rows']) == {
        'dead_end': 9, 'timeout': 15}
    assert not any(row['solver_complete'] or row['checker_certified']
                   for row in search['rows'])
    budgets = read(HERE / 'search/final.json')
    assert all(x < 3600 for x in budgets['actual_search_seconds'].values())
    assert all(x < 3600 for x in budgets['search_plus_cp_seconds_on_121_maps'].values())
    roof = read(HERE / 'capacity/visibility-roof.json')
    assert not any(roof[k] for k in (
        'cross_checker_pose_equivalence_proved',
        'full_checker_infeasibility_certified', 'all_ballast_failure_certified'))
    fill = read(HERE / 'search/fill-verification.json')
    assert fill['passed'] and fill['train_inputs'] == 30
    assert fill['fullchecker_certificates'] == fill['plan_occupancy_checks'] == 29
    for name in ('FILL.md', 'fill-evidence.json', 'fill_audit.py'):
        path = HERE / 'search' / name
        assert sha(path) == fill[name + '_sha256']
        checked[str(path.relative_to(HERE))] = sha(path)
    tasks = read(HERE / 'finisher/tasks.json')
    finishers = []
    for task in tasks:
        base = HERE / 'finisher/runs' / task['name']
        result = read(base / 'complete.json')
        assert result['task'] == task and result['num_workers'] == 1
        assert result['status'] == 'not_found'
        assert result['diagnostics']['full_checker_calls'] == 0
        assert task['budget_s'] == 3600
        representative = read(base / 'representative-summary.json')
        assert not representative['checker']['passed']
        for name in ('complete.json', 'outcome.json', 'representative-plan.json',
                     'representative-full-report.json', 'representative-summary.json'):
            path = base / name
            checked[str(path.relative_to(HERE))] = sha(path)
        finishers.append({
            'map_id': task['map_id'], 'status': result['status'],
            'elapsed_s': result['elapsed_s'],
            'independent_full_checker_passed': representative['checker']['passed']})
    completion = read(HERE / 'finisher/FINAL_COMPLETION.json')
    assert completion['complete'] and completion['verification_passed']
    assert completion['outstanding_numeric_workers'] == 0
    for name, key in (('FINISHER.md', 'report_sha256'),
                      ('summary.json', 'summary_sha256'),
                      ('verification.json', 'verification_sha256'),
                      ('postcheck-completion.json', 'postcheck_completion_sha256')):
        path = HERE / 'finisher' / name
        assert sha(path) == completion[key], str(path)
        checked[str(path.relative_to(HERE))] = sha(path)
    output = {
        'passed': True, 'verified_utc': datetime.now(timezone.utc).isoformat(),
        'primary_capacity_witnesses': {'geometry': 12, 'fractional': 42},
        'strict_mask_certificates_only': 4, 'planner_outcomes': 24,
        'additional_train_small_fill_audit': {'maps': 30, 'existing_passes': 29},
        'four_cumulative_planning_budgets_under_3600s': True,
        'conditional_roof_certificate_only': True, 'finisher_runs': finishers,
        'full_checker_certified_new_plans': 0, 'checked_artifact_hashes': checked,
        'scope': 'Frozen current-epoch train inputs only; no source changes or solver calls in this audit.'}
    (HERE / 'final-audit.json').write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps({k: v for k, v in output.items() if k != 'checked_artifact_hashes'}))


if __name__ == '__main__':
    main()
