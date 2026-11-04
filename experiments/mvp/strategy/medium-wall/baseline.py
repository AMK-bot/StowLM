"""Read only D191's frozen TRAIN artifacts; no planner or held-out access."""
from collections import Counter
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent
BLOCK = HERE.parent / 'blockrule'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    manifest_path = BLOCK / 'cohort-medium.json'
    manifest = json.loads(manifest_path.read_text())
    assert manifest['split'] == 'train'
    rows, artifacts = [], {str(manifest_path.relative_to(ROOT)): sha(manifest_path)}
    for job in manifest['maps']:
        source = ROOT / job.get('input', job.get('path'))
        expected = job.get('sha256', job.get('input_sha256'))
        assert sha(source) == expected
        assert '/maps/train/medium/' in str(source)
        folder = BLOCK / 'runs/medium-train/maps' / job['map_id']
        path = folder / 'summary.json'
        summary = json.loads(path.read_text())
        artifacts[str(path.relative_to(ROOT))] = sha(path)
        item = {'map_id': job['map_id'], 'input_sha256': expected, 'arms': {}}
        for name, arm in summary['arms'].items():
            m = arm.get('metrics', {})
            c = m.get('constructor_metrics', {})
            row = {
                'solve_status': arm['status'], 'solve_message': arm.get('message'),
                'solve_seconds': arm.get('eval_seconds'),
                'greedy_status': m.get('preplan_greedy_primary_status'),
                'greedy_budget_s': m.get('preplan_greedy_primary_budget_s'),
                'greedy_seconds': m.get('preplan_greedy_primary_time_s'),
                'constructor_status': m.get('constructor_status'),
                'constructor_seconds': m.get('constructor_time_s'),
                'constructor_requested_seconds': m.get('constructor_requested_time_s'),
                'deepest_depth': c.get('deepest_depth'), 'partial_loaded': c.get('partial_loaded'),
                'backtracks': c.get('backtracks'), 'restarts': c.get('restarts'),
                'construction_attempts': c.get('construction_attempts'),
                'oracle_dead_end': c.get('oracle_dead_end'),
                'last_deficits': c.get('v4_last_deficits'),
                'securing_geometry_rejections': c.get('securing_geometry_rejections'),
                'search_scope': c.get('v4_search_scope'),
                'terminal_status': m.get('terminal_status'),
                'terminal_seconds': m.get('terminal_time_s'), 'checks': {},
            }
            for variant in ('raw', 'compact'):
                record = arm.get(variant, {})
                report_path = record.get('report_path')
                detail = {k: record.get(k) for k in (
                    'passed', 'finalize_status', 'message', 'finisher_seconds',
                    'checker_attempted', 'checker_passed', 'checker_seconds', 'report_path')}
                if report_path:
                    p = ROOT / report_path
                    report = json.loads(p.read_text())
                    artifacts[str(p.relative_to(ROOT))] = sha(p)
                    # Checker JSON stores violations in parts; top-level is a summary.
                    detail['violations_by_part'] = {
                        key: {'rule_counts': dict(Counter(v.get('rule_id', '') for v in part.get('violations', []))),
                              'samples': part.get('violations', [])[:3], 'error': part.get('error')}
                        for key, part in report.get('parts', {}).items()
                        if part.get('violations') or part.get('error')}
                    detail['failed_margins_by_part'] = {
                        key: {k: v for k, v in part.get('margins', {}).items()
                              if isinstance(v, (int, float)) and v < 0}
                        for key, part in report.get('parts', {}).items()
                        if any(isinstance(v, (int, float)) and v < 0
                               for v in part.get('margins', {}).values())}
                row['checks'][variant] = detail
            item['arms'][name] = row
        rows.append(item)
    counts = {arm: dict(Counter(row['arms'][arm]['solve_status'] for row in rows))
              for arm in ('default', 'rule')}
    output = {'manifest_sha256': sha(manifest_path), 'maps': len(rows),
              'counts': counts, 'rows': rows, 'artifacts_sha256': artifacts}
    (HERE / 'baseline.json').write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps({'maps': len(rows), 'counts': counts, 'artifact_hashes': len(artifacts)}))


if __name__ == '__main__':
    main()
